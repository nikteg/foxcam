from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Frame:
    path: Path | None  # a JPEG; None for frames decoded straight into memory
    offset_ms: int  # from the start of the segment (or of the local file)
    image: Any = None  # BGR numpy array (height x width x 3) when decoded into memory


def _sample(fps: float) -> str:
    """Frame ``i`` is the picture at ``i / fps`` (its ``offset_ms``). ffmpeg's default
    (round=near) takes a frame up to half an interval later, so boxes trailed moving targets."""
    return f"fps={fps}:round=up"


def extract_frames(video: Path, out_dir: Path, fps: float, *, filters: Sequence[str] = (),
                   start_s: float | None = None, duration_s: float | None = None) -> list[Frame]:
    """Decode ``video`` to JPEGs at ``fps``. The server already sampled the video, so
    passing the job's ``input_fps`` keeps every frame; a lower value subsamples further.

    ``filters`` are extra ffmpeg video filters applied after sampling (used by
    ``detect-file`` to mimic the server's censoring).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-v", "error"]
    if start_s:
        args += ["-ss", f"{start_s:.3f}"]
    args += ["-i", str(video)]
    if duration_s:
        args += ["-t", f"{duration_s:.3f}"]
    args += ["-vf", ",".join([_sample(fps), *filters]), "-q:v", "2", str(out_dir / "%06d.jpg")]
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extraction failed: {proc.stderr.strip()[-500:]}")
    files = sorted(out_dir.glob("*.jpg"))
    base_ms = round((start_s or 0) * 1000)
    return [Frame(p, base_ms + round(i * 1000 / fps)) for i, p in enumerate(files)]


def _size(video: Path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "json", str(video)],
                         capture_output=True, text=True, check=True).stdout
    stream = json.loads(out)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def frame_batches(video: Path, fps: float, batch: int) -> Iterator[list[Frame]]:
    """Decode ``video`` at ``fps`` straight into memory (BGR arrays), ``batch`` frames at a
    time. No JPEG per frame written and read back: much less CPU than ``extract_frames``,
    and only one batch in memory at once."""
    import numpy as np

    width, height = _size(video)
    size = width * height * 3
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(video), "-an", "-vf", _sample(fps),
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None and proc.stderr is not None
    try:
        frames: list[Frame] = []
        i = 0
        while len(buf := proc.stdout.read(size)) == size:
            image = np.frombuffer(buf, np.uint8).reshape(height, width, 3)
            frames.append(Frame(None, round(i * 1000 / fps), image))
            i += 1
            if len(frames) == batch:
                yield frames
                frames = []
        if proc.wait() != 0:
            err = proc.stderr.read().decode(errors="replace").strip()
            raise RuntimeError(f"ffmpeg frame decoding failed: {err[-500:]}")
        if frames:
            yield frames
    finally:
        if proc.poll() is None:  # stopped early (an error in the detector)
            proc.kill()
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()
