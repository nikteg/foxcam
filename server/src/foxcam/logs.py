"""Log files the web UI shows (Settings → Logs).

Each long-running process (server, ingest, compact) logs to stdout, as before, and to
``<data_dir>/logs/<process>.log``. MediaMTX's lines go through ``foxcam mediamtx`` to
``<data_dir>/mediamtx/mediamtx.log`` (its container sees only that folder). Workers, which
may run on other machines, send theirs (POST /api/worker/logs) to
``<data_dir>/logs/worker-<worker id>.log``. A file rotates
at ``MAX_BYTES`` and keeps ``BACKUPS`` old ones, so a process never has more than
(1 + BACKUPS) × MAX_BYTES on disk.

Every line is redacted first, on stdout too: logins in URLs (camera sources carry their
password) and token/password/secret values.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 4
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
PROCESSES = ("server", "ingest", "compact", "mediamtx")

# scheme://login@host: the login runs to the last @ in the word (passwords may hold @).
_URL_LOGIN = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://)[^\s\"'<>]*@")
# token=…, "password": "…", Authorization: Bearer …
_SECRET = re.compile(
    r"""(?ix)
    \b((?:access_|refresh_|api_|worker_|download_)?token|password|passwd|secret|api_?key
       |authorization|cookie|session)
    (["']?\s*[:=]\s*["']?)
    (?:bearer\s+|basic\s+)?
    [^\s"'&,;}]+""")


def redact(text: str) -> str:
    """`text` without logins in URLs or values of token/password/secret fields."""
    text = _URL_LOGIN.sub(r"\1***@", text)
    return _SECRET.sub(r"\1\2***", text)


class RedactingFormatter(logging.Formatter):
    """The usual line, in UTC with a Z, with secrets taken out (tracebacks too)."""

    def __init__(self) -> None:
        super().__init__(FORMAT)

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        t = datetime.fromtimestamp(record.created, UTC)
        return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


MAX_WORKER_LOGS = 20
_WORKER_LOG = re.compile(r"^worker-[A-Za-z0-9._-]{1,80}$")


def worker_process(worker_id: str) -> str:
    """The log name of a worker: its id with only safe characters."""
    return "worker-" + (re.sub(r"[^A-Za-z0-9._-]", "_", worker_id)[:80] or "_")


def known(process: str) -> bool:
    return process in PROCESSES or bool(_WORKER_LOG.match(process))


def log_path(data_dir: Path, process: str) -> Path:
    if not known(process):
        raise ValueError(f"no such log: {process}")
    if process == "mediamtx":
        return data_dir / "mediamtx" / "mediamtx.log"
    return data_dir / "logs" / f"{process}.log"


def file_handler(path: Path) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS,
                                  encoding="utf-8")
    handler.setFormatter(RedactingFormatter())
    return handler


def setup(level: int) -> None:
    """Logging to stdout, redacted (every command)."""
    logging.basicConfig(level=level)
    for h in logging.getLogger().handlers:
        h.setFormatter(RedactingFormatter())


def to_file(data_dir: Path, process: str) -> None:
    """Also write this process's log to its file (the long-running ones). Uvicorn's line
    per request stays on stdout only."""
    handler = file_handler(log_path(data_dir, process))
    handler.addFilter(lambda record: record.name != "uvicorn.access")
    logging.getLogger().addHandler(handler)


# MediaMTX: "2026/11/01 10:00:00 INF [path x] ..." (its clock: UTC in the container).
_MTX_LINE = re.compile(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d (DEB|INF|WAR|ERR) (.*)$")
_MTX_LEVEL = {"DEB": logging.DEBUG, "INF": logging.INFO, "WAR": logging.WARNING,
              "ERR": logging.ERROR}


def mediamtx_logger(data_dir: Path) -> logging.Logger:
    """A logger for MediaMTX's lines that only writes its file (stdout gets them as is)."""
    log = logging.getLogger("mediamtx")
    log.propagate = False
    log.setLevel(logging.DEBUG)
    log.handlers = [file_handler(log_path(data_dir, "mediamtx"))]
    return log


def mediamtx_line(log: logging.Logger, line: str) -> None:
    m = _MTX_LINE.match(line)
    if m:
        log.log(_MTX_LEVEL[m.group(1)], "%s", m.group(2))
    elif line.strip():
        log.info("%s", line)


# Reading ------------------------------------------------------------------------------

_HEAD = re.compile(
    r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z) (DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
    r"([^:\s]+): ?(.*)$")
LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def files(data_dir: Path, process: str) -> list[Path]:
    """The process's files, newest first."""
    path = log_path(data_dir, process)
    candidates = [path, *(path.with_name(f"{path.name}.{i}") for i in range(1, BACKUPS + 1))]
    return [p for p in candidates if p.exists()]


def _reverse_lines(path: Path, block: int = 64 * 1024) -> Iterator[str]:
    """The file's lines, last first, read from the end in blocks."""
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        rest = b""
        while pos > 0:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            chunk = f.read(step) + rest
            lines = chunk.split(b"\n")
            rest = lines[0]
            for raw in reversed(lines[1:]):
                yield raw.decode("utf-8", "replace")
        if rest:
            yield rest.decode("utf-8", "replace")


def _entries(data_dir: Path, process: str) -> Iterator[dict[str, str]]:
    """Log entries, newest first; lines without a head (tracebacks) join the entry above."""
    for path in files(data_dir, process):
        tail: list[str] = []
        for line in _reverse_lines(path):
            m = _HEAD.match(line)
            if not m:
                if line:
                    tail.append(line)
                continue
            message = "\n".join([m.group(4), *reversed(tail)])
            tail = []
            yield {"ts": m.group(1), "level": m.group(2), "logger": m.group(3),
                   "message": message}


def read(data_dir: Path, process: str, limit: int = 500, level: str | None = None,
         text: str | None = None) -> list[dict[str, str]]:
    """The newest `limit` entries at `level` or above that contain `text` (any case)."""
    floor = LEVELS.get((level or "").upper(), 0)
    needle = (text or "").lower()
    out: list[dict[str, str]] = []
    for e in _entries(data_dir, process):
        if LEVELS[e["level"]] < floor:
            continue
        if needle and needle not in f"{e['logger']} {e['message']}".lower():
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def _worker_logs(data_dir: Path) -> list[str]:
    return sorted(p.name.removesuffix(".log") for p in (data_dir / "logs").glob("worker-*.log")
                  if known(p.name.removesuffix(".log")))


def available(data_dir: Path) -> list[dict[str, object]]:
    """Which processes have a log, and its size on disk."""
    result = []
    for process in [*PROCESSES, *_worker_logs(data_dir)]:
        found = files(data_dir, process)
        if found:
            result.append({"process": process,
                           "bytes": sum(p.stat().st_size for p in found),
                           "updated_at": datetime.fromtimestamp(
                               found[0].stat().st_mtime, UTC).isoformat()
                           .replace("+00:00", "Z")})
    return result


def chunks(data_dir: Path, process: str) -> Iterator[bytes]:
    """All of a process's log, oldest first (for downloading)."""
    for path in reversed(files(data_dir, process)):
        with path.open("rb") as f:
            while block := f.read(256 * 1024):
                yield block


_worker_handlers: dict[str, logging.Handler] = {}


def write_worker_entries(data_dir: Path, worker_id: str,
                         entries: list[dict[str, str]]) -> bool:
    """Append a worker's log entries to its file (redacted). False when there are already
    MAX_WORKER_LOGS other workers' logs (a worker id per run would fill the disk)."""
    process = worker_process(worker_id)
    path = log_path(data_dir, process)
    handler = _worker_handlers.get(str(path))
    if handler is None:
        if not path.exists() and len(_worker_logs(data_dir)) >= MAX_WORKER_LOGS:
            return False
        handler = _worker_handlers[str(path)] = file_handler(path)
    for e in entries:
        try:
            created = datetime.fromisoformat(e["ts"]).timestamp()
        except ValueError:
            created = datetime.now(UTC).timestamp()
        record = logging.makeLogRecord({
            "name": e.get("logger") or "worker", "levelno": LEVELS.get(e["level"], 20),
            "levelname": e["level"] if e["level"] in LEVELS else "INFO",
            "msg": e["message"], "created": created})
        handler.handle(record)
    return True
