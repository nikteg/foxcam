"""Recording profiles, burned-in masks and the processing stage they share.

Normally MediaMTX records ``<camera>`` exactly as the camera sends it. When a camera's
recording profile re-encodes, or its censor rectangles are burned in, the camera's
source goes to ``<camera>/raw`` instead and MediaMTX runs an ffmpeg that reads it, draws
the masks, scales / re-encodes and publishes ``<camera>``. Everything downstream
(recording, live view, low-data view, snapshots, workers) keeps reading ``<camera>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import CameraConfig, RecordingProfileConfig, Settings
from .db import db_ready, session_scope
from .models import CameraState, MaskVersion

RAW_SUFFIX = "/raw"

BUILTIN: list[RecordingProfileConfig] = [
    RecordingProfileConfig(id="original", name="Original", managed="builtin"),
    RecordingProfileConfig(id="1080p", name="1080p", transcode=True,
                           max_height=1080, bitrate_kbit=2500, managed="builtin"),
    RecordingProfileConfig(id="720p", name="720p", transcode=True,
                           max_height=720, bitrate_kbit=1200, managed="builtin"),
    RecordingProfileConfig(id="480p", name="480p",
                           transcode=True, max_height=480, fps=15, bitrate_kbit=600,
                           managed="builtin"),
    RecordingProfileConfig(id="h265", name="H.265", transcode=True,
                           codec="h265", managed="builtin"),
]


class RecordingError(ValueError):
    pass


def raw_path(camera_id: str) -> str:
    return f"{camera_id}{RAW_SUFFIX}"


def all_recording_profiles(settings: Settings) -> list[RecordingProfileConfig]:
    """Built-in profiles, with config.yaml profiles replacing / extending them."""
    by_id = {p.id: p for p in BUILTIN}
    for p in settings.recording_profiles:
        by_id[p.id] = p
    return list(by_id.values())


def recording_profile(settings: Settings, profile_id: str | None
                      ) -> RecordingProfileConfig | None:
    return next((p for p in all_recording_profiles(settings) if p.id == profile_id), None)


def _state(camera_id: str) -> dict[str, Any]:
    if not db_ready():
        return {"recording_profile": None, "burn_masks": False, "rects": []}
    with session_scope() as s:
        st = s.get(CameraState, camera_id)
        mask = s.get(MaskVersion, st.mask_version_id) if st and st.mask_version_id else None
        return {"recording_profile": st.recording_profile if st else None,
                "burn_masks": bool(st and st.burn_masks),
                "rects": list(mask.rects) if mask else []}


def camera_recording_profile(settings: Settings, cam: CameraConfig,
                             state: dict[str, Any] | None = None
                             ) -> tuple[RecordingProfileConfig, str]:
    """The camera's recording profile and where the choice comes from (ui/config/default)."""
    state = state if state is not None else _state(cam.id)
    for pid, source in ((state["recording_profile"], "ui"), (cam.recording_profile, "config"),
                        (settings.recording_profile_default, "default")):
        if pid and (p := recording_profile(settings, pid)):
            return p, source
    return BUILTIN[0], "default"


def set_camera_recording_profile(settings: Settings, camera_id: str,
                                 profile_id: str | None) -> None:
    """UI selection; None = back to the camera's config / the default."""
    if profile_id is not None and recording_profile(settings, profile_id) is None:
        raise RecordingError(f"unknown recording profile '{profile_id}'")
    with session_scope() as s:
        st = s.get(CameraState, camera_id) or CameraState(camera_id=camera_id)
        s.add(st)
        st.recording_profile = profile_id


def set_burn_masks(camera_id: str, on: bool) -> None:
    with session_scope() as s:
        st = s.get(CameraState, camera_id) or CameraState(camera_id=camera_id)
        s.add(st)
        st.burn_masks = on


@dataclass
class Processing:
    """What the processing stage does for one camera."""

    profile: RecordingProfileConfig
    rects: list[list[float]]

    @property
    def transcode(self) -> bool:
        return self.profile.transcode


def processing(settings: Settings, cam: CameraConfig) -> Processing | None:
    """None = record the camera's stream untouched (no processing stage)."""
    state = _state(cam.id)
    profile, _ = camera_recording_profile(settings, cam, state)
    rects = state["rects"] if state["burn_masks"] else []
    if not profile.transcode and not rects:
        return None
    return Processing(profile, rects)


def source_path(settings: Settings, cam: CameraConfig) -> str:
    """The MediaMTX path the camera's own stream goes to."""
    return raw_path(cam.id) if processing(settings, cam) else cam.id


def video_encoder_args(encoder: str, *, bitrate_kbit: int | None, fps: int | None,
                       live: bool) -> list[str]:
    """Encoder flags. ``live``: no B-frames and frequent keyframes (WebRTC friendly)."""
    gop = (fps or 15) * 2
    args = ["-c:v", encoder, "-g", str(gop), "-bf", "0"] if live else ["-c:v", encoder,
                                                                        "-g", str(gop)]
    if encoder in ("libx264", "libx265"):
        args += ["-preset", "veryfast"]
        if live and encoder == "libx264":
            args += ["-tune", "zerolatency"]
        if encoder == "libx265":
            args += ["-tag:v", "hvc1", "-x265-params", "log-level=error"]
    elif encoder.endswith("_videotoolbox"):
        args += ["-realtime", "1", "-allow_sw", "1"]
        if encoder.startswith("hevc"):
            args += ["-tag:v", "hvc1"]
    if bitrate_kbit:
        args += ["-b:v", f"{bitrate_kbit}k", "-maxrate", f"{bitrate_kbit}k",
                 "-bufsize", f"{bitrate_kbit * 2}k"]
    elif encoder in ("libx264", "libx265"):
        args += ["-crf", "23" if encoder == "libx264" else "26"]
    elif encoder.endswith("_videotoolbox"):
        args += ["-q:v", "60"]
    return args


def mask_filters(rects: list[list[float]]) -> list[str]:
    return [f"drawbox=x=iw*{x:.5f}:y=ih*{y:.5f}:w=iw*{w:.5f}:h=ih*{h:.5f}:color=black:t=fill"
            for x, y, w, h in rects]


def processor_args(settings: Settings, cam: CameraConfig, proc: Processing) -> list[str]:
    """ffmpeg reading ``<camera>/raw`` and publishing the processed ``<camera>``.

    Run by MediaMTX, which fills in ``$RTSP_PORT`` and ``$MTX_PATH``.
    """
    m, p = settings.mediamtx, proc.profile
    vf = [*mask_filters(proc.rects)]
    if p.transcode and p.fps:
        vf.insert(0, f"fps={p.fps}")
    if p.transcode and p.max_height:
        # Only ever scale down; the comma inside min() must be escaped for the filtergraph.
        vf.append(f"scale=-2:min({p.max_height}\\,ih)")
    vf.append("format=yuv420p")
    encoder = m.encoder_h265 if p.transcode and p.codec == "h265" else m.encoder
    return [m.ffmpeg, "-v", "error", "-rtsp_transport", "tcp",
            "-i", f"rtsp://localhost:$RTSP_PORT/{raw_path(cam.id)}",
            "-map", "0:v:0", "-map", "0:a:0?", "-vf", ",".join(vf),
            *video_encoder_args(encoder, bitrate_kbit=p.bitrate_kbit if p.transcode else None,
                                fps=p.fps if p.transcode else None, live=True),
            "-c:a", "copy",
            "-f", "rtsp", "-rtsp_transport", "tcp", "rtsp://localhost:$RTSP_PORT/$MTX_PATH"]
