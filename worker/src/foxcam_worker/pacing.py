"""Spread detection out over time instead of running each job flat out.

A camera's segments finish together, so without a cap the worker runs at full power for a
minute and then idles for half an hour, and the fan follows. With ``--max-fps`` the same
frames are analysed at an even rate. Idle time earns no credit: after a quiet spell the
next job is paced from its first batch.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class Pacer:
    def __init__(self, max_fps: float | None, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.max_fps = max_fps if max_fps and max_fps > 0 else None
        self.clock, self.sleep = clock, sleep
        self.next_at = 0.0  # when the frames analysed so far are "paid for"

    def start(self) -> None:
        """Call before a job's first frames: time spent idle is not saved up."""
        self.next_at = max(self.next_at, self.clock())

    def __call__(self, frames: int) -> None:
        """Call after analysing ``frames`` frames: waits until they fit under the cap."""
        if self.max_fps is None or frames <= 0:
            return
        self.next_at += frames / self.max_fps
        if (wait := self.next_at - self.clock()) > 0:
            self.sleep(wait)
