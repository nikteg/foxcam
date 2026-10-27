"""Live view over the Tapo cloud relay, kept alive the way the Tapo app does it.

The relay closes a live-view session after about two minutes with
``{"event_type":"connection_closed","error_code":15,"close_reason":"get_lack_heart_beat"}``
unless the client keeps talking. ``ontapo``'s ``live_preview`` sends the preview request
once and stays silent; the Tapo app (as reverse engineered by tothi/tapo-view) sends, on
the same multipart connection:

- a heartbeat every 20 s: ``{"type":"notification","params":{"event_type":"heartbeat"}}``
- every 25th video part, a ``stream_sequence`` notification with the
  ``X-Data-Received: <sequence>`` and ``X-Session-Id`` headers (flow control: the camera
  only keeps a window of unacknowledged parts in flight).

This module does the same, using ontapo's relay set-up and framing helpers. It relies on
ontapo internals; if they change, ``supported()`` turns false and the caller falls back
to plain ``live_preview`` with reconnects.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

IDLE_GAP = 12.0  # seconds without video that mean the camera/relay is done
HEARTBEAT = '{"type":"notification","params":{"event_type":"heartbeat"}}'
SEQUENCE_ACK = '{"type":"notification","params":{"event_type":"stream_sequence"}}'
ACK_EVERY = 25
WINDOW = 50


@dataclass
class RelayResult:
    bytes: int
    seconds: float
    reason: str
    heartbeats: int


def supported() -> bool:
    try:
        from ontapo import relay, stream

        return all(hasattr(stream, n) for n in (
            "_open_relay", "_relay_head", "_read_status", "_read_headers", "_read_line",
            "_control_error", "_stream_refused", "_CLIENT_BOUNDARY", "_DEVICE_BOUNDARY",
            "_MAX_PART_SIZE")) and hasattr(relay, "request_relay")
    except ImportError:
        return False


def _part(st, payload: str, headers: dict[str, str] | None = None) -> bytes:
    """One client multipart part (JSON)."""
    body = payload.encode()
    extra = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    return (f"--{st._CLIENT_BOUNDARY}\r\nContent-Type: application/json\r\n{extra}"
            f"Content-Length: {len(body)}\r\n\r\n").encode() + body + b"\r\n"


async def _read_part(st, reader: asyncio.StreamReader) -> tuple[dict[str, str], bytes]:
    """One device multipart part: (lower-cased headers, payload)."""
    while True:
        line = await st._read_line(reader)
        if st._DEVICE_BOUNDARY in line:
            break
    headers = await st._read_headers(reader)
    length = int(headers.get("content-length") or 0)
    if length < 0 or length > st._MAX_PART_SIZE:
        raise ValueError(f"bad multipart content-length {length}")
    return headers, (await reader.readexactly(length) if length else b"")


def _preview_request(resolution: str) -> str:
    return json.dumps({"type": "request", "seq": 1, "params": {"method": "get", "preview": {
        "channels": [0], "resolutions": [resolution], "audio": ["default"]}}},
        separators=(",", ":"))


async def relay_preview(session, dev, out: Callable[[bytes], None], *, resolution: str = "HD",
                        heartbeat: float = 20.0, max_seconds: float = 0,
                        stop: threading.Event | None = None) -> RelayResult:
    """Stream the camera's live view as MPEG-TS into ``out`` until the relay ends it,
    ``max_seconds`` pass (0 = no limit) or ``stop`` is set."""
    from ontapo import stream as st
    from ontapo.errors import RelayError
    from ontapo.relay import StreamType, request_relay

    track_id = f"preview-{dev.device_id}"
    relay = await request_relay(session, dev.device_id, StreamType.PREVIEW, track_id)
    reader, writer, host, path = await st._open_relay(relay, session.terminal_uuid, track_id)
    started = time.monotonic()
    beats, written, session_id = 0, 0, ""
    reason = "stopped"
    try:
        writer.write(st._relay_head(relay, session.terminal_uuid, track_id, path, host).encode())
        writer.write(_part(st, _preview_request(resolution), {"X-Data-Window-Size": str(WINDOW)}))
        await writer.drain()
        status = await st._read_status(reader)
        await st._read_headers(reader)
        if status != 200:
            raise RelayError(f"relay preview HTTP {status}")
        last_beat = last_data = time.monotonic()
        while not (stop and stop.is_set()):
            now = time.monotonic()
            if max_seconds and now - started >= max_seconds:
                reason = "max_seconds"
                break
            if heartbeat and now - last_beat >= heartbeat:
                writer.write(_part(st, HEARTBEAT))
                await writer.drain()
                last_beat, beats = now, beats + 1
            try:
                headers, payload = await asyncio.wait_for(_read_part(st, reader), timeout=2)
            except TimeoutError:
                if time.monotonic() - last_data > IDLE_GAP:
                    reason = f"no video for {IDLE_GAP:.0f} s"
                    break
                continue
            except asyncio.IncompleteReadError:
                reason = "relay closed the connection"
                break
            session_id = headers.get("x-session-id") or session_id
            kind = headers.get("content-type", "")
            if "video/mp2t" in kind and payload:
                out(payload)
                written += len(payload)
                last_data = time.monotonic()
                seq = headers.get("x-data-sequence", "")
                if session_id and seq.isdigit() and int(seq) % ACK_EVERY == 0:
                    writer.write(_part(st, SEQUENCE_ACK, {"X-Data-Received": seq,
                                                         "X-Session-Id": session_id}))
                    await writer.drain()
            elif "application/json" in kind:
                text = payload.decode("utf-8", "replace")
                code = st._control_error(payload)
                if code:
                    raise st._stream_refused(code)
                if "connection_closed" in text:
                    reason = f"relay closed the session: {text[:200]}"
                    break
                if '"session_id"' in text:
                    try:
                        session_id = str(json.loads(text)["params"]["session_id"])
                    except (ValueError, KeyError, TypeError):
                        pass
                log.debug("relay control: %s", text[:200])
        if not written:
            raise RelayError(f"relay preview produced no video data ({reason})")
        return RelayResult(written, time.monotonic() - started, reason, beats)
    finally:
        writer.close()
        with contextlib.suppress(Exception):  # closing a dead TLS connection
            await writer.wait_closed()
