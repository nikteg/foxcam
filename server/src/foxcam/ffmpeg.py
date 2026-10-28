"""Thin, synchronous wrappers around ffmpeg/ffprobe. Call them from a worker thread."""

from __future__ import annotations

import itertools
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

Rect = list[float]  # normalized [x, y, w, h]

log = logging.getLogger(__name__)


class FfmpegError(RuntimeError):
    pass


def run_long(args: list[str]) -> subprocess.CompletedProcess[str]:
    """For hour-long jobs (joining / re-encoding archives)."""
    return _run(args, timeout=6 * 3600)


def _run(args: list[str], timeout: float = 600) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise FfmpegError(f"{args[0]} exited {proc.returncode}: {tail}")
    return proc


@dataclass
class ProbeInfo:
    duration_s: float
    width: int | None
    height: int | None
    codec: str | None


def probe(path: Path) -> ProbeInfo:
    out = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                "-show_streams", "-select_streams", "v:0", str(path)], timeout=60).stdout
    data = json.loads(out)
    stream = (data.get("streams") or [{}])[0]
    duration = float(data.get("format", {}).get("duration") or stream.get("duration") or 0)
    return ProbeInfo(duration, stream.get("width"), stream.get("height"), stream.get("codec_name"))


def sample(fps: float | str) -> str:
    """Frame ``n`` shows the picture at ``n / fps``, as a seek there does. ffmpeg's default
    (round=near) takes the last frame before ``(n + 0.5) / fps`` instead: detection boxes
    then trailed a moving target by up to half a frame interval."""
    return f"fps={fps}:round=up"


def _mask_filters(rects: list[Rect]) -> list[str]:
    filters = []
    for x, y, w, h in rects:
        filters.append(
            f"drawbox=x=iw*{x:.5f}:y=ih*{y:.5f}:w=iw*{w:.5f}:h=ih*{h:.5f}:color=black:t=fill"
        )
    return filters


def make_masked_derivative(src: Path, dst: Path, rects: list[Rect], fps: float,
                           max_width: int, vaapi_device: str | None = None) -> None:
    """Low-fps, downscaled H.264 copy with censor rectangles burned in. No audio.

    This is the only artifact a worker ever receives, so masked pixels never leave the box.
    With ``vaapi_device`` the GPU decodes and scales (a quarter of the CPU time for a 5 MP
    camera); software if that fails.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    out = ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-movflags", "+faststart", str(dst)]
    if vaapi_device and Path(vaapi_device).exists():
        vf = [sample(fps), f"scale_vaapi=w='min({max_width},iw)':h=-2:format=nv12",
              "hwdownload", "format=nv12", *_mask_filters(rects), "format=yuv420p"]
        try:
            _run(["ffmpeg", "-y", "-v", "error", "-init_hw_device", f"vaapi=hw:{vaapi_device}",
                  "-hwaccel", "vaapi", "-hwaccel_output_format", "vaapi",
                  "-hwaccel_device", "hw", "-i", str(src), "-vf", ",".join(vf), *out])
            return
        except FfmpegError as e:
            log.warning("GPU preprocessing failed, using the CPU: %s", e)
    vf = [sample(fps), *_mask_filters(rects),
          f"scale='min({max_width},iw)':-2", "format=yuv420p"]
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", ",".join(vf), *out])


@dataclass
class LightStats:
    saturation_avg: float
    luma_avg: float


_STAT_RE = re.compile(r"lavfi\.signalstats\.(SATAVG|YAVG)=([0-9.]+)")


def light_stats(src: Path, duration_s: float, samples: int = 6) -> LightStats | None:
    """Average colour saturation and brightness over ~``samples`` frames of the segment."""
    # Spread the samples over the whole segment, whatever its length (min 1 frame).
    fps = min(max(samples / max(duration_s, 0.1), 0.02), 5.0)
    # Keyframes are enough for a few samples, and a tenth of the decoding.
    out = _run(["ffmpeg", "-v", "error", "-skip_frame", "nokey", "-i", str(src), "-an",
                "-vf", f"fps={fps:.4f},scale=320:-2,signalstats,metadata=mode=print:file=-",
                "-f", "null", "-"], timeout=120).stdout
    sat, luma = [], []
    for key, val in _STAT_RE.findall(out):
        (sat if key == "SATAVG" else luma).append(float(val))
    if not sat:
        return None
    return LightStats(sum(sat) / len(sat), sum(luma) / len(luma) if luma else 0.0)


#: How far before the moment a thumbnail decodes to find the frame on screen then.
THUMB_WINDOW_S = 1.0


def thumbnail(src: Path, dst: Path, at_s: float = 0.0, width: int = 480,
              box: Rect | None = None, masks: list[Rect] | None = None,
              label: str | None = None) -> None:
    """The frame on screen at ``at_s``, ``width`` wide. ``box``: drawn in red, with
    ``label`` (e.g. "fox 94%") on a red tag above it like the web app's; without the tag
    when ffmpeg can't draw text (no drawtext filter or no font)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = []
    for x, y, w, h in masks or []:  # censor areas, e.g. for images leaving the server
        vf.append(f"drawbox=x=iw*{x:.5f}:y=ih*{y:.5f}:w=iw*{w:.5f}:h=ih*{h:.5f}"
                  ":color=black:t=fill")
    tag = None
    if box:
        x, y, w, h = box
        vf.append(f"drawbox=x=iw*{x:.5f}:y=ih*{y:.5f}:w=iw*{w:.5f}:h=ih*{h:.5f}"
                  ":color=red@0.9:t=4")
        if label:
            tag = _box_tag(x, y, label, width)
    if tag:
        try:
            _grab(src, dst, at_s, [*vf, f"scale={width}:-2", tag])
            return
        except FfmpegError as e:
            log.info("thumbnail without the box's label (%s)", e)
    _grab(src, dst, at_s, [*vf, f"scale={width}:-2"])


