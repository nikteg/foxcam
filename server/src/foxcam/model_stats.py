"""Compare models: per profile, detector and model (the worker's model string) the
detections, targets, human feedback (precision), speed, job outcomes and how often other
detectors of the same profile agreed on the same recordings (``GET /api/models/stats``)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select

from .config import Settings
from .db import session_scope
from .models import Detection, Job, JobState

Key = tuple[str, str, str]  # profile, detector, model


def _key(profile_id: str | None, detector_id: str | None, model: str | None) -> Key:
    return (profile_id or "default", detector_id or "default", model or "")


def model_stats(settings: Settings, since: datetime | None = None) -> list[dict[str, Any]]:
    rows: dict[Key, dict[str, Any]] = defaultdict(lambda: {
        "detections": 0, "targets": 0, "feedback_correct": 0, "feedback_wrong": 0,
        "jobs_done": 0, "jobs_dead": 0, "jobs_skipped": 0, "avg_processing_ms": None,
        "segments_with_target": 0, "segments_agreed": 0})
    with session_scope() as s:
        jq = select(Job.profile_id, Job.detector_id, Job.model, Job.state, func.count(),
                    func.avg(Job.processing_ms)).group_by(
            Job.profile_id, Job.detector_id, Job.model, Job.state)
        if since:
            jq = jq.where(Job.finished_at >= since)
        for pid, did, model, state, n, avg_ms in s.execute(jq):
            r = rows[_key(pid, did, model)]
            if state == JobState.DONE:
                r["jobs_done"] += n
                r["avg_processing_ms"] = round(avg_ms) if avg_ms is not None else None
            elif state == JobState.DEAD:
                r["jobs_dead"] += n
            elif state == JobState.SKIPPED:
                r["jobs_skipped"] += n

        dq = select(
            Detection.profile_id, Detection.detector_id, Detection.model, func.count(),
            func.sum(case((Detection.is_target.is_(True), 1), else_=0)),
            func.sum(case((Detection.feedback.is_(True), 1), else_=0)),
            func.sum(case((Detection.feedback.is_(False), 1), else_=0)),
        ).group_by(Detection.profile_id, Detection.detector_id, Detection.model)
        if since:
            dq = dq.where(Detection.ts >= since)
        for pid, did, model, n, targets, good, bad in s.execute(dq):
            r = rows[_key(pid, did, model)]
            r.update(detections=n, targets=int(targets or 0), feedback_correct=int(good or 0),
                     feedback_wrong=int(bad or 0))

        # Agreement: of the recordings where this detector found a target and another
        # detector of the profile also looked, how often that one found one too.
        tq = select(Detection.segment_id, Detection.profile_id, Detection.detector_id,
                    Detection.model).where(Detection.is_target.is_(True)).distinct()
        if since:
            tq = tq.where(Detection.ts >= since)
        hits: dict[tuple[int, str], set[str]] = defaultdict(set)
        found: list[tuple[int, str, Key]] = []
        for seg, pid, did, model in s.execute(tq):
            key = _key(pid, did, model)
            hits[(seg, key[0])].add(key[1])
            found.append((seg, key[0], key))
        looked: dict[tuple[int, str], set[str]] = defaultdict(set)
        if found:
            segs = {seg for seg, _, _ in found}
            for seg, pid, did in s.execute(select(Job.segment_id, Job.profile_id,
                                                  Job.detector_id).where(
                    Job.segment_id.in_(segs), Job.state == JobState.DONE)):
                looked[(seg, pid or "default")].add(did or "default")
        seen: set[tuple[int, Key]] = set()
        for seg, pid, key in found:
            if (seg, key) in seen:
                continue
            seen.add((seg, key))
            others = looked[(seg, pid)] - {key[1]}
            if not others:
                continue  # nobody else looked: nothing to agree or disagree with
            r = rows[key]
            r["segments_with_target"] += 1
            if hits[(seg, pid)] - {key[1]}:
                r["segments_agreed"] += 1

    # Jobs that never finished (gave up, skipped) have no model: count them with the
    # detector's model when it ran a single one.
    for key in [k for k in rows if not k[2]]:
        ran = [k for k in rows if k[:2] == key[:2] and k[2]]
        if len(ran) == 1:
            r, into = rows.pop(key), rows[ran[0]]
            for field in ("jobs_dead", "jobs_skipped", "detections", "targets",
                          "feedback_correct", "feedback_wrong", "segments_with_target",
                          "segments_agreed"):
                into[field] += r[field]

    out = []
    for (pid, did, model), r in rows.items():
        profile = settings.profile(pid)
        det = profile.detector(did) if profile else None
        rated = r["feedback_correct"] + r["feedback_wrong"]
        out.append({
            "profile_id": pid, "profile_name": profile.display_name if profile else pid,
            "detector_id": did, "detector_name": det.display_name if det else did,
            "model": model or None, **r,
            "precision": round(r["feedback_correct"] / rated, 3) if rated else None,
            "agreement_rate": round(r["segments_agreed"] / r["segments_with_target"], 3)
            if r["segments_with_target"] else None,
        })
    return sorted(out, key=lambda r: (r["profile_id"], r["detector_id"], r["model"] or ""))
