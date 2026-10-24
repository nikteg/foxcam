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


def test_env_vars_in_comments_are_ignored(tmp_path, monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    s = load_settings(write(tmp_path, "worker_token: abc\n# url: ${NOPE}\n  # ${NOPE}\n"))
    assert s.worker_token == "abc"


def test_example_config_loads(monkeypatch):
    for name in ("TAPO_USER", "TAPO_PASS", "VAR"):
        monkeypatch.delenv(name, raising=False)
    load_settings(Path(__file__).parents[2] / "config.example.yaml")


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


def test_public_url_from_environment(tmp_path, monkeypatch):
    from media_streamer.config import Settings

    monkeypatch.setenv("MEDIA_STREAMER_PUBLIC_URL", "http://192.168.1.10:5173/")
    assert Settings.model_validate({"data_dir": str(tmp_path)}).public_url == \
        "http://192.168.1.10:5173"
    explicit = Settings.model_validate({"data_dir": str(tmp_path), "public_url": "https://cams"})
    assert explicit.public_url == "https://cams"  # the config wins
