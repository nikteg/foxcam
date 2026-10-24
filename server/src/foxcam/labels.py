"""Label suggestions for the label pickers (profile targets and aliases, notification
filters): what the connected detectors can report, and what has been seen.

Sources, by kind of detector:

- closed-set models (COCO or a fine-tuned YOLO, SpeciesNet's taxonomy): each worker
  reports its vocabulary on lease (``worker.vocabulary``); a target outside every
  vocabulary of the workers that may run a profile can never fire;
- open-vocabulary models (YOLO-World / YOLOE, tag ``open-vocabulary``): any noun works,
  best ones from their training vocabularies (LVIS and Objects365, bundled, plus a short
  hand-picked wildlife list: fox, badger and hedgehog are in neither dataset);
- labels seen in detections, with how often and how confidently.

Open-vocabulary prompts are sensitive to synonyms: prompting "person, man, woman" scored
a clearly visible person at 0.43, "person" alone at 0.89 (docs/MODELS.md). Synonym groups
let the editor suggest one target per thing, and aliases for the rest.
"""

from __future__ import annotations

import json
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import case, func, select

from .db import session_scope
from .models import Detection, Worker, utcnow

OPEN_VOCABULARY_TAG = "open-vocabulary"
ONLINE_SECONDS = 120  # like the worker list on the queue page
DATA = Path(__file__).parent / "data" / "open_vocabulary.json"

# Words that name the same thing for a detector. Prompting several of them at once splits
# an open-vocabulary model's confidence between them; pick one, map the rest as aliases.
SYNONYM_GROUPS: list[list[str]] = [
    ["person", "people", "human", "man", "woman", "child", "kid", "boy", "girl",
     "pedestrian", "walker"],
    ["fox", "red fox", "vulpes vulpes"],
    ["car", "automobile", "vehicle", "sedan", "suv"],
    ["truck", "lorry", "pickup truck"],
    ["dog", "puppy", "domestic dog", "canis familiaris"],
    ["cat", "kitten", "domestic cat", "house cat", "felis catus"],
    ["bicycle", "bike", "cycle"],
    ["motorcycle", "motorbike", "scooter"],
    ["deer", "roe deer", "red deer", "fallow deer"],
    ["bird", "songbird"],
    ["hedgehog", "european hedgehog"],
    ["badger", "european badger", "eurasian badger"],
]


@lru_cache(maxsize=1)
def open_vocabulary() -> dict[str, list[str]]:
    data = json.loads(DATA.read_text())
    return {"wildlife": data["wildlife"], "lvis": data["lvis"],
            "objects365": data["objects365"]}


def seen_labels(camera_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Labels in the detection table: count, best confidence, last time, and how many were
    targets."""
    q = select(Detection.label, func.count(), func.max(Detection.confidence),
               func.max(Detection.ts),
               # case(): summing the Boolean column directly comes back as True/False.
               # Marked wrong on the Detections page = not a target.
               func.sum(case((Detection.is_target.is_(True) & Detection.feedback.is_not(False),
                              1), else_=0))
               ).group_by(Detection.label)
    if camera_ids is not None:
        q = q.where(Detection.camera_id.in_(camera_ids))
    with session_scope() as s:
        rows = s.execute(q).all()
    out = [{"label": label, "count": n, "max_confidence": round(conf or 0, 3),
            "last_seen": ts.isoformat(timespec="seconds") + "Z" if ts else None,
            "targets": int(targets or 0)} for label, n, conf, ts, targets in rows]
    return sorted(out, key=lambda r: -r["count"])


def workers() -> list[dict[str, Any]]:
    """Every known worker with its detector, tags and (for closed-set models) vocabulary."""
    now = utcnow()
    with session_scope() as s:
        rows = s.scalars(select(Worker).order_by(Worker.last_seen.desc())).all()
        out = []
        for w in rows:
            tags = list(w.tags or [])
            out.append({
                "id": w.id, "detector": (w.info or {}).get("detector"), "tags": tags,
                "online": (now - w.last_seen) < timedelta(seconds=ONLINE_SECONDS),
                "last_seen": w.last_seen.isoformat(timespec="seconds") + "Z",
                # Open vocabulary: anything goes, the list is a suggestion only.
                "open_vocabulary": OPEN_VOCABULARY_TAG in tags,
                "vocabulary": list(w.vocabulary) if w.vocabulary is not None else None,
            })
        return out


def suggestions() -> dict[str, Any]:
    """Everything the label pickers need (GET /api/labels)."""
    return {
        "workers": workers(),
        "open_vocabulary": open_vocabulary(),
        "seen": seen_labels(),
        "synonym_groups": SYNONYM_GROUPS,
    }
