"""Analyse a recording again: detections replaced, events kept consistent."""

from __future__ import annotations

from datetime import timedelta

import pytest
from foxcam.db import session_scope
from foxcam.models import Detection, Event, Job, JobState, Segment, SegmentStatus, utcnow
from foxcam.pipeline import compaction, events, preprocess, reanalyse

from .conftest import add_detection, add_job, add_segment, needs_ffmpeg


def _done_segment(settings, start, conf=0.9):
    seg_id = add_segment(settings, start=start, with_file=False)
    job_id = add_job(seg_id, state=JobState.DONE)
    det_id = add_detection(seg_id, job_id, 500, confidence=conf)
    return seg_id, det_id


def test_detections_replaced_and_lonely_event_removed(settings):
    seg_id, _ = _done_segment(settings, utcnow() - timedelta(minutes=10))
    events.assign_events(settings)
    with session_scope() as s:
        event_id = s.query(Event.id).scalar()
        s.get(Segment, seg_id).pinned = True
    assert reanalyse.reanalyse_segment(settings, seg_id) == 1
    with session_scope() as s:
        seg = s.get(Segment, seg_id)
        assert seg.status == SegmentStatus.NEW and not seg.pinned and not seg.has_target
        assert s.query(Detection).count() == 0 and s.query(Job).count() == 0
        assert s.get(Event, event_id) is None


def test_event_spanning_two_recordings_keeps_the_other(settings):
    start = utcnow() - timedelta(minutes=10)
    a, _ = _done_segment(settings, start, conf=0.95)
    b, det_b = _done_segment(settings, start + timedelta(seconds=2), conf=0.7)
    events.assign_events(settings)
    with session_scope() as s:
        [ev] = s.query(Event).all()
        assert ev.detection_count == 2
    reanalyse.reanalyse_segment(settings, a)
    with session_scope() as s:
        ev = s.get(Event, ev.id)
        assert ev.detection_count == 1 and ev.best_detection_id == det_b
        assert [g.id for g in ev.segments] == [b] and ev.clip_dirty


def test_refused_while_a_worker_has_it(settings):
    seg_id = add_segment(settings, with_file=False)
    add_job(seg_id, state=JobState.LEASED)
    with pytest.raises(reanalyse.ReanalyseError):
        reanalyse.reanalyse_segment(settings, seg_id)


@needs_ffmpeg
def test_compacted_recording_is_preprocessed_from_its_hourly_file(settings):
    start = (utcnow() - timedelta(hours=3)).replace(minute=5, second=0, microsecond=0)
    ids = [add_segment(settings, start=start + timedelta(seconds=2 * i)) for i in range(3)]
    compaction.compact_once(settings)
    compaction.delete_replaced(settings, now=utcnow() + timedelta(minutes=5))
    reanalyse.reanalyse_segment(settings, ids[1])
    preprocess.preprocess_segment(settings, ids[1])
    with session_scope() as s:
        seg = s.get(Segment, ids[1])
        assert seg.status == SegmentStatus.QUEUED, seg.error
        assert seg.derived_path and (settings.data_dir / seg.derived_path).exists()
        assert s.query(Job).filter(Job.segment_id == ids[1]).count() == 1
