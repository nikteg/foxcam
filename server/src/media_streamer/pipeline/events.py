"""Group target detections (foxes) into events and cut a clip for each event.

Segments with a target detection are merged with any event on the same camera whose
detections are within ``merge_gap_seconds``. Once an event has been quiet for
``settle_seconds`` (and the post-roll footage exists) its clip is (re)built from the
original, unmasked recordings, and the underlying segments are pinned.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from .. import ffmpeg
from ..config import Settings
from ..db import session_scope
from ..models import Detection, Event, Segment, event_segments, utcnow

log = logging.getLogger(__name__)


def assign_events(settings: Settings) -> int:
    gap = timedelta(seconds=settings.events.merge_gap_seconds)
    changed = 0
    with session_scope() as s:
        in_event = select(event_segments.c.segment_id)
        segs = s.scalars(
            select(Segment).where(Segment.has_target.is_(True), Segment.id.not_in(in_event))
            .order_by(Segment.start_at)
        ).all()
        for seg in segs:
            dets = s.scalars(select(Detection).where(Detection.segment_id == seg.id,
                                                     Detection.is_target.is_(True))).all()
            if not dets:
                continue
            first = min(d.ts for d in dets)
            last = max(d.ts for d in dets)
            event = s.scalars(
                select(Event).where(Event.camera_id == seg.camera_id,
                                    Event.last_ts >= first - gap, Event.first_ts <= last + gap)
                .order_by(Event.first_ts).limit(1)
            ).first()
            best = max(dets, key=lambda d: d.confidence)
            if event is None:
                event = Event(camera_id=seg.camera_id, label=best.label, first_ts=first,
                              last_ts=last, max_confidence=best.confidence, detection_count=0,
                              best_detection_id=best.id)
                s.add(event)
            else:
                event.first_ts = min(event.first_ts, first)
                event.last_ts = max(event.last_ts, last)
                if best.confidence > event.max_confidence:
                    event.max_confidence = best.confidence
                    event.best_detection_id = best.id
                    event.label = best.label
            event.detection_count += len(dets)
            event.segments.append(seg)
            event.clip_dirty = True
            event.updated_at = utcnow()
            changed += 1
    return changed


def build_clips(settings: Settings) -> int:
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
        sources = [settings.recordings_dir / g.path for g in segs]
        ffmpeg.concat_clip(sources, settings.data_dir / clip_rel, offset, duration)

        best = s.get(Detection, ev.best_detection_id) if ev.best_detection_id else None
        thumb_rel = None
        if best is not None:
            thumb_rel = Path("clips") / ev.camera_id / f"event-{ev.id}.jpg"
            src_seg = s.get(Segment, best.segment_id)
            ffmpeg.thumbnail(settings.recordings_dir / src_seg.path,
                             settings.data_dir / thumb_rel, at_s=best.offset_ms / 1000,
                             width=640, box=best.bbox)

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
        log.info("event %s: clip %s (%.0fs)", ev.id, clip_rel, duration)
    return True
