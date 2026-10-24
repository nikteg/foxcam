#!/usr/bin/env python3
"""Download freely licensed fox clips from Wikimedia Commons into samples/.

Each clip is converted to H.264 MP4 (max 1280 px wide, no audio) so it works as a fake
camera (`source: {type: file}`) and with `media-worker detect-file`. Licences and authors
are written to samples/CREDITS.md - keep them if you share the clips.

    python3 scripts/fetch_samples.py            # default set
    python3 scripts/fetch_samples.py --list     # show what would be downloaded
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "media-streamer-dev/0.1 (fox detection test footage)"

# (output name, Commons file title, why it is useful)
CLIPS = [
    ("fox-garden.mp4", "File:Foxes in The Garden.webm", "garden, static camera"),
    ("fox-yard-day.mp4", "File:Red fox playing with prey in yard, Charlton MA 2025-12-26.webm",
     "backyard, daylight"),
    ("fox-family-cam.mp4", "File:Fox Family Cam.webm", "fixed wildlife camera"),
    ("fox-night-urban.mp4", "File:Urban fox at night.webm", "night, low light"),
    ("fox-small.mp4", "File:Red Fox 2012 11 640 480.webm", "small 640x480 clip, quick tests"),
]


def commons_info(title: str) -> dict:
    query = urllib.parse.urlencode({
        "action": "query", "format": "json", "titles": title, "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiextmetadatafilter": "LicenseShortName|Artist|LicenseUrl",
    })
    req = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        pages = json.load(r)["query"]["pages"]
    page = next(iter(pages.values()))
    if "imageinfo" not in page:
        raise RuntimeError(f"{title} not found on Commons")
    info = page["imageinfo"][0]
    meta = info.get("extmetadata", {})
    return {
        "url": info["url"],
        "page": info.get("descriptionurl", f"https://commons.wikimedia.org/wiki/{title}"),
        "size": info.get("size", 0),
        "license": meta.get("LicenseShortName", {}).get("value", "?"),
        "author": re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "?")).strip(),
    }


def download(url: str, dst: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r, dst.open("wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)


def to_mp4(src: Path, dst: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-an",
         "-vf", "scale='min(1280,iw)':-2,format=yuv420p",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-movflags", "+faststart",
         str(dst)],
        check=True,
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list", action="store_true", help="only list the clips")
    p.add_argument("--force", action="store_true", help="re-download existing files")
    args = p.parse_args()

    SAMPLES.mkdir(exist_ok=True)
    credits = ["# Sample footage credits", "",
               "Downloaded from Wikimedia Commons by `scripts/fetch_samples.py`.", ""]
    for name, title, why in CLIPS:
        info = commons_info(title)
        size_mb = info["size"] / 1e6
        credits.append(f"- **{name}** ({why}): [{title.removeprefix('File:')}]({info['page']})"
                       f" by {info['author']}, {info['license']}")
        if args.list:
            print(f"{name:<22} {size_mb:6.1f} MB  {info['license']:<14} {why}")
            continue
        dst = SAMPLES / name
        if dst.exists() and not args.force:
            print(f"✓ {name} (exists)")
            continue
        print(f"↓ {name}: {title} ({size_mb:.1f} MB, {info['license']})", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "source"
            download(info["url"], raw)
            to_mp4(raw, dst)
        print(f"✓ {name} ({dst.stat().st_size / 1e6:.1f} MB)")
    if not args.list:
        (SAMPLES / "CREDITS.md").write_text("\n".join(credits) + "\n")
        print(f"\nCredits written to {SAMPLES / 'CREDITS.md'}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
