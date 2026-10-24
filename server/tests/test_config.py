from pathlib import Path

import pytest
from media_streamer.config import load_settings


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return p


def test_env_expansion_with_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("CAM_PASS", "s3cret")
    cfg = write(tmp_path, f"""
data_dir: {tmp_path}/data
worker_token: ${{TOKEN:-}}
cameras:
  - id: porch
    source: {{ type: rtsp, url: "rtsp://admin:${{CAM_PASS}}@10.0.0.2/stream1" }}
""")
    s = load_settings(cfg)
    assert s.cameras[0].source.url == "rtsp://admin:s3cret@10.0.0.2/stream1"
    assert s.worker_token is None  # empty string becomes "no token"
    assert s.recordings_dir == (tmp_path / "data" / "recordings").resolve()


def test_missing_env_var_is_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    with pytest.raises(ValueError, match="NOPE"):
        load_settings(write(tmp_path, "worker_token: ${NOPE}\n"))


def test_duplicate_camera_ids_rejected(tmp_path):
    with pytest.raises(ValueError, match="unique"):
        load_settings(write(tmp_path, f"""
data_dir: {tmp_path}/data
cameras:
  - {{ id: a, source: {{ type: test }} }}
  - {{ id: a, source: {{ type: test }} }}
"""))


def test_per_camera_segment_length(settings):
    assert settings.segment_seconds_for("cam") == 2
    assert settings.segment_seconds_for("unknown") == 2
