"""Background loops run inside the web process (single process, single SQLite writer)."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from .. import notify, profiles
from ..config import Settings
from . import events, preprocess, queue, retention, scanner, uploader

log = logging.getLogger(__name__)


class Gate:
    """The background loops' work runs inside ``work()``. ``exclusive()`` (a reset from
    Settings → Danger zone) holds new work back and waits until none runs, so data is not
    deleted while the pipeline writes it."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._busy = 0
        self._closed = False

    @contextmanager
    def work(self) -> Iterator[None]:
        with self._cond:
            self._cond.wait_for(lambda: not self._closed)
            self._busy += 1
        try:
            yield
        finally:
            with self._cond:
                self._busy -= 1
                self._cond.notify_all()

    @contextmanager
    def exclusive(self, timeout: float = 120) -> Iterator[None]:
        with self._cond:
            if not self._cond.wait_for(lambda: not self._closed, timeout):
                raise TimeoutError("another reset is running")
            self._closed = True
            if not self._cond.wait_for(lambda: self._busy == 0, timeout):
                self._closed = False
                self._cond.notify_all()
                raise TimeoutError("the background work did not finish in time")
        try:
            yield
        finally:
            with self._cond:
                self._closed = False
                self._cond.notify_all()


gate = Gate()


def _gated(fn: Callable[..., object], *args: object) -> object:
    with gate.work():
        return fn(*args)


async def _loop(name: str, interval: float, fn: Callable[[], object]) -> None:
    while True:
        try:
            await asyncio.to_thread(_gated, fn)
        except Exception:
            log.exception("background task %s failed", name)
        await asyncio.sleep(interval)


async def _preprocess_loop(settings: Settings) -> None:
    # At most processing.concurrency at once, read each round: it can change while running
    # (Settings page). Lowering it lets the running ones finish.
    in_flight: set[int] = set()

    async def one(seg_id: int) -> None:
        try:
            await asyncio.to_thread(_gated, preprocess.preprocess_segment, settings, seg_id)
        finally:
            in_flight.discard(seg_id)

    while True:
        try:
            ids = await asyncio.to_thread(preprocess.pending_segment_ids)
            for seg_id in ids:
                if len(in_flight) >= settings.processing.concurrency:
                    break
                if seg_id not in in_flight:
                    in_flight.add(seg_id)
                    asyncio.create_task(one(seg_id))
        except Exception:
            log.exception("preprocess loop failed")
        await asyncio.sleep(3)


def _events_tick(settings: Settings) -> None:
    events.assign_events(settings)
    events.build_clips(settings)


def _reap_tick(settings: Settings) -> None:
    queue.reap(settings)
    profiles.cancel_jobs_without_profile(settings)


def start_background(settings: Settings) -> list[asyncio.Task[None]]:
    return [
        asyncio.create_task(_loop("scanner", 5, lambda: scanner.scan_once(settings))),
        asyncio.create_task(_preprocess_loop(settings)),
        asyncio.create_task(_loop("reaper", 15, lambda: _reap_tick(settings))),
        asyncio.create_task(_loop("events", 10, lambda: _events_tick(settings))),
        asyncio.create_task(_loop("retention", 600, lambda: retention.run_retention(settings))),
        asyncio.create_task(_loop("uploader", 30, lambda: uploader.upload_pending(settings))),
        asyncio.create_task(_loop("notifier", 5, lambda: notify.deliver_pending(settings))),
    ]
