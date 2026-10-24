from __future__ import annotations

from media_streamer.config import Settings
from media_streamer.mediamtx import desired_paths
from media_streamer.web import queries


def test_low_data_path_per_camera(settings: Settings) -> None:
    paths = desired_paths(settings)
    assert set(paths) == {"cam", "cam/low"}
    assert paths["cam"]["record"] is True
    low = paths["cam/low"]
    assert low["record"] is False and low["source"] == "publisher"
    cmd = low["runOnDemand"]
    assert "rtsp://localhost:$RTSP_PORT/cam " in cmd
    assert cmd.endswith("rtsp://localhost:$RTSP_PORT/$MTX_PATH")
    assert "scale=-2:480" in cmd and "500k" in cmd


def test_low_data_disabled(settings: Settings) -> None:
    settings.mediamtx.low_data.enabled = False
    assert set(desired_paths(settings)) == {"cam"}
    cam = queries.cameras(settings, {})[0]
    assert cam["webrtc_low_url"] is None


def test_low_data_urls(settings: Settings) -> None:
    cam = queries.cameras(settings, {})[0]
    assert cam["webrtc_low_url"] == "http://localhost:8889/cam/low/"
    assert cam["hls_low_url"] == "http://localhost:8888/cam/low/"
