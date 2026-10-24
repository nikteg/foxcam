"""Several processes (the server, ingest and compaction containers) starting together on a
new database."""

from __future__ import annotations

import multiprocessing as mp

from media_streamer.config import Settings


def _start(data_dir: str, q) -> None:
    from media_streamer.db import init_db
    try:
        init_db(Settings(data_dir=data_dir))
        q.put("ok")
    except Exception as e:  # noqa: BLE001 - reported to the test
        q.put(repr(e))


def test_processes_starting_together_on_a_new_database(tmp_path):
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    procs = [ctx.Process(target=_start, args=(str(tmp_path), q)) for _ in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert sorted(q.get(timeout=5) for _ in procs) == ["ok"] * 6
