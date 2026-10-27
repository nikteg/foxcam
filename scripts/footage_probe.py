"""What is really in a video file: its streams, jumps in the timestamps, keyframes, and a
strip of the camera's own clock (burned into the picture) to see footage that is missing
although the timestamps run on.

    uv run python scripts/footage_probe.py event:21            # an event's clip (prod API)
    uv run python scripts/footage_probe.py segment:2602 --clock 2
    uv run python scripts/footage_probe.py archive:5 --gap 0.5
    uv run python scripts/footage_probe.py some/file.mp4 --clock 1 --at 12-16

event:/segment:/archive: download through scripts/prod-api.sh into out/footage/ (kept, so
running again is quick). --clock N saves a picture of the camera's clock every N seconds
(--at FROM-TO: only there) to out/footage/<name>-clock.png; open it to read the times.
--crop picks the part of the picture with the clock (fractions x,y,w,h; default the top
left, where Tapo cameras put it).
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "out" / "footage"
SOURCES = {
    "event": "/api/events/{}/clip",
    "segment": "/api/segments/{}/download",
    "archive": "/api/archives/{}/download",
}


def fetch(spec: str) -> Path:
    kind, _, ident = spec.partition(":")
    if kind not in SOURCES or not ident.isdigit():
        path = Path(spec)
        if not path.exists():
            sys.exit(f"{spec}: no such file (or use event:ID, segment:ID, archive:ID)")
        return path
    dst = OUT / f"{kind}-{ident}.mp4"
    if not dst.exists():
        OUT.mkdir(parents=True, exist_ok=True)
        part = dst.with_suffix(".part")
        subprocess.run([str(REPO / "scripts" / "prod-api.sh"), "GET",
                        SOURCES[kind].format(ident), "-o", str(part)], check=True)
        part.rename(dst)
    return dst


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          ("stream=index,codec_type,codec_name,width,height,r_frame_rate,"
                           "avg_frame_rate,start_time,duration,nb_frames:format=duration"),
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def packets(path: Path) -> list[tuple[float, bool]]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "packet=pts_time,flags", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    pkts = []
    for line in out.splitlines():
        pts, _, flags = line.partition(",")
        if pts and pts != "N/A":
            pkts.append((float(pts), "K" in flags))
    return sorted(pkts)


def clock_strip(path: Path, every: float, start: float, end: float,
                crop: tuple[float, ...]) -> Path:
    x, y, w, h = crop
    times = []
    t = start
    while t <= end:
        times.append(t)
        t += every
    tiles = []
    for i, t in enumerate(times):
        tile = OUT / f".tile-{i:04d}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path),
                        "-frames:v", "1", "-vf",
                        f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale=-2:48", str(tile)],
                       check=True)
        tiles.append((t, tile))
    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / f"{path.stem}-clock.png"
    inputs = [arg for _, tile in tiles for arg in ("-i", str(tile))]
    labels = "".join(f"[{i}:v]" for i in range(len(tiles)))
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex",
                    f"{labels}vstack=inputs={len(tiles)}" if len(tiles) > 1 else "null",
                    str(dst)], check=True)
    for _, tile in tiles:
        tile.unlink()
    print(f"\nclock strip ({len(tiles)} rows, top to bottom):")
    for t, _ in tiles:
        print(f"  row at {t:7.2f} s")
    print(f"  -> {dst}")
    return dst


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="file, or event:ID / segment:ID / archive:ID")
    ap.add_argument("--gap", type=float, default=0.3,
                    help="report timestamp steps longer than this (s, default 0.3)")
    ap.add_argument("--clock", type=float, metavar="N",
                    help="save the camera's clock every N seconds")
    ap.add_argument("--at", help="FROM-TO seconds for --clock (default: all)")
    ap.add_argument("--crop", default="0,0,0.36,0.06",
                    help="where the clock is: x,y,w,h fractions (default top left)")
    args = ap.parse_args()

    path = fetch(args.source)
    info = probe(path)
    print(f"{path}  ({path.stat().st_size / 1e6:.1f} MB, "
          f"format {float(info['format'].get('duration', 0)):.2f} s)")
    for s in info["streams"]:
        size = f" {s['width']}x{s['height']}" if s.get("width") else ""
        print(f"  #{s['index']} {s['codec_type']} {s['codec_name']}{size} "
              f"start {s.get('start_time')} dur {s.get('duration')} frames {s.get('nb_frames')} "
              f"fps {s.get('r_frame_rate')} (avg {s.get('avg_frame_rate')})")

    pkts = packets(path)
    if not pkts:
        sys.exit("no video packets")
    steps = [b[0] - a[0] for a, b in itertools.pairwise(pkts)]
    typical = sorted(steps)[len(steps) // 2] if steps else 0
    print(f"\nvideo: {len(pkts)} frames, {pkts[0][0]:.3f} -> {pkts[-1][0]:.3f} s, "
          f"typical step {typical * 1000:.0f} ms")
    jumps = [(a, b) for a, b in itertools.pairwise(pkts) if b[0] - a[0] > args.gap]
    print(f"timestamp steps over {args.gap} s: {len(jumps) or 'none'}")
    for a, b in jumps:
        print(f"  {a[0]:9.3f} -> {b[0]:9.3f}  (+{b[0] - a[0]:.3f} s){'  keyframe' if b[1] else ''}")
    keys = [t for t, k in pkts if k]
    print(f"keyframes ({len(keys)}): " + " ".join(f"{t:.2f}" for t in keys[:60])
          + (" ..." if len(keys) > 60 else ""))

    if args.clock:
        start, end = pkts[0][0], pkts[-1][0]
        if args.at:
            lo, _, hi = args.at.partition("-")
            start, end = float(lo), float(hi or end)
        crop = tuple(float(v) for v in args.crop.split(","))
        clock_strip(path, args.clock, start, end, crop)


if __name__ == "__main__":
    main()
