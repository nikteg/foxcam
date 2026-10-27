"""The cloud relay's MPEG-TS gets the camera's timestamps, continuous across sessions."""

from __future__ import annotations

import itertools
import subprocess

from foxcam.ingest.ts_retime import Retimer

from .conftest import needs_ffmpeg


def _ts(path, start: float) -> bytes:
    """2 s of 15 fps video whose timestamps start at ``start`` (a camera clock)."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=size=160x90:rate=15", "-t", "2", "-c:v", "libx264",
                    "-preset", "ultrafast", "-output_ts_offset", str(start), str(path)],
                   check=True)
    return path.read_bytes()


def _pts(path) -> list[float]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return sorted(float(v.split(",")[0]) for v in out.split())


@needs_ffmpeg
def test_sessions_keep_camera_spacing_and_real_gaps(tmp_path):
    first = _ts(tmp_path / "a.ts", 100.0)
    second = _ts(tmp_path / "b.ts", 7.0)  # the next session: the camera clock elsewhere
    now = [0.0]
    out = bytearray()
    r = Retimer(out.extend, clock=lambda: now[0])
    for i in range(0, len(first), 1000):  # arrives over 2 s, not on packet boundaries
        now[0] = 2.0 * i / len(first)
        r.write(first[i:i + 1000])
    now[0] = 2.0 + 5.0  # the next session 5 s later
    r.new_session()
    r.write(second)
    (tmp_path / "out.ts").write_bytes(out)

    pts = _pts(tmp_path / "out.ts")
    assert len(pts) == 60
    gaps = [b - a for a, b in itertools.pairwise(pts)]
    # Within a session: the camera's 1/15 s.
    assert all(abs(g - 1 / 15) < 0.002 for g in gaps[:29] + gaps[30:])
    # Between the sessions: the real 5 s since the last output, not the camera clock's
    # jump back.
    assert 4.8 < gaps[29] < 5.3


@needs_ffmpeg
def test_the_clock_wrapping_is_not_a_jump(tmp_path):
    """MPEG timestamps wrap every 26.5 h; that is no reason to anchor again."""
    data = _ts(tmp_path / "w.ts", 2**33 / 90_000 - 2.5)  # wraps a second in
    out = bytearray()
    r = Retimer(out.extend, clock=lambda: 0.0)
    r.write(data)
    assert r._offset == 0  # still as anchored first: the camera's own times
    assert bytes(out) == data


@needs_ffmpeg
def test_a_jump_ahead_in_the_camera_clock_is_reported(tmp_path):
    """The relay catching up drops footage: the camera's clock jumps ahead within a
    session. The output closes it up (real time since), and the jump is reported."""
    first = _ts(tmp_path / "a.ts", 100.0)
    later = _ts(tmp_path / "b.ts", 113.0)  # 11 s the camera never delivered
    skips: list[float] = []
    out = bytearray()
    r = Retimer(out.extend, clock=lambda: 0.0, on_skip=skips.append)
    r.write(first)
    r.write(later)
    assert len(skips) == 1
    assert 10 < skips[0] < 11.5  # from the last frame to the next PCR: about


@needs_ffmpeg
def test_a_new_session_is_not_a_skip(tmp_path):
    skips: list[float] = []
    r = Retimer(lambda _: None, clock=lambda: 0.0, on_skip=skips.append)
    r.write(_ts(tmp_path / "a.ts", 100.0))
    r.new_session()
    r.write(_ts(tmp_path / "b.ts", 500.0))
    assert skips == []
