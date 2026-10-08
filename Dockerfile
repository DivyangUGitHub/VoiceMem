# syntax=docker/dockerfile:1

# ── 1. frontend ─────────────────────────────────────────────────────────────
FROM node:22-bookworm-slim AS frontend-builder
WORKDIR /src/frontend

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build

# ── 2. python dependencies (build tools stay in this stage) ─────────────────
FROM python:3.11-slim AS python-builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# Optional: a CPU-only torch index shrinks the image by several GB, e.g.
#   --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG TORCH_INDEX_URL=

RUN apt-get update && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /build
COPY requirements.lock ./
# Pinned set (regenerate with the command in docs/PRODUCTION.md).
RUN if [ -n "$TORCH_INDEX_URL" ]; then \
        pip install --index-url "$TORCH_INDEX_URL" \
            "$(grep -iE '^torch==' requirements.lock)" \
            "$(grep -iE '^torchaudio==' requirements.lock)" \
            "$(grep -iE '^torchvision==' requirements.lock)"; \
    fi \
    && pip install -r requirements.lock

# Register the package itself (gives voicemem.__version__ its real value); dependencies are already pinned above.
COPY pyproject.toml README.md ./
COPY voicemem/ ./voicemem/
RUN pip install --no-deps .

# ── 3. runtime ──────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    VOICEMEM_PORT=10000 \
    DEMO_MODE=llm_tts \
    VOICEMEM_LOG_FORMAT=json \
    VOICEMEM_MEMORYSPACE_ROOT=/var/lib/voicemem/voicemem_memoryspace \
    VOICEMEM_MODELS_DIR=/var/lib/voicemem/models \
    HF_HOME=/var/lib/voicemem/huggingface \
    HF_HUB_CACHE=/var/lib/voicemem/huggingface/hub

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /var/lib/voicemem --shell /usr/sbin/nologin voicemem

COPY --from=python-builder /opt/venv /opt/venv
COPY pyproject.toml README.md ./
COPY voicemem/ ./voicemem/
COPY assets/ ./assets/
COPY web/ ./web/
COPY production/ ./production/
COPY --from=frontend-builder /src/frontend/out ./frontend/out

# Model location: web/run.py looks in /app/models, which points at the persistent volume.
RUN mkdir -p /var/lib/voicemem/voicemem_memoryspace /var/lib/voicemem/logs \
        /var/lib/voicemem/huggingface /var/lib/voicemem/models \
    && ln -s /var/lib/voicemem/models /app/models \
    && chown -R voicemem:voicemem /var/lib/voicemem /app/web \
    && chmod +x /app/production/entrypoint.sh

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:10000/healthz', timeout=5).read()"

# Starts as root only to fix ownership of a mounted volume, then drops to `voicemem`.
ENTRYPOINT ["/app/production/entrypoint.sh"]
CMD ["uvicorn", "production.asgi:app", "--host", "0.0.0.0", "--port", "10000", \
     "--proxy-headers", "--no-server-header", "--ws-max-size", "2097152", "--timeout-graceful-shutdown", "20"]
