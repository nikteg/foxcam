# Worker protocol

Workers pull work over plain HTTP + JSON. Anything that can speak HTTP can be a worker: the
bundled Python worker, a script on a GPU box, or an adapter that forwards frames to a SaaS
detection API. The server never calls workers, so they can sit behind NAT and come and go.

All timestamps are UTC (ISO 8601). Interactive API docs: `http://<server>:8000/docs`.

## Authentication

Every worker request must send a token: one per worker, created in the web UI under
**Worker queue → Workers → Add worker** or with `media-streamer worker-token <name>`, or the shared
`worker_token` from `config.yaml` / `MEDIA_WORKER_TOKEN`:

```
Authorization: Bearer <token>
```

Until a token exists no worker can connect. Only `open_worker_access: true` in the config
lets workers in without a token (trusted test networks). `GET /api/worker/whoami` checks credentials without side
effects: `200 {"authenticated": true, "token_name": "gpu-box"}` or `401`.

## Flow

```
POST /api/worker/lease ──► 204 (no work, poll again later)
                        └► 200 job + lease_token
GET  /api/worker/jobs/{id}/input          (X-Lease-Token header)  → censored MP4
POST /api/worker/jobs/{id}/heartbeat      every ~30 s while working
POST /api/worker/jobs/{id}/result         done
POST /api/worker/jobs/{id}/fail           gave up (retryable or not)
```

A lease lasts `queue.lease_seconds` (default 300 s) and is extended by each heartbeat. If a
worker disappears, the lease expires and the job goes back to the queue. After
`queue.max_attempts` failures the job is marked `dead`. Any request with a stale or wrong
token gets **409**: drop the job and lease a new one.

### Lease

```http
POST /api/worker/lease
{"worker_id": "gpu-box", "info": {"detector": "speciesnet", "gpu": "RTX 4090"},
 "tags": ["speciesnet", "gpu"], "labels": null}
```

`labels` (optional): if the worker can only detect certain labels (e.g. a fixed set of
classes), list them; it then only gets jobs of profiles looking for at least one of them.
`null` = it can look for anything (open-vocabulary models are prompted with the job's
`hints.target_labels`).

`vocabulary` (optional): everything the detector can report, e.g. a COCO model's 80
classes or SpeciesNet's ~3,500 taxonomy names. Not a filter: the server stores it per
worker and suggests these labels in the profile and notification editors (`GET
/api/labels`), and warns about targets no eligible worker can report. Send it with the
first lease and then every few minutes (the reference worker: every 10 minutes), not with
every lease; leases without it keep the stored list. Workers that send only `labels` have
those stored as their vocabulary. Open-vocabulary workers send neither: they can report
anything (tag `open-vocabulary`).

`info` is free-form and shown in the web UI's worker queue tab. `tags` are capabilities:
you only get jobs of [detection profiles](ARCHITECTURE.md#detection-profiles) whose
`requires` tags you all have (tags on the access token are added by the server). Each
segment has one job per profile, so the same segment may come back for another profile.

```json
{
  "job_id": 42,
  "lease_token": "…",
  "lease_expires_at": "2026-10-24T10:05:00",
  "attempt": 1,
  "input_url": "/api/worker/jobs/42/input",
  "input_fps": 2.0,
  "sample_fps": 2.0,
  "profile": {"id": "default", "name": "Fox", "targets": ["fox"],
              "min_confidence": 0.5},
  "segment": {"id": 17, "camera_id": "garden", "start_at": "2026-10-24T10:00:00",
              "duration_s": 60.0, "width": 1920, "height": 1080, "is_night": true},
  "hints": {"target_labels": ["fox"], "min_confidence": 0.5,
            "profile": "default", "country": "SWE", "latitude": null, "longitude": null}
}
```

`sample_fps` is the frame rate the profile wants analysed (≤ `input_fps`; the input is shared
by all profiles of the camera, so subsample to it). `hints.target_labels` are what the profile
looks for: open-vocabulary detectors should prompt with them. Report every label you see; the
server applies the profile's aliases and threshold to decide what is a target.

Newest segments are handed out first, so fresh footage is processed first after an outage.

### Input

`GET input_url` with header `X-Lease-Token: <lease_token>` (or `?lease_token=`). Returns an H.264
MP4 with censor areas blacked out, sampled to `input_fps` frames per second, at most
`processing.max_width` pixels wide and without audio. Frame `i` is at `i / input_fps` seconds.

### Result

```http
POST /api/worker/jobs/42/result
{
  "lease_token": "…",
  "model": "speciesnet:4.0.1a",
  "processing_ms": 5300,
  "detections": [
    {"offset_ms": 12500, "label": "red fox", "confidence": 0.91,
     "bbox": [0.41, 0.52, 0.12, 0.09], "extra": {"detector_conf": 0.97}}
  ]
}
```

- `offset_ms`: from the start of the segment.
- `bbox`: `[x, y, width, height]`, normalized 0–1, top-left origin (optional).
- `label`: free text. The server marks a detection as a *target* when the label (after the
  profile's `aliases`, e.g. `red fox` → `fox`) is in its targets (case-insensitive) and
  `confidence ≥ min_confidence`.
- Report every object you like (people, cats, …); only targets create fox events.
- An empty `detections` list is a valid result ("nothing seen").

### Fail

```http
POST /api/worker/jobs/42/fail
{"lease_token": "…", "error": "CUDA out of memory", "retryable": true}
```

`retryable: false` marks the job dead immediately (e.g. unreadable input).

## Writing a detector for the bundled worker

Implement `media_worker.detectors.base.Detector`:

```python
class MyDetector(Detector):
    name = "my-saas:v1"

    def detect(self, frames: list[Frame], hints: dict) -> list[Detection]:
        out = []
        for f in frames:                      # f.path = JPEG, f.offset_ms
            for obj in call_my_api(f.path):   # e.g. a cloud vision API
                out.append(Detection(f.offset_ms, obj.label, obj.score, obj.box_xywh_norm))
        return out
```

Register it in `detectors/__init__.py` (`create_detector`) and test it offline first:

```sh
uv run media-worker detect-file samples/fox-garden.mp4 --detector my-saas --expect fox --out out/test
```
