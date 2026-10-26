"""Several processes (the server, ingest and compaction containers) starting together on a
new database."""

from __future__ import annotations

import multiprocessing as mp

from foxcam.config import Settings


def _start(data_dir: str, q) -> None:
    from foxcam.db import init_db
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


def test_existing_database_gets_new_indexes(tmp_path):
    """create_all leaves existing tables alone: indexes added to the models later (also on
    columns added by the migration) are created on startup."""
    import sqlite3

    from foxcam.db import init_db

    init_db(Settings(data_dir=str(tmp_path)))
    con = sqlite3.connect(tmp_path / "foxcam.db")
    con.execute("DROP INDEX ix_detection_event_id")
    con.execute("DROP INDEX ix_detection_camera_ts")
    con.commit()
    init_db(Settings(data_dir=str(tmp_path)))
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"ix_detection_event_id", "ix_detection_camera_ts"} <= names
