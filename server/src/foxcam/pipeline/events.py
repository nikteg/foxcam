"""Group target detections into events and cut a clip for each event.

Target detections (per detection profile, e.g. foxes) are merged into an event with the
same camera, profile and label when within ``merge_gap_seconds``; a fox and a car seen at
the same time become two events. A target standing still (the profile's
``still_minutes``, pipeline/still.py) is no match and joins no event, so a parked car
neither keeps one open forever nor pins its footage; it counts again once it moves.
Detections of the profile's different detectors (models)
join the same event; ``Event.detectors`` / ``agreement`` say which and how many saw it,
so notification channels can wait for several models to agree (``min_agreement``). Once an event has been quiet for
``settle_seconds`` (and the post-roll footage exists) its clip is (re)built from the
original, unmasked recordings, and the underlying segments are pinned.
"""

from __future__ import annotations

import logging
import shutil
import threading
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, func, select, update

from .. import ffmpeg, notify, sprites
from ..config import Settings
from ..db import session_scope
from ..models import Detection, Event, Segment, event_segments, utcnow
from . import still
from .compaction import segment_source
from .queue import counts_as_match, refresh_match

log = logging.getLogger(__name__)


# Building events and clips, and rebuilding them all (rebuild_events), one at a time.
_lock = threading.RLock()


def assign_events(settings: Settings, quiet: bool = False) -> int:
    """Group detections not in an event yet. ``quiet``: the new events notify nobody."""
    with _lock:
        return _assign_events(settings, quiet)


def _assign_events(settings: Settings, quiet: bool) -> int:
    gap = timedelta(seconds=settings.events.merge_gap_seconds)
    changed = 0
    with session_scope() as s:
        pending = s.scalars(
            select(Detection).where(counts_as_match(), Detection.event_id.is_(None))
            .order_by(Detection.ts)
        ).all()
        groups: dict[tuple, list[Detection]] = defaultdict(list)
        for d in pending:
            groups[(d.segment_id, d.camera_id, d.profile_id, d.label.lower())].append(d)
        for (segment_id, camera_id, profile_id, _), dets in groups.items():
            first = min(d.ts for d in dets)
            last = max(d.ts for d in dets)
            best = max(dets, key=lambda d: d.confidence)
            event = s.scalars(
                select(Event).where(Event.camera_id == camera_id, Event.profile_id == profile_id,
                                    func.lower(Event.label) == best.label.lower(),
                                    Event.last_ts >= first - gap, Event.first_ts <= last + gap)
                .order_by(Event.first_ts).limit(1)
            ).first()
            if event is None:
                event = Event(camera_id=camera_id, profile_id=profile_id, label=best.label,
                              first_ts=first, last_ts=last, max_confidence=best.confidence,
                              detection_count=0, best_detection_id=best.id, quiet=quiet)
                s.add(event)
                s.flush()
            else:
                event.first_ts = min(event.first_ts, first)
                event.last_ts = max(event.last_ts, last)
                if best.confidence > event.max_confidence:
                    event.max_confidence = best.confidence
                    event.best_detection_id = best.id
            for d in dets:
                d.event_id = event.id
            event.detection_count += len(dets)
            refresh_models(settings, s, event)
            # New events notify at once; a later rise above a channel's min_confidence, or
            # another model agreeing (min_agreement), too - once per channel.
            notify.enqueue(s, settings, event, "event.new")
            seg = s.get(Segment, segment_id)
            if seg not in event.segments:
                event.segments.append(seg)
            # Joined an event already marked wrong: not a match in this recording either.
            refresh_match(settings, seg)
            event.clip_dirty = True
            event.updated_at = utcnow()
            changed += 1
    return changed


def refresh_models(settings: Settings, s, event: Event) -> None:
    """Which detectors saw the event, from its detections (also after some are removed),
    and whether a confirming detector (cascade ``confirm``) is among them."""
    s.flush()
    found = s.scalars(select(Detection.detector_id).where(Detection.event_id == event.id)
                      .distinct()).all()
    event.detectors = sorted(d or "default" for d in found)
    event.agreement = len(event.detectors)
    profile = settings.profile(event.profile_id)
    confirming = profile.confirming if profile else set()
    event.confirmed = not confirming or bool(confirming & set(event.detectors))


