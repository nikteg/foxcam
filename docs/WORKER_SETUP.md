# Setting up a GPU worker

The worker is a small Python program that runs on the machine with the GPU. It connects
**out** to the server over HTTP (no ports to open on the worker), leases jobs, runs the
detector and posts the results. Create a token first: web UI → **Jobs → Add worker**.
The dialog also shows ready-to-paste commands for each option below.

| Option | Good for | GPU |
|---|---|---|
| [Windows + WSL 2](#windows-with-wsl-2-recommended-on-windows) | a Windows gaming PC | NVIDIA, via the Windows driver |
| [Windows (native)](#windows-native) | YOLO-World without WSL | NVIDIA |
| [Docker](#docker-linux-or-docker-desktop) | Linux servers, Docker Desktop on Windows | NVIDIA |
| [Linux](#linux) | a dedicated Linux box | NVIDIA (AMD via ROCm, untested) |
| [Intel GPU, next to the server](#intel-gpu-next-to-the-server) | the recorder box itself (N100, Core with UHD / Iris Xe, Arc) | Intel, via OpenVINO |

Whatever you choose, finish with `foxcam-worker doctor`: it checks ffmpeg, the GPU driver,
PyTorch's CUDA support, the installed detectors and the connection + token to the server.
Example output on a Windows PC with WSL:

```
[ ok ] python         3.12.8 on wsl (Linux 6.6.87.2-microsoft-standard-WSL2, x86_64)
[ ok ] ffmpeg         ffmpeg version 6.1.1-3ubuntu5 ...
[ ok ] nvidia driver  NVIDIA GeForce RTX 3070, 576.88, 8192 MiB
[ ok ] pytorch        2.8.0+cu128, CUDA 12.8: NVIDIA GeForce RTX 3070
[ ok ] detectors      dummy, speciesnet
[ ok ] server         http://192.168.1.10:8000 reachable, token 'gaming-pc' accepted
```

The worker code is the `worker/` folder of this repository; copy or clone it to the GPU
machine. Commands below run from the repository root. Any NVIDIA GPU with 4 GB of memory or
more is enough for the bundled models.

> **Why `--torch-backend auto`?** PyTorch on PyPI is CPU-only for Windows, and its Linux CUDA
> build may not match your driver. uv's `--torch-backend auto` picks the right CUDA build
> for the installed driver. Without it the worker runs on the CPU (very slow).

## Windows with WSL 2 (recommended on Windows)

WSL 2 runs the Linux version of the worker with full CUDA support. All detectors work
(SpeciesNet is only tested on Linux), and only the normal Windows NVIDIA driver is needed.

1. **NVIDIA driver on Windows**: install the current Game Ready / Studio driver from
   nvidia.com or the NVIDIA app. **Do not install an NVIDIA driver inside WSL**; Windows
   provides it to WSL (`/usr/lib/wsl/lib`).
2. **WSL** (PowerShell as administrator), then reboot if asked:

   ```powershell
   wsl --install -d Ubuntu-24.04
   wsl --update
   ```

3. **Inside Ubuntu** (open "Ubuntu" from the Start menu):

   ```sh
   nvidia-smi                      # must list your GPU; if not: update the Windows driver, wsl --shutdown
   sudo apt update && sudo apt install -y ffmpeg git
   curl -LsSf https://astral.sh/uv/install.sh | sh && exec $SHELL
   git clone <this repository> foxcam && cd foxcam   # or copy the folder
   # Keep it in the Linux file system (~), not /mnt/c: much faster model loading.

   uv tool install --python 3.12 --torch-backend auto "foxcam-worker[speciesnet] @ ./worker"
   # or, for YOLO-World:
   # uv tool install --torch-backend auto "foxcam-worker[ultralytics] @ ./worker"

   foxcam-worker doctor --server http://192.168.1.10:8000 --token msw_...
   foxcam-worker --server http://192.168.1.10:8000 --token msw_... --detector speciesnet
   ```

4. **Start automatically** when you sign in to Windows (PowerShell, in the repository folder):

   ```powershell
   powershell -ExecutionPolicy Bypass -File worker\deploy\windows\install-task.ps1 `
     -Mode wsl -Distro Ubuntu-24.04 -Server http://192.168.1.10:8000 -Token msw_... `
     -WorkerArgs "--detector speciesnet"
   ```

   The scheduled task keeps `wsl.exe` running, which also keeps the WSL VM from shutting
   down when no terminal is open. Remove it with `install-task.ps1 -Uninstall`.
   Alternatively enable systemd in WSL (`/etc/wsl.conf`: `[boot]` `systemd=true`) and use
   the [systemd unit](#linux); WSL itself must then still be started at logon.

### WSL networking

- The worker only makes outgoing connections, so WSL's default NAT networking works when
  the server is another machine on your network or reachable over a VPN (Tailscale etc.).
- **Server on the same Windows PC** (e.g. Docker Desktop): `localhost` inside WSL is the WSL
  VM, not Windows. Either use the PC's LAN address, or enable mirrored networking in
  `%UserProfile%\.wslconfig`, then `wsl --shutdown`:

  ```ini
  [wsl2]
  networkingMode=mirrored
  ```

- A Tailscale/VPN client on Windows is usually visible to WSL in mirrored mode; with NAT,
  install the VPN client inside WSL instead.

### WSL resources

WSL may use up to half of the PC's memory by default. SpeciesNet and YOLO-World need about
4–6 GB of RAM. To cap it, add `memory=8GB` under `[wsl2]` in `.wslconfig`.

## Windows (native)

Works for the **ultralytics** (YOLO-World / fine-tuned YOLO) and **dummy** detectors.
SpeciesNet may work but is untested on Windows; prefer WSL for it.

```powershell
winget install astral-sh.uv
winget install Gyan.FFmpeg
winget install Git.Git
# open a new terminal so PATH is updated, then in the repository folder:
uv tool install --torch-backend auto "foxcam-worker[ultralytics] @ .\worker"
foxcam-worker doctor --server http://192.168.1.10:8000 --token msw_...
foxcam-worker --server http://192.168.1.10:8000 --token msw_... `
  --detector ultralytics --model yolov8s-worldv2.pt --device cuda
```

Start at sign-in: `worker\deploy\windows\install-task.ps1 -Mode native -Server ... -Token ...
-WorkerArgs "--detector ultralytics --model yolov8s-worldv2.pt --device cuda"`.

Notes:

- `uv tool install` puts `foxcam-worker.exe` in `%USERPROFILE%\.local\bin`; run
  `uv tool update-shell` once if that folder is not on PATH.
- Model weights download to the current folder (`yolov8s-worldv2.pt`) and to
  `%USERPROFILE%\.cache`. Run the worker from a fixed folder so they are reused.
- Real-time antivirus scanning slows down frame extraction. Optionally exclude
  `%TEMP%\foxcam-worker-*` in Windows Security.

## Docker (Linux or Docker Desktop)

The worker image (`worker/Dockerfile`) contains ffmpeg, PyTorch with CUDA and one detector.

- **Windows**: Docker Desktop with the WSL 2 backend supports NVIDIA GPUs out of the box
  (Windows driver only).
- **Linux**: install the NVIDIA driver and the
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

Check GPU access first: `docker run --rm --gpus all ubuntu nvidia-smi`.

```sh
# worker/.env
FOXCAM_SERVER=http://192.168.1.10:8000
FOXCAM_WORKER_TOKEN=msw_...
DETECTOR=speciesnet                           # or ultralytics
WORKER_ARGS=--detector speciesnet
# WORKER_ARGS=--detector ultralytics --model yolov8s-worldv2.pt --device cuda

docker compose -f worker/compose.yaml up --build -d
docker compose -f worker/compose.yaml run --rm worker doctor
docker compose -f worker/compose.yaml logs -f
```

Weights and caches are kept in the `models` volume. `TORCH_BACKEND` (default `cu128`)
selects the PyTorch CUDA build; use `cu126` for older drivers.

## Intel GPU, next to the server

`docker-compose.worker.yml` adds a YOLO-World worker to the server's stack that runs on the
Intel GPU through OpenVINO (`--device intel:gpu`; N100: ~100 ms per frame, 6x its CPU).
Create a token under **Jobs → Add worker**, then:

```sh
LOCAL_WORKER_TOKEN=msw_... docker compose -f docker-compose.yml -f docker-compose.vaapi.yml \
  -f docker-compose.worker.yml up --build -d
```

The GPU detects, but decoding and preparing frames still take CPU: the worker gets one
core (`WORKER_CPUS=1`) so the box stays quiet. Raise it when jobs pile up. To check that
detection really runs on the GPU, look for `on GPU.0` in the worker's log, or watch
`sudo intel_gpu_top` on the host while a job runs.

Each new prompt (a profile's targets) is exported to OpenVINO once, which takes a few
seconds; the first job also downloads the weights. In LXC containers, pass
`/dev/dri/renderD128` through to the container (as for hardware encoding, [HARDWARE.md](HARDWARE.md)).

## Linux

```sh
sudo apt install -y ffmpeg              # plus the NVIDIA driver for your distribution
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install --python 3.12 --torch-backend auto "foxcam-worker[speciesnet] @ ./worker"
foxcam-worker doctor --server http://192.168.1.10:8000 --token msw_...
```

Run as a service with `worker/deploy/foxcam-worker.service` (instructions in the file).
AMD GPUs: `--torch-backend rocm7.1` (match your ROCm install), untested.

## Tags: which jobs a worker gets

Each recording is analysed once per [detection profile](ARCHITECTURE.md#detection-profiles)
of its camera, and a profile may require worker tags. A worker's tags are its detector
(`speciesnet`, `ultralytics`, plus `open-vocabulary` for YOLO-World / YOLOE,
`dummy`), anything passed with `--tags gpu,winhack` (or `FOXCAM_WORKER_TAGS`), and the tags
set on its access token in the web UI (**Jobs → Workers → Tags**). To pin a profile to this
machine, give the machine a unique tag and require it in the profile. The Profiles page and
the queue tab show profiles that no online worker can run.

## Troubleshooting

| Symptom | Fix |
|---|---|
| doctor: `pytorch … no GPU acceleration`, version ends in `+cpu` | Reinstall with `--torch-backend auto`: `uv tool install --reinstall --torch-backend auto "foxcam-worker[…] @ ./worker"`. |
| `nvidia-smi` not found in WSL | Update the **Windows** NVIDIA driver, `wsl --update`, `wsl --shutdown`. Never install the Linux driver inside WSL. |
| `CUDA error: no kernel image is available` | GPU too old for the PyTorch build; try `--torch-backend cu126` (or older). |
| `server … Connection refused` from WSL to `localhost` | Use the server's LAN address or [mirrored networking](#wsl-networking). |
| `server … worker token required` / `invalid worker token` | Create a token in **Jobs → Add worker**; tokens are shown once. |
| Worker idles while jobs wait | Their profile requires tags the worker lacks; compare **Cameras → Detection profiles** with the worker's tags in the queue tab. |
| Jobs start but are retried / `lease lost` | The worker was too slow or offline for longer than `queue.lease_seconds`; check the GPU is used, or raise the lease. |
| SpeciesNet: every job takes ~25 s extra | Expected: the model loads per job (see [ROADMAP.md](ROADMAP.md)). |
