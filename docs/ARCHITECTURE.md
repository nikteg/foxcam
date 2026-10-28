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
| MediaMTX | `foxcam mediamtx mediamtx/mediamtx.yml` (starts `mediamtx` with the internal login, see docs/AUTH.md) | Pulls RTSP cameras, accepts published streams, records segments, serves live view. Configured by the server through its API, including an on-demand `<camera>/low` path per camera (`foxcam transcode` to ~480p while someone watches it in low-data mode). |
| server | `foxcam serve` | API, web UI and the background pipeline (single process, single SQLite writer). |
| ingest | `foxcam ingest` | Publishes non-RTSP sources (Tapo cloud, file, test) into MediaMTX. Re-reads the camera list every 5 s. Keeps each Tapo cloud camera's name in the Tapo app (`device_name`, shown as "Name in Tapo") up to date, as does listing the account's cameras; a new name alone does not restart the relay. |
| compaction | `foxcam compact` | Joins finished segments into hourly files, re-encoding them when the camera's recording profile says so ([below](#archiving-compaction-recording-profiles)). |
| worker | `foxcam-worker` | Runs anywhere with HTTP access to the server. Leases jobs, runs a detector, posts results. |

## Pipeline (server background loops)

1. **Scanner** (every 5 s): registers finished recording files as `segment` rows. A file is finished
   when a newer file exists for the camera, or it has not changed for `segment_seconds + 30 s`.
   Scanning the filesystem means segments written while the server was down are picked up too.
