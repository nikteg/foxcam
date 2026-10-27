---
name: footage-debug
description: Find out what is really in a recording, clip or archive - timestamp jumps, keyframes, missing footage (the camera's burned-in clock skipping), frames out of place. Use for "the video jumps", "footage is missing", "the box is on the wrong frame", "previews don't line up", and any question about what a video file contains.
---

# What is in the footage

```sh
uv run python scripts/footage_probe.py event:21
uv run python scripts/footage_probe.py segment:2602 --clock 2
uv run python scripts/footage_probe.py event:21 --clock 0.5 --at 12-16
uv run python scripts/footage_probe.py out/some.mp4 --gap 0.5
```

- `event:`/`segment:`/`archive:` download from production through `scripts/prod-api.sh`
  (allow the production host in the Bash call) into `out/footage/`, kept for reruns.
  Local files work too.
- It prints the streams, the timestamp steps longer than `--gap`, and the keyframes.
- `--clock N` saves `out/footage/<name>-clock.png`: the camera's own clock every N
  seconds, one row each, listed top to bottom. Read it with the Read tool. A clock that
  jumps while the timestamps run on evenly means footage the camera never delivered;
  a clock that runs behind `start_at` means it arrived late. `--crop x,y,w,h` (fractions)
  if the clock is somewhere else than the top left.

Then compare with the database's view: the recordings around it (`prod` skill:
`/segments?camera=&day=`, their `start_at` and `duration_s`), gaps between them, the
event's `clip_start` and `clip_duration_s`.

How the footage is made (to know where a fault can come from):
- Tapo cloud cameras: relay → `ingest/ts_retime.py` (camera timestamps, re-anchored on a
  jump of more than 5 s) → ffmpeg → MediaMTX, which records segments and starts a new one
  when the stream's time drifts from its clock ("detected drift" in its log).
- Event clips: `pipeline/events.py` → `ffmpeg.concat_clip` (stream copy; gaps between
  recordings kept as gaps).
- Archives: `pipeline/compaction.py` joins recordings back to back (`archive_offset_s`).
- Missing footage shown in the player: `footage.py`.

For frame-exact checks, extract single frames with `ffmpeg -ss T -i FILE -frames:v 1`
and look at them; `ffprobe -show_entries packet=pts_time,flags` lists every frame.
