"""Hardware video encoding/decoding for the live transcodes, with automatic fallback.

There is no single cross-platform API, so each OS has its own backends:

========== ==================================== =========================================
backend    hardware                             notes
========== ==================================== =========================================
videotoolbox  every Mac                         macOS' own API
nvenc      NVIDIA                               Linux and Windows; Docker: NVIDIA toolkit
qsv        Intel Quick Sync                     Linux (via VAAPI device) and Windows
vaapi      Intel and AMD (Mesa), Linux          the generic Linux API; Docker: /dev/dri
amf        AMD, Windows
mf         any GPU, Windows                     Media Foundation, the generic Windows API
vulkan     any GPU with Vulkan Video            cross-vendor and cross-OS, but drivers are
                                                still young: never picked by ``auto``
software   CPU (libx264 / libx265)              always works
========== ==================================== =========================================

``auto`` tries the platform's backends in order with a tiny real encode, so it only picks
one whose driver, device and ffmpeg build all work *where ffmpeg runs* (in Docker: inside
the MediaMTX container). ``foxcam transcode`` also falls back to software when a
hardware encoder fails on the real stream.

Decoding uses the same backend (``-hwaccel``); ffmpeg itself falls back to software
decoding for streams the hardware cannot decode. Filters (masks, scaling, fps) run on the
CPU, which keeps one code path for every backend; at camera resolutions that is cheap
compared to encoding.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

BACKENDS = ("videotoolbox", "nvenc", "qsv", "vaapi", "amf", "mf", "vulkan", "software")
DEFAULT_DEVICE = "/dev/dri/renderD128"  # Linux render node for vaapi / qsv / vulkan
# Hardware that fails within this many seconds of starting falls back to software.
STARTUP_SECONDS = 15.0
# A backend that failed is tried again after this long (driver installed, GPU freed, …).
RETRY_SECONDS = 600.0


def auto_order(system: str | None = None) -> list[str]:
    """Backends ``auto`` tries, best first."""
    system = system or platform.system()
    if system == "Darwin":
        return ["videotoolbox", "software"]
    if system == "Windows":
        return ["nvenc", "qsv", "amf", "mf", "software"]
    # VAAPI before Quick Sync: same Intel hardware, but h264_qsv rejects some camera
    # streams (odd frame rates) that VAAPI handles.
    return ["nvenc", "vaapi", "qsv", "software"]


_ENCODERS = {
    "videotoolbox": ("h264_videotoolbox", "hevc_videotoolbox"),
    "nvenc": ("h264_nvenc", "hevc_nvenc"),
    "qsv": ("h264_qsv", "hevc_qsv"),
    "vaapi": ("h264_vaapi", "hevc_vaapi"),
    "amf": ("h264_amf", "hevc_amf"),
    "mf": ("h264_mf", "hevc_mf"),
    "vulkan": ("h264_vulkan", "hevc_vulkan"),
    "software": ("libx264", "libx265"),
}


def encoder_name(backend: str, codec: str) -> str:
    return _ENCODERS[backend][codec == "h265"]


@dataclass
class Encode:
    """What to produce; independent of the backend."""

    codec: str = "h264"  # h264 | h265
    max_height: int | None = None  # scale down to at most this (never up)
    fps: int | None = None
    bitrate_kbit: int | None = None  # None = constant quality
    masks: list[list[float]] = field(default_factory=list)  # normalized x, y, w, h
    audio: str = "copy"  # copy | opus | none
    live: bool = True  # no B-frames, keyframe every 2 s


def _device_args(backend: str, device: str | None) -> tuple[list[str], bool]:
    """(global args, whether frames must be uploaded to the device before encoding)."""
    dev = device or DEFAULT_DEVICE
    if backend == "vaapi":
        return ["-init_hw_device", f"vaapi=hw:{dev}", "-hwaccel", "vaapi",
                "-hwaccel_device", "hw", "-filter_hw_device", "hw"], True
    if backend == "qsv":
        if platform.system() == "Linux":
            return ["-init_hw_device", f"vaapi=va:{dev}", "-init_hw_device", "qsv=hw@va",
                    "-hwaccel", "qsv", "-hwaccel_device", "hw"], False
        return ["-init_hw_device", "qsv=hw", "-hwaccel", "qsv", "-hwaccel_device", "hw"], False
    if backend == "vulkan":
        init = "vulkan=hw" if not device else f"vulkan=hw:{device}"
        return ["-init_hw_device", init, "-hwaccel", "vulkan", "-hwaccel_device", "hw",
                "-filter_hw_device", "hw"], True
    if backend == "nvenc":
        return ["-hwaccel", "cuda"], False
    if backend == "videotoolbox":
        return ["-hwaccel", "videotoolbox"], False
    if backend in ("amf", "mf"):
        return ["-hwaccel", "d3d11va"], False
    return [], False


def _rate_args(backend: str, enc: Encode) -> list[str]:
    if enc.bitrate_kbit:
        b = enc.bitrate_kbit
        return ["-b:v", f"{b}k", "-maxrate", f"{b}k", "-bufsize", f"{b * 2}k"]
    # Constant quality, roughly CRF 23 (H.264) / 26 (H.265) everywhere.
    q = 26 if enc.codec == "h265" else 23
    return {
        "software": ["-crf", str(q)],
        "nvenc": ["-rc", "vbr", "-cq", str(q + 2), "-b:v", "0"],
        "qsv": ["-global_quality", str(q + 2)],
        "vaapi": ["-rc_mode", "CQP", "-qp", str(q + 2)],
        "videotoolbox": ["-q:v", "60"],
        "amf": ["-rc", "cqp", "-qp_i", str(q + 2), "-qp_p", str(q + 2)],
        "vulkan": ["-rc_mode", "cqp", "-qp", str(q + 2)],
    }.get(backend, ["-b:v", "4000k"])  # mf: no quality mode worth trusting


def _encoder_args(backend: str, enc: Encode) -> list[str]:
    encoder = encoder_name(backend, enc.codec)
    args = ["-c:v", encoder]
    if backend == "software":
        args += ["-preset", "veryfast"]
        if enc.codec == "h265":
            args += ["-x265-params", "log-level=error"]
        elif enc.live:
            args += ["-tune", "zerolatency"]
    elif backend == "nvenc":
        args += ["-preset", "p4", *(["-tune", "ll"] if enc.live else [])]
    elif backend == "qsv":
        args += ["-preset", "veryfast", *(["-low_power", "1"] if enc.live else [])]
    elif backend == "videotoolbox":
        # allow_sw 0: fail instead of silently encoding on the CPU (so auto can tell).
        args += [*(["-realtime", "1"] if enc.live else []), "-allow_sw", "0"]
    elif backend == "amf":
        args += ["-usage", "lowlatency" if enc.live else "transcoding"]
    elif backend == "mf":
        args += ["-hw_encoding", "1", "-scenario", "live_streaming"]
    if enc.live:
        args += ["-g", str((enc.fps or 15) * 2)]
        if backend != "mf":
            args += ["-bf", "0"]
    if enc.codec == "h265":
        args += ["-tag:v", "hvc1"]  # plays in Safari / browsers from fMP4
    return args + _rate_args(backend, enc)


def _filters(backend: str, enc: Encode, upload: bool) -> str:
    vf = [f"fps={enc.fps}"] if enc.fps else []
    vf += [f"drawbox=x=iw*{x:.5f}:y=ih*{y:.5f}:w=iw*{w:.5f}:h=ih*{h:.5f}:color=black:t=fill"
           for x, y, w, h in enc.masks]
    if enc.max_height:
        vf.append(f"scale=-2:min({enc.max_height}\\,ih)")
    vf.append("format=nv12,hwupload" if upload
              else "format=nv12" if backend == "qsv" else "format=yuv420p")
    return ",".join(vf)


def ffmpeg_args(backend: str, enc: Encode, src: str, dst: str, *, ffmpeg: str = "ffmpeg",
                device: str | None = None, input_args: Sequence[str] = (),
                output_args: Sequence[str] = ()) -> list[str]:
    """Complete ffmpeg command: RTSP (or a file) in, (masked / scaled / re-encoded) RTSP (or
    a file) out. ``input_args`` go before ``-i``, ``output_args`` before the output."""
    dev_args, upload = _device_args(backend, device)
    audio = {"copy": ["-c:a", "copy"],
             "opus": ["-c:a", "libopus", "-b:a", "24k", "-ac", "1", "-ar", "48000"],
             "none": ["-an"]}[enc.audio]
    src_args = ["-rtsp_transport", "tcp"] if src.startswith("rtsp") else []
    dst_args = ["-f", "rtsp", "-rtsp_transport", "tcp"] if dst.startswith("rtsp") else []
    return [ffmpeg, "-hide_banner", "-v", "error", *dev_args, *src_args, *input_args,
            "-i", src, "-map", "0:v:0", *(["-map", "0:a:0?"] if enc.audio != "none" else []),
            "-vf", _filters(backend, enc, upload), *_encoder_args(backend, enc), *audio,
            *output_args, *dst_args, dst]


# --- detection ----------------------------------------------------------------------------

def _cache_file(ffmpeg: str, device: str | None) -> Path:
    key = f"{ffmpeg}|{device or ''}".encode().hex()[:40]
    return Path(tempfile.gettempdir()) / f"foxcam-hwaccel-{key}.json"


def _load_cache(path: Path) -> dict[str, list]:
    """{"nvenc:h264": [works, checked_at]}; failures expire after RETRY_SECONDS."""
    try:
        cache = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {k: v for k, v in cache.items()
            if isinstance(v, list) and (v[0] or now - v[1] < RETRY_SECONDS)}


def _save_cache(path: Path, cache: dict[str, list]) -> None:
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(cache))


def probe(backend: str, codec: str = "h264", *, ffmpeg: str = "ffmpeg",
          device: str | None = None) -> tuple[bool, str]:
    """Encode a few test frames with this backend. (works, error message)."""
    if backend == "software":
        return True, ""
    enc = Encode(codec=codec, max_height=360, fps=15, bitrate_kbit=500, audio="none")
    args = ffmpeg_args(backend, enc, "testsrc2=size=640x360:rate=15", "-", ffmpeg=ffmpeg,
                       device=device)
    # Test pattern instead of a camera, and no decoder acceleration to test.
    i = args.index("-i")
    args[i:i + 2] = ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15"]
    args = [a for a in args if a not in ("-map", "0:v:0")]
    args[-1:] = ["-frames:v", "10", "-f", "null", "-"]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr.strip().splitlines() or ["failed"])[-1]


def select(preference: str = "auto", codec: str = "h264", *, ffmpeg: str = "ffmpeg",
           device: str | None = None, refresh: bool = False) -> str:
    """The backend to use: ``preference`` if it works, else the best working one."""
    cache_path = _cache_file(ffmpeg, device)
    cache = {} if refresh else _load_cache(cache_path)
    if preference in ("none", "software"):
        return "software"
    candidates = auto_order() if preference == "auto" else [preference, "software"]
    for backend in candidates:
        key = f"{backend}:{codec}"
        if key not in cache:
            ok, err = probe(backend, codec, ffmpeg=ffmpeg, device=device)
            cache[key] = [ok, time.time()]
            if not ok and preference != "auto":
                log.warning("hwaccel %s not usable (%s); using software", backend, err)
            _save_cache(cache_path, cache)
        if cache[key][0]:
            return backend
    return "software"


def mark_broken(backend: str, codec: str, *, ffmpeg: str, device: str | None) -> None:
    path = _cache_file(ffmpeg, device)
    cache = _load_cache(path)
    cache[f"{backend}:{codec}"] = [False, time.time()]
    _save_cache(path, cache)


def report(ffmpeg: str = "ffmpeg", device: str | None = None) -> list[tuple[str, str, bool, str]]:
    """(backend, codec, works, error) for every backend relevant on this machine."""
    out = []
    for backend in [*auto_order()[:-1], "vulkan"]:
        for codec in ("h264", "h265"):
            ok, err = probe(backend, codec, ffmpeg=ffmpeg, device=device)
            out.append((backend, codec, ok, err))
    return out


# --- running ------------------------------------------------------------------------------

def run(enc: Encode, src: str, dst: str, *, preference: str = "auto", ffmpeg: str = "ffmpeg",
        device: str | None = None) -> int:
    """Run the transcode until it ends, with the best backend; software if hardware fails
    right away. Forwards SIGTERM / SIGINT (MediaMTX stops on-demand commands that way)."""
    backend = select(preference, enc.codec, ffmpeg=ffmpeg, device=device)
    while True:
        args = ffmpeg_args(backend, enc, src, dst, ffmpeg=ffmpeg, device=device)
        log.info("transcode %s -> %s: %s (%s)", src.rsplit("/", 1)[-1], dst.rsplit("/", 1)[-1],
                 backend, encoder_name(backend, enc.codec))
        started = time.monotonic()
        proc = subprocess.Popen(args)

        def forward(signum: int, _frame: object, p: subprocess.Popen = proc) -> None:
            with contextlib.suppress(ProcessLookupError):
                p.send_signal(signum)

        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, forward)
        code = proc.wait()
        quick = time.monotonic() - started < STARTUP_SECONDS
        if code == 0 or backend == "software" or not quick:
            return code
        mark_broken(backend, enc.codec, ffmpeg=ffmpeg, device=device)
        failed, backend = backend, select(preference, enc.codec, ffmpeg=ffmpeg, device=device)
        log.warning("transcode: %s failed on this stream (exit %s); trying %s", failed, code,
                    backend)


def transcode_command(enc: Encode, src: str, dst: str, *, executable: str,
                      preference: str, ffmpeg: str, device: str | None) -> list[str]:
    """``foxcam transcode …`` arguments for MediaMTX's run hooks."""
    args = [executable, "transcode", "--input", src, "--output", dst,
            "--hwaccel", preference, "--codec", enc.codec, "--audio", enc.audio]
    if ffmpeg != "ffmpeg":
        args += ["--ffmpeg", ffmpeg]
    if device:
        args += ["--device", device]
    for flag, value in (("--max-height", enc.max_height), ("--fps", enc.fps),
                        ("--bitrate", enc.bitrate_kbit)):
        if value:
            args += [flag, str(value)]
    for x, y, w, h in enc.masks:
        args += ["--mask", f"{x:.5f},{y:.5f},{w:.5f},{h:.5f}"]
    if not enc.live:
        args.append("--archive")
    return args


def default_executable() -> str:
    """This installation's ``foxcam`` command (venv-aware)."""
    here = Path(sys.executable).with_name("foxcam.exe" if os.name == "nt"
                                          else "foxcam")
    return str(here) if here.exists() else "foxcam"
