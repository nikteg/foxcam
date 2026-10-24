#!/usr/bin/env python3
"""Fill a running server with test data: one fake camera per clip in samples/ and,
optionally, extra detection profiles.

    uv run python scripts/seed.py                       # cameras from samples/
    uv run python scripts/seed.py --profiles            # + a "vehicles" profile
    uv run python scripts/seed.py --server http://localhost:18000

Idempotent: cameras/profiles that already exist are skipped.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
EXTRA_PROFILES = [
    {"id": "vehicles", "name": "Vehicles", "targets": ["car", "truck", "person"],
     "requires": ["open-vocabulary"], "fps": 1},
]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server", default=os.environ.get("MEDIA_SERVER", "http://localhost:8000"))
    p.add_argument("--profiles", action="store_true", help="also add example profiles")
    args = p.parse_args()

    api = httpx.Client(base_url=args.server.rstrip("/"), timeout=30)
    try:
        files = api.get("/api/cameras-sample-files").raise_for_status().json()
    except httpx.HTTPError as e:
        sys.exit(f"server not reachable at {args.server} ({e}); start it with `mise run dev`")
    if not files:
        print("no clips in samples/; run `mise run setup:samples` first")
    existing = {c["source_config"].get("path", "").rsplit("/", 1)[-1]
                for c in api.get("/api/cameras").json()}
    for name in files:
        if name in existing:
            print(f"  skip  camera for {name} (exists)")
            continue
        title = Path(name).stem.replace("-", " ").replace("_", " ").title()
        r = api.post("/api/cameras", json={"name": title,
                                           "source": {"type": "file", "path": name}})
        print(f"  {'added' if r.is_success else 'FAIL '} camera {title}"
              f"{'' if r.is_success else ': ' + r.text}")

    if args.profiles:
        have = {p["id"] for p in api.get("/api/profiles").json()}
        for prof in EXTRA_PROFILES:
            if prof["id"] in have:
                print(f"  skip  profile {prof['id']} (exists)")
                continue
            r = api.post("/api/profiles", json=prof)
            print(f"  {'added' if r.is_success else 'FAIL '} profile {prof['id']}"
                  f"{'' if r.is_success else ': ' + r.text}")
    print(f"done: {args.server}/cameras")


if __name__ == "__main__":
    main()
