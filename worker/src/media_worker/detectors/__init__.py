from __future__ import annotations

import argparse

from .base import Detection, Detector

__all__ = ["Detection", "Detector", "add_detector_args", "create_detector"]


def add_detector_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--detector", choices=["dummy", "ultralytics", "speciesnet"], default="dummy")
    p.add_argument("--model", help="ultralytics weights, e.g. yolov8x-worldv2.pt or best.pt")
    p.add_argument("--classes", help="fixed open-vocabulary classes, e.g. 'fox'. Default: use "
                   "each job's profile targets (extra classes like dog steal fox detections, "
                   "see docs/MODELS.md)")
    p.add_argument("--conf", type=float, default=0.15, help="minimum confidence to report")
    p.add_argument("--device", help="torch device, e.g. cuda:0, mps, cpu")
    p.add_argument("--dummy-label", help="dummy detector: label to fake (e.g. fox)")
    p.add_argument("--dummy-every", type=int, default=3, help="dummy: fake every N-th job")


def create_detector(args: argparse.Namespace) -> Detector:
    if args.detector == "dummy":
        from .dummy import DummyDetector

        return DummyDetector(label=args.dummy_label, every=args.dummy_every)
    if args.detector == "ultralytics":
        from .ultralytics_det import UltralyticsDetector

        classes = [c.strip() for c in args.classes.split(",")] if args.classes else None
        return UltralyticsDetector(model=args.model or "yolov8s-worldv2.pt", classes=classes,
                                   conf=args.conf, device=args.device)
    if args.detector == "speciesnet":
        from .speciesnet_det import SpeciesNetDetector

        return SpeciesNetDetector(min_detection_conf=args.conf)
    raise SystemExit(f"unknown detector {args.detector}")
