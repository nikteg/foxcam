"""The camera's own timestamps for the cloud relay's MPEG-TS, kept continuous across
relay sessions.

The relay delivers frames in bunches (several within a few milliseconds, then a gap), so
stamping them with their arrival time put a quarter second of jitter on every frame. The
camera's timestamps in the stream are even, but each relay session can start from another
point on the camera's clock. So every timestamp passing through (PTS, DTS and PCR, on every
stream) keeps the camera's spacing, and the stream is anchored again at each new session,
or at a jump in the camera's clock, to where the previous output was plus the real time
since: gaps stay real gaps, and the stream cannot drift away from the clock.
"""

from __future__ import annotations

import time
from collections.abc import Callable

PACKET = 188
SYNC = 0x47
HZ = 90_000  # MPEG timestamps (and the PCR base) count 90 kHz ticks...
WRAP = 1 << 33  # ...in 33 bits
JUMP = 5 * HZ  # a step this big in the camera's clock is a discontinuity, not a frame
# PES stream ids without the optional header (no timestamps).
NO_PES_HEADER = {0xBC, 0xBE, 0xBF, 0xF0, 0xF1, 0xF2, 0xF8, 0xFF}


def _signed(delta: int) -> int:
    """A difference of two 33-bit timestamps, across the wrap."""
    delta %= WRAP
    return delta - WRAP if delta >= WRAP // 2 else delta


class Retimer:
    """Rewrites the timestamps of an MPEG-TS byte stream passed through ``write`` and hands
    whole packets on to ``out``. Call ``new_session`` when the next relay session starts.
    ``on_skip`` hears of each jump ahead in the camera's clock within a session (seconds):
    footage the camera did not deliver, which the output closes up."""

    def __init__(self, out: Callable[[bytes], None],
                 clock: Callable[[], float] = time.monotonic,
                 on_skip: Callable[[float], None] | None = None) -> None:
        self._out = out
        self._clock = clock
        self._on_skip = on_skip
        self._buf = bytearray()
        self._offset = 0
        self._anchor = True  # at the next timestamp
        self._last_in: int | None = None
        # Per kind ("pcr", "pes": a frame's time runs ahead of the PCR by the muxer's
        # delay), the latest output timestamp and when it passed.
        self._last_out: dict[str, tuple[int, float]] = {}

    def new_session(self) -> None:
        self._buf.clear()  # a part of a packet from the last session is no use
        self._anchor = True

    def write(self, data: bytes) -> None:
        self._buf += data
        out = bytearray()
        while len(self._buf) >= PACKET:
            if self._buf[0] != SYNC or (len(self._buf) > PACKET and self._buf[PACKET] != SYNC):
                # Out of step (a broken packet): on to the next sync byte.
                nxt = self._buf.find(SYNC, 1)
                del self._buf[:nxt if nxt > 0 else len(self._buf)]
                continue
            packet = self._buf[:PACKET]
            del self._buf[:PACKET]
            self._packet(packet)
            out += packet
        if out:
            self._out(bytes(out))

    # --- timestamps ---------------------------------------------------------------------

    def _map(self, ts: int, kind: str) -> int:
        now = self._clock()
        if self._last_in is not None and abs(jump := _signed(ts - self._last_in)) > JUMP:
            if jump > 0 and not self._anchor and self._on_skip:
                self._on_skip(jump / HZ)
            self._anchor = True
        if self._anchor:
            # Continue from the last output of this kind plus the real time since (the
            # very first time: as is).
            last = self._last_out.get(kind) or next(iter(self._last_out.values()), None)
            target = ts if last is None else (last[0] + round((now - last[1]) * HZ)) % WRAP
            self._offset = (target - ts) % WRAP
            self._anchor = False
        self._last_in = ts
        mapped = (ts + self._offset) % WRAP
        last = self._last_out.get(kind)
        if last is None or _signed(mapped - last[0]) > 0:
            self._last_out[kind] = (mapped, now)
        return mapped

    def _packet(self, p: bytearray) -> None:
        control = (p[3] >> 4) & 3
        pos = 4
        if control & 2:  # adaptation field, maybe with a PCR
            length = p[4]
            if length >= 7 and p[5] & 0x10:
                base = (p[6] << 25) | (p[7] << 17) | (p[8] << 9) | (p[9] << 1) | (p[10] >> 7)
                base = self._map(base, "pcr")
                p[6:10] = bytes(((base >> 25) & 0xFF, (base >> 17) & 0xFF,
                                 (base >> 9) & 0xFF, (base >> 1) & 0xFF))
                p[10] = ((base & 1) << 7) | (p[10] & 0x7F)
            pos = 5 + length
        if not control & 1 or not p[1] & 0x40:  # no payload, or not the start of a PES
            return
        if pos + 14 > PACKET or p[pos:pos + 3] != b"\x00\x00\x01" or p[pos + 3] in NO_PES_HEADER:
            return
        flags = p[pos + 7] >> 6
        if flags & 2:
            self._rewrite(p, pos + 9)  # PTS
        if flags == 3 and pos + 19 <= PACKET:
            self._rewrite(p, pos + 14)  # DTS

    def _rewrite(self, p: bytearray, at: int) -> None:
        b = p[at:at + 5]
        ts = (((b[0] >> 1) & 7) << 30) | (b[1] << 22) | ((b[2] >> 1) << 15) | (b[3] << 7) \
            | (b[4] >> 1)
        ts = self._map(ts, "pes")
        p[at:at + 5] = bytes(((b[0] & 0xF0) | ((ts >> 29) & 0x0E) | 1, (ts >> 22) & 0xFF,
                              ((ts >> 14) & 0xFE) | 1, (ts >> 7) & 0xFF,
                              ((ts << 1) & 0xFE) | 1))
