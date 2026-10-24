"""Regenerate the README screenshots (docs/screenshots/*.jpg) from a throwaway demo stack.

    mise run screenshots            # or: uv run --with playwright python scripts/screenshots.py

Starts MediaMTX, the server, ingest and a worker on their own ports with their own data
(out/screenshots/), using the sample fox clips (`mise run setup:samples`) as cameras. The
worker is YOLO-World when it is installed (`mise run setup:yolo`), else the dummy worker.
Once enough events with clips exist, it captures the pages with the installed Google
Chrome (or Playwright's Chromium) in dark mode, desktop and phone size, and stops it all.
Your dev stack and its data are not touched.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / "out" / "screenshots"
SHOTS = ROOT / "docs" / "screenshots"
BIN = ROOT / ".venv" / "bin"
MODEL = ROOT / "out" / "models" / "yolov8s-worldv2.pt"
PORT = 8780
SERVER = f"http://localhost:{PORT}"
# MediaMTX ports, shifted so the dev stack can keep running.
MTX_PORTS = {":9997": ":19997", ":8554": ":18554", ":8888": ":18888", ":8889": ":18889",
             ":8189": ":18189"}
CAMERAS = [  # id, name, sample clip
    ("backyard", "Snowy backyard", "fox-yard-day.mp4"),
    ("street", "Street (night)", "fox-night-urban.mp4"),
    ("den", "Den cam", "fox-family-cam.mp4"),
    ("meadow", "Woodland edge", "fox-small.mp4"),
]
DESKTOP = {"viewport": {"width": 1280, "height": 800}, "device_scale_factor": 1.25}
PHONE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 1.5,
         "is_mobile": True, "has_touch": True}


def log(msg: str) -> None:
    print(msg, flush=True)


# --- demo stack ---------------------------------------------------------------------------

def write_config() -> Path:
    STACK.mkdir(parents=True)
    mtx = (ROOT / "mediamtx" / "mediamtx.yml").read_text()
    for old, new in MTX_PORTS.items():
        mtx = mtx.replace(old, new)
    # RTSP over TCP only: the default UDP ports (8000/8001) belong to the dev MediaMTX.
    mtx += "\nrtspTransports: [tcp]\n"
    (STACK / "mediamtx.yml").write_text(mtx)
    cams = "\n".join(
        f"  - {{id: {cid}, name: {json.dumps(name)}, "
        f"source: {{type: file, path: {ROOT / 'samples' / clip}}}}}"
        for cid, name, clip in CAMERAS)
    config = STACK / "config.yaml"
    config.write_text(f"""\
data_dir: {STACK / 'data'}
samples_dir: {ROOT / 'samples'}
segment_seconds: 20
mediamtx:
  api_url: http://localhost:19997
  rtsp_url: rtsp://localhost:18554
  webrtc_public_url: http://localhost:18889
  hls_public_url: http://localhost:18888
cameras:
{cams}
profiles:
  - id: wildlife
    name: Wildlife
    targets: [fox]
    aliases: {{red fox: fox, vulpes vulpes: fox}}
    min_confidence: 0.4
    default: true
