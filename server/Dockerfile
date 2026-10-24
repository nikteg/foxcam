# Build context: repository root (see docker-compose.yml).

FROM node:22-slim AS web
RUN corepack enable
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY server/pyproject.toml ./
COPY server/src ./src
RUN uv pip install --system ".[tapo-cloud]"
COPY --from=web /web/dist /app/web-dist

ENV MEDIA_STREAMER_CONFIG=/app/config.yaml \
    MEDIA_STREAMER_WEB_DIST=/app/web-dist
EXPOSE 8000
ENTRYPOINT ["media-streamer"]
CMD ["serve"]
