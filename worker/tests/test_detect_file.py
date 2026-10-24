"""Tests for the offline ``detect-file`` command.

The dummy-detector tests always run (they only need ffmpeg). The real-footage test runs
when you drop fox clips into ``samples/`` and pick a detector:

    MEDIA_WORKER_TEST_DETECTOR=speciesnet uv run --package media-worker pytest worker/tests
    MEDIA_WORKER_TEST_DETECTOR=ultralytics MEDIA_WORKER_TEST_CLASSES=fox ...
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from media_worker.detectors.speciesnet_det import _classification
from media_worker.local import main

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("clip") / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10",
                    "-t", "6", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def run(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    return exc.value.code or 0


def test_expect_met_and_outputs_written(clip: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    code = run([str(clip), "--dummy-label", "fox", "--dummy-every", "1", "--expect", "fox",
                "--mask", "0,0,0.5,0.5", "--night", "--out", str(out)])
    assert code == 0
    report = json.loads((out / "detections.json").read_text())
    assert report["frames"] == 12  # 6 s @ 2 fps
    assert report["summary"]["fox"]["count"] > 0
    assert report["masks"] == [[0, 0, 0.5, 0.5]]
    assert any((out / "annotated").glob("*.jpg"))


def test_expect_fails_without_detection(clip: Path) -> None:
    assert run([str(clip), "--expect", "fox"]) == 1


def test_expect_none(clip: Path) -> None:
    assert run([str(clip), "--dummy-label", "fox", "--dummy-every", "1",
                "--expect-none", "fox"]) == 1
    assert run([str(clip), "--expect-none", "fox"]) == 0


def test_speciesnet_output_formats() -> None:
    old = {"prediction": "uuid;mammalia;carnivora;canidae;vulpes;vulpes;red fox",
           "prediction_score": 0.91}
    new = {"processed_classification": {"class": "red fox", "score": 0.88}}
    assert _classification(old)[:2] == ("red fox", 0.91)
    assert _classification(new)[:2] == ("red fox", 0.88)
    assert _classification({})[0] is None


def test_speciesnet_per_box_classification(tmp_path, monkeypatch):
    """SpeciesNet >= 5 puts the species inside each detection."""
    import json as _json
    import subprocess as _sp

    from media_worker.detectors.speciesnet_det import SpeciesNetDetector
    from media_worker.frames import Frame

    frame = tmp_path / "000001.jpg"
    frame.write_bytes(b"")
    fox = "ac0e;mammalia;carnivora;canidae;vulpes;vulpes;red fox"
    output = {"predictions": [{"filepath": str(frame), "model_version": "4.0.3a", "detections": [
        {"label": "animal", "conf": 0.82, "bbox": [0.6, 0.2, 0.3, 0.6],
         "processed_classification": {"class": fox, "score": 0.97}},
        {"label": "person", "conf": 0.4, "bbox": [0, 0, 0.1, 0.1]}]}]}

    def fake_run(cmd, **_):
        out = cmd[cmd.index("--predictions_json") + 1]
        Path(out).write_text(_json.dumps(output))
        return _sp.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(_sp, "run", fake_run)
    det = SpeciesNetDetector()
    found = det.detect([Frame(frame, 500)], {"country": "SWE"})
    assert [(d.label, d.confidence) for d in found] == [("red fox", 0.97), ("person", 0.4)]
    assert found[0].extra["detector_conf"] == 0.82 and det.name == "speciesnet:4.0.3a"


REAL_DETECTOR = os.environ.get("MEDIA_WORKER_TEST_DETECTOR")
FOX_CLIPS = sorted(SAMPLES.glob("fox*.*")) if SAMPLES.is_dir() else []


@pytest.mark.skipif(not REAL_DETECTOR or not FOX_CLIPS,
                    reason="set MEDIA_WORKER_TEST_DETECTOR and add samples/fox*.mp4")
@pytest.mark.parametrize("night", [False, True], ids=["day", "night-sim"])
@pytest.mark.parametrize("path", FOX_CLIPS, ids=[p.name for p in FOX_CLIPS])
def test_real_fox_footage(path: Path, night: bool, tmp_path: Path) -> None:
    argv = [str(path), "--detector", REAL_DETECTOR, "--expect",
            os.environ.get("MEDIA_WORKER_TEST_LABEL", "fox"),
            "--min-confidence", os.environ.get("MEDIA_WORKER_TEST_MIN_CONF", "0.5"),
            "--out", str(tmp_path / "out")]
    if os.environ.get("MEDIA_WORKER_TEST_CLASSES"):
        argv += ["--classes", os.environ["MEDIA_WORKER_TEST_CLASSES"]]
    if night:
        argv.append("--night")
    assert run(argv) == 0, (tmp_path / "out" / "detections.json").read_text()


def test_speciesnet_keeps_top_guesses(tmp_path, monkeypatch):
    """The classifier's top 5 go into extra, per box (newer) or per image (older)."""
    import json as _json
    import subprocess as _sp

    from media_worker.detectors.speciesnet_det import SpeciesNetDetector
    from media_worker.frames import Frame

    frames = [tmp_path / "000001.jpg", tmp_path / "000002.jpg"]
    for f in frames:
        f.write_bytes(b"")
    tax = "u;mammalia;carnivora;canidae;{};{};{}"
    classes = [tax.format("", "", ""), tax.format("vulpes", "vulpes", "red fox"),
               tax.format("canis", "familiaris", "domestic dog"),
               tax.format("", "", "animal"), tax.format("felis", "catus", "cat"),
               tax.format("lynx", "lynx", "eurasian lynx")]
    scores = [0.40, 0.35, 0.10, 0.05, 0.04, 0.01]
    per_image = {"classifications": {"classes": classes, "scores": scores}}
    output = {"predictions": [
        # Newer: per box.
        {"filepath": str(frames[0]), "detections": [
            {"label": "animal", "conf": 0.8, "bbox": [0.1, 0.1, 0.2, 0.2],
             "processed_classification": {"class": classes[0], "score": 0.4},
             "classifications": {"classes": classes[1:3], "scores": [0.7, 0.2]}}]},
        # Older: per image, applied to the most confident box only.
        {"filepath": str(frames[1]), "prediction": classes[1], "prediction_score": 0.35,
         **per_image, "detections": [
             {"label": "animal", "conf": 0.9, "bbox": [0.5, 0.5, 0.2, 0.2]},
             {"label": "animal", "conf": 0.3, "bbox": [0.1, 0.1, 0.1, 0.1]}]}]}

    def fake_run(cmd, **_):
        Path(cmd[cmd.index("--predictions_json") + 1]).write_text(_json.dumps(output))
        return _sp.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(_sp, "run", fake_run)
    found = SpeciesNetDetector().detect([Frame(frames[0], 0), Frame(frames[1], 500)], {})
    assert found[0].extra["classifications"] == [
        {"label": "red fox", "score": 0.7}, {"label": "domestic dog", "score": 0.2}]
    top = found[1].extra["classifications"]
    # Top 5 only; a roll-up without a common name is named by its family.
    assert [g["label"] for g in top] == ["canidae", "red fox", "domestic dog", "animal", "cat"]
    assert top[0]["score"] == 0.4
    assert "classifications" not in found[2].extra

