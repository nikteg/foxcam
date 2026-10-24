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
from .hwaccel import Encode, default_executable, transcode_command
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
    # Always with a bitrate: in constant-quality mode H.265 came out *bigger* than a 3K
    # camera's own 7 Mbit/s stream, and on hardware encoders it gains little over H.264
    # at the same bitrate. Size comes from resolution and bitrate; H.265 adds a bit.
    RecordingProfileConfig(id="h265", name="1080p H.265", transcode=True, codec="h265",
                           max_height=1080, bitrate_kbit=1500, managed="builtin"),
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


def transcode(settings: Settings, enc: Encode, src_path: str) -> list[str]:
    """``media-streamer transcode`` from ``src_path`` to the hook's own path, run by
    MediaMTX (which fills in ``$RTSP_PORT`` and ``$MTX_PATH``)."""
    m = settings.mediamtx
    return transcode_command(enc, f"rtsp://localhost:$RTSP_PORT/{src_path}",
                             "rtsp://localhost:$RTSP_PORT/$MTX_PATH",
                             executable=m.transcoder or default_executable(),
                             preference=m.hwaccel, ffmpeg=m.ffmpeg, device=m.hw_device)


def processor_args(settings: Settings, cam: CameraConfig, proc: Processing) -> list[str]:
    """Reads ``<camera>/raw``, publishes the processed ``<camera>``."""
    p = proc.profile
    enc = Encode(codec=p.codec if p.transcode else "h264", masks=proc.rects,
                 max_height=p.max_height if p.transcode else None,
                 fps=p.fps if p.transcode else None,
                 bitrate_kbit=p.bitrate_kbit if p.transcode else None)
    return transcode(settings, enc, raw_path(cam.id))
