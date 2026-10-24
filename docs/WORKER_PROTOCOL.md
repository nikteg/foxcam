# Worker protocol

Workers pull work over plain HTTP + JSON. Anything that can speak HTTP can be a worker: the
bundled Python worker, a script on a GPU box, or an adapter that forwards frames to a SaaS
detection API. The server never calls workers, so they can sit behind NAT and come and go.

All timestamps are UTC (ISO 8601). Interactive API docs: `http://<server>:8000/docs`.

## Authentication

If `worker_token` is set in `config.yaml` (or `MEDIA_WORKER_TOKEN`), every worker request must send

```
Authorization: Bearer <token>
```

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
{"worker_id": "gpu-box", "info": {"detector": "speciesnet", "gpu": "RTX 4090"}}
```

`info` is free-form and shown in the web UI's worker queue tab.

```json
{
  "job_id": 42,
  "lease_token": "…",
  "lease_expires_at": "2026-10-24T10:05:00",
  "attempt": 1,
  "input_url": "/api/worker/jobs/42/input",
  "input_fps": 2.0,
  "segment": {"id": 17, "camera_id": "garden", "start_at": "2026-10-24T10:00:00",
              "duration_s": 60.0, "width": 1920, "height": 1080, "is_night": true},
  "hints": {"target_labels": ["fox", "red fox", "vulpes vulpes"], "min_confidence": 0.5,
            "country": "SWE", "latitude": null, "longitude": null}
}
```

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
- `label`: free text. The server marks a detection as a *target* when the label is in
  `detection.target_labels` (case-insensitive) and `confidence ≥ min_confidence`.
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
