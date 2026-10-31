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
        Base.metadata.create_all(engine)
        _add_missing_columns(engine)
        _optimize(engine)
    _engine = engine
    _SessionLocal = sessionmaker(engine, expire_on_commit=False)
    # Tunables changed in the web UI win over config.yaml from the start.
    from . import runtime_settings

    runtime_settings.refresh(settings, force=True)
    return engine


@contextmanager
def _init_lock(settings: Settings) -> Iterator[None]:
    """An exclusive lock (a file in the data folder) across processes and containers."""
    with open(settings.data_dir / ".db-init.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _add_missing_columns(engine: Engine) -> None:
    """The only migration: add new nullable columns (and indexes) to existing tables.

    ``create_all`` creates missing tables but never alters existing ones; new optional
    columns are added here so existing databases keep working after an upgrade. One-off data
    fixes for an upgrade (filling a new column, dropping an old table) go next to this while
    they are needed, and are removed once production has run them: no backwards
    compatibility is kept, so they don't pile up.
    """
    insp = inspect(engine)
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
            # Indexes of added columns (and indexes added to old columns) too.
            existing_ix = {ix["name"] for ix in insp.get_indexes(table.name)}
            for ix in table.indexes:
                if ix.name not in existing_ix:
                    ix.create(conn)
                    log.info("database: added index %s", ix.name)


def _optimize(engine: Engine) -> None:
    """Table statistics for the query planner: without them SQLite picks an index on a
    column with few values (camera, is_target) over the one that matches the query. A
    bounded sample, and only for tables whose statistics are missing or out of date."""
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA analysis_limit=1000")
        conn.exec_driver_sql("PRAGMA optimize=0x10002")


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