2. **Preprocess**: per new segment
   - light tags: mean colour saturation (IR night vision is grayscale → `is_night`), brightness,
     and sun elevation when a location is configured;
   - a thumbnail;
   - private camera, or no [detection profile](#detection-profiles) → stop (no job);
   - otherwise a **censored derivative**: censor rectangles burned in, sampled to the highest
     frame rate any of the camera's profiles (and their detectors) need (≤ `sample_fps`),
     downscaled to `max_width`, no audio. This is the only thing a worker can download.
   - **motion** in that derivative ([below](#motion-detection)): how much of the picture
     moved, and where, stored on the segment;
   - one `job` row per profile and detector. Profiles with `skip_without_motion` get theirs
     already `skipped` ("no motion") when nothing moved. The segment is `done` once all its
     jobs are done, skipped (or dead).
   Every detection a worker reports is stored, except non-matches below
   `processing.keep_non_targets_from` (default 0.25; Settings → Detection, "Store non-matches
   from"). That setting only decides storage. What is a match (events, "with a match", pinned
   footage) is decided by the match threshold (below), and a worker reports far more
   (YOLO-World from 15 %). Retention also drops stored ones below it, unless they were rated.
3. **Queue**: workers lease jobs (see [WORKER_PROTOCOL.md](WORKER_PROTOCOL.md)). Expired leases are
   reclaimed every 15 s; failures retry with backoff up to `max_attempts`, then the job is `dead`.
4. **Events** (every 10 s): segments with a match (a detection of a target label at or above
   the match threshold) are merged into events when within `merge_gap_seconds`. After
   `settle_seconds` (and once the post-roll footage exists) a clip is cut from the **original**
   recordings with pre/post roll, plus a thumbnail of the best detection's frame (its box,
   `thumb_box`, is drawn over it by the web app). Event segments are pinned.
   A gap between two recordings (the recorder stopped and started again) stays a gap in the
   clip, the picture holding there, so clip time is clock time; the player marks it as missing
   footage (`footage.py`), as it does skips inside a recording (below).
   Three kinds of target detection join no event:
   - **marked wrong** (❌ on the Detections page): a wrong detection is no match anywhere,
     not in counts, "with a match", the Status page, notifications or archives. An event
     counts as wrong once one of its detections is marked wrong and none correct;
   - **standing still**: a target whose box has had no motion under it for the profile's
     `still_minutes` (default 10; 0 = it must be moving right now; off) is marked `still`
     and is no match (`pipeline/still.py`, with [motion detection](#motion-detection)): a
     parked car neither keeps an event open forever nor pins its footage, and a stump
     never makes one. It counts again once it moves; its arrival counts. The Detections
     page hides these unless *Show standing still* is ticked.
   - **below the sun limit**: a camera's `sun_limit` (Cameras → Settings → Detection →
     Sun limit, degrees of sun elevation; off by default) marks targets seen while the sun
     is lower `below_sun_limit`, and they are no match (`pipeline/daylight.py`): a window
     reflecting the lit room after dark. The sun is computed for the camera's own
     latitude/longitude, else `location` (Settings → General). Changing the limit or the
     location marks the camera's detections again at once: those now below leave their
     events (an event left empty goes), those above form events again, quietly.
5. **Retention** (every 10 min): see below.
6. **Upload** (every 30 s, only with `storage.s3.enabled`): see [Object storage](#object-storage-s3).
7. **Notify** (every 5 s): new events and finished clips are queued per matching channel in
   the `notification` outbox and sent to ntfy / webhooks / Apprise, with retries
   ([NOTIFICATIONS.md](NOTIFICATIONS.md)).

## Detection profiles

A profile (`profiles.py`) says **what** to look for and **which models** look for it:

| Field | Meaning |
|---|---|
| `targets` | labels that create events (`fox`, or `car, person`) |
| `aliases` | map model labels to yours (`red fox: fox`), so different models agree |
| `min_confidence` | the match threshold ("Match from" in the UI): a detection of a target label at or above it is a match, and matches make events, notifications and pinned footage |
| `target_min_confidence` | a target's own match threshold, e.g. `{person: 0.8}` where false alarms are common; wins over the detector's and the profile's |
| `requires` | worker tags every detector of the profile needs |
| `detectors` | the models that look (below); none = one detector `default` needing just `requires` |
| `fps` | frames per second to analyse (cheaper profiles for slow-moving things) |
| `still_minutes` | a target with no motion under its box this long stops counting until it moves (default 10; 0 = must be moving now; empty = off) |
| `skip_without_motion` | recordings where nothing moved get no jobs of this profile (default off) |
| `default` | runs on cameras without their own selection |
| `events_days` | per-profile event retention |

Each **detector** has an `id` (unique in the profile), an optional `name`, its own
`requires` tags (on top of the profile's), and optional `min_confidence` and `fps` that
override the profile's. A detector's `min_confidence` is the same match threshold, used for
that detector's detections instead of the profile's (`ProfileConfig.min_confidence_for`):
models score differently, YOLO-World usually lower than SpeciesNet. Every segment gets one job per profile and detector, so e.g. YOLO-World
and SpeciesNet both look at each recording of a `wildlife` profile; their detections merge
into the same events (per camera, profile and label).

```yaml
profiles:
  - id: wildlife
    targets: [fox, badger]
    aliases: { red fox: fox }
    detectors:
      - { id: yolo, name: YOLO-World, requires: [open-vocabulary], min_confidence: 0.3 }
      - { id: speciesnet, requires: [speciesnet] }
```

**Cascades.** A detector with `run: on_hit` and `after: <detector id>` only runs on segments
where that detector found a target, e.g. a fast first pass on everything and SpeciesNet to
look closer:

```yaml
    detectors:
      - { id: yolo, requires: [open-vocabulary], min_confidence: 0.3 }
      - { id: speciesnet, requires: [speciesnet], run: on_hit, after: yolo, confirm: true }
```

Preprocessing only queues the `always` detectors. When a job's result contains a target,
the jobs of the detectors that follow it are created in the same transaction (with a higher
priority, so the confirmation runs soon) and before the segment is settled, so a segment is
never finished while a follow-up could still come. When it found nothing, or gave up, each
follow-up is stored as a job in state `skipped` with the reason ("skipped – no hit from
yolo"), which the recording's run list shows; chains skip all the way down. With
`confirm: true` the profile's events only count once that detector agrees: until then the
event is marked unconfirmed and sends no notifications, and a segment only gets its target
flag from the confirming detector's detections. Analysing a recording again starts the
cascade over. `after` must name another detector of the profile, chains may not go round in
circles, and at least one detector must run always.

Profiles come from `config.yaml` or the web UI (Cameras → Detection profiles). A camera
runs its UI selection, else its `profiles:` in config, else all `default` profiles; an empty
selection means "record only". With no profiles at all, cameras only record until one is
added. A `detection:` section in `config.yaml` (and no `profiles:`) gives a quick built-in
`default` profile instead.

**Routing by tags, not by machine.** Workers send tags with each lease: the detector adds
its own (`speciesnet`, `ultralytics`, `open-vocabulary`, `dummy`), `--tags` adds more, and
the access token can carry tags set in the UI. A job is only handed to a worker that has all
of its detector's tags (the profile's `requires` plus the detector's own), so a detector
can be pinned to one machine by requiring a tag only that machine has, while several equal
machines share the load. Profiles that no online worker can run are flagged in the UI.
Removing or disabling a profile, or removing a detector, cancels its waiting jobs.

**Detecting anything.** Open-vocabulary workers (YOLO-World / YOLOE) prompt the
model with each job's profile targets, so a new profile (`badger`, `deer`, `car`) needs no
new model. Closed-set models (SpeciesNet, fine-tuned YOLO) report what they know; the
profile's targets and aliases pick the relevant labels.

**Events** are per camera, profile and label: a fox and a car seen at the same time are two
events with two clips. Detections of a profile's different detectors join the same event;
the event records which detectors saw it (`detectors`) and how many (`agreement`), the
Events page can filter on "seen by ≥ N models", and notification channels can wait for
`min_agreement` models.

## Motion detection

`pipeline/motion.py`, run in preprocessing on the censored derivative (so censor areas never
count). Its frames are decoded once more as 96×54 greyscale: the downscale averages away
most sensor noise, also the grain of IR night pictures. Each frame is compared with a
running average of the ones before. A pixel counts when it differs by more than the
sensitivity's threshold from every background pixel around it (3×3), so edges that wobble
by a pixel (camera shake, compression) do not; counted pixels make 2×2 cells that need two
each. Touching cells make a region, and regions smaller than the sensitivity's minimum are
dropped (a leaf, a raindrop), and so are regions where nothing differs from both the
previous and the next frame: the place something just left, still fading from the
background. A region with the same pattern as before, only brighter or
darker, is light (a flickering lamp, headlights, a cloud's shadow) and dropped too. When
more than `motion.light_change_fraction` of the picture changes at once, the frame is a
light change (night mode switching, exposure) and the average starts over. Night pictures
get a higher threshold. The per-pixel work is numpy: well under a second for a 5-minute segment.

Per camera (the camera's settings → Detection → *Edit motion detection*): the sensitivity (low, medium,
high; else `motion.sensitivity`) and **motion-ignore areas**, rectangles drawn like censor
areas whose movement never counts (trees in the wind, a road), for what the filters above
do not catch.

Stored on the segment: `motion_score`, the largest share of the picture that moved in one
frame (0 = nothing moved, null = not measured), `motion_boxes`, per frame with motion
the boxes around its regions, and `motion_faint`, the same for **faint motion**: regions
the filters above dropped for being too small or for changing only since the last frame
or only before the next one (a small dog sniffing around, a person walking straight away
from the camera). Faint motion is never motion on its own and leaves the score alone; it
counts only for a detection whose box it lies in (its centre inside the box). So does
the detector's own box moving between two looks at the same target (same label, boxes
overlapping, at most 5 s apart): its centre moving by more than a tenth of its size, or a
side growing or shrinking by more than 15 % (`pipeline/still.py`). Profiles use it two
ways:

- `skip_without_motion` (opt-in): when nothing moved, the profile's jobs are created
  `skipped` and no worker sees the recording.
- `still_minutes` (standing still, above): a target counts until its box has had no motion
  box over it, faint motion in it or its own box moving (within `motion.window_seconds`)
  for that long, looking back through the camera's earlier recordings. Detections without a box need any motion. Recordings
  without a measurement count as moving.

They apply to recordings analysed from then on; *Detect again* re-evaluates one, and
*Rebuild events* (Settings → Detection) decides standing still again for all detections.
Where motion shows: the recording drawer (share of the picture, *Show motion* over the
video), the Recordings list and filter, hourly files (recordings with motion), and the
detection and event drawers (whether the box was moving within `motion.window_seconds`,
the same test as standing still, or counts as standing still).

**Whether it helps** (Events → Motion, admins; `pipeline/motion_report.py`): since motion is
measured on every recording whatever the profiles say, the report shows per camera and
profile (ones that still exist) what each option *would* do with the period's recordings and targets, split by the
correct / wrong marks: how many recordings skipping would save and which targets it would
miss, and which targets standing still would drop (with the profile's minutes, or 10 when
it has the option off). Recordings from before motion
detection, or measured before faint motion was kept, can be measured afterwards from their stored footage (*Measure motion* there,
or `foxcam motion-backfill --days 7`); that changes no detections or events.

## Data model (SQLite)

| Table | Holds |
|---|---|
| `segment` | One recording file: camera, start (UTC), duration, status, light tags, mask version used, motion (score, boxes and faint boxes), skips in the camera's footage, match flag, pinned, archive and offset in it, `deleted_at`. |
| `archive` | One hourly file made by compaction: camera, start, size, recording profile and encoder used, S3 key. |
| `job` | Work item per segment, profile and detector (with the worker's raw result, ≤ 256 kB, for debugging): state (`pending/leased/done/failed/dead`, `skipped` for cascade detectors that did not run and profiles that skip recordings without motion), lease token and expiry, attempts, model, timing. |
| `detection` | Worker result: profile, detector, offset, absolute time, (aliased) label, confidence, normalized box, model, worker, feedback, favorite (starred; an event is a favorite when one of its detections is, so stars survive rebuilding events), standing still, event. |
| `event` | Merged sighting of one label for one profile: time range, best detection, detectors that saw it and how many (`agreement`), confirmed, quiet (rebuilt, notifies nobody), clip and thumbnail paths, the gaps between recordings the clip keeps. |
| `footage_skip` | Footage a camera never delivered: when its stream jumped ahead (the Tapo cloud relay catching up after a stall) and how far the camera's clock jumped. Ingest also leaves a half-second pause in the stream there, which preparing the recording finds (`segment.skips`), so the player marks the exact place. |
| `worker` | Last-seen info, tags and reported vocabulary per worker id (Jobs → Detection workers). |
| `worker_token` | Worker access tokens (hashed) with their tags. |
| `detection_profile` | Detection profiles added in the web UI. |
| `notification_channel` | Notification channels added in the web UI. |
| `notification` | Outbox: one row per channel, event and trigger; status, attempts, error. |
| `mask_version` | Immutable censor-rectangle sets; segments reference the version they were censored with. |
| `camera_state` | Per-camera private flag, current mask version, profile selection, motion sensitivity and motion-ignore areas. |
| `managed_camera` | Cameras added in the web UI (config.yaml cameras are not stored). |
| `app_setting` | Settings changed in the web UI ([below](#settings-changed-in-the-web-ui)). |
| `user_account`, `oauth_identity`, `user_session`, `api_key`, `camera_access`, `invite` | Accounts, their sign-ins, sessions, API keys, camera shares and invite links ([AUTH.md](AUTH.md)). |

All timestamps are UTC. Recording file names carry their UTC offset
(`2026-10-24_14-00-35-560256+0200.mp4`), so MediaMTX may run in any time zone; this matters on
Windows, where it always uses the local time zone. Older names without an offset are read as UTC.

## Retention

Runs every 10 minutes (`pipeline/retention.py`):

- events older than their profile's `events_days`, else `retention.events_days` (0 = forever),
  are deleted, and their segments unpinned; favorites (one of their detections starred) are
  kept;
- unpinned segments older than `segments_days` are deleted, **except** segments still waiting for a
  worker, which are kept up to `unprocessed_max_days` (so an offline GPU box does not lose footage);
- if `max_disk_gb` is set, the oldest unpinned segments are deleted until under the cap;
- a segment with a starred detection counts as pinned, also when the detection is no match
  (so starring a near miss on the Detections page keeps its footage);
- the censored worker input is deleted as soon as its job is done (`delete_derived_after_done`).

Deleted segments keep their database row (`deleted_at`), so detections stay browsable.

The day counts and `max_disk_gb` can also be changed in the web UI (see below).

## Settings changed in the web UI

Admins can change these on the Settings page (General, Detection and Retention tabs) without
a restart: `public_url`, `segment_seconds`, the default recording quality
(`recording_profile_default`), `compaction.enabled` and `delay_minutes`,
`processing.sample_fps`, `max_width`, `concurrency`, `night_saturation_threshold` and
`keep_non_targets_from`, the low-data live view (`mediamtx.low_data`), the `events` timings,
the `motion` defaults (sensitivity, light-change share, time window), the `queue` lease,
attempts and backoff, and the `retention` days and disk cap
(`runtime_settings.EDITABLE`). Each value set there is an `app_setting` row keyed by its
config.yaml path and wins over config.yaml (and its environment variables) until it is
reset, which brings config.yaml's value back. They are validated with the bounds of the
models in `config.py`. Language and theme (Settings → General → Appearance, also on the
Account page) are per browser, not server settings.

Every process applies them onto its own settings object: the server right after a change
(and every 5 s for changes from elsewhere), ingest and compaction on each round of their
loops. Segment length and the low-data live view are pushed to MediaMTX: a new
`segment_seconds` applies from the next file, and low-data streams being watched restart
with the new settings. Left out on purpose: secrets, auth and storage,
`compaction.period_minutes` (archive boundaries) and the `mediamtx` encoder (every
transcoder would restart).

`foxcam settings list | set KEY VALUE | reset [KEY...]` does the same from a shell,
e.g. to undo an https `public_url` that broke signing in over plain http.

**Maintenance** (Settings → Detection) applies changed rules to what is already there:

- **Rebuild events** (`POST /api/admin/rebuild-events`, admins): deletes every event and
  makes them again from the detections with the profiles' current rules (e.g. the
  standing-still check, also for detections from before it existed). Rebuilt events are
  `quiet`: they notify nobody. Their clips are cut again in the background; detections and
  ✅/❌ marks stay.
- **Detect again** (`POST /api/segments/reanalyse` with `start`, `end` and an optional
  `camera`): runs detection again on every recording that starts in the range, with the
  current censor areas and profiles, like *Analyse again* on one recording
  (`POST /api/segments/{id}/reanalyse`). Their detections and ✅/❌ marks are replaced.
  At most 500 recordings at once; recordings a worker is busy with or already waiting are
  skipped. The API also lets editors do it for their own cameras.

**Settings export and import** (Settings → Configuration, `GET /api/admin/settings-export`,
`POST /api/admin/settings-import`, admins; `backup.py`): everything an import can set, as
one JSON file, with the values the server runs with (config.yaml's and the defaults too, not
only what was changed in the web UI): every Settings tunable, the default recording profile,
detection profiles, cameras, each camera's masks, motion areas, privacy, profile and
recording choices, and notification channels. An imported value that matches this server's
config.yaml (or default) is left to it rather than stored, and profiles, cameras and
channels that config.yaml defines here are left alone (listed only when the file's differ).
Passwords and tokens are left out (a stream URL's login, channel tokens, passwords, signing secrets, header values,
Apprise URLs). An import updates the same ids and adds new ones, and removes nothing;
things already here keep their secrets. A new camera whose URL had a login is not added
(the file lacks the password), and new channels may need their token again: the import
lists both. The file starts with `config_format_version`: imports upgrade older formats one
step at a time (`backup.MIGRATIONS`) and refuse newer ones, so a change to the format comes
with a migration from the version before.

**Danger zone** (Settings → Danger zone, `POST /api/admin/reset/{scope}`, admins) deletes
one kind of data and keeps the rest, like `foxcam reset` on the server: the detection
queue, detections (recordings are analysed again), footage (the video files only, like
retention for everything: detections, events, marks and reports stay), recordings (a clean
start: archives, detections, events and marks too), workers and tokens, profiles added in the UI, or cameras
added in the UI with every camera's settings. Not accounts: that would lock everyone out
(`foxcam reset users` from a shell). The server's background loops wait meanwhile
(`runner.gate`). The archiving process need not be stopped: an archive whose
recordings are deleted while it is being built is dropped (`compaction.build`). The ✅/❌ marks can be downloaded first
(`GET /api/detections/feedback-export`). Each action asks at its button and wants a word
typed first (`ConfirmPopover` with `typeToConfirm`).

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
- The web UI shows original footage (live view, recordings, clips) to signed-in users, and
  only for the cameras shared with them (see [AUTH.md](AUTH.md)).
- Camera passwords (RTSP URLs) are stored on the server and redacted in API responses. The Tapo
  cloud session token is stored in `data/tapo-session.json` (mode 0600); the password is never stored.

## Code map

```
server/src/foxcam/
  config.py          settings (config.yaml + ${ENV} expansion), camera list
  camera_store.py    cameras added from the UI
  profiles.py        detection profiles: camera selection, tag matching, UI profiles
  recording.py       recording profiles, default recording quality, burned-in masks
  runtime_settings.py  settings changed in the web UI (app_setting)
  accounts.py, oauth.py, api_keys.py, download_links.py   users, sign-in, API keys, signed links
  mediamtx.py        MediaMTX API client (path sync, live status)
  ffmpeg.py, hwaccel.py  probe, censored derivative, thumbnails, clips; hardware encoders
  pipeline/          scanner, preprocess, motion (+ still, motion_report), queue, events,
                     retention, uploader, compaction, reanalyse, runner
  storage.py         S3-compatible object storage client (boto3)
  notify.py          notification channels, outbox and senders (ntfy, webhook, Apprise)
  ingest/            publishers for tapo_cloud / file / test sources
  tapo_account.py    Tapo cloud sign-in (MFA) and device discovery for the UI
  web/               FastAPI app: worker, camera, auth, user, notification and live APIs
worker/src/foxcam_worker/
  cli.py             lease loop; `detect-file` for offline tests (local.py)
  detectors/         dummy, ultralytics (YOLO-World / YOLO), speciesnet
web/src/             React + Fluent UI app (pages/, components/, i18n/)
```

## Processing stage (burned-in masks)

By default MediaMTX records `<camera>` exactly as the camera sends it. When a camera's
censor areas are burned in, the server reconfigures MediaMTX:

- the camera's own stream goes to `<camera>/raw` (the RTSP source moves there; ingest
  publishers switch their publish URL, see `recording.source_path`);
- `<camera>` becomes a publisher path whose `runOnInit` (`foxcam transcode`) reads `<camera>/raw`, draws
  the masks, scales / limits fps, re-encodes (on the GPU when possible, see [HARDWARE.md](HARDWARE.md)) and publishes `<camera>`.

Recording, live view, the low-data path, snapshots and worker jobs all keep reading
`<camera>`, so nothing downstream changes. The mask editor snapshots `<camera>/raw`
(`?source=true`) so you can see what is under existing masks. It can also show a picture
from the last two days' recordings instead (`snapshot-moments`, then `snapshot.jpg?at=`),
since night pictures show other things; those were recorded with the masks burned in at
the time, and footage only in remote storage is left out.

## Archiving (compaction, recording profiles)

Segments are short (`segment_seconds`) so detection starts soon after something happens,
but thousands of small files per camera and day are a burden on disk and in S3.
`foxcam compact` (its own process; `compact` service in Docker) joins them into
archive files. Jobs → Archiving shows whether it runs, what it is doing, which periods wait
and what was archived last (`GET /api/archiving`); the files are under Recordings (Files
on phones) → Archives (`GET /api/archives`), and the Status page has a one-line summary.

- Once a period (`compaction.period_minutes`, default an hour) is over by
  `delay_minutes` and none of its segments is still being preprocessed, each camera's
  segments of that period are joined into `recordings/<camera>/archive/<start>Z.mp4`.
  Runs of segments whose streams differ (resolution, codec, audio) become separate files.
- Cameras on the `original` recording profile are joined with stream copy (the same bytes,
  in seconds). Profiles that transcode are re-encoded here (best hardware encoder, see
  [HARDWARE.md](HARDWARE.md)), with a keyframe forced at every segment start; if that
  fails or the keyframes are missing, the hour is joined without re-encoding instead.
- Segment rows stay and point into the file (`archive_id`, `archive_offset_s`); their
  `size_bytes` become their share of it. Detections, events, feedback and the Recordings
  page work as before: playback and downloads cut the segment back out without
  re-encoding (cached briefly in `data/cache/segments`), clips and frames read from the
  archive at the right offset (`pipeline/compaction.segment_source`).
- The original files are deleted a couple of minutes later, so clips being cut from them
  right then still find them.
- Retention removes segments as before; an archive file goes once all its segments are
  gone (an event's pinned segments keep their whole hour). With `storage.s3.recordings`,
  the hourly files are uploaded instead of every segment.

Each camera picks its recording profile under its Settings → Recording tab; cameras
without a choice use the default recording quality (Settings → General, or
`recording_profile_default` in config.yaml, default `original`). A change applies to
periods joined from then on. Recording profiles may set `keep_days`, which replaces
`retention.segments_days` for their cameras. The encode step is behind
`compaction.Encoder`, so it could later run on another (trusted) machine with a better
GPU.

