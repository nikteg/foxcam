# Hardware video encoding and decoding

Foxcam re-encodes video in three places:

| What | When | Run by |
|---|---|---|
| **Low-data live view** (`<camera>/low`, ~480p) | only while someone watches it | MediaMTX (`foxcam transcode`) |
| **Burned-in censor areas** | continuously, live | MediaMTX (`foxcam transcode`) |
| **Recording profiles** that re-encode (1080p, 720p, 480p, H.265, custom) | once per finished hour | `foxcam compact` |

Recording profiles are applied when compaction joins an hour into one file, not live:
detection and live view keep the camera's original, and a batch encode compresses better
than a live one. With a GPU an hour of 3K footage takes a few minutes. Every segment
must start on a keyframe in the hourly file (so it can be cut back out without
re-encoding): compaction forces keyframes there and checks them, and joins the hour
without re-encoding if the encoder did not comply.

The censored copy each worker gets is made on the CPU (libx264, small and low frame
rate); where a VAAPI render node exists (`mediamtx.hw_device`, default
`/dev/dri/renderD128`), the GPU decodes and scales it first, with a CPU fallback.

Each of the three decodes the camera stream and encodes it again. On the CPU that is fine for a
camera or two; with a GPU's video engine it costs almost nothing, even for many cameras.

## Backends

There is no single cross-platform API for video encoding. Each OS has its own:

