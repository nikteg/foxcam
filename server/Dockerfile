# Build context: repository root (see docker-compose.yml).

FROM node:22-slim AS web
RUN corepack enable
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

# MediaMTX itself runs from this image too (docker-compose.yml), so the transcodes it starts
# (`foxcam transcode`) find ffmpeg and the GPU drivers.
FROM bluenviron/mediamtx:1.21.2 AS mediamtx

FROM python:3.12-slim
# ffmpeg with VAAPI / QSV / NVENC / Vulkan support, plus the user-space video drivers:
# mesa (AMD, VAAPI), Intel media driver + oneVPL runtime (Intel VAAPI / Quick Sync, amd64).
# NVIDIA's libraries come from the host via the NVIDIA Container Toolkit. A font for the
# labels on the boxes in notification pictures (ffmpeg's drawtext).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg mesa-va-drivers vainfo \
       fonts-dejavu-core \
    && if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
         apt-get install -y --no-install-recommends intel-media-va-driver libvpl2 \
           libmfx-gen1.2; \
       fi \
    && rm -rf /var/lib/apt/lists/*
COPY --from=mediamtx /mediamtx /usr/local/bin/mediamtx
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY server/pyproject.toml ./
COPY server/src ./src
RUN uv pip install --system ".[tapo-cloud,s3,notify]"
COPY --from=web /web/dist /app/web-dist

ENV FOXCAM_CONFIG=/app/config.yaml \
    FOXCAM_WEB_DIST=/app/web-dist
EXPOSE 8000
ENTRYPOINT ["foxcam"]
CMD ["serve"]
