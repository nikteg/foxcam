from __future__ import annotations

from foxcam.config import Settings
from foxcam.mediamtx import MediaMtx, desired_paths
from foxcam.web import queries
from foxcam.web.deps import OPEN_ACCESS


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
    from foxcam.recording import source_path

    cam = settings.camera("cam")
    assert source_path(settings, cam) == "cam"
    assert "runOnInit" not in desired_paths(settings)["cam"]


def test_transcoding_profile_is_not_live(settings: Settings) -> None:
    """Recording profiles re-encode at compaction; live view and recording stay original."""
    from foxcam import recording

    recording.set_camera_recording_profile(settings, "cam", "720p")
    cam = settings.camera("cam")
    assert recording.source_path(settings, cam) == "cam"
    assert "runOnInit" not in desired_paths(settings)["cam"]


def test_masks_add_processing_stage(settings: Settings) -> None:
    from foxcam import recording
    from foxcam.web import queries

    queries.save_camera_state("cam", [[0.1, 0.2, 0.3, 0.4]], False)
    recording.set_burn_masks("cam", True)
    cam = settings.camera("cam")
    assert recording.source_path(settings, cam) == "cam/raw"
    paths = desired_paths(settings)
    assert paths["cam/raw"] == {"source": "publisher", "record": False}
    main = paths["cam"]
    assert main["record"] is True and main["source"] == "publisher"
    assert "rtsp://localhost:$RTSP_PORT/cam/raw" in main["runOnInit"]
    # The low-data view reads the processed stream.
    assert "$RTSP_PORT/cam " in paths["cam/low"]["runOnDemand"]


def test_burned_in_masks(settings: Settings) -> None:
    from foxcam import recording
    from foxcam.web import queries

    queries.save_camera_state("cam", [[0.1, 0.2, 0.3, 0.4]], False)
    assert recording.processing(settings, settings.camera("cam")) is None  # not burned in
    recording.set_burn_masks("cam", True)
    proc = recording.processing(settings, settings.camera("cam"))
    assert proc is not None and proc.rects == [[0.1, 0.2, 0.3, 0.4]]
    cmd = desired_paths(settings)["cam"]["runOnInit"]
    assert "--mask 0.10000,0.20000,0.30000,0.40000" in cmd
    assert "--max-height" not in cmd and "--bitrate" not in cmd
    cam = queries.cameras(settings, {}, OPEN_ACCESS)[0]
    assert cam["processed"] is True and cam["recording_profile"] == "original"


def test_rtsp_camera_moves_to_raw(settings: Settings) -> None:
    from foxcam import recording
    from foxcam.config import CameraConfig
    from foxcam.web import queries

    settings.cameras.append(CameraConfig.model_validate(
        {"id": "door", "recording_profile": "h265",
         "source": {"type": "rtsp", "url": "rtsp://10.0.0.2/stream1"}}))
    assert desired_paths(settings)["door"]["source"] == "rtsp://10.0.0.2/stream1"
    queries.save_camera_state("door", [[0, 0, 0.5, 0.5]], False)
    recording.set_burn_masks("door", True)
    paths = desired_paths(settings)
    assert paths["door/raw"]["source"] == "rtsp://10.0.0.2/stream1"
    assert "--mask 0.00000,0.00000,0.50000,0.50000" in paths["door"]["runOnInit"]
    profile, source = recording.camera_recording_profile(settings, settings.camera("door"))
    assert (profile.id, source) == ("h265", "config")


def test_unknown_recording_profile_rejected(settings: Settings) -> None:
    import pytest
    from foxcam import recording

    with pytest.raises(recording.RecordingError):
        recording.set_camera_recording_profile(settings, "cam", "nope")


def test_bitrate_between_samples(settings: Settings) -> None:
    mtx = MediaMtx(settings)
    assert mtx._bitrate("cam", 1_000, 100.0) is None  # one sample: nothing to compare
    assert mtx._bitrate("cam", 251_000, 110.0) == 200.0  # 250 kB in 10 s
    assert mtx._bitrate("cam", 300_000, 111.0) == 200.0  # too soon: the last value
    assert mtx._bitrate("cam", 10, 120.0) is None  # restarted
    assert mtx._bitrate("cam", 1_000_000, 500.0) is None  # stale


def test_live_stream_facts() -> None:
    path = {"ready": True, "readyTime": "2026-10-28T22:14:49.828307123+01:00",
            "tracks2": [{"codec": "H264", "codecProps": {"width": 1280, "height": 720}},
                        {"codec": "Opus", "codecProps": {"channelCount": 1}}],
            "readers": [{"type": "webRTCSession"}, {"type": "rtspSession"},
                        {"type": "hlsMuxer"}],
            "bitrate_kbit": 512.0}
    assert queries._stream(path) == {"codecs": ["H264", "Opus"], "width": 1280,
                                     "height": 720, "bitrate_kbit": 512.0}
    assert queries._stream({**path, "ready": False}) is None
    assert queries._viewers(path) == 2
    assert queries._viewers(None) == 0
    assert queries._mediamtx_time(path["readyTime"]) == "2026-10-28T21:14:49Z"
