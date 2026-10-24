from __future__ import annotations

import shutil

import pytest
from media_streamer import hwaccel
from media_streamer.hwaccel import Encode, ffmpeg_args


def _arg(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def test_software_filters_masks_and_rate() -> None:
    enc = Encode(max_height=720, fps=10, bitrate_kbit=800, masks=[[0.1, 0.2, 0.3, 0.4]])
    args = ffmpeg_args("software", enc, "rtsp://h/cam/raw", "rtsp://h/cam")
    vf = _arg(args, "-vf")
    assert vf.startswith("fps=10,drawbox=x=iw*0.10000")
    assert vf.endswith(r"scale=-2:min(720\,ih),format=yuv420p")
    assert _arg(args, "-c:v") == "libx264" and _arg(args, "-b:v") == "800k"
    assert "-hwaccel" not in args and args[-1] == "rtsp://h/cam"


@pytest.mark.parametrize(("backend", "encoder", "hwaccel", "upload"), [
    ("nvenc", "h264_nvenc", "cuda", False),
    ("qsv", "h264_qsv", "qsv", False),
    ("vaapi", "h264_vaapi", "vaapi", True),
    ("videotoolbox", "h264_videotoolbox", "videotoolbox", False),
    ("amf", "h264_amf", "d3d11va", False),
    ("mf", "h264_mf", "d3d11va", False),
])
def test_hardware_backends(backend: str, encoder: str, hwaccel: str, upload: bool) -> None:
    args = ffmpeg_args(backend, Encode(), "rtsp://h/a", "rtsp://h/b")
    assert _arg(args, "-c:v") == encoder
    assert _arg(args, "-hwaccel") == hwaccel  # hardware decoding too
    assert _arg(args, "-vf").endswith(",hwupload") is upload
    # Constant quality without a bitrate, never a bare default.
    assert "-b:v" not in args or backend in ("nvenc", "mf")


def test_vaapi_uses_render_node() -> None:
    args = ffmpeg_args("vaapi", Encode(codec="h265"), "a", "b", device="/dev/dri/renderD129")
    assert "vaapi=hw:/dev/dri/renderD129" in args
    assert _arg(args, "-c:v") == "hevc_vaapi" and _arg(args, "-tag:v") == "hvc1"


def test_auto_order_per_os() -> None:
    assert hwaccel.auto_order("Darwin") == ["videotoolbox", "software"]
    assert hwaccel.auto_order("Linux") == ["nvenc", "qsv", "vaapi", "software"]
    assert hwaccel.auto_order("Windows")[-2:] == ["mf", "software"]


def test_select_falls_back_and_caches(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_probe(backend, codec, **_):
        calls.append(backend)
        return backend == "vaapi", "no device"

    monkeypatch.setattr(hwaccel, "probe", fake_probe)
    monkeypatch.setattr(hwaccel, "auto_order", lambda: ["nvenc", "qsv", "vaapi", "software"])
    monkeypatch.setattr(hwaccel, "_cache_file", lambda *_: tmp_path / "cache.json")
    assert hwaccel.select("auto") == "vaapi"
    assert hwaccel.select("auto") == "vaapi"
    assert calls == ["nvenc", "qsv", "vaapi"]  # second call answered from the cache
    assert hwaccel.select("nvenc") == "software"  # unusable explicit choice
    assert hwaccel.select("none") == "software"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_software_probe_and_run(tmp_path) -> None:
    src = tmp_path / "in.mp4"
    import subprocess

    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10",
                    "-t", "1", "-pix_fmt", "yuv420p", str(src)], check=True)
    out = tmp_path / "out.mp4"
    enc = Encode(max_height=120, masks=[[0, 0, 0.5, 0.5]], audio="none")
    assert hwaccel.run(enc, str(src), str(out), preference="none") == 0
    assert out.stat().st_size > 0
