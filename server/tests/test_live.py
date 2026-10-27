"""Live job counts: the backlog, nudges from the queue, and the event stream."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from foxcam import changes
from foxcam.models import utcnow
from foxcam.web.api import job_counts

from .conftest import add_job, add_segment, app_client


def test_job_counts_count_the_backlog(settings):
    start = utcnow().replace(microsecond=0)
    add_segment(settings, start=start, status="new")
    for i, state in enumerate(("pending", "leased", "done")):
        add_job(add_segment(settings, start=start + timedelta(seconds=10 * (i + 1))),
                state=state)
    counts = job_counts(settings)
    assert counts == {"preparation": 1, "archiving": 0, "detection": 2}


def test_a_nudge_wakes_listeners():
    async def main() -> bool:
        with changes.listen() as changed:
            await asyncio.to_thread(changes.notify)  # from a worker thread, like the queue
            await asyncio.wait_for(changed.wait(), 1)
            return changed.is_set()

    assert asyncio.run(main())


def test_the_stream_starts_with_the_counts(settings):
    add_job(add_segment(settings), state="pending")
    with app_client(settings) as c, c.stream("GET", "/api/live/jobs") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        line = next(line for line in r.iter_lines() if line.startswith("data: "))
    assert json.loads(line.removeprefix("data: "))["detection"] == 1
