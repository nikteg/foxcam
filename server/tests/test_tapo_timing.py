"""tapo-check's report on how evenly a sample's frame timestamps are spaced."""

from __future__ import annotations

import subprocess

from foxcam.ingest.tapo_cloud import frame_timing

from .conftest import needs_ffmpeg


@needs_ffmpeg
def test_even_frames_are_reported_as_such(tmp_path):
    ts = tmp_path / "even.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=15",
                    "-t", "2", "-c:v", "libx264", "-preset", "ultrafast", str(ts)], check=True)
    report = frame_timing(ts)
    assert "median gap 67 ms" in report and "0% of gaps under 10 ms" in report
