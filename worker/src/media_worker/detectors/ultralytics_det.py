"""Ultralytics models: YOLO-World / YOLOE (open vocabulary, prompt with "fox"), or any
YOLO / RT-DETR weights you fine-tuned yourself.

    media-worker --detector ultralytics --model yolov8s-worldv2.pt --classes fox
    media-worker --detector ultralytics --model runs/detect/fox/weights/best.pt

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
        if classes:
            if not hasattr(self.model, "set_classes"):
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
        return {"detector": self.name, "classes": ",".join(self.classes or []) or "model default"}

    def detect(self, frames: list[Frame], hints: dict[str, Any]) -> list[Detection]:
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
