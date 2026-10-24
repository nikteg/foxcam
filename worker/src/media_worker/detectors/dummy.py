"""No-ML detector for exercising the pipeline end to end.

``--dummy-label fox`` reports that label in the middle frame of every N-th job, which is
enough to see events, clips, the detections tab and feedback buttons work.
"""

from __future__ import annotations

import random
from typing import Any

from ..frames import Frame
from .base import Detection, Detector


class DummyDetector(Detector):
    name = "dummy"

    def __init__(self, label: str | None = None, every: int = 3):
        self.label = label
        self.every = max(every, 1)
        self.count = 0

    def detect(self, frames: list[Frame], hints: dict[str, Any]) -> list[Detection]:
        self.count += 1
        if not self.label or not frames or self.count % self.every:
            return []
        out = []
        for f in frames[len(frames) // 3: 2 * len(frames) // 3]:
            out.append(Detection(offset_ms=f.offset_ms, label=self.label,
                                 confidence=round(random.uniform(0.6, 0.95), 3),
                                 bbox=[0.4, 0.45, 0.2, 0.2]))
        return out
