# Sample footage

Put test clips here (video files in this folder are git-ignored). Name the clips that show
a fox `fox*.mp4` (e.g. `fox-day.mp4`, `fox-ir-night.mp4`) and the real-footage tests will
pick them up. Keep some clips without foxes (cat, dog, deer, empty garden at night) as
negative controls.

## Where to get fox footage

`python3 scripts/fetch_samples.py` downloads five freely licensed fox clips from Wikimedia
Commons (garden, backyard, wildlife cam, night street, snow) and writes `CREDITS.md`.
More sources:

- **Pexels / Pixabay**: search for "fox". These clips are free to use, but most are daytime and well lit.
- **Wikimedia Commons**: search for *Vulpes vulpes* videos. Check each file's licence.
- **LILA BC (lila.science)**: public camera-trap image datasets, many shot with IR at night. Pick one
  with fox categories. `detect-file` accepts a folder of images.
- **Your own cameras**: the most valuable source. Once the system runs, download fox events
  or segments from the web UI and keep the hard cases (night, far away, partly hidden) here.

## Offline test on a clip (no server needed)

```sh
# One-off check with an annotated output folder
uv run media-worker detect-file samples/fox-garden.mp4 --detector speciesnet --country SWE \
    --expect fox --out out/fox-day

# Same clip with a simulated IR night look and a censor rectangle
uv run media-worker detect-file samples/fox-garden.mp4 --detector ultralytics \
    --classes fox --night --mask 0,0,0.3,0.2 --expect fox

# Negative control
uv run media-worker detect-file samples/cat.mp4 --detector speciesnet --expect-none fox

# All fox*.mp4 clips, both day and simulated night, as tests
MEDIA_WORKER_TEST_DETECTOR=speciesnet uv run --package media-worker pytest worker/tests
```

`--expect fox` also matches more specific labels such as "red fox". The exit code is 0 when
every expectation is met, so you can compare models or thresholds from a script.

## End-to-end with the server

To run the whole pipeline (recording, censoring, queue, worker, fox event clip), loop a
clip as a fake camera in `config.yaml`:

```yaml
cameras:
  - id: foxclip
    source: { type: file, path: ./samples/fox-garden.mp4 }
```
