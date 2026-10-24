from __future__ import annotations

from media_streamer.config import Settings
from media_streamer.mediamtx import desired_paths
from media_streamer.web import queries
from media_streamer.web.deps import OPEN_ACCESS


def test_low_data_path_per_camera(settings: Settings) -> None:
    paths = desired_paths(settings)
    assert set(paths) == {"cam", "cam/low"}
    assert paths["cam"]["record"] is True
    low = paths["cam/low"]
    assert low["record"] is False and low["source"] == "publisher"
    cmd = low["runOnDemand"]
    assert " transcode --input rtsp://localhost:$RTSP_PORT/cam " in cmd
    assert "--output rtsp://localhost:$RTSP_PORT/$MTX_PATH" in cmd
    assert "--max-height 480 --fps 15 --bitrate 500" in cmd and "--audio opus" in cmd
    assert "--hwaccel auto" in cmd


def test_low_data_disabled(settings: Settings) -> None:
    settings.mediamtx.low_data.enabled = False
    assert set(desired_paths(settings)) == {"cam"}
    cam = queries.cameras(settings, {}, OPEN_ACCESS)[0]
    assert cam["webrtc_low_url"] is None


def test_low_data_urls(settings: Settings) -> None:
    cam = queries.cameras(settings, {}, OPEN_ACCESS)[0]
    # Live view goes through the server's login-checking proxy, never to MediaMTX directly.
    assert cam["webrtc_url"] == "/api/live/cam/main/webrtc/"
    assert cam["webrtc_low_url"] == "/api/live/cam/low/webrtc/"
    assert cam["hls_low_url"] == "/api/live/cam/low/hls/"


def test_original_profile_records_source_directly(settings: Settings) -> None:
    from media_streamer.recording import source_path

    cam = settings.camera("cam")
    assert source_path(settings, cam) == "cam"
    assert "runOnInit" not in desired_paths(settings)["cam"]


def test_transcoding_profile_adds_processing_stage(settings: Settings) -> None:
    from media_streamer import recording

    recording.set_camera_recording_profile(settings, "cam", "720p")
    cam = settings.camera("cam")
    assert recording.source_path(settings, cam) == "cam/raw"
    paths = desired_paths(settings)
    assert paths["cam/raw"] == {"source": "publisher", "record": False}
    main = paths["cam"]
    assert main["record"] is True and main["source"] == "publisher"
    cmd = main["runOnInit"]
    assert "rtsp://localhost:$RTSP_PORT/cam/raw" in cmd
    assert "--max-height 720 --bitrate 1200" in cmd and "--fps" not in cmd
    # The low-data view reads the processed stream.
    assert "$RTSP_PORT/cam " in paths["cam/low"]["runOnDemand"]


def test_burned_in_masks(settings: Settings) -> None:
    from media_streamer import recording
    from media_streamer.web import queries

    queries.save_camera_state("cam", [[0.1, 0.2, 0.3, 0.4]], False)
    assert recording.processing(settings, settings.camera("cam")) is None  # not burned in
    recording.set_burn_masks("cam", True)
    proc = recording.processing(settings, settings.camera("cam"))
    assert proc is not None and not proc.transcode
    cmd = desired_paths(settings)["cam"]["runOnInit"]
    assert "--mask 0.10000,0.20000,0.30000,0.40000" in cmd
    assert "--max-height" not in cmd and "--bitrate" not in cmd
    cam = queries.cameras(settings, {}, OPEN_ACCESS)[0]
    assert cam["processed"] is True and cam["recording_profile"] == "original"


def test_rtsp_camera_moves_to_raw(settings: Settings) -> None:
    from media_streamer import recording
    from media_streamer.config import CameraConfig

    settings.cameras.append(CameraConfig.model_validate(
        {"id": "door", "recording_profile": "h265",
         "source": {"type": "rtsp", "url": "rtsp://10.0.0.2/stream1"}}))
    paths = desired_paths(settings)
    assert paths["door/raw"]["source"] == "rtsp://10.0.0.2/stream1"
    assert "--codec h265" in paths["door"]["runOnInit"]
    profile, source = recording.camera_recording_profile(settings, settings.camera("door"))
    assert (profile.id, source) == ("h265", "config")


def test_unknown_recording_profile_rejected(settings: Settings) -> None:
    import pytest
    from media_streamer import recording

    with pytest.raises(recording.RecordingError):
        recording.set_camera_recording_profile(settings, "cam", "nope")
