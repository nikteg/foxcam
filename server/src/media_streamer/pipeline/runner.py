"""Background loops run inside the web process (single process, single SQLite writer)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from .. import profiles
from ..config import Settings
from . import events, preprocess, queue, retention, scanner, uploader

log = logging.getLogger(__name__)


async def _loop(name: str, interval: float, fn: Callable[[], object]) -> None:
    while True:
        try:
            await asyncio.to_thread(fn)
        except Exception:
            log.exception("background task %s failed", name)
        await asyncio.sleep(interval)


async def _preprocess_loop(settings: Settings) -> None:
    sem = asyncio.Semaphore(settings.processing.concurrency)
    in_flight: set[int] = set()

    async def one(seg_id: int) -> None:
        async with sem:
            try:
                await asyncio.to_thread(preprocess.preprocess_segment, settings, seg_id)
            finally:
                in_flight.discard(seg_id)

    while True:
        try:
            ids = await asyncio.to_thread(preprocess.pending_segment_ids)
            for seg_id in ids:
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
    ]
