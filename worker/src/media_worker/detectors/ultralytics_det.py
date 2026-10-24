"""Ultralytics models: YOLO-World / YOLOE (open vocabulary, prompt with "fox"), or any
YOLO / RT-DETR weights you fine-tuned yourself.

    media-worker --detector ultralytics --model yolov8s-worldv2.pt   # prompted per job
    media-worker --detector ultralytics --model yolov8s-worldv2.pt --classes fox  # fixed
    media-worker --detector ultralytics --model runs/detect/fox/weights/best.pt

Without ``--classes`` an open-vocabulary model (YOLO-World / YOLOE) is prompted with each
job's profile targets, so one worker can look for foxes on one camera and cars on another.
It then advertises the ``open-vocabulary`` tag.

Note: Ultralytics is AGPL-3.0.
"""

from __future__ import annotations

from typing import Any

from ..frames import Frame
from .base import Detection, Detector


class UltralyticsDetector(Detector):
    def __init__(self, model: str = "yolov8s-worldv2.pt", classes: list[str] | None = None,
                 conf: float = 0.15, batch: int = 16, device: str | None = None):
        from ultralytics import YOLO

        self.model_path = model
        self.model = YOLO(model)
        self.classes = classes
        self.open_vocabulary = hasattr(self.model, "set_classes")
        self.prompt: list[str] | None = None
        if classes:
            if not self.open_vocabulary:
                raise ValueError(f"{model} is not an open-vocabulary model; drop --classes")
            # Open-vocabulary prompt. Prompt only the classes you want: in our tests,
            # adding "dog,cat,person" made YOLO-World label most foxes as "dog" (see
            # docs/MODELS.md).
            self.model.set_classes(classes)
        self.conf = conf
        self.batch = batch
        self.device = device
        self.name = f"ultralytics:{model}"

    def info(self) -> dict[str, Any]:
        classes = ",".join(self.classes or []) or (
            "from profile" if self.open_vocabulary else "model default")
        return {"detector": self.name, "classes": classes}

    def labels(self) -> list[str] | None:
        if self.classes:  # fixed prompt: only these
            return list(self.classes)
        if self.open_vocabulary:  # prompted per job with the profile's targets
            return None
        return [str(n) for n in self.model.names.values()]  # closed-set model, e.g. COCO

    def tags(self) -> list[str]:
        # Fixed --classes: only profiles that ask for this worker by tag make sense.
        return ["ultralytics", *(["open-vocabulary"]
                                 if self.open_vocabulary and not self.classes else [])]

    def _prompt_for(self, hints: dict[str, Any]) -> None:
        """Prompt an open-vocabulary model with the job's targets (only when they change)."""
        if self.classes or not self.open_vocabulary:
            return
        targets = list(dict.fromkeys(t.strip() for t in hints.get("target_labels", [])
                                     if t.strip()))
        if targets and targets != self.prompt:
            self.model.set_classes(targets)
            self.prompt = targets

    def detect(self, frames: list[Frame], hints: dict[str, Any]) -> list[Detection]:
        self._prompt_for(hints)
        out: list[Detection] = []
        for i in range(0, len(frames), self.batch):
            chunk = frames[i:i + self.batch]
            results = self.model.predict([str(f.path) for f in chunk], conf=self.conf,
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