events: {{settle_seconds: 20, pre_roll_seconds: 5, post_roll_seconds: 5}}
location: {{latitude: 57.7, longitude: 11.97, country: SWE}}
""")
    return config


class Stack:
    def __init__(self) -> None:
        self.procs: list[subprocess.Popen] = []
        self.names: list[str] = []
        self.env = {**os.environ, "MEDIA_STREAMER_CONFIG": str(STACK / "config.yaml"),
                    "MEDIA_STREAMER_WEB_DIST": str(ROOT / "web" / "dist"), "TZ": "UTC"}

    def start(self, name: str, args: list[str], cwd: Path = ROOT,
              env: dict[str, str] | None = None) -> None:
        out = (STACK / f"{name}.log").open("w")
        self.names.append(name)
        self.procs.append(subprocess.Popen(args, cwd=cwd, env=env or self.env, stdout=out,
                                           stderr=subprocess.STDOUT, start_new_session=True))

    def check(self) -> None:
        """Fail fast when part of the stack died (e.g. a port in use)."""
        for p, name in zip(self.procs, self.names, strict=True):
            if p.poll() is not None:
                tail = (STACK / f"{name}.log").read_text().strip().splitlines()[-3:]
                raise SystemExit(f"{name} exited ({p.returncode}): " + " | ".join(tail))

    def stop(self) -> None:
        for p in reversed(self.procs):
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
        deadline = time.monotonic() + 10
        for p in self.procs:
            try:
                p.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)


def get(path: str):
    with urllib.request.urlopen(SERVER + path, timeout=5) as r:
        return json.loads(r.read())


def put(path: str, body: dict) -> None:
    req = urllib.request.Request(SERVER + path, data=json.dumps(body).encode(), method="PUT",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


def wait_for(what: str, check, timeout: float, every: float = 3,
             stack: Stack | None = None) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stack:
            stack.check()
        try:
            if check():
                return
        except OSError:
            pass
        time.sleep(every)
    raise SystemExit(f"timed out waiting for {what}; see the logs in {STACK}")


def worker_args() -> tuple[list[str], str]:
    try:
        import importlib.util

        yolo = importlib.util.find_spec("ultralytics") is not None and MODEL.exists()
    except ImportError:
        yolo = False
    if yolo:
        return (["--detector", "ultralytics", "--model", str(MODEL)], "YOLO-World")
    return (["--detector", "dummy", "--dummy-label", "fox", "--dummy-every", "1"],
            "dummy (run `mise run setup:yolo` for real detections)")


def port_in_use(port: int) -> bool:
    import socket

    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def run_stack(stack: Stack, min_events: int, timeout: float) -> None:
    busy = [p for p in (PORT, 19997, 18554) if port_in_use(p)]
    if busy:
        raise SystemExit(f"port(s) {busy} in use: is an earlier screenshots run still going?")
    mediamtx = ROOT / ".tools" / "mediamtx"
    if not mediamtx.exists():
        raise SystemExit("MediaMTX missing: mise run setup:mediamtx")
    missing = [clip for _, _, clip in CAMERAS if not (ROOT / "samples" / clip).exists()]
    if missing:
        raise SystemExit(f"sample clips missing ({', '.join(missing)}): mise run setup:samples")
    if not (ROOT / "web" / "dist" / "index.html").exists():
        raise SystemExit("web UI not built: mise run build")

    stack.start("mediamtx", [str(mediamtx), str(STACK / "mediamtx.yml")], cwd=STACK)
    stack.start("server", [str(BIN / "media-streamer"), "serve", "--port", str(PORT)])
    wait_for("the server", lambda: get("/api/status") is not None, 60, every=1, stack=stack)
    stack.start("ingest", [str(BIN / "media-streamer"), "ingest"])
    token = subprocess.run([str(BIN / "media-streamer"), "worker-token", "gpu-box",
                            "--tags", "gpu"], env=stack.env, check=True, capture_output=True,
                           text=True).stdout.strip()
    args, kind = worker_args()
    log(f"worker: {kind}")
    worker_env = {**stack.env, "MEDIA_WORKER_TOKEN": token, "MEDIA_WORKER_HOST": "gpu-box"}
    stack.start("worker", [str(BIN / "media-worker"), "--server", SERVER, *args],
                cwd=MODEL.parent if MODEL.parent.exists() else ROOT, env=worker_env)
    # A censor area to show in the mask editor.
    put("/api/cameras/street/mask", {"rects": [[0.0, 0.0, 0.34, 0.12]]})

    # The sample clips loop, so a fox is on screen nearly all the time and an event would
    # grow forever, never settling into a clip. Once a camera's event has a few detections,
    # pause detection on it (no new jobs), so the event settles and its clip is built.
    log(f"recording until {min_events} cameras have an event with a clip (a few minutes)")
    paused: set[str] = set()
    last = [-1]

    def enough() -> bool:
        events = get("/api/events?limit=100")
        for e in events:
            if e["camera_id"] not in paused and e["detection_count"] >= 3:
                put(f"/api/cameras/{e['camera_id']}/profiles", {"profiles": []})
                paused.add(e["camera_id"])
        ready = {e["camera_id"] for e in events if e.get("clip_url") and not e["building"]}
        if len(ready) != last[0]:
            last[0] = len(ready)
            log(f"  {len(ready)}/{min_events} cameras")
        return len(ready) >= min_events

    wait_for("events", enough, timeout, every=5, stack=stack)
    for cam in paused:  # back to the default profiles (the profiles screenshot shows them)
        put(f"/api/cameras/{cam}/profiles", {"profiles": None})


# --- capture ------------------------------------------------------------------------------

def best_detection() -> dict:
    dets = [d for d in get("/api/detections?only_targets=true&limit=500") if d.get("bbox")]
    if not dets:
        raise SystemExit("no target detections to show")
    return max(dets, key=lambda d: d["confidence"])


def to_jpg(png: Path, name: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(png), "-q:v", "3",
                    str(SHOTS / f"{name}.jpg")], check=True)


def settle(page) -> None:
    """Wait until the page's images (thumbnails, snapshots) have loaded."""
    page.wait_for_load_state("load")
    page.wait_for_timeout(1200)
    page.wait_for_function(
        "[...document.images].every(i => i.complete)", timeout=20_000)
    page.wait_for_timeout(600)


