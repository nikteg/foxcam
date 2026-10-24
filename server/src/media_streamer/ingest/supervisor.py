"""``media-streamer ingest``: publish non-RTSP camera sources into MediaMTX.

RTSP cameras are pulled by MediaMTX itself and need nothing here. Every other source
type is turned into an RTSP *publish* to ``<mediamtx.rtsp_url>/<camera id>``, so from
MediaMTX onwards (live view, recording, segments) all cameras look the same.

Each camera runs in its own thread and is restarted with backoff when it fails.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time

from ..config import CameraConfig, Settings
from ..recording import source_path

log = logging.getLogger(__name__)

H264_OUT = ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-g", "30",
            "-pix_fmt", "yuv420p"]


def publish_url(settings: Settings, cam: CameraConfig) -> str:
    """Where to publish: ``<camera>``, or ``<camera>/raw`` when it is processed (masks /
    re-encoding, see ``recording``)."""
    return f"{settings.mediamtx.rtsp_url.rstrip('/')}/{source_path(settings, cam)}"


def ffmpeg_publisher_args(settings: Settings, cam: CameraConfig) -> list[str]:
    src = cam.source
    out = ["-f", "rtsp", "-rtsp_transport", "tcp", publish_url(settings, cam)]
    if src.type == "test":
        return ["ffmpeg", "-v", "error", "-re", "-f", "lavfi",
                "-i", f"testsrc2=size={src.size}:rate={src.fps}", *H264_OUT, *out]
    if src.type == "file":
        # Re-encode so arbitrary sample clips (any codec) work in the browser.
        return ["ffmpeg", "-v", "error", "-re", "-stream_loop", "-1", "-i", src.path, "-an",
                *H264_OUT, *out]
    raise ValueError(f"no ffmpeg publisher for source type {src.type}")


def _run_ffmpeg_camera(settings: Settings, cam: CameraConfig, stop: threading.Event) -> None:
    backoff = 2.0
    while not stop.is_set():
        args = ffmpeg_publisher_args(settings, cam)
        log.info("[%s] starting publisher (%s)", cam.id, cam.source.type)
        started = time.monotonic()
        proc = subprocess.Popen(args, stderr=subprocess.PIPE, text=True)
        while proc.poll() is None and not stop.is_set():
            time.sleep(0.5)
        if stop.is_set():
            proc.terminate()
            break
        err = (proc.stderr.read() if proc.stderr else "").strip().splitlines()[-3:]
        log.warning("[%s] publisher exited %s: %s", cam.id, proc.returncode, " | ".join(err))
        backoff = 2.0 if time.monotonic() - started > 60 else min(backoff * 2, 60)
        stop.wait(backoff)


def _fingerprint(settings: Settings, cam: CameraConfig) -> str:
    # The publish path is part of it: switching processing on/off restarts the publisher.
    return json.dumps([cam.source.model_dump(), source_path(settings, cam)], sort_keys=True)


def run_ingest(settings: Settings, poll_seconds: float = 5.0) -> None:
    """Keep one publisher thread per enabled non-RTSP camera.

    The camera list (config.yaml + cameras added in the web UI) is re-read every few
    seconds, so adding, removing, disabling or changing a camera needs no restart.
    """
    from ..db import init_db

    init_db(settings)
    running: dict[str, tuple[str, threading.Event, threading.Thread]] = {}

    def stop(cam_id: str) -> None:
        _, event, thread = running.pop(cam_id)
        event.set()
        thread.join(timeout=15)

    try:
        while True:
            try:
                wanted = {c.id: c for c in settings.all_cameras() if c.source.type != "rtsp"}
            except Exception:
                log.exception("could not read camera list")
                wanted = None
            if wanted is not None:
                for cam_id in list(running):
                    cam = wanted.get(cam_id)
                    if cam is None or _fingerprint(settings, cam) != running[cam_id][0]:
                        log.info("[%s] stopping publisher (camera removed or changed)", cam_id)
                        stop(cam_id)
                for cam_id, cam in wanted.items():
                    if cam_id in running:
                        continue
                    if cam.source.type == "tapo_cloud":
                        from .tapo_cloud import run_tapo_cloud_camera

                        target = run_tapo_cloud_camera
                    else:
                        target = _run_ffmpeg_camera
                    event = threading.Event()
                    thread = threading.Thread(target=target, args=(settings, cam, event),
                                              name=f"ingest-{cam_id}", daemon=True)
                    thread.start()
                    running[cam_id] = (_fingerprint(settings, cam), event, thread)
                if not running and not wanted:
                    log.debug("no cameras need an ingest publisher (all RTSP or none)")
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        log.info("stopping ingest")
        for cam_id in list(running):
            stop(cam_id)
