"""Detector plugin interface.

A detector gets the sampled frames of one segment (censor areas already blacked out by
the server) plus hints, and returns detections. Implement this to plug in any model or
SaaS API; the server does not care which.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

from ..frames import Frame


@dataclass
class Detection:
    offset_ms: int
    label: str
    confidence: float
    bbox: list[float] | None = None  # normalized [x, y, w, h] (top-left origin)
    extra: dict[str, Any] | None = field(default=None)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["confidence"] = float(min(max(self.confidence, 0.0), 1.0))
        return d


class Detector(ABC):
    #: Reported to the server and stored on each detection (include the model version).
    name: str = "detector"

    def info(self) -> dict[str, Any]:
        return {"detector": self.name}

    @abstractmethod
    def detect(self, frames: list[Frame], hints: dict[str, Any]) -> list[Detection]:
        """``hints`` contains target_labels, min_confidence, country, latitude, longitude."""
