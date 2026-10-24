# Architecture

```
            ┌────────────── recorder box (always on) ──────────────┐
 cameras ──►│ MediaMTX ──► segments on disk (fMP4, N s each)        │
 (RTSP or   │    │  ▲                                               │
 Tapo cloud)│    │  └── ingest: publishes tapo_cloud/file/test      │
            │    └──► live view (WebRTC / HLS) ──────────────────────┼──► browser
            │                                                        │
            │ server (FastAPI + SQLite)                              │
            │   scanner → preprocess → job queue → events → retention│
            │   JSON API + React UI ─────────────────────────────────┼──► browser
            └──────────────────────────▲─────────────────────────────┘
                                       │ HTTP: lease / input / heartbeat / result
                              workers (GPU PC, SaaS adapter, …)
```

## Processes

| Process | Command | Role |
|---|---|---|
| MediaMTX | `mediamtx mediamtx/mediamtx.yml` | Pulls RTSP cameras, accepts published streams, records segments, serves live view. Configured by the server through its API. |
| server | `media-streamer serve` | API, web UI and the background pipeline (single process, single SQLite writer). |
| ingest | `media-streamer ingest` | Publishes non-RTSP sources (Tapo cloud, file, test) into MediaMTX. Re-reads the camera list every 5 s. |
| worker | `media-worker` | Runs anywhere with HTTP access to the server. Leases jobs, runs a detector, posts results. |

## Pipeline (server background loops)

1. **Scanner** (every 5 s): registers finished recording files as `segment` rows. A file is finished
   when a newer file exists for the camera, or it has not changed for `segment_seconds + 30 s`.
   Scanning the filesystem means segments written while the server was down are picked up too.
2. **Preprocess**: per new segment
   - light tags: mean colour saturation (IR night vision is grayscale → `is_night`), brightness,
     and sun elevation when a location is configured;
   - a thumbnail;
   - private camera → stop (`status=private`, no job);
   - otherwise a **censored derivative**: censor rectangles burned in, sampled to `sample_fps`,
     downscaled to `max_width`, no audio. This is the only thing a worker can download.
   - a `job` row.
3. **Queue**: workers lease jobs (see [WORKER_PROTOCOL.md](WORKER_PROTOCOL.md)). Expired leases are
   reclaimed every 15 s; failures retry with backoff up to `max_attempts`, then the job is `dead`.
4. **Events** (every 10 s): segments with a target detection (`detection.target_labels` ≥
   `min_confidence`) are merged into events when within `merge_gap_seconds`. After
   `settle_seconds` (and once the post-roll footage exists) a clip is cut from the **original**
   recordings with pre/post roll, plus a thumbnail with the box drawn. Event segments are pinned.
5. **Retention** (every 10 min): see below.
6. **Upload** (every 30 s, only with `storage.s3.enabled`): see [Object storage](#object-storage-s3).

## Data model (SQLite)

| Table | Holds |
|---|---|
| `segment` | One recording file: camera, start (UTC), duration, status, light tags, mask version used, fox flag, pinned, `deleted_at`. |
| `job` | Work item per segment: state (`pending/leased/done/failed/dead`), lease token and expiry, attempts, model, timing. |
| `detection` | Worker result: offset, absolute time, label, confidence, normalized box, model, worker, feedback. |
| `event` | Merged fox sighting: time range, best detection, clip and thumbnail paths. |
| `worker` | Last-seen info per worker id (shown in the queue tab). |
| `mask_version` | Immutable censor-rectangle sets; segments reference the version they were censored with. |
| `camera_state` | Per-camera private flag and current mask version. |
| `managed_camera` | Cameras added in the web UI (config.yaml cameras are not stored). |

All timestamps are UTC. MediaMTX must run with `TZ=UTC` because recording file names are parsed
as UTC (`scripts/dev.sh` and `docker-compose.yml` do this).

## Retention

Runs every 10 minutes (`pipeline/retention.py`):

- events older than `retention.events_days` (0 = forever) are deleted, and their segments unpinned;
- unpinned segments older than `segments_days` are deleted, **except** segments still waiting for a
  worker, which are kept up to `unprocessed_max_days` (so an offline GPU box does not lose footage);
- if `max_disk_gb` is set, the oldest unpinned segments are deleted until under the cap;
- the censored worker input is deleted as soon as its job is done (`delete_derived_after_done`).

Deleted segments keep their database row (`deleted_at`), so detections stay browsable.

## Object storage (S3)

Optional copy in any S3-compatible bucket (AWS S3, Cloudflare R2, Backblaze B2, MinIO, Garage,
SeaweedFS, Hetzner, Wasabi…), configured in `storage.s3` (`pipeline/uploader.py`, `storage.py`):

- **Clips** (default on): once an event clip is built it is uploaded with its thumbnail as
  `<prefix>clips/…`. If the event grows and the clip is rebuilt, it is uploaded again.
  `delete_local_clips: true` frees local disk after the upload.
- **Recordings** (`recordings: true`): every finished segment is archived as
  `<prefix>recordings/<camera>/<file>.mp4`. Local retention still applies; deleted segments
  stay downloadable from the bucket. `recordings_days` removes archived recordings from the
  bucket again (or use a bucket lifecycle rule).
- **Serving**: the API serves the local file while it exists, otherwise answers with a `307`
  redirect to a pre-signed URL valid `presign_seconds`. The bucket can (and should) stay private.
- **Retention**: expired events delete their remote clip and thumbnail too.
- Upload errors are retried every round and shown under Settings → Storage, which also has a
  *Test connection* button (write, read and delete a small object).
- The remote keys are stored in `segment.remote_key`, `event.clip_remote_key` and
  `event.thumb_remote_key` (added to existing databases automatically at startup).

## Privacy model

- Workers only receive the censored, downsampled derivative. Masks are burned into pixels on the
  server, so a SaaS detector never sees censored areas.
- Private cameras never produce jobs.
- The web UI shows original footage (live view, recordings, clips). Until auth exists, do not
  expose the UI beyond your own network (see [AUTH.md](AUTH.md)).
- Camera passwords (RTSP URLs) are stored on the server and redacted in API responses. The Tapo
  cloud session token is stored in `data/tapo-session.json` (mode 0600); the password is never stored.

## Code map

```
server/src/media_streamer/
  config.py          settings (config.yaml + ${ENV} expansion), camera list
  camera_store.py    cameras added from the UI
  mediamtx.py        MediaMTX API client (path sync, live status)
  ffmpeg.py          probe, censored derivative, light stats, thumbnails, clips
  pipeline/          scanner, preprocess, queue, events, retention, uploader, runner
  storage.py         S3-compatible object storage client (boto3)
  ingest/            publishers for tapo_cloud / file / test sources
  tapo_account.py    Tapo cloud sign-in (MFA) and device discovery for the UI
  web/               FastAPI app, worker API, public API, camera API
worker/src/media_worker/
  cli.py             lease loop; `detect-file` for offline tests (local.py)
  detectors/         dummy, ultralytics (YOLO-World / YOLO), speciesnet
web/src/             React + Fluent UI app (pages/, components/, i18n/)
```
