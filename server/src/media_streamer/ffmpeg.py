"""Thin, synchronous wrappers around ffmpeg/ffprobe. Call them from a worker thread."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

Rect = list[float]  # normalized [x, y, w, h]


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


def _mask_filters(rects: list[Rect]) -> list[str]:
    filters = []
    for x, y, w, h in rects:
        filters.append(
            f"drawbox=x=iw*{x:.5f}:y=ih*{y:.5f}:w=iw*{w:.5f}:h=ih*{h:.5f}:color=black:t=fill"
        )
    return filters


def make_masked_derivative(src: Path, dst: Path, rects: list[Rect], fps: float,
                           max_width: int) -> None:
    """Low-fps, downscaled H.264 copy with censor rectangles burned in. No audio.

    This is the only artifact a worker ever receives, so masked pixels never leave the box.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = [f"fps={fps}", *_mask_filters(rects),
          f"scale='min({max_width},iw)':-2", "format=yuv420p"]
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-an", "-vf", ",".join(vf),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
          "-movflags", "+faststart", str(dst)])


@dataclass
class LightStats:
    saturation_avg: float
    luma_avg: float


_STAT_RE = re.compile(r"lavfi\.signalstats\.(SATAVG|YAVG)=([0-9.]+)")


def light_stats(src: Path, duration_s: float, samples: int = 6) -> LightStats | None:
    """Average colour saturation and brightness over ~``samples`` frames of the segment."""
    # Spread the samples over the whole segment, whatever its length (min 1 frame).
    fps = min(max(samples / max(duration_s, 0.1), 0.02), 5.0)
    out = _run(["ffmpeg", "-v", "error", "-i", str(src), "-an",
                "-vf", f"fps={fps:.4f},scale=320:-2,signalstats,metadata=mode=print:file=-",
                "-f", "null", "-"], timeout=120).stdout
    sat, luma = [], []
    for key, val in _STAT_RE.findall(out):
        (sat if key == "SATAVG" else luma).append(float(val))
    if not sat:
        return None
    return LightStats(sum(sat) / len(sat), sum(luma) / len(luma) if luma else 0.0)


def thumbnail(src: Path, dst: Path, at_s: float = 0.0, width: int = 480,
              box: Rect | None = None, masks: list[Rect] | None = None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = []
    for x, y, w, h in masks or []:  # censor areas, e.g. for images leaving the server
        vf.append(f"drawbox=x=iw*{x:.5f}:y=ih*{y:.5f}:w=iw*{w:.5f}:h=ih*{h:.5f}"
                  ":color=black:t=fill")
    if box:
        x, y, w, h = box
        vf.append(f"drawbox=x=iw*{x:.5f}:y=ih*{y:.5f}:w=iw*{w:.5f}:h=ih*{h:.5f}"
                  ":color=red@0.9:t=4")
    vf.append(f"scale={width}:-2")
    _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(at_s, 0):.3f}", "-i", str(src),
          "-frames:v", "1", "-vf", ",".join(vf), "-q:v", "4", str(dst)], timeout=60)


def snapshot_from_url(url: str, dst: Path, timeout: float = 10) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-v", "error", "-rtsp_transport", "tcp", "-i", url,
          "-frames:v", "1", "-q:v", "3", str(dst)], timeout=timeout)


# A piece of footage for concat_clip: a file, or the [inpoint, outpoint) part of one
# (a segment inside a compacted archive).
ClipPart = tuple[Path, float | None, float | None]


def concat_clip(sources: list[Path | ClipPart], dst: Path, offset_s: float,
                duration_s: float) -> None:
    """Join consecutive segments and cut [offset, offset+duration) without re-encoding."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    list_file = dst.with_suffix(".txt")
    lines = []
    for src in sources:
        path, inpoint, outpoint = src if isinstance(src, tuple) else (src, None, None)
        lines.append(f"file '{path.as_posix()}'\n")
        if inpoint is not None:  # segments start on keyframes, so this cut is exact
            lines.append(f"inpoint {inpoint:.6f}\noutpoint {outpoint:.6f}\n")
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

