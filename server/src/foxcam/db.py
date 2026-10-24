from __future__ import annotations

import fcntl
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import Base

log = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init_db(settings: Settings) -> Engine:
    global _engine, _SessionLocal
    settings.ensure_dirs()
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False,
                                                                 "timeout": 30})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        # WAL is stored in the file: switch once. Switching needs the database to itself,
        # which fails ("database is locked") while another process is using it.
        if cur.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    # The server, ingest and compaction containers start together on a new database: one
    # at a time creates and migrates it, the others then find it ready.
    with _init_lock(settings):
        _adopt_old_database(settings)
        Base.metadata.create_all(engine)
        added = _add_missing_columns(engine)
        _backfill(engine, added)
    _engine = engine
    _SessionLocal = sessionmaker(engine, expire_on_commit=False)
    # Tunables changed in the web UI win over config.yaml from the start.
    from . import runtime_settings

    runtime_settings.refresh(settings, force=True)
    return engine


def _adopt_old_database(settings: Settings) -> None:
    """Before the rename to Foxcam the database was media-streamer.db: carry it over
    (with its WAL files) the first time, instead of starting empty."""
    new = settings.data_dir / "foxcam.db"
    old = settings.data_dir / "media-streamer.db"
    if settings.database_url != f"sqlite:///{new}" or new.exists() or not old.exists():
        return
    for suffix in ("", "-wal", "-shm"):
        src = old.with_name(old.name + suffix)
        if src.exists():
            src.rename(new.with_name(new.name + suffix))
    log.info("database %s renamed to %s", old.name, new.name)


@contextmanager
def _init_lock(settings: Settings) -> Iterator[None]:
    """An exclusive lock (a file in the data folder) across processes and containers."""
    with open(settings.data_dir / ".db-init.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _add_missing_columns(engine: Engine) -> set[str]:
    """Minimal migration: add new nullable columns to existing tables.

    ``create_all`` creates missing tables but never alters existing ones; new optional
    columns are added here so existing databases keep working after an upgrade.
    """
    insp = inspect(engine)
    added: set[str] = set()
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                if not col.nullable:
                    raise RuntimeError(f"cannot add NOT NULL column {table.name}.{col.name}")
                ddl = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'))
                log.info("database: added column %s.%s", table.name, col.name)
                added.add(f"{table.name}.{col.name}")
    return added


def _backfill(engine: Engine, added: set[str]) -> None:
    """Data written before detection profiles existed belongs to the `default` profile."""
    with engine.begin() as conn:
        for table in ("job", "detection", "event"):
            conn.execute(text(f"UPDATE {table} SET profile_id = 'default' "
                              "WHERE profile_id IS NULL"))
        # ... and to its single detector, from before profiles had several.
        for table in ("job", "detection"):
            conn.execute(text(f"UPDATE {table} SET detector_id = 'default' "
                              "WHERE detector_id IS NULL"))
        if "user_account.email_verified" in added:
            # Accounts from before: admins, and those an OAuth provider confirmed.
            conn.execute(text(
                "UPDATE user_account SET email_verified = (is_admin = 1 OR EXISTS (SELECT 1 "
                "FROM oauth_identity o WHERE o.user_id = user_account.id "
                "AND lower(o.email) = user_account.email))"))
        if "event.agreement" in added:
            # Which detectors saw each event (one, `default`, before there were several).
            conn.execute(text(
                "UPDATE event SET detectors = (SELECT json_group_array(DISTINCT d.detector_id) "
                "FROM detection d WHERE d.event_id = event.id), agreement = (SELECT "
                "COUNT(DISTINCT d.detector_id) FROM detection d WHERE d.event_id = event.id)"))
            conn.execute(text("UPDATE event SET detectors = '[\"default\"]', agreement = 1 "
                              "WHERE agreement IS NULL OR agreement = 0"))
        if "detection.event_id" in added:
            # Events used to be per segment: link their target detections.
            conn.execute(text(
                "UPDATE detection SET event_id = (SELECT es.event_id FROM event_segment es "
                "JOIN event e ON e.id = es.event_id WHERE es.segment_id = detection.segment_id "
                "AND e.camera_id = detection.camera_id ORDER BY es.event_id LIMIT 1) "
                "WHERE is_target = 1 AND event_id IS NULL"))


def db_ready() -> bool:
    return _SessionLocal is not None


@contextmanager
def session_scope() -> Iterator[Session]:
    if _SessionLocal is None:
        raise RuntimeError("init_db() has not been called")
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s
