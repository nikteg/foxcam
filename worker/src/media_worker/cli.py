"""media-worker: lease a job, run the detector, post results, repeat."""

from __future__ import annotations

import argparse
import logging
import os
import platform
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Self

import httpx

from . import __version__
from .client import Client, LeaseLost
from .detectors import add_detector_args, create_detector
from .doctor import environment, gpu_summary
from .frames import extract_frames

log = logging.getLogger("media_worker")


class Heartbeat:
    """Keeps the lease alive while a (possibly slow) detector runs."""

    def __init__(self, client: Client, job: dict, interval: float):
        self.client, self.job, self.interval = client, job, interval
        self.stop = threading.Event()
        self.lost = False
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop.wait(self.interval):
            try:
                self.client.heartbeat(self.job)
            except LeaseLost:
                self.lost = True
                return
            except httpx.HTTPError as e:
                log.warning("heartbeat failed: %s", e)

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop.set()
        self.thread.join(timeout=5)


def process(client: Client, detector, job: dict, args: argparse.Namespace) -> None:
    seg = job["segment"]
    log.info("job %s: camera=%s profile=%s start=%s (attempt %s)", job["job_id"],
             seg["camera_id"], job.get("profile", {}).get("id", "-"), seg["start_at"],
             job["attempt"])
    started = time.monotonic()
    # ignore_cleanup_errors: on Windows a file may still be open (antivirus, model cache).
    with tempfile.TemporaryDirectory(prefix="media-worker-", ignore_cleanup_errors=True) as tmp, \
            Heartbeat(client, job, args.heartbeat) as hb:
        tmp_path = Path(tmp)
        video = tmp_path / "input.mp4"
        try:
            client.download_input(job, video)
            # The profile's frame rate (older servers: everything in the input).
            fps = min(args.fps or job.get("sample_fps") or job["input_fps"], job["input_fps"])
            frames = extract_frames(video, tmp_path / "frames", fps)
            detections = detector.detect(frames, job.get("hints", {}))
        except LeaseLost:
            log.warning("job %s: lease lost, dropping", job["job_id"])
            return
        except Exception as e:
            log.exception("job %s failed", job["job_id"])
            client.fail(job, f"{type(e).__name__}: {e}")
            return
        if hb.lost:
            log.warning("job %s: lease lost while processing, dropping result", job["job_id"])
            return
    ms = int((time.monotonic() - started) * 1000)
    client.result(job, [d.to_json() for d in detections], detector.name, ms)
    labels = sorted({d.label for d in detections})
    log.info("job %s: %d frames, %d detections %s in %.1fs", job["job_id"], len(frames),
             len(detections), labels, ms / 1000)


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", response.status_code))
    except ValueError:
        return str(response.status_code)


def graceful_signals() -> None:
    """Ctrl+C and SIGTERM stop the worker cleanly. SIGINT is re-enabled because processes
    started in the background (scripts/dev.sh, watchfiles reload) inherit it as ignored."""

    def stop(*_: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, stop)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "detect-file":
        from .local import main as detect_file_main

        detect_file_main(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        from .doctor import main as doctor_main

        doctor_main(sys.argv[2:])
        return

    p = argparse.ArgumentParser(
        prog="media-worker", description=__doc__,
        epilog="Check this machine (ffmpeg, GPU, server): media-worker doctor --help\n"
               "Offline test on a local video: media-worker detect-file --help",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server", default=os.environ.get("MEDIA_SERVER", "http://localhost:8000"))
    p.add_argument("--token", default=os.environ.get("MEDIA_WORKER_TOKEN"))
    p.add_argument("--worker-id", default=os.environ.get("MEDIA_WORKER_ID", socket.gethostname()))
    p.add_argument("--tags", default=os.environ.get("MEDIA_WORKER_TAGS", ""),
                   help="extra capability tags, comma separated (e.g. gpu,winhack). Detection "
                        "profiles that require tags only go to workers that have them; the "
                        "detector adds its own (speciesnet, ultralytics, open-vocabulary, dummy)")
    add_detector_args(p)
    p.add_argument("--fps", type=float, help="subsample frames further (default: all)")
    p.add_argument("--poll", type=float, default=5, help="seconds to wait when queue is empty")
    p.add_argument("--heartbeat", type=float, default=30)
    p.add_argument("--once", action="store_true", help="process at most one job and exit")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not found on PATH (run `media-worker doctor` for install hints)")
    graceful_signals()
    detector = create_detector(args)
    # MEDIA_WORKER_HOST: what the queue page shows as host (e.g. for demos and screenshots).
    host = os.environ.get("MEDIA_WORKER_HOST") or platform.node()
    info = {**detector.info(), "version": __version__, "host": host,
            "python": platform.python_version(), "platform": environment()}
    if gpu := gpu_summary():
        info["gpu"] = gpu
    tags = list(dict.fromkeys(
        t.strip().lower() for t in [*detector.tags(), *args.tags.split(",")] if t.strip()))
    client = Client(args.server, args.worker_id, args.token, info, tags, detector.labels())
    log.info("worker %s using %s (tags: %s) -> %s", args.worker_id, detector.name,
             ",".join(tags), args.server)

    backoff = args.poll
    try:
        while True:
            try:
                job = client.lease()
                backoff = args.poll
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    detail = _detail(e.response)
                    hint = "" if "Add worker" in detail else (
                        "; create one under Worker queue → Add worker")
                    raise SystemExit(f"server rejected this worker: {detail}. Pass a valid "
                                     f"--token (or MEDIA_WORKER_TOKEN){hint}.") from None
                log.warning("server error (%s); retrying in %.0fs", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue
            except httpx.HTTPError as e:
                log.warning("server unreachable (%s); retrying in %.0fs", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue
            if job is None:
                if args.once:
                    log.info("no work")
                    return
                time.sleep(args.poll)
                continue
            try:
                process(client, detector, job, args)
            except httpx.HTTPError as e:
                log.warning("job %s: server error %s (lease will expire and be retried)",
                            job["job_id"], e)
            if args.once:
                return
    except KeyboardInterrupt:
        log.info("bye")
    finally:
        client.close()


if __name__ == "__main__":
    main()
