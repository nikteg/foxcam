#!/usr/bin/env python3
"""Fill a running server with test data: one fake camera per clip in samples/ and,
optionally, extra detection profiles.

    uv run python scripts/seed.py                       # cameras from samples/
    uv run python scripts/seed.py --profiles            # + a "vehicles" profile
    uv run python scripts/seed.py --server http://localhost:18000

Signs in as an admin: --email (or FOXCAM_EMAIL), password from FOXCAM_PASSWORD or asked for.
Idempotent: cameras/profiles that already exist are skipped.
"""

from __future__ import annotations

import argparse
import getpass
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
    p.add_argument("--server", default=os.environ.get("FOXCAM_SERVER", "http://localhost:8000"))
    p.add_argument("--profiles", action="store_true", help="also add example profiles")
    p.add_argument("--email", default=os.environ.get("FOXCAM_EMAIL"), help="admin account")
    args = p.parse_args()

    api = httpx.Client(base_url=args.server.rstrip("/"), timeout=30)
    try:
        state = api.get("/api/auth/state").raise_for_status().json()
    except httpx.HTTPError as e:
        sys.exit(f"server not reachable at {args.server} ({e}); start it with `mise run dev`")
    if state["enabled"]:
        if state["setup_required"]:
            sys.exit("create the first account in the web UI first (setup code in the log)")
        email = args.email or input("Admin e-mail: ")
        password = os.environ.get("FOXCAM_PASSWORD") or getpass.getpass("Password: ")
        r = api.post("/api/auth/login", json={"email": email, "password": password})
        if r.status_code != 200:
            sys.exit(f"sign-in failed: {r.json().get('detail')}")
    files = api.get("/api/cameras-sample-files").raise_for_status().json()
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