| Backend | Hardware | OS | Notes |
|---|---|---|---|
| `videotoolbox` | every Mac | macOS | Apple's own API |
| `nvenc` | NVIDIA GeForce / RTX / Quadro | Linux, Windows | NVENC encode, NVDEC (CUDA) decode |
| `qsv` | Intel Quick Sync (iGPU, Arc) | Linux, Windows | fastest path on Intel |
| `vaapi` | Intel and AMD | Linux | **the generic Linux API** (Intel media driver, Mesa for AMD) |
| `amf` | AMD Radeon | Windows | |
| `mf` | any GPU | Windows | **Media Foundation, the generic Windows API** (uses the vendor's encoder) |
| `vulkan` | any GPU with Vulkan Video | Linux, Windows | cross-vendor and cross-OS, but drivers are still young: opt-in only |
| `software` | CPU (libx264 / libx265) | all | always works |

With `hwaccel: auto` (the default) the transcoder tries, in order:

- macOS: `videotoolbox`
- Linux: `nvenc`, `qsv`, `vaapi`
- Windows: `nvenc`, `qsv`, `amf`, `mf`

and then the CPU. Each candidate is tested with a tiny real encode **where the transcode
runs** (in Docker: inside the MediaMTX container), so it only picks a backend whose driver,
device and ffmpeg build all work. Results are cached; failures are retried after 10 minutes.
If a hardware encoder fails on the real stream within 15 seconds, that transcode falls back
to the CPU on its own.

Decoding uses the same hardware (`-hwaccel`); ffmpeg falls back to CPU decoding for streams
the GPU cannot decode. Masks, scaling and frame-rate changes run on the CPU, which is cheap
next to encoding and keeps one code path for every backend.

```yaml
mediamtx:
  hwaccel: auto                  # or force one: nvenc | qsv | vaapi | videotoolbox | … | none
  hw_device: /dev/dri/renderD128 # Linux: render node for vaapi / qsv (several GPUs)
```

Check what works on a machine:

```console
$ foxcam hwaccel                                # bare metal / dev
$ docker compose exec mediamtx foxcam hwaccel   # Docker
auto picks, in order: nvenc, qsv, vaapi, software
  no   nvenc         h264_nvenc         Cannot load libcuda.so.1
  ok   qsv           h264_qsv
  ok   qsv           hevc_qsv
  ok   vaapi         h264_vaapi
  …
auto -> qsv (h264_qsv)
```

The MediaMTX log shows the choice for each transcode, e.g.
`transcode garden -> low: qsv (h264_qsv)`.

## Docker

The image includes ffmpeg with VAAPI, QSV, NVENC and Vulkan support, the Intel media driver
and Quick Sync runtime (amd64), and Mesa's VAAPI driver for AMD. MediaMTX runs from the same
image, so its transcodes find all of it. The container only needs access to the GPU:

The override files below give the GPU to both the `mediamtx` service (live transcodes) and
the `compact` service (re-encoding finished hours).

**Intel or AMD** (VAAPI / Quick Sync): pass the render node.

```sh
docker compose -f docker-compose.yml -f docker-compose.vaapi.yml up --build -d
```

Several GPUs: `FOXCAM_HW_DEVICE=/dev/dri/renderD129` and `mediamtx.hw_device` to match.
`ls -l /dev/dri` on the host lists them. Intel before Broadwell (2014) needs the `i965`
driver instead (`LIBVA_DRIVER_NAME=i965`, package `i965-va-driver`).

**NVIDIA**: install the driver and the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
on the host, then:

```sh
docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up --build -d
```

`NVIDIA_DRIVER_CAPABILITIES` must include `video` (the override file sets it), otherwise the
toolkit does not mount the encoder library. Consumer GeForce cards limit how many NVENC
sessions run at once (8 on current drivers); each continuously re-encoded camera and each
watched low-data stream is one session.

**macOS / Windows hosts**: Docker Desktop runs Linux in a VM without access to the GPU's
video engine (except NVIDIA under WSL 2 on Windows, with the override above). Run the stack
natively there for hardware encoding, or accept CPU encoding.

## Without Docker

- **macOS**: works out of the box (VideoToolbox) with Homebrew's ffmpeg.
- **Linux**: install your distribution's ffmpeg plus `intel-media-va-driver` (Intel) or
  `mesa-va-drivers` (AMD), and `libmfx-gen1.2` for Quick Sync. The user running MediaMTX
  needs access to `/dev/dri/renderD128` (group `render`). NVIDIA: the proprietary driver.
- **Windows**: an ffmpeg build with `nvenc` / `qsv` / `amf` / `mf` (e.g. gyan.dev "full").

## Quality notes

Hardware encoders need a little more bitrate than x264 for the same picture. The built-in
recording profiles set explicit bitrates, so they look the same on every backend. Profiles
without a bitrate use constant quality (CRF 23 on the CPU, the equivalent QP elsewhere).

### Measured: a 3K camera (Tapo C530WS, 2880×1620, H.264 at about 7 Mbit/s)

30 s of footage, re-encoded on an Apple Silicon Mac (VideoToolbox). SSIM compares each
result with the camera's original (1.0 = identical):

| Encoding | Size | SSIM | Time for 30 s |
|---|---|---|---|
| camera original, 3K H.264 | 100% | 1.000 | – |
| H.265, 3K, constant quality | **125%** | – | 5.3 s |
| H.265, 3K, 3 Mbit/s | 52% | 0.954 | 5.4 s |
| H.264, 3K, 3 Mbit/s | 45% | 0.953 | 5.1 s |
| H.264, 1080p, 2.5 Mbit/s (built-in `1080p`) | 37% | 0.958 | 2.5 s |
| H.265, 1080p, 1.5 Mbit/s (built-in `h265`) | 32% | 0.945 | 2.6 s |

- Constant quality can make files *bigger* than what the camera sends: set a bitrate.
- On hardware encoders H.265 saves little over H.264 at the same bitrate; resolution and
  bitrate are what shrink recordings. (x265 on the CPU does better, at a much higher cost.)
- The cheapest saving is often in the camera itself: a lower resolution or quality in its
  app means less to store and nothing to re-encode.
- Re-encoding runs on the server, never on a worker: workers only get the censored,
  low-frame-rate copy, and shipping full-quality video to them and back would cost the
  camera's bitrate twice per camera.

