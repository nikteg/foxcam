from datetime import timedelta

import pytest
from media_streamer.db import session_scope
from media_streamer.models import Detection, Job, JobState, Segment, SegmentStatus, Worker, utcnow
from media_streamer.pipeline import queue

from .conftest import add_job, add_segment


@pytest.fixture
def two_jobs(settings):
    old = add_segment(settings, start=utcnow() - timedelta(minutes=5), with_file=False,
                      status=SegmentStatus.QUEUED, derived_path="derived/old.mp4")
    new = add_segment(settings, start=utcnow() - timedelta(minutes=1), with_file=False,
                      status=SegmentStatus.QUEUED, derived_path="derived/new.mp4")
    # Created in this order, so the second job is the newest.
    return add_job(old), add_job(new)


def test_lease_hands_out_newest_first_and_never_twice(settings, two_jobs):
    old_job, new_job = two_jobs
    a = queue.lease(settings, "w1", {"detector": "x"})
    b = queue.lease(settings, "w2")
    assert (a.id, b.id) == (new_job, old_job)
    assert a.lease_token != b.lease_token
    assert queue.lease(settings, "w3") is None
    with session_scope() as s:
        assert s.get(Worker, "w1").info == {"detector": "x"}
        assert s.get(Worker, "w1").current_job_id == new_job


def test_complete_stores_detections_and_marks_targets(settings, two_jobs):
    job = queue.lease(settings, "w1")
    derived = settings.data_dir / job.segment.derived_path
    derived.parent.mkdir(parents=True, exist_ok=True)
    derived.write_bytes(b"x")
    queue.complete(settings, job.id, job.lease_token, [
        queue.DetectionIn(offset_ms=500, label="Fox", confidence=0.9, bbox=[0.1, 0.1, 0.2, 0.2]),
        queue.DetectionIn(offset_ms=900, label="fox", confidence=0.3),  # below min_confidence
        queue.DetectionIn(offset_ms=900, label="cat", confidence=0.99),
    ], model="m1", processing_ms=1234)
    with session_scope() as s:
        j = s.get(Job, job.id)
        seg = s.get(Segment, j.segment_id)
        dets = s.query(Detection).order_by(Detection.id).all()
        assert j.state == JobState.DONE and j.model == "m1" and j.lease_token is None
        assert seg.status == SegmentStatus.DONE and seg.has_target and seg.max_confidence == 0.9
        assert [d.is_target for d in dets] == [True, False, False]
        assert dets[0].ts == seg.start_at + timedelta(milliseconds=500)
        assert s.get(Worker, "w1").jobs_done == 1
    assert not derived.exists()  # worker input removed once done


def test_wrong_or_reused_token_is_rejected(settings, two_jobs):
    job = queue.lease(settings, "w1")
    with pytest.raises(queue.LeaseError):
        queue.heartbeat(settings, job.id, "bogus")
    queue.complete(settings, job.id, job.lease_token, [], None, None)
    with pytest.raises(queue.LeaseError):
        queue.complete(settings, job.id, job.lease_token, [], None, None)


def test_heartbeat_extends_the_lease(settings, two_jobs):
    job = queue.lease(settings, "w1")
    with session_scope() as s:
        s.get(Job, job.id).lease_expires_at = utcnow() + timedelta(seconds=5)
    expires = queue.heartbeat(settings, job.id, job.lease_token)
    assert expires > utcnow() + timedelta(seconds=50)


def test_fail_retries_then_gives_up(settings, two_jobs):
    job = queue.lease(settings, "w1")
    queue.fail(settings, job.id, job.lease_token, "boom", retryable=True)
    with session_scope() as s:
        assert s.get(Job, job.id).state == JobState.FAILED
    again = queue.lease(settings, "w1")  # backoff is 0 in tests
    assert again.id == job.id and again.attempts == 2
    queue.fail(settings, job.id, again.lease_token, "boom again", retryable=True)
    with session_scope() as s:
        j = s.get(Job, job.id)
        assert j.state == JobState.DEAD  # max_attempts = 2
        assert s.get(Segment, j.segment_id).status == SegmentStatus.FAILED


def test_expired_leases_are_reclaimed(settings, two_jobs):
    job = queue.lease(settings, "w1")
    with session_scope() as s:
        s.get(Job, job.id).lease_expires_at = utcnow() - timedelta(seconds=1)
    assert queue.reap(settings) == 1
    with session_scope() as s:
        assert s.get(Job, job.id).state == JobState.FAILED
        assert s.get(Worker, "w1").current_job_id is None
    with pytest.raises(queue.LeaseError):
        queue.heartbeat(settings, job.id, job.lease_token)


def test_requeue_resets_a_dead_job(settings, two_jobs):
    job_id = two_jobs[0]
    derived = settings.data_dir / "derived" / "old.mp4"  # the worker copy still exists
    derived.parent.mkdir(parents=True, exist_ok=True)
    derived.write_bytes(b"x")
    with session_scope() as s:
        s.get(Job, job_id).state = JobState.DEAD
        s.get(Job, job_id).attempts = 2
    queue.requeue(settings, job_id)
    with session_scope() as s:
        j = s.get(Job, job_id)
        assert (j.state, j.attempts) == (JobState.PENDING, 0)


def test_requeue_without_worker_copy_analyses_the_recording_again(settings, two_jobs):
    job_id = two_jobs[0]  # its derived file was deleted after the jobs were done
    with session_scope() as s:
        job = s.get(Job, job_id)
        job.state, seg_id = JobState.DONE, job.segment_id
        job.segment.status = SegmentStatus.DONE
    queue.requeue(settings, job_id)
    with session_scope() as s:
        assert s.get(Job, job_id) is None  # replaced by preprocessing
        assert s.get(Segment, seg_id).status == SegmentStatus.NEW


def test_stats(settings, two_jobs):
    queue.lease(settings, "w1")
    st = queue.stats()
    assert st["counts"]["pending"] == 1 and st["counts"]["leased"] == 1
    assert st["oldest_pending_age_s"] is not None
