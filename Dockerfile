# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS frontend-builder
WORKDIR /src/frontend

COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN corepack enable && pnpm install --no-frozen-lockfile

COPY frontend/ ./
RUN pnpm build

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PIP_NO_CACHE_DIR=1     VOICEMEM_PORT=10000     DEMO_MODE=llm_tts     VOICEMEM_MEMORYSPACE_ROOT=/var/lib/voicemem/voicemem_memoryspace

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends     ffmpeg     libsndfile1     build-essential     git     && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY voicemem/ ./voicemem/
COPY assets/ ./assets/
COPY web/ ./web/
COPY production/ ./production/

RUN pip install --upgrade pip setuptools wheel && pip install -e .

COPY --from=frontend-builder /src/frontend/out ./frontend/out
COPY --from=frontend-builder /src/frontend/package.json ./frontend/package.json

RUN mkdir -p /var/lib/voicemem/voicemem_memoryspace /var/lib/voicemem/logs

EXPOSE 10000

CMD ["uvicorn", "production.asgi:app", "--host", "0.0.0.0", "--port", "10000", "--proxy-headers"]
