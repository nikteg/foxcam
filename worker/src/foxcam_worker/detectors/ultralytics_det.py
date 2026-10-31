"""Ultralytics models: YOLO-World / YOLOE (open vocabulary, prompt with "fox"), or any
YOLO / RT-DETR weights you fine-tuned yourself.

    foxcam-worker --detector ultralytics --model yolov8s-worldv2.pt   # prompted per job
    foxcam-worker --detector ultralytics --model runs/detect/fox/weights/best.pt

An open-vocabulary model (YOLO-World / YOLOE) is prompted with each job's profile targets,
so one worker looks for foxes on one camera and people or cars on another, and picks up
new profiles without a restart. It advertises the ``open-vocabulary`` tag. ``--classes``
is only the prompt for jobs that name no targets (e.g. ``foxcam-worker-detect`` runs).

Intel GPUs (integrated UHD / Iris Xe / Arc, e.g. an N100 home server) run the model through
OpenVINO with ``--device intel:gpu`` (N100: ~100 ms per frame, 6x the CPU). OpenVINO models
have their classes built in, so each prompt is exported once (a few seconds) and kept in
``~/.cache/foxcam-worker/openvino``. Needs the ``openvino`` extra.

Note: Ultralytics is AGPL-3.0.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

from ..frames import Frame
from .base import Detection, Detector


class UltralyticsDetector(Detector):
    in_memory = True  # numpy frames go straight into predict()

    def __init__(self, model: str = "yolov8s-worldv2.pt", classes: list[str] | None = None,
                 conf: float = 0.15, batch: int = 16, device: str | None = None):
        from ultralytics import YOLO

        self.model_path = model
        self.model = YOLO(model)
        self.classes = classes
        self.open_vocabulary = hasattr(self.model, "set_classes")
        self.prompt: list[str] | None = None
        if classes and not self.open_vocabulary:
            raise ValueError(f"{model} is not an open-vocabulary model; drop --classes")
        self.conf = conf
        self.batch = batch
        self.device = device
        # OpenVINO models, one per prompt (classes are fixed at export).
        self.openvino = bool(device and device.startswith("intel"))
        self.ov_models: dict[tuple[str, ...], Any] = {}
        self.predictor = self.model
        # The weights file, not its path (which may be long and personal).
        self.name = f"ultralytics:{Path(model).name}"
        if self.openvino and not self.open_vocabulary:
            self.predictor = self._openvino_model(())

    def info(self) -> dict[str, Any]:
        classes = "from profile" if self.open_vocabulary else "model default"
        return {"detector": self.name, "classes": classes}

    def labels(self) -> list[str] | None:
        if self.open_vocabulary:  # prompted per job with the profile's targets: anything
            return None
        return [str(n) for n in self.model.names.values()]  # closed-set model, e.g. COCO

    def tags(self) -> list[str]:
        return ["ultralytics", *(["open-vocabulary"] if self.open_vocabulary else [])]

    def _prompt_for(self, hints: dict[str, Any]) -> None:
        """Prompt an open-vocabulary model with the job's targets (only when they change).

        Prompt only what the profile wants: in our tests, adding "dog,cat,person" made
        YOLO-World label most foxes as "dog" (see docs/MODELS.md).
        """
        if not self.open_vocabulary:
            return
        targets = list(dict.fromkeys(t.strip() for t in hints.get("target_labels", [])
                                     if t.strip())) or list(self.classes or [])
        if targets and targets != self.prompt:
            if self.openvino:
                self.predictor = self._openvino_model(tuple(targets))
            else:
                self.model.set_classes(targets)
            self.prompt = targets

    def _openvino_model(self, targets: tuple[str, ...]) -> Any:
        """The model exported to OpenVINO with this prompt, exported on first use."""
        if targets in self.ov_models:
            return self.ov_models[targets]
        from ultralytics import YOLO

        weights = Path(self.model.ckpt_path or self.model_path)
        key = hashlib.sha256("\n".join([weights.name, *targets]).encode()).hexdigest()[:12]
        cache = Path(os.environ.get("FOXCAM_WORKER_CACHE", Path.home() / ".cache/foxcam-worker"))
        target = cache / "openvino" / f"{weights.stem}-{key}_openvino_model"
        if not target.exists():
            if targets:
                self.model.set_classes(list(targets))
            exported = Path(self.model.export(format="openvino", verbose=False))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(target, ignore_errors=True)
            shutil.move(exported, target)
        model = YOLO(str(target), task="detect")
        self.ov_models[targets] = model
        return model

    def detect(self, frames: list[Frame], hints: dict[str, Any]) -> list[Detection]:
        self._prompt_for(hints)
        if self.predictor is self.model and self.openvino:  # no prompt: the model's own
            self.predictor = self._openvino_model(())
        out: list[Detection] = []
        for i in range(0, len(frames), self.batch):
            chunk = frames[i:i + self.batch]
            images = [f.image if f.image is not None else str(f.path) for f in chunk]
            results = self.predictor.predict(images, conf=self.conf,
                                             device=self.device, verbose=False)
            for frame, res in zip(chunk, results, strict=True):
                names = res.names
                for (cx, cy, w, h), conf, cls in zip(res.boxes.xywhn.tolist(),
                                                     res.boxes.conf.tolist(),
                                                     res.boxes.cls.tolist(), strict=True):
                    out.append(Detection(
                        offset_ms=frame.offset_ms, label=str(names[int(cls)]),
                        confidence=float(conf), bbox=[cx - w / 2, cy - h / 2, w, h],
                    ))
        return out
