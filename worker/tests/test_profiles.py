"""Worker side of detection profiles: tags, per-job frame rate, open-vocabulary prompts."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import pytest
from foxcam_worker import cli
from foxcam_worker.detectors.base import Detector
from foxcam_worker.detectors.dummy import DummyDetector
from foxcam_worker.detectors.ultralytics_det import UltralyticsDetector


def test_detector_tags():
    assert DummyDetector().tags() == ["dummy"]


class FakeModel:
    def __init__(self):
        self.prompts: list[list[str]] = []

    def set_classes(self, classes):
        self.prompts.append(classes)

    def predict(self, paths, **kw):
        return []


def _ultralytics(classes=None) -> UltralyticsDetector:
    det = object.__new__(UltralyticsDetector)  # skip loading real weights
    det.model, det.classes, det.open_vocabulary, det.prompt = FakeModel(), classes, True, None
    det.batch, det.conf, det.device, det.name = 16, 0.1, None, "ultralytics:x"
    det.openvino, det.ov_models, det.predictor = False, {}, det.model
    return det


def test_open_vocabulary_model_is_prompted_with_the_profile_targets():
    det = _ultralytics()
    assert det.tags() == ["ultralytics", "open-vocabulary"]
    assert det.labels() is None
    det.detect([], {"target_labels": ["fox"]})
    det.detect([], {"target_labels": ["fox"]})  # unchanged: no re-prompt
    det.detect([], {"target_labels": ["car", "person"]})
    assert det.model.prompts == [["fox"], ["car", "person"]]


def test_classes_are_only_the_fallback_prompt():
    # An old `--classes fox` command line must not lock the worker to foxes.
    det = _ultralytics(classes=["fox"])
    assert det.tags() == ["ultralytics", "open-vocabulary"]
    assert det.labels() is None  # the server sends every profile
    det.detect([], {"target_labels": ["person"]})
    det.detect([], {})  # no targets (local file run): the --classes prompt
    assert det.model.prompts == [["person"], ["fox"]]


class CountingDetector(Detector):
    name = "count"

    def __init__(self):
        self.frames = 0

    def detect(self, frames, hints):
        self.frames = len(frames)
        return []


class FakeClient:
    def __init__(self, clip: Path):
        self.clip, self.results = clip, []

    def download_input(self, job, dst):
        shutil.copy(self.clip, dst)

    def heartbeat(self, job):
        pass

    def result(self, job, detections, model, ms):
        self.results.append(detections)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_job_frame_rate_comes_from_the_profile(tmp_path):
    clip = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=4",
                    "-t", "4", "-pix_fmt", "yuv420p", str(clip)], check=True)
    job = {"job_id": 1, "attempt": 1, "input_fps": 4, "sample_fps": 1,
           "profile": {"id": "vehicles"}, "lease_token": "t",
           "segment": {"camera_id": "cam", "start_at": "x"}, "hints": {}}
    det = CountingDetector()
    cli.process(FakeClient(clip), det, job, argparse.Namespace(fps=None, heartbeat=30))
    assert 3 <= det.frames <= 5  # ~1 fps over 4 s, not 16 frames


def test_speciesnet_vocabulary_from_model_files(tmp_path):
    from foxcam_worker.detectors.speciesnet_det import model_vocabulary

    (tmp_path / "info.json").write_text(
        '{"classifier_labels": "labels.txt", "taxonomy": "taxonomy.txt"}')
    (tmp_path / "labels.txt").write_text(
        "a;mammalia;carnivora;canidae;vulpes;vulpes;red fox\n"
        "b;;;;;;blank\n"
        "c;mammalia;carnivora;mustelidae;;;\n")  # roll-up without a common name
    (tmp_path / "taxonomy.txt").write_text(
        "d;mammalia;carnivora;canidae;;;canine family\n"
        "a;mammalia;carnivora;canidae;vulpes;vulpes;red fox\n")
    assert model_vocabulary(tmp_path) == [
        "animal", "canine family", "mustelidae", "person", "red fox", "vehicle"]


def test_vocabulary_goes_with_the_first_lease_only(monkeypatch):
    import json as _json

    import httpx
    from foxcam_worker import client as client_mod

    bodies = []

    def handler(request):
        bodies.append(_json.loads(request.content))
        return httpx.Response(204)

    c = client_mod.Client("http://server", "w1", labels=None, vocabulary=["fox", "badger"])
    c.http = httpx.Client(base_url="http://server", transport=httpx.MockTransport(handler))
    c.lease()
    c.lease()
    assert bodies[0]["vocabulary"] == ["fox", "badger"] and "vocabulary" not in bodies[1]
    assert bodies[1]["labels"] is None  # the lease filter stays "anything"
    monkeypatch.setattr(client_mod, "VOCABULARY_EVERY", 0)
    c.lease()
    assert bodies[2]["vocabulary"] == ["fox", "badger"]


def test_open_vocabulary_has_no_vocabulary():
    det = _ultralytics()
    assert det.labels() is None and det.vocabulary() is None


class InMemoryDetector(Detector):
    name = "memory"
    in_memory = True

    def __init__(self):
        self.batches = []

    def detect(self, frames, hints):
        self.batches.append([(f.path, f.offset_ms, f.image.shape) for f in frames])
        return []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_in_memory_detectors_get_decoded_frames_in_batches(tmp_path):
    pytest.importorskip("numpy")
    clip = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=4",
                    "-t", "10", "-pix_fmt", "yuv420p", str(clip)], check=True)
    job = {"job_id": 1, "attempt": 1, "input_fps": 4, "sample_fps": 4,
           "profile": {"id": "p"}, "lease_token": "t",
           "segment": {"camera_id": "cam", "start_at": "x"}, "hints": {}}
    det = InMemoryDetector()
    cli.process(FakeClient(clip), det, job, argparse.Namespace(fps=None, heartbeat=30))
    frames = [f for b in det.batches for f in b]
    assert len(det.batches[0]) == 16 and 38 <= len(frames) <= 41
    assert all(path is None and shape == (90, 160, 3) for path, _, shape in frames)
    assert [ms for _, ms, _ in frames[:3]] == [0, 250, 500]


def test_pacer_spreads_frames_but_saves_no_idle_credit():
    from foxcam_worker.pacing import Pacer

    now, slept = [100.0], []

    def sleep(s):
        slept.append(s)
        now[0] += s

    pace = Pacer(4, clock=lambda: now[0], sleep=sleep)
    pace.start()
    now[0] += 1  # 16 frames took 1 s: wait 3 s more (16 frames at 4 fps = 4 s)
    pace(16)
    now[0] += 5  # slower than the cap: no wait
    pace(16)
    assert slept == [3.0]
    now[0] += 600  # idle between jobs
    pace.start()
    pace(8)
    assert slept[-1] == 2.0  # paced from the new job's start, not from the old debt


def test_no_cap_never_sleeps():
    from foxcam_worker.pacing import Pacer

    pace = Pacer(0, sleep=lambda s: pytest.fail("slept"))
    pace.start()
    pace(1000)
