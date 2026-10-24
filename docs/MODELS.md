# Detection models for foxes

The goal is to keep the footage in which a **fox** is visible, often at night under IR, at a
distance, partly hidden. That rules out the default choice:

> **COCO-trained models (stock YOLO, RT-DETR, etc.) have no "fox" class.** They report a fox
> as `dog`, `cat` or `bear`, if at all. You need an open-vocabulary model, a species
> classifier, or a model fine-tuned on foxes.

## Options

| Model | Type | Fox support | Speed | Licence | Notes |
|---|---|---|---|---|---|
| **MegaDetector + SpeciesNet** (Google) | camera-trap detector + species classifier | ✅ "red fox" (and ~2000 species) | moderate | Apache-2.0 | Trained on millions of camera-trap images incl. IR night shots. Supports country geofencing (`location.country` in `config.yaml`, sent to workers). **Recommended starting point.** Detector: `speciesnet`. |
| **YOLO-World / YOLOE** (Ultralytics) | open-vocabulary detector | ✅ prompt "fox" | fast (real time) | AGPL-3.0 | Weaker on IR/night, small or hidden animals. **Prompt only `fox`**: adding `dog,cat,…` makes it label foxes as "dog" (measured below). Detector: `ultralytics` (prompted with each profile's targets; with `--classes` it only serves profiles looking for those classes). |
| **OWLv2 / Grounding DINO** | zero-shot detector | ✅ prompt "fox" | slow | Apache-2.0 | Good for labelling a dataset, too slow for every frame. Not bundled. |
| **RF-DETR / YOLO11 fine-tuned** | custom detector | ✅ after training | fast | Apache-2.0 / AGPL-3.0 | Best long-term accuracy *for your cameras*. Train on your own ✅/❌-reviewed detections (see below). Detector: `ultralytics --model best.pt`. |
| **VLM verification** (Qwen-VL class) | vision-language model | ✅ "is this a fox?" | slow | varies | Optional second stage on crops to cut false positives. Not bundled. |
| **Cloud APIs** (AWS Rekognition, Google Vision, Azure Vision) | SaaS | ✅ usually "fox" / "red fox" label | network bound | paid | Write a small detector adapter (see [WORKER_PROTOCOL.md](WORKER_PROTOCOL.md)). Only censored frames leave your network. |
| **Stock YOLO / RT-DETR on COCO** | closed-set detector | ❌ (fox → dog/cat) | fastest | AGPL / Apache | Useful only as a motion/"some animal" pre-filter. |

## Several models side by side

[Detection profiles](ARCHITECTURE.md#detection-profiles) let different workers look for
different things: e.g. a `wildlife` profile requiring `speciesnet` on the garden cameras and
a `vehicles` profile requiring `open-vocabulary` (YOLO-World, prompted with the profile's
targets) on the driveway. Use `aliases` (`red fox: fox`) so both models report the same name.
Keep a fox profile's YOLO-World targets to fox synonyms only (see the measurements below).

## Recommended path

1. **Start** with SpeciesNet (MegaDetector + classifier) on the GPU box:
   `uv run media-worker --server http://<server>:8000 --detector speciesnet`.
2. **Review** detections in the web UI and mark them ✅/❌. Keep hard cases (night, far away,
   partly hidden) as test clips in `samples/`.
3. **Measure** each candidate on your clips without the server:
   `uv run media-worker detect-file samples/fox-night.mp4 --detector … --night --expect fox`.
4. **Fine-tune** RF-DETR or YOLO11 on the exported reviews
   (`GET /api/detections/feedback-export`) once you have a few hundred confirmed frames from
   your own cameras. Run it with `--detector ultralytics --model best.pt`.

## What we measured

`media-worker detect-file` on the sample clips (`scripts/fetch_samples.py`), CPU only (Apple
M-series, no GPU), October 2026. "Day" is the original clip; "night" adds the simulated IR look
(`--night`: grayscale, more contrast, noise). Numbers are the share of sampled frames with a fox
at ≥ 50 % confidence. A fox event only needs one such frame per segment.

| Clip | What it shows | YOLO-World `fox` day / night | YOLO-World `fox,dog,cat,person` | SpeciesNet day / night | MegaDetector "animal" box |
|---|---|---|---|---|---|
| `fox-yard-day` | backyard, clear view | 91 % / 77 % | 20 % (rest: "dog") | **95 % / 75 %** | 100 % |
| `fox-family-cam` | night camera, close | 15 % / 6 % | 0 % ("dog") | **85 % / 31 %** | 87 % |
| `fox-small` | fox in snow | 17 % / 0 % | 0 % ("dog") | **75 % / 25 %** | 75 % |
| `fox-night-urban` | small, distant, street lights | **42 %** / 25 % | 0 % ("dog") | 32 % / 5 % | 75 % |
| `fox-garden` | two foxes behind leaves | 0 % / 0 % | 0 % | 4 % / 0 % | 76 % |
| **Clips with an event** | | 4 / 5 day, 3 / 5 night | 1 / 5 | **5 / 5 day, 4 / 5 night** | – |

Speed (CPU): YOLO-World ≈ 20 frames/s. SpeciesNet ≈ 1–2 s per frame plus ~25 s to load the model
for every job, so on a CPU it is slower than real time; use a GPU worker.

Takeaways:

- **SpeciesNet is the most reliable fox detector** here, including the night-camera clip.
- **MegaDetector almost always finds the animal** (75–100 % of frames), even when the species
  classifier is unsure and answers "animal", "canine family" or "canis species". Adding
  `canine family` to `detection.target_labels` would raise recall at the cost of also matching dogs.
- **Never give YOLO-World distractor classes** for this use case.
- Night/IR footage is clearly harder for every model; collect your own night clips and
  ✅/❌ feedback early.
