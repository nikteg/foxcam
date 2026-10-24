from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Frame:
    path: Path
    offset_ms: int  # from the start of the segment (or of the local file)


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
    args += ["-vf", ",".join([f"fps={fps}", *filters]), "-q:v", "2", str(out_dir / "%06d.jpg")]
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extraction failed: {proc.stderr.strip()[-500:]}")
    files = sorted(out_dir.glob("*.jpg"))
    base_ms = round((start_s or 0) * 1000)
    return [Frame(p, base_ms + round(i * 1000 / fps)) for i, p in enumerate(files)]