def _box_tag(x: float, y: float, label: str, width: int) -> str | None:
    """drawtext for a box's label on the picture scaled to ``width``: white on red, at the
    box's top left corner just above it (inside it at the top of the picture). Only
    letters, digits, spaces, '.', '-' and '%' are kept: nothing the filter syntax reads."""
    text = re.sub(r"[^\w .%-]", "", label).strip()
    if not text:
        return None
    size, pad = max(width // 36, 10), max(width // 240, 2)
    return (f"drawtext=text='{text}':expansion=none:font=Sans:fontcolor=white"
            f":fontsize={size}:box=1:boxcolor=red@0.9:boxborderw={pad}"
            f":x=W*{x:.5f}+{pad}:y=max(H*{y:.5f}-th-{2 * pad}\\,{pad})")


def _grab(src: Path, dst: Path, at_s: float, vf: list[str]) -> None:
    # The picture on screen at ``at_s``: the last frame at or before it, as sampling picks
    # (``sample``). A seek gives the first frame at or after it: up to a frame later
    # (older cloud-relay recordings, stamped on arrival in bunches: a quarter second).
    at_s = max(at_s, 0)
    start = max(at_s - THUMB_WINDOW_S, 0)
    dst.unlink(missing_ok=True)
    _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.6f}", "-i", str(src),
          "-t", f"{at_s - start + 0.0005:.6f}", "-vf", ",".join(vf), "-update", "1",
          "-q:v", "4", str(dst)], timeout=60)
    if not dst.exists():  # no frame in the window (a gap in the recording): the next one
        _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at_s:.3f}", "-i", str(src),
              "-frames:v", "1", "-vf", ",".join(vf), "-q:v", "4", str(dst)], timeout=60)


def snapshot_from_url(url: str, dst: Path, timeout: float = 10) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-v", "error", "-rtsp_transport", "tcp", "-i", url,
          "-frames:v", "1", "-q:v", "3", str(dst)], timeout=timeout)


# A piece of footage for concat_clip: a file, or the [inpoint, outpoint) part of one
# (a segment inside a compacted archive), and how long after its start the next piece
# starts (None: right after it, or the last piece).
ClipPart = tuple[Path, float | None, float | None, float | None]


def concat_clip(sources: list[ClipPart], dst: Path, offset_s: float,
                duration_s: float) -> None:
    """Join segments and cut [offset, offset+duration) without re-encoding. A gap between
    two pieces (a piece's span longer than its footage) stays a gap: the picture holds
    there, so the clip's time is the clock's."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    list_file = dst.with_suffix(".txt")
    lines = []
    for path, inpoint, outpoint, span in sources:
        lines.append(f"file '{path.as_posix()}'\n")
        if inpoint is not None:  # segments start on keyframes, so this cut is exact
            lines.append(f"inpoint {inpoint:.6f}\noutpoint {outpoint:.6f}\n")
        if span is not None:  # the next piece starts this long after this one
            lines.append(f"duration {span:.6f}\n")
    list_file.write_text("".join(lines))
    try:
        # Input seeking snaps to the previous keyframe, so stream copy stays decodable.
        _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
              "-ss", f"{max(offset_s, 0):.3f}", "-i", str(list_file),
              "-t", f"{duration_s:.3f}", "-c", "copy", "-movflags", "+faststart", str(dst)])
    finally:
        list_file.unlink(missing_ok=True)


def cut(src: Path, dst: Path, offset_s: float, duration_s: float) -> None:
    """[offset, offset+duration) of a file, without re-encoding (offset on a keyframe)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.stem + ".part.mp4")
    _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{offset_s:.6f}", "-i", str(src),
          "-t", f"{duration_s:.6f}", "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
          "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(part)])
    part.replace(dst)


def timestamp_steps(path: Path, min_s: float) -> list[float]:
    """Where the video goes on after a pause in its timestamps of at least ``min_s``
    (seconds into the file), reading only the container's index."""
    out = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                "packet=pts_time", "-of", "csv=p=0", str(path)], timeout=600).stdout
    times = sorted(float(v.split(",")[0]) for v in out.split() if v.split(",")[0] != "N/A")
    return [round(b, 3) for a, b in itertools.pairwise(times) if b - a >= min_s]


def keyframe_times(path: Path) -> list[float]:
    """Timestamps (s) of the video keyframes, decoding only the keyframes."""
    out = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
                "-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0",
                str(path)], timeout=1800).stdout
    times = []
    for line in out.splitlines():  # "1.234" or "1.234," (side data adds empty fields)
        value = line.split(",", 1)[0].strip()
        if value and value != "N/A":
            times.append(float(value))
    return times


def stream_signature(path: Path) -> tuple:
    """Codec, size and pixel format of the video, and the audio codec: files with the
    same signature can be joined without re-encoding."""
    out = _run(["ffprobe", "-v", "error", "-show_entries",
                "stream=codec_type,codec_name,width,height,pix_fmt,sample_rate,channels",
                "-of", "json", str(path)], timeout=60).stdout
    streams = json.loads(out).get("streams", [])
    video = next((x for x in streams if x.get("codec_type") == "video"), None)
    if video is None:
        raise FfmpegError(f"{path.name}: no video stream")
    audio = next((x for x in streams if x.get("codec_type") == "audio"), {})
    return (video.get("codec_name"), video.get("width"), video.get("height"),
            video.get("pix_fmt"), audio.get("codec_name"), audio.get("sample_rate"),
            audio.get("channels"))

