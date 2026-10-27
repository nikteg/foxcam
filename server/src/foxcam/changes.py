"""A nudge for live views: code that changes the jobs' backlog (a job leased, finished or
queued again, a recording starting or ending its preparation) calls ``notify``, and each
open live stream (``/api/live/jobs``) counts again at once instead of at its next check.

Thread safe: the queue and preparation run in worker threads, the streams on the event
loop. A nudge without listeners costs nothing.
"""

from __future__ import annotations

import asyncio
import functools
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

_lock = threading.Lock()
_listeners: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()


def notify() -> None:
    with _lock:
        listeners = list(_listeners)
    for loop, event in listeners:
        loop.call_soon_threadsafe(event.set)


def notifies(fn: Callable[P, R]) -> Callable[P, R]:
    """Decorator: ``notify`` after ``fn`` returned (it changed the backlog)."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        result = fn(*args, **kwargs)
        notify()
        return result

    return wrapper


@contextmanager
def listen() -> Iterator[asyncio.Event]:
    """An event set by every ``notify`` while inside (clear it after reacting)."""
    event = asyncio.Event()
    key = (asyncio.get_running_loop(), event)
    with _lock:
        _listeners.add(key)
    try:
        yield event
    finally:
        with _lock:
            _listeners.discard(key)
