"""The database of an installation from before the rename (media-streamer.db) is kept."""

from __future__ import annotations

import sqlite3

from foxcam import db
from foxcam.config import Settings
from foxcam.db import init_db, session_scope
from foxcam.models import AppSetting


def test_old_database_is_carried_over(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    first = Settings.model_validate({"data_dir": str(tmp_path / "first")})
    init_db(first)
    with session_scope() as s:
        s.add(AppSetting(key="marker", value=1))
    db._engine.dispose()  # closed, as by the old version shutting down
    for suffix in ("-wal", "-shm"):
        (tmp_path / "first" / f"foxcam.db{suffix}").unlink(missing_ok=True)
    (tmp_path / "first" / "foxcam.db").rename(data / "media-streamer.db")

    settings = Settings.model_validate({"data_dir": str(data)})
    init_db(settings)
    assert not (data / "media-streamer.db").exists()
    with session_scope() as s:
        assert s.get(AppSetting, "marker").value == 1
    assert sqlite3.connect(data / "foxcam.db").execute("select count(*) from app_setting").fetchone()
