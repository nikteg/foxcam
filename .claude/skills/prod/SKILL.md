---
name: prod
description: Look at what the production Foxcam server is doing - its API (events, recordings, clips, jobs, settings) with the private API key, and the processes' logs (GET /api/logs; Docker logs the user can fetch for the rest). Use whenever a question is about real data or real behaviour ("why did event X...", "check the live server", "re-run detections", "is the worker busy"), before theorising from the code.
---

# Production Foxcam

Private details (URL, how it is deployed, container names) are in `CLAUDE.local.md`. The
API key is in `mise.local.toml` and only `scripts/prod-api.sh` reads it.

## The API

```sh
scripts/prod-api.sh GET '/api/events?limit=30'
scripts/prod-api.sh GET /api/events/21/clip -o out/footage/event-21.mp4
scripts/prod-api.sh POST /api/segments/1492/reanalyse
```

- Give the Bash call the production host in its allowed domains (sandbox).
- Never echo, print or paste the key, and never pass it on a command line. If the script
  says the key is missing, ask the user to fill in `mise.local.toml`; don't ask for the
  key in chat.
- Pipe JSON through `python3 -I -c` or `jq` to pick out fields; save files under `out/`
  (gitignored).
- Times in the API are UTC (`Z`); the user speaks local time (see `CLAUDE.local.md`).

Useful endpoints (all under `/api`; see `server/src/foxcam/web/api*.py` for the rest):

| What | Endpoint |
|---|---|
| Events (newest first) | `GET /events?limit=&camera=&profile=` |
| An event's clip, its timeline (motion, gaps) | `GET /events/{id}/clip`, `GET /events/{id}/timeline` |
| Recordings of a camera on a day | `GET /segments?camera=&day=YYYY-MM-DD&limit=2000` |
| One recording, its file, its jobs | `GET /segments/{id}`, `/segments/{id}/download`, `/segments/{id}/jobs` |
| Hourly files (archives) | `GET /archives`, `/archives/{id}/download`, `/archives/{id}/timeline` |
| Detections | `GET /detections?event=&segment=&limit=` |
| Run detection again | `POST /segments/{id}/reanalyse`, `POST /segments/reanalyse` |
| Job queue, workers | `GET /queue?limit=`, `GET /workers` |
| Preparation and archiving | `GET /preparation`, `GET /archiving` |
| Cameras (status, online since, source) | `GET /cameras` |
| Profiles | `GET /profiles` |
| Settings an admin tunes | `GET /settings/runtime`, `PUT /settings/runtime` |
| Motion report | `GET /motion/report?days=` |

Changing things on production (PUT/POST/DELETE, re-analysing many recordings) needs the
user's go-ahead for that change, unless they asked for it.

## Waiting for a deploy or a backlog

After a push the stack redeploys (see `CLAUDE.local.md`). Don't guess when it is done:
poll something that changes (e.g. a new API field, or `GET /cameras` → `online_since`)
with a Monitor or a bounded loop, at a pace that matches (a minute, not seconds).

## Logs

Read them yourself through the API first (admin key, which `prod-api.sh` has). Each
process writes its own rotated log file, secrets taken out; the same as Settings → Logs:

```sh
scripts/prod-api.sh GET /api/logs                       # processes with a log, size, last write
scripts/prod-api.sh GET '/api/logs/server?level=WARNING&limit=5000'
scripts/prod-api.sh GET '/api/logs/mediamtx?q=grusgang&limit=1000'
```

- Processes: `server`, `ingest`, `compact`, `mediamtx`, and `worker-<id>` per worker
  (workers send theirs, also from other machines).
- `GET /api/logs/{process}?level=&q=&limit=`: entries newest first, `{ts, level, logger,
  message}`. `level` is a floor (`DEBUG`, `INFO`, `WARNING`, `ERROR`); `q` is plain text,
  any case, matched in logger and message; `limit` up to 5000. A traceback is one
  entry's message. `GET /api/logs/{process}/download` gives the whole text.
- `ts` is UTC. There is no time window parameter: fetch enough and filter on `ts` (pipe
  through `python3 -I -c`, reversed for oldest first). Save long ones under `out/logs/`.
- Start with `level=WARNING` for every process, then read INFO entries around what turns
  up (all processes in the same minutes: a stall shows in ingest, MediaMTX and the
  scanner at once).
- Not in these files: the HTTP access log (`uvicorn.access`), anything printed before
  logging was set up (a crash at import), and lines from before the log files existed or
  older than the rotation keeps (5 × 5 MB per process). For those, and for restart
  counts, fall back to Docker.

The Docker side, which you cannot reach: write the commands for the user, with a UTC
window around the moment in question (convert from local time), grepped to the camera:

```sh
sudo docker inspect -f '{{.Name}} started={{.State.StartedAt}} restarts={{.RestartCount}}' \
  foxcam-server-1 foxcam-ingest-1 foxcam-mediamtx-1 foxcam-compact-1 foxcam-worker-1
sudo docker logs --since 2026-10-27T10:20:00Z --until 2026-10-27T10:45:00Z -t foxcam-ingest-1 2>&1 | grep -i <camera>
sudo docker logs --since ... --until ... -t foxcam-mediamtx-1 2>&1 | grep -i <camera>
```

What to look for: restarts (`StartedAt`, remember it is UTC), `relay session ended/failed`
and `stream skipped` (ingest), `detected drift ... resetting` (MediaMTX splitting a
recording), tracebacks in `foxcam-server-1`. Say beforehand what each outcome would mean.

Never read files on the server or in Komodo; give commands or GUI steps.
