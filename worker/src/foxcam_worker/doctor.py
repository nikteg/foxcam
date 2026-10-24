"""Check that this machine can run a worker: ffmpeg, GPU/PyTorch, detector packages, server.

    foxcam-worker doctor
    foxcam-worker doctor --server http://192.168.1.10:8000 --token msw_...

Prints one line per check and exits 1 if something required is missing. Useful after
setting up a GPU box, WSL or Docker (see docs/WORKER_SETUP.md).
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""


def environment() -> str:
    """'wsl', 'docker', 'windows', 'macos' or 'linux'."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if Path("/.dockerenv").exists():
        return "docker"
    try:
        release = Path("/proc/sys/kernel/osrelease").read_text().lower()
    except OSError:
        release = ""
    return "wsl" if "microsoft" in release or os.environ.get("WSL_DISTRO_NAME") else "linux"


def check_ffmpeg() -> Check:
    path = shutil.which("ffmpeg")
    if not path:
        hints = {
            "windows": "winget install Gyan.FFmpeg (then open a new terminal)",
            "macos": "brew install ffmpeg",
        }
        return Check("ffmpeg", FAIL, "not found on PATH",
                     hints.get(environment(), "sudo apt install ffmpeg"))
    out = subprocess.run([path, "-version"], capture_output=True, text=True, check=False)
    first = out.stdout.splitlines()[0] if out.stdout else path
    return Check("ffmpeg", OK, first)


def check_nvidia_driver() -> Check:
    env = environment()
    smi = shutil.which("nvidia-smi")
    if env == "wsl" and not smi and Path("/usr/lib/wsl/lib/nvidia-smi").exists():
        smi = "/usr/lib/wsl/lib/nvidia-smi"
    if not smi:
        if env == "macos":
            return Check("nvidia driver", OK, "not applicable (Apple GPU uses mps)")
        hint = ("Install the NVIDIA driver on Windows (not inside WSL), then `wsl --shutdown`."
                if env == "wsl" else
                "Docker: run with --gpus all (NVIDIA Container Toolkit)." if env == "docker"
                else "Install the NVIDIA driver, or run on CPU (slow).")
        return Check("nvidia driver", WARN, "nvidia-smi not found: no NVIDIA GPU visible", hint)
    out = subprocess.run([smi, "--query-gpu=name,driver_version,memory.total",
                          "--format=csv,noheader"], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        return Check("nvidia driver", WARN, (out.stderr or out.stdout).strip()[:200],
                     "Driver installed but not working; reboot or reinstall it.")
    return Check("nvidia driver", OK, "; ".join(out.stdout.strip().splitlines()))


def gpu_summary() -> str | None:
    """GPU name(s) for the queue tab, without importing torch if a detector did not."""
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available():
        return ", ".join(torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count()))
    smi = shutil.which("nvidia-smi")
    if smi:
        out = subprocess.run([smi, "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, text=True, check=False)
        if out.returncode == 0 and out.stdout.strip():
            return ", ".join(out.stdout.split("\n")).strip(", ")
    return None


def check_torch() -> Check:
    if importlib.util.find_spec("torch") is None:
        return Check("pytorch", WARN, "not installed (only the dummy detector works)",
                     "install the worker with the ultralytics or speciesnet extra")
    import torch

    version = torch.__version__
    if torch.cuda.is_available():
        names = ", ".join(torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count()))
        return Check("pytorch", OK, f"{version}, CUDA {torch.version.cuda}: {names}")
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return Check("pytorch", OK, f"{version}, Apple mps")
    hint = "CPU only: detection is slow."
    if environment() == "docker" and torch.version.cuda is None:
        hint = "CPU-only PyTorch in this image: rebuild with TORCH_BACKEND=cu128 (or cu126)."
    elif "+cpu" in version or (torch.version.cuda is None and environment() == "windows"):
        hint = ("This is a CPU-only PyTorch build. Reinstall with "
                "`--torch-backend auto` (see docs/WORKER_SETUP.md).")
    elif shutil.which("nvidia-smi"):
        hint = "A GPU is present but PyTorch cannot use it; check the driver/CUDA versions."
    return Check("pytorch", WARN, f"{version}, no GPU acceleration", hint)


def check_detectors() -> Check:
    found = [name for name, mod in (("ultralytics", "ultralytics"), ("speciesnet", "speciesnet"))
             if importlib.util.find_spec(mod) is not None]
    if not found:
        return Check("detectors", WARN, "dummy only",
                     "install the ultralytics or speciesnet extra")
    return Check("detectors", OK, ", ".join(["dummy", *found]))


def check_server(server: str, token: str | None) -> Check:
    import httpx

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = httpx.get(f"{server.rstrip('/')}/api/worker/whoami", headers=headers, timeout=10)
    except httpx.HTTPError as e:
        hint = "Is the server reachable from here? Firewall / VPN / address?"
        if environment() == "wsl" and ("localhost" in server or "127.0.0.1" in server):
            hint = ("From WSL, localhost is the WSL VM. Use the server's LAN address, or "
                    "enable mirrored networking (docs/WORKER_SETUP.md).")
        return Check("server", FAIL, f"{server}: {e}", hint)
    if r.status_code == 401:
        return Check("server", FAIL, f"{server}: {r.json().get('detail', 'unauthorized')}",
                     "Create a token under Worker queue → Add worker and pass --token.")
    if r.status_code != 200:
        return Check("server", FAIL, f"{server}: HTTP {r.status_code}",
                     "Is this the foxcam server URL?")
    name = r.json().get("token_name")
    return Check("server", OK, f"{server} reachable, "
                               f"{f'token {name!r} accepted' if name else 'access allowed'}")


def run_checks(server: str | None, token: str | None) -> list[Check]:
    checks = [
        Check("python", OK, f"{platform.python_version()} on {environment()} "
                            f"({platform.system()} {platform.release()}, {platform.machine()})"),
        check_ffmpeg(), check_nvidia_driver(), check_torch(), check_detectors(),
    ]
    if server:
        checks.append(check_server(server, token))
    return checks


def main(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="foxcam-worker doctor", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server", default=os.environ.get("FOXCAM_SERVER"))
    p.add_argument("--token", default=os.environ.get("FOXCAM_WORKER_TOKEN"))
    args = p.parse_args(argv)

    checks = run_checks(args.server, args.token)
    marks = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]"}
    for c in checks:
        print(f"{marks[c.status]} {c.name:<14} {c.detail}")
        if c.hint and c.status != OK:
            print(f"{'':21}-> {c.hint}")
    sys.exit(1 if any(c.status == FAIL for c in checks) else 0)
