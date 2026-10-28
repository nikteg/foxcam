"""Whether a target was seen while the sun was below the camera's ``sun_limit``.

A camera behind a window sees the lit room reflected in it once it is darker outside than
inside, and the detector finds the people in the room. Below the limit (degrees of sun
elevation where the camera is, negative after sunset) targets are stored with
``below_sun_limit`` set and are no match (no event, notification or "with a match"), like
targets standing still (pipeline/still.py). Without a location or a limit, nothing is
ignored.

A camera's location is its own (camera settings) or else ``location`` (Settings). Changing
either, or the limit, marks the camera's detections again (``apply``): those now ignored
leave their events (events left empty go), and those that count again form events quietly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from ..config import Settings
from ..db import session_scope
from ..models import CameraState, Detection

Place = tuple[float, float]


def place(settings: Settings, s, camera_id: str) -> Place | None:
    """Latitude and longitude of the camera, else of ``location``; None when neither is set."""
    st = s.get(CameraState, camera_id)
    if st and st.latitude is not None and st.longitude is not None:
        return st.latitude, st.longitude
    loc = settings.location
    if loc.latitude is None or loc.longitude is None:
        return None
    return loc.latitude, loc.longitude


def elevation(where: Place, ts: datetime) -> float:
    """The sun's elevation in degrees at ``ts`` (naive UTC)."""
    from astral import Observer
    from astral.sun import elevation as sun_elevation

    return float(sun_elevation(Observer(*where), ts.replace(tzinfo=UTC)))


def camera_limit(s, camera_id: str) -> float | None:
    st = s.get(CameraState, camera_id)
    return st.sun_limit if st else None


def mark(settings: Settings, s, detections: list[Any]) -> None:
    """Set ``below_sun_limit`` on target detections from their camera's limit (None when
    there is no limit or no location)."""
    cameras: dict[str, tuple[float | None, Place | None]] = {}
    for d in detections:
        if d.camera_id not in cameras:
            cameras[d.camera_id] = (camera_limit(s, d.camera_id),
                                    place(settings, s, d.camera_id))
        limit, where = cameras[d.camera_id]
        if not d.is_target or limit is None or where is None:
            d.below_sun_limit = None
        else:
            d.below_sun_limit = elevation(where, d.ts) < limit


def apply(settings: Settings, camera_id: str) -> int:
    """Mark the camera's detections again with its current limit and location, and update
    their events. Returns how many detections changed."""
    from .events import _lock, assign_events, refresh_event
    from .queue import refresh_match

    with _lock:
        with session_scope() as s:
            targets = list(s.scalars(select(Detection).where(
                Detection.camera_id == camera_id, Detection.is_target.is_(True))))
            before = {d.id: bool(d.below_sun_limit) for d in targets}
            mark(settings, s, targets)
            changed = [d for d in targets if bool(d.below_sun_limit) != before[d.id]]
            events = {d.event_id for d in changed if d.below_sun_limit and d.event_id}
            for d in changed:
                if d.below_sun_limit:
                    d.event_id = None
            s.flush()
            for event_id in events:
                refresh_event(settings, s, event_id)
            for seg in {d.segment for d in changed if d.segment is not None}:
                refresh_match(settings, seg)
        assign_events(settings, quiet=True)  # those that count again
    return len(changed)


def chart(settings: Settings, camera_id: str, days: int) -> dict[str, Any]:
    """The sun's elevation over the last ``days`` days where the camera is, and its target
    detections in that time with their feedback, for choosing a limit."""
    from .queue import in_wrong_event

    step = 15  # minutes
    end = datetime.now(UTC).replace(tzinfo=None, second=0, microsecond=0)
    start = end - timedelta(days=days)
    start -= timedelta(minutes=start.minute % step)
    with session_scope() as s:
        where = place(settings, s, camera_id)
        out: dict[str, Any] = {"limit": camera_limit(s, camera_id), "start": start,
                               "step_minutes": step, "located": where is not None,
                               "sun": [], "detections": []}
        if where is None:
            return out
        t = start
        while t <= end:
            out["sun"].append(round(elevation(where, t), 2))
            t += timedelta(minutes=step)
        recent = (Detection.camera_id == camera_id) & (Detection.ts >= start)
        # Marked wrong or correct themselves, or through their event.
        wrong = set(s.scalars(select(Detection.id).where(recent, in_wrong_event())))
        correct = set(s.scalars(select(Detection.event_id).where(
            recent, Detection.event_id.is_not(None), Detection.feedback.is_(True))))
        for d in s.scalars(select(Detection).where(recent, Detection.is_target.is_(True))
                           .order_by(Detection.ts.desc()).limit(5000)):
            feedback = d.feedback
            if feedback is None and d.id in wrong:
                feedback = False
            elif feedback is None and d.event_id in correct:
                feedback = True
            out["detections"].append({
                "id": d.id, "ts": d.ts, "label": d.label, "event_id": d.event_id,
                "segment_id": d.segment_id, "confidence": round(d.confidence, 3),
                "elevation": round(elevation(where, d.ts), 2), "feedback": feedback,
                "still": bool(d.still)})
    return out