def rebuild_events(settings: Settings) -> dict[str, int]:
    """All events again from the detections, with the profiles' current rules (standing
    still is decided again with today's still_minutes, also for older detections). The
    rebuilt events notify nobody; their clips are cut again by the background loop."""
    with _lock:
        with session_scope() as s:
            removed = s.scalar(select(func.count()).select_from(Event)) or 0
            s.execute(delete(event_segments))
            s.execute(delete(Event))
            s.execute(update(Segment).values(pinned=False))
            s.execute(update(Detection).values(event_id=None))
            targets = s.scalars(select(Detection).where(Detection.is_target.is_(True))
                                .order_by(Detection.ts)).all()
            still.mark(settings, s, list(targets))
            for seg in s.scalars(select(Segment).where(Segment.id.in_(
                    select(Detection.segment_id).where(Detection.is_target.is_(True))))):
                refresh_match(settings, seg)
        if settings.clips_dir.exists():
            shutil.rmtree(settings.clips_dir)
        settings.clips_dir.mkdir(parents=True, exist_ok=True)
        _assign_events(settings, quiet=True)
        with session_scope() as s:
            created = s.scalar(select(func.count()).select_from(Event)) or 0
            standing = s.scalar(select(func.count()).select_from(Detection).where(
                Detection.still.is_(True))) or 0
    log.info("events rebuilt: %d removed, %d created, %d detections standing still",
             removed, created, standing)
    return {"removed": removed, "created": created, "still": standing}


def build_clips(settings: Settings) -> int:
    with _lock:
        return _build_clips(settings)


def _build_clips(settings: Settings) -> int:
    cfg = settings.events
    now = utcnow()
    with session_scope() as s:
        ids = list(s.scalars(select(Event.id).where(
            Event.clip_dirty.is_(True),
            Event.updated_at <= now - timedelta(seconds=cfg.settle_seconds))))
    built = 0
    for event_id in ids:
        try:
            if _build_clip(settings, event_id):
                built += 1
        except Exception as e:
            log.exception("building clip for event %s failed", event_id)
            with session_scope() as s:
                ev = s.get(Event, event_id)
                ev.clip_error = str(e)[:500]
                ev.clip_dirty = False
    return built


def _build_clip(settings: Settings, event_id: int) -> bool:
    cfg = settings.events
    with session_scope() as s:
        ev = s.get(Event, event_id)
        want_start = ev.first_ts - timedelta(seconds=cfg.pre_roll_seconds)
        want_end = ev.last_ts + timedelta(seconds=cfg.post_roll_seconds)

        # Wait for the post-roll footage to be recorded, unless that is taking too long
        # (camera offline) - then cut what we have.
        latest = s.scalars(select(Segment).where(Segment.camera_id == ev.camera_id)
                           .order_by(Segment.start_at.desc()).limit(1)).first()
        if latest is not None and latest.end_at < want_end and \
                ev.updated_at > utcnow() - timedelta(minutes=10):
            return False

        segs = s.scalars(
            select(Segment).where(
                Segment.camera_id == ev.camera_id, Segment.deleted_at.is_(None),
                Segment.duration_s > 0, Segment.start_at < want_end,
                Segment.start_at >= want_start - timedelta(hours=1),
            ).order_by(Segment.start_at)
        ).all()
        segs = [g for g in segs if g.end_at > want_start]
        if not segs:
            raise RuntimeError("no recordings available for event")

        start = max(want_start, segs[0].start_at)
        end = min(want_end, segs[-1].end_at)
        offset = (start - segs[0].start_at).total_seconds()
        duration = (end - start).total_seconds()

        clip_rel = Path("clips") / ev.camera_id / f"event-{ev.id}.mp4"
        sources: list[ffmpeg.ClipPart] = []
        for g in segs:
            src = segment_source(settings, g)
            if src is None:
                raise RuntimeError(f"recording {g.id} is gone")
            sources.append((src.path, src.offset_s, src.offset_s + src.duration_s)
                           if src.archived else (src.path, None, None))
        ffmpeg.concat_clip(sources, settings.data_dir / clip_rel, offset, duration)
        try:  # the timeline previews, now while the clip is surely on disk
            sprites.event_sprite(settings, clip_rel, duration)
        except RuntimeError as e:
            log.warning("event %s: no previews: %s", event_id, e)

        best = s.get(Detection, ev.best_detection_id) if ev.best_detection_id else None
        thumb_rel = None
        if best is not None:
            thumb_rel = Path("clips") / ev.camera_id / f"event-{ev.id}.jpg"
            src = segment_source(settings, s.get(Segment, best.segment_id))
            if src is not None:
                ffmpeg.thumbnail(src.path, settings.data_dir / thumb_rel,
                                 at_s=src.offset_s + best.offset_ms / 1000, width=640,
                                 box=best.bbox)
            else:
                thumb_rel = None

        for g in segs:
            g.pinned = True
            if g not in ev.segments:
                ev.segments.append(g)
        nights = [g.is_night for g in segs if g.is_night is not None]
        ev.is_night = (sum(nights) > len(nights) / 2) if nights else None
        ev.clip_path = str(clip_rel)
        ev.thumb_path = str(thumb_rel) if thumb_rel else None
        ev.clip_start = start
        ev.clip_duration_s = duration
        ev.clip_dirty = False
        ev.clip_error = None
        # The clip changed: upload it again (same key, overwritten).
        ev.clip_remote_key = None
        ev.thumb_remote_key = None
        notify.enqueue(s, settings, ev, "event.clip")  # first clip only (once per channel)
        log.info("event %s: clip %s (%.0fs)", ev.id, clip_rel, duration)
    return True
