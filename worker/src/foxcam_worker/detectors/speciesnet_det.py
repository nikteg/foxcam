"""Google SpeciesNet ensemble: MegaDetector finds animals/people/vehicles (robust on
night/IR camera-trap footage), a classifier names the species ("red fox").

Runs the official CLI on the frame folder and parses its JSON. That keeps us on the
documented interface, at the cost of loading the model once per job.

    pip install speciesnet   (or: uv sync --package foxcam-worker --extra speciesnet)
    foxcam-worker --detector speciesnet

The output format has changed between SpeciesNet releases; both the older
``prediction`` string and the newer ``processed_classification`` object are handled.
VERIFY on your GPU machine before relying on it.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..frames import Frame

log = logging.getLogger(__name__)
from .base import Detection, Detector


def _common_name(cls: str) -> str:
    # Older format: "uuid;class;order;family;genus;species;common name"
    return cls.split(";")[-1].strip() if ";" in cls else cls.strip()


TOP_CLASSES = 5


def _top_classes(obj: dict[str, Any]) -> list[dict[str, Any]]:
    """The classifier's top guesses, ``classifications: {classes: [...], scores: [...]}``
    (per image, or per box in newer versions), as common names with scores. Kept for
    debugging: why a fox became "canine family" or a dog."""
    raw = obj.get("classifications")
    if not isinstance(raw, dict):
        return []
    out = []
    for cls, score in zip(raw.get("classes") or [], raw.get("scores") or [], strict=False):
        # Roll-ups such as "uuid;mammalia;carnivora;canidae;;;" have no common name:
        # use the most specific taxonomy level instead.
        name = _common_name(cls) or next(
            (p.strip() for p in reversed(cls.split(";")[1:]) if p.strip()), cls)
        out.append({"label": name, "score": round(float(score), 4)})
        if len(out) == TOP_CLASSES:
            break
    return out


def _uninformative(name: str) -> bool:
    """Roll-up results that say nothing beyond "animal" (keep the detector label then)."""
    return name.lower() in {"animal", "blank", "no cv result", "mammal", ""}


def _classification(pred: dict[str, Any]) -> tuple[str | None, float | None, str | None]:
    pc = pred.get("processed_classification")
    if isinstance(pc, dict) and pc.get("class"):
        return _common_name(pc["class"]), pc.get("score"), pc["class"]
    if pred.get("prediction"):
        return _common_name(pred["prediction"]), pred.get("prediction_score"), pred["prediction"]
    return None, None, None


# What MegaDetector reports for boxes the classifier does not name.
DETECTOR_LABELS = ("animal", "person", "vehicle")
# Taxonomy entries that are not something seen.
_NOT_A_THING = {"blank", "no cv result", ""}


def _taxonomy_name(line: str) -> str:
    """"uuid;class;order;family;genus;species;common name" -> the common name, or the most
    specific taxonomy level for roll-ups without one."""
    parts = [p.strip() for p in line.strip().split(";")]
    if not any(parts[1:]):
        return ""
    return _common_name(line) or next((p for p in reversed(parts[1:]) if p), "")


def model_vocabulary(model_dir: Path) -> list[str]:
    """Names SpeciesNet can report: its classifier labels and taxonomy roll-ups (e.g.
    "canine family"), plus MegaDetector's animal / person / vehicle."""
    info = json.loads((model_dir / "info.json").read_text())
    names = set(DETECTOR_LABELS)
    for key in ("classifier_labels", "taxonomy"):
        path = model_dir / info.get(key, "")
        if info.get(key) and path.is_file():
            names.update(_taxonomy_name(line) for line in path.read_text().splitlines())
    return sorted(n for n in names if n.lower() not in _NOT_A_THING)


class SpeciesNetDetector(Detector):
    name = "speciesnet"

    def __init__(self, min_detection_conf: float = 0.1, extra_args: list[str] | None = None):
        self.min_detection_conf = min_detection_conf
        self.extra_args = extra_args or []
        self._vocabulary: list[str] | None = None

    def vocabulary(self) -> list[str] | None:
        """The model's label names (from its files; downloads the model the first time,
        like the first job would). None if they cannot be read. Not a lease filter: jobs
        stay routed by tags, as before."""
        if self._vocabulary is None:
            try:
                from speciesnet import DEFAULT_MODEL
                from speciesnet.utils import ModelInfo

                args = self.extra_args
                model = args[args.index("--model") + 1] if "--model" in args else DEFAULT_MODEL
                info = ModelInfo(model)
                self._vocabulary = model_vocabulary(Path(info.classifier_labels).parent)
            except Exception as e:  # noqa: BLE001 - suggestions only; never stop the worker
                log.info("speciesnet: could not read the model's labels (%s)", e)
                self._vocabulary = []
        return self._vocabulary or None

    def detect(self, frames: list[Frame], hints: dict[str, Any]) -> list[Detection]:
        if not frames:
            return []
        folder = frames[0].path.parent
        by_name = {f.path.name: f for f in frames}
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            out_json = Path(tmp) / "predictions.json"
            cmd = [sys.executable, "-m", "speciesnet.scripts.run_model",
                   "--folders", str(folder), "--predictions_json", str(out_json),
                   *self.extra_args]
            if hints.get("country"):
                cmd += ["--country", hints["country"]]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0 or not out_json.exists():
                raise RuntimeError(f"speciesnet failed: {proc.stderr.strip()[-800:]}")
            data = json.loads(out_json.read_text())

        out: list[Detection] = []
        model_version = None
        for pred in data.get("predictions", []):
            frame = by_name.get(Path(pred.get("filepath", "")).name)
            if frame is None:
                continue
            model_version = pred.get("model_version", model_version)
            species, score, taxonomy = _classification(pred)
            guesses = _top_classes(pred)
            dets = sorted(pred.get("detections") or [], key=lambda d: -d.get("conf", 0))
            dets = [d for d in dets if d.get("conf", 0) >= self.min_detection_conf]
            for i, d in enumerate(dets):
                label = d.get("label", "object")
                conf = float(d.get("conf", 0))
                extra = {"detector_label": label, "detector_conf": conf}
                # Newer SpeciesNet classifies each animal box; older versions gave one
                # image-level species, which applies to the most confident animal box.
                box_species, box_score, box_taxonomy = _classification(d)
                if box_species is None and i == 0:
                    box_species, box_score, box_taxonomy = species, score, taxonomy
                box_guesses = _top_classes(d) or (guesses if i == 0 else [])
                if box_guesses:
                    extra["classifications"] = box_guesses
                if label == "animal" and box_species and box_score is not None \
                        and not _uninformative(box_species):
                    extra["taxonomy"] = box_taxonomy
                    label, conf = box_species, float(box_score)
                out.append(Detection(offset_ms=frame.offset_ms, label=label, confidence=conf,
                                     bbox=d.get("bbox"), extra=extra))
        if model_version:
            self.name = f"speciesnet:{model_version}"
        return out
