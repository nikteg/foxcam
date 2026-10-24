from __future__ import annotations

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
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    added = _add_missing_columns(engine)
    _backfill(engine, added)
    _engine = engine
    _SessionLocal = sessionmaker(engine, expire_on_commit=False)
    return engine


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
