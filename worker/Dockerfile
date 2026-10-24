# GPU worker image. Build context: worker/ (see worker/compose.yaml).
#
#   docker compose -f worker/compose.yaml up --build -d
#
# PyTorch wheels for Linux bundle CUDA, so a plain Python base image is enough; the
# NVIDIA driver comes from the host via the NVIDIA Container Toolkit (`--gpus all`).
# Works on Linux and on Windows with Docker Desktop (WSL 2 backend).

# Intel GPUs (OpenVINO): python:3.12-slim-bookworm, the last Debian with Intel's OpenCL
# runtime (intel-opencl-icd).
ARG BASE=python:3.12-slim
FROM ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ultralytics (YOLO-World, fine-tuned YOLO), speciesnet or dummy. One per image: the
# ultralytics and speciesnet dependencies conflict. "ultralytics,openvino" for Intel GPUs
# (--device intel:gpu; adds Intel's OpenCL runtime, the GPU comes from /dev/dri).
ARG DETECTOR=speciesnet
# PyTorch build: auto (detect at build time; no GPU there, so falls back to the default
# CUDA wheels), cu128, cu126, cpu, ...
ARG TORCH_BACKEND=cu128

RUN case "$DETECTOR" in *openvino*) apt-get update \
      && apt-get install -y --no-install-recommends intel-opencl-icd \
      && rm -rf /var/lib/apt/lists/* ;; esac

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN if [ "$DETECTOR" = dummy ]; then uv pip install --system . ; \
    else uv pip install --system --torch-backend "$TORCH_BACKEND" ".[${DETECTOR}]"; fi

# Model weights and caches (Ultralytics weights, CLIP, kagglehub/SpeciesNet) live in a
# volume so they are downloaded once.
ENV HOME=/models YOLO_CONFIG_DIR=/models/ultralytics PYTHONUNBUFFERED=1
WORKDIR /models
VOLUME /models

ENTRYPOINT ["media-worker"]
