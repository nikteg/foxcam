"""Offline detector test on local footage - no server needed.

Runs the same steps a job goes through (sample frames, black out censor areas, detect)
on a video or image(s) you supply, e.g. a clip with an actual fox in it::

    foxcam-worker detect-file samples/fox.mp4 --detector ultralytics \\
        --classes fox --expect fox --out out/fox-test

    # Same clip as if it were IR night footage, with a censor rectangle:
    foxcam-worker detect-file samples/fox.mp4 --detector speciesnet --night \\
        --mask 0,0,0.3,0.2 --expect "red fox"

    # Negative control: a clip without foxes must not produce one.
    foxcam-worker detect-file samples/cat.mp4 --detector speciesnet --expect-none fox

Exit code: 0 = expectations met (or none given), 1 = not met. Usable in CI or scripts.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from .detectors import Detection, add_detector_args, create_detector
from .frames import Frame, extract_frames

log = logging.getLogger("foxcam_worker")

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
# Rough IR night-vision look: no colour, flatter and a bit darker.
NIGHT_FILTERS = ["hue=s=0", "eq=contrast=1.3:brightness=-0.08", "noise=alls=8:allf=t"]


def parse_rect(text: str) -> list[float]:
    try:
        rect = [float(v) for v in text.split(",")]
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"bad rect {text!r}") from e
    if len(rect) != 4 or not all(0 <= v <= 1 for v in rect):
        raise argparse.ArgumentTypeError("rect must be x,y,w,h normalized to 0..1")
    return rect


def mask_filters(rects: list[list[float]]) -> list[str]:
    """Identical to what the server burns into worker input."""
    return [f"drawbox=x=iw*{x:.5f}:y=ih*{y:.5f}:w=iw*{w:.5f}:h=ih*{h:.5f}:color=black:t=fill"
            for x, y, w, h in rects]


def build_filters(args: argparse.Namespace) -> list[str]:
    filters = mask_filters(args.mask)
    if args.night:
        filters += NIGHT_FILTERS
    filters.append(f"scale='min({args.max_width},iw)':-2")
    return filters


def load_frames(inputs: list[Path], work: Path, args: argparse.Namespace) -> list[Frame]:
    filters = build_filters(args)
    if len(inputs) == 1 and inputs[0].is_file() and inputs[0].suffix.lower() not in IMAGE_SUFFIXES:
        return extract_frames(inputs[0], work, args.fps, filters=filters,
                              start_s=args.start, duration_s=args.duration)
    images = []
    for p in inputs:
        images += sorted(q for q in p.iterdir() if q.suffix.lower() in IMAGE_SUFFIXES) \
            if p.is_dir() else [p]
    work.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, img in enumerate(images):
        dst = work / f"{i:06d}.jpg"
        _ffmpeg(["-i", str(img), "-vf", ",".join(filters), "-frames:v", "1", "-q:v", "2",
                 str(dst)])
        # Images have no timeline; number them 1 s apart so reports stay readable.
        frames.append(Frame(dst, i * 1000))
    return frames


def _ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-500:])


def annotate(frames: list[Frame], detections: list[Detection], out_dir: Path,
             targets: set[str], limit: int) -> int:
    """Write copies of the frames with boxes drawn (orange = target, white = other)."""
    by_offset: dict[int, list[Detection]] = defaultdict(list)
    for d in detections:
        if d.bbox:
            by_offset[d.offset_ms].append(d)
    ranked = sorted(by_offset, key=lambda o: -max(d.confidence for d in by_offset[o]))[:limit]
    frame_at = {f.offset_ms: f for f in frames}
    out_dir.mkdir(parents=True, exist_ok=True)
    for offset in ranked:
        boxes = []
        for d in by_offset[offset]:
            x, y, w, h = d.bbox
            color = "orange" if d.label.lower() in targets else "white"
            boxes.append(f"drawbox=x=iw*{x:.4f}:y=ih*{y:.4f}:w=iw*{w:.4f}:h=ih*{h:.4f}"
                         f":color={color}:t=3")
        best = max(by_offset[offset], key=lambda d: d.confidence)
        name = f"{offset / 1000:08.2f}s_{best.label.replace(' ', '-')}_{best.confidence:.2f}.jpg"
        _ffmpeg(["-i", str(frame_at[offset].path), "-vf", ",".join(boxes), str(out_dir / name)])
    return len(ranked)


def summarize(detections: list[Detection]) -> dict[str, dict]:
    summary: dict[str, dict] = {}
    for d in detections:
        s = summary.setdefault(d.label, {"count": 0, "max_confidence": 0.0,
                                         "first_s": d.offset_ms / 1000,
                                         "last_s": d.offset_ms / 1000})
        s["count"] += 1
        s["max_confidence"] = max(s["max_confidence"], d.confidence)
        s["first_s"] = min(s["first_s"], d.offset_ms / 1000)
        s["last_s"] = max(s["last_s"], d.offset_ms / 1000)
    return dict(sorted(summary.items(), key=lambda kv: -kv[1]["max_confidence"]))


def check(summary: dict[str, dict], expect: list[str], expect_none: list[str],
          min_conf: float) -> list[str]:
    def best(wanted: str) -> float:
        # "fox" also matches more specific labels such as "red fox" / "arctic fox".
        w = wanted.lower()
        return max((s["max_confidence"] for label, s in summary.items()
                    if label.lower() == w or label.lower().endswith(" " + w)), default=0.0)

    problems = []
    for label in expect:
        conf = best(label)
        if conf < min_conf:
            problems.append(f"expected {label!r} >= {min_conf:.2f}, best was {conf:.2f}")
    for label in expect_none:
        conf = best(label)
        if conf >= min_conf:
            problems.append(f"did not expect {label!r}, but got it at {conf:.2f}")
    return problems


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="foxcam-worker detect-file", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("inputs", nargs="+", type=Path, help="video file, image(s) or image folder")
    add_detector_args(p)
    p.add_argument("--fps", type=float, default=2.0, help="sample rate (server default: 2)")
    p.add_argument("--max-width", type=int, default=1280)
    p.add_argument("--start", type=float, help="start at this second")
    p.add_argument("--duration", type=float, help="only process this many seconds")
    p.add_argument("--mask", type=parse_rect, action="append", default=[],
                   metavar="X,Y,W,H", help="censor rectangle (normalized), repeatable")
    p.add_argument("--night", action="store_true", help="simulate grayscale IR night footage")
    p.add_argument("--country", help="hint for SpeciesNet geofencing, e.g. SWE")
    p.add_argument("--targets", default="fox,red fox,vulpes vulpes",
                   help="labels highlighted as targets in output")
    p.add_argument("--expect", action="append", default=[], metavar="LABEL",
                   help="fail unless this label is detected (repeatable)")
    p.add_argument("--expect-none", action="append", default=[], metavar="LABEL",
                   help="fail if this label is detected (repeatable)")
    p.add_argument("--min-confidence", type=float, default=0.5,
                   help="threshold for --expect/--expect-none (server default: 0.5)")
    p.add_argument("--out", type=Path, help="write detections.json and annotated frames here")
    p.add_argument("--max-annotated", type=int, default=40)
    p.add_argument("--keep-frames", action="store_true", help="also copy the sampled frames")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)-7s %(message)s")
    for path in args.inputs:
        if not path.exists():
            p.error(f"{path} does not exist")

    targets = {t.strip().lower() for t in args.targets.split(",") if t.strip()}
    detector = create_detector(args)
    hints = {"target_labels": sorted(targets), "min_confidence": args.min_confidence,
             "country": args.country}

    with tempfile.TemporaryDirectory(prefix="foxcam-worker-local-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp) / "frames"
        t0 = time.monotonic()
        frames = load_frames(args.inputs, work, args)
        if not frames:
            sys.exit("no frames decoded")
        t1 = time.monotonic()
        detections = sorted(detector.detect(frames, hints), key=lambda d: d.offset_ms)
        t2 = time.monotonic()
        summary = summarize(detections)
        problems = check(summary, args.expect, args.expect_none, args.min_confidence)
        report = {
            "inputs": [str(i) for i in args.inputs], "detector": detector.name,
            "frames": len(frames), "fps": args.fps, "night_simulation": args.night,
            "masks": args.mask, "decode_s": round(t1 - t0, 2), "detect_s": round(t2 - t1, 2),
            "summary": summary, "detections": [d.to_json() for d in detections],
            "expectations_met": not problems, "problems": problems,
        }
        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / "detections.json").write_text(json.dumps(report, indent=2))
            n = annotate(frames, detections, args.out / "annotated", targets, args.max_annotated)
            if args.keep_frames:
                shutil.copytree(work, args.out / "frames", dirs_exist_ok=True)
            log.info("wrote %s (%d annotated frames)", args.out, n)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report, targets)
    sys.exit(1 if problems else 0)


def _print_report(report: dict, targets: set[str]) -> None:
    print(f"\n{report['detector']}: {report['frames']} frames @ {report['fps']} fps "
          f"(decode {report['decode_s']}s, detect {report['detect_s']}s)"
          + ("  [night simulation]" if report["night_simulation"] else ""))
    if not report["summary"]:
        print("  no detections")
    else:
        print(f"  {'label':<24} {'count':>5} {'max conf':>9} {'first':>8} {'last':>8}")
        for label, s in report["summary"].items():
            mark = "🦊" if label.lower() in targets else "  "
            print(f"{mark}{label:<24} {s['count']:>5} {s['max_confidence']:>9.2f} "
                  f"{s['first_s']:>7.1f}s {s['last_s']:>7.1f}s")
    for problem in report["problems"]:
        print(f"  ✗ {problem}")
    if report["expectations_met"]:
        print("  ✓ expectations met")