def capture(tmp: Path) -> list[str]:
    from playwright.sync_api import sync_playwright

    det = best_detection()
    shots: list[str] = []

    def shot(page, name: str, path: str, element: str | None = None, prepare=None) -> None:
        page.goto(SERVER + path)
        settle(page)
        if prepare:
            prepare(page)
        png = tmp / f"{name}.png"
        if element:
            page.locator(element).screenshot(path=str(png))
        else:
            page.screenshot(path=str(png))
        to_jpg(png, name)
        shots.append(name)
        log(f"  {name}.jpg")

    def add_worker(page) -> None:
        page.get_by_role("button", name="Add worker").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_role("textbox").first.fill("garage-pc")
        dialog.get_by_role("button", name="Create token").click()
        page.wait_for_timeout(800)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:  # noqa: BLE001 - no Google Chrome: Playwright's own Chromium
            browser = p.chromium.launch()
        init = ("localStorage.setItem('media-streamer.lang', 'en');"
                "localStorage.setItem('media-streamer.theme', 'dark');")
        desk = browser.new_context(color_scheme="dark", **DESKTOP)
        desk.add_init_script(init)
        page = desk.new_page()
        shot(page, "dashboard", "/")
        shot(page, "events", "/events")
        shot(page, "detections", f"/detections?selected={det['id']}")
        shot(page, "recordings", f"/recordings?selected={det['segment_id']}")
        shot(page, "mask-editor", "/cameras/street/mask")
        shot(page, "profiles", "/cameras#profiles", element="section#profiles")
        shot(page, "queue", "/queue")
        phone = browser.new_context(color_scheme="dark", **PHONE)
        phone.add_init_script(init)
        page = phone.new_page()
        shot(page, "mobile-events", "/events")
        shot(page, "mobile-detections", f"/detections?selected={det['id']}")
        shot(page, "add-worker", "/queue", prepare=add_worker)
        browser.close()
    return shots


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--min-events", type=int, default=len(CAMERAS),
                    help="cameras that need an event with a clip first")
    ap.add_argument("--timeout", type=float, default=900, help="seconds to wait for events")
    ap.add_argument("--keep", action="store_true", help="leave out/screenshots/ for debugging")
    args = ap.parse_args()

    if STACK.exists():
        shutil.rmtree(STACK)
    write_config()
    stack = Stack()
    try:
        run_stack(stack, args.min_events, args.timeout)
        SHOTS.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            shots = capture(Path(tmp))
        log(f"wrote {len(shots)} screenshots to {SHOTS.relative_to(ROOT)}")
    finally:
        stack.stop()
        if not args.keep:
            shutil.rmtree(STACK, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
