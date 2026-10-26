"""Layout check: open the web UI's pages at phone and desktop width and report what the eye
keeps missing (AGENTS.md → Web UI → Layout):

- blocks too close: a heading, banner, field, stat row, card or table less than
  ``MIN_GAP`` px below the block before it (or overlapping it);
- wider than the screen: the page scrolls sideways, or an element sticks out past the
  right edge (outside the bars meant to scroll sideways).

    mise run layout-check                     # http://localhost:8000, the dev admin
    uv run --with playwright python scripts/layout_check.py --url URL [--page /jobs] ...

Sign in with FOXCAM_LAYOUT_EMAIL / FOXCAM_LAYOUT_PASSWORD (or --email / --password).
Exits 1 when something is reported. It reads pages only: nothing is clicked or changed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

MIN_GAP = 8  # px; Fluent's spacingVerticalS
VIEWPORTS = {"phone": {"width": 390, "height": 844}, "desktop": {"width": 1280, "height": 800}}
PAGES = [
    "/", "/events", "/events/detections", "/events/models", "/events/motion",
    "/recordings", "/recordings/segments",
    "/jobs/preparation", "/jobs/archiving", "/jobs/detection", "/jobs/detection-workers",
    "/cameras", "/cameras/profiles", "/cameras/integrations",
    "/settings/general", "/settings/detection", "/settings/retention", "/settings/users",
    "/settings/notifications", "/settings/storage", "/settings/configuration",
    "/settings/danger", "/account",
]

# The in-page check (a function returning problems as strings).
CHECK = (Path(__file__).with_name("layout_check.js")).read_text()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.environ.get("FOXCAM_SERVER", "http://localhost:8000"))
    p.add_argument("--email", default=os.environ.get("FOXCAM_LAYOUT_EMAIL"))
    p.add_argument("--password", default=os.environ.get("FOXCAM_LAYOUT_PASSWORD"))
    p.add_argument("--page", action="append", help="only these paths (repeatable)")
    p.add_argument("--theme", choices=["light", "dark"], default="light")
    args = p.parse_args()

    from playwright.sync_api import sync_playwright

    url = args.url.rstrip("/")
    found = 0
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome")
        except Exception:  # noqa: BLE001 - no Chrome installed: Playwright's Chromium
            browser = pw.chromium.launch()
        for label, viewport in VIEWPORTS.items():
            context = browser.new_context(viewport=viewport, color_scheme=args.theme,
                                          is_mobile=label == "phone",
                                          has_touch=label == "phone")
            if args.email:
                r = context.request.post(f"{url}/api/auth/login",
                                         data={"email": args.email, "password": args.password})
                if not r.ok:
                    print(f"sign-in failed: {r.status} {r.text()[:200]}", file=sys.stderr)
                    return 2
            page = context.new_page()
            for path in args.page or PAGES:
                page.goto(url + path, wait_until="networkidle")
                page.wait_for_timeout(300)  # late layout (fonts, images)
                if "/login" in page.url and path != "/login":
                    print(f"{label} {path}: not signed in (set FOXCAM_LAYOUT_EMAIL/PASSWORD)")
                    found += 1
                    continue
                for problem in page.evaluate(CHECK, MIN_GAP):
                    print(f"{label} {path}: {problem}")
                    found += 1
            context.close()
        browser.close()
    print(f"{found} layout problem(s)" if found else "layout OK")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
