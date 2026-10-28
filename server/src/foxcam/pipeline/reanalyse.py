"""Run detection on a recording again, from scratch.

The masked copy a worker analysed is deleted once its jobs are done, so a retry starts
over: the segment goes back to ``new``, preprocessing builds a fresh copy (with the
camera's current censor areas and detection profiles) and queues new jobs. The old
detections are replaced; events that relied on them are updated, or removed when nothing
is left (new detections form events again as usual).
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, select

from ..config import Settings
from ..db import session_scope
from ..models import Detection, Job, JobState, Segment, SegmentStatus
from .events import refresh_event

log = logging.getLogger(__name__)


class ReanalyseError(ValueError):
    pass


def reanalyse_segment(settings: Settings, segment_id: int) -> int:
    """Queue the recording for detection again. Returns how many detections were removed."""
    with session_scope() as s:
        seg = s.get(Segment, segment_id)
        if seg is None:
            raise ReanalyseError("unknown recording")
        if seg.deleted_at is not None:
            raise ReanalyseError("the recording was deleted by retention")
        if seg.status == SegmentStatus.NEW:
            return 0  # already on its way
        busy = s.scalar(select(Job.id).where(Job.segment_id == segment_id,
                                             Job.state == JobState.LEASED).limit(1))
        if busy is not None:
            raise ReanalyseError("a worker is analysing this recording right now")
        dets = s.scalars(select(Detection).where(Detection.segment_id == segment_id)).all()
        events = {d.event_id for d in dets if d.event_id is not None}
        for d in dets:
            s.delete(d)
        s.execute(delete(Job).where(Job.segment_id == segment_id))
        s.flush()
        for event_id in events:
            refresh_event(settings, s, event_id)
        if seg.derived_path:
            (settings.data_dir / seg.derived_path).unlink(missing_ok=True)
        seg.derived_path = None
        seg.status = SegmentStatus.NEW
        seg.reanalysed = True
        seg.error = None
        seg.has_target = False
        seg.max_confidence = None
        log.info("recording %s queued for detection again (%d detection(s) removed)",
                 segment_id, len(dets))
        return len(dets)
