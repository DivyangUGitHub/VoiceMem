# Running VoiceMem in production

This layer wraps the research runtime under `web/` and `voicemem/`. It adds sign-in,
request limits, health checks, logs, metrics, a locked dependency set and a hardened
container. It does not change how memory works.

**Scope.** This is a hardened single-instance deployment for one team or a pilot. It is
not a multi-tenant SaaS yet. See [Not built yet](#not-built-yet) before you put real
customers on it.

## How it fits together

```
Browser / script
  |  HTTPS, WSS
  v
production.asgi:app
  |-- SecurityMiddleware      sign-in, origin check, rate limits, size limits, headers
  |-- Observability           request id, JSON access log, metrics
  |
  |-- /                       Next.js static export (public)
  |-- /auth/login, /logout    public, rate limited
  |-- /healthz, /readyz       public, for the platform
  |-- /metrics, /api-docs     need a key
  `-- /backend/**             the existing VoiceMem FastAPI + WebSocket app, needs a key
```

The frontend and the runtime share one origin, so there is no CORS setup to maintain.

## Run it

```bash
cp .env.production.example .env.production
python -c "import secrets; print(secrets.token_urlsafe(32))"   # paste as VOICEMEM_API_KEYS
# also set OPENAI_API_KEY in .env.production
docker compose -f docker-compose.production.yml up --build
```

Open `http://localhost:8787` and sign in with the key. Scripts send the same key as
`Authorization: Bearer <key>` or `X-API-Key: <key>`.

The app refuses to start with no keys, or with keys shorter than 16 characters. Set
`VOICEMEM_AUTH_DISABLED=1` only on your own machine.

To revoke a key, remove it from `VOICEMEM_API_KEYS` and restart. Sessions signed with
that key stop working at once.

### Render

`render.yaml` builds the Dockerfile and attaches a 10 GB disk at `/var/lib/voicemem`.
Create a Blueprint from the repository. Render asks for `OPENAI_API_KEY` and
`VOICEMEM_API_KEYS` because they are marked `sync: false`. Keep the service at one
instance (see below).

The blueprint sets `FORWARDED_ALLOW_IPS=*` because Render's proxy is the only way in.
On any other host, set it to your proxy's address. Without it every user shares the
proxy's address and therefore one rate-limit bucket.

### Models

The image does not contain model weights. They live on the data volume under
`/var/lib/voicemem/models` (`VOICEMEM_MODELS_DIR`). Fetch them with
`scripts/download_models.sh` before the first real session. The app starts without them,
but voice features that need them will not work.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | none | Credentials for the LLM and TTS calls. `/readyz` fails without it. |
| `VOICEMEM_API_KEYS` | none | Comma-separated access keys, 16+ characters each. Required. |
| `DEMO_MODE` | `llm_tts` | `realtime` needs OpenAI Realtime access. |
| `VOICEMEM_MEMORYSPACE_ROOT` | `/var/lib/voicemem/voicemem_memoryspace` | Where memory data is stored. Keep it on the volume. |
| `VOICEMEM_ALLOWED_ORIGINS` | none | Extra browser origins allowed to call the API. Same-origin always works. |
| `VOICEMEM_RATE_LIMIT_PER_MIN` | 120 | Protected requests per client per minute. |
| `VOICEMEM_LOGIN_LIMIT_PER_MIN` | 10 | Login attempts per client per minute. |
| `VOICEMEM_MAX_WS_PER_CLIENT` | 4 | Concurrent WebSockets per client. |
| `VOICEMEM_MAX_BODY_BYTES` | 1000000 | Largest accepted request body. |
| `VOICEMEM_MAX_WS_MESSAGE_BYTES` | 1000000 | Largest WebSocket message. Larger closes with 1009. |
| `VOICEMEM_ENABLE_DOCS` | `0` | Set to `1` to serve `/api-docs` (still needs a key). |
| `VOICEMEM_LOG_FORMAT` | `json` | `json` or `text`. |
| `VOICEMEM_LOG_LEVEL` | `INFO` | Standard level names. |
| `FORWARDED_ALLOW_IPS` | uvicorn default | Proxy addresses allowed to set `X-Forwarded-For`. |

Never put keys in `NEXT_PUBLIC_*` variables. Next.js embeds those in the browser bundle.

## What the security layer does

- Everything under `/backend`, plus `/metrics` and the docs, needs a signed session cookie or a key header.
- State-changing requests and WebSocket connections from another origin are refused, even with valid credentials.
- Rate limits cover login attempts, rejected credentials, protected requests and WebSocket connects.
- Oversized bodies (including chunked ones) and oversized WebSocket messages are refused.
- Every response carries a Content-Security-Policy and the usual hardening headers. HSTS is sent only over HTTPS. API responses are not cached.
- Dot-segment and double-slash paths are refused. Memory-space names are reduced to safe characters before they become folder names.

The behaviour is covered by `tests/test_security.py`. The state (rate-limit counters,
revoked sessions) is held in memory, which is correct for one instance and wrong for
several.

## Health and monitoring

| Path | Use |
|---|---|
| `/healthz` | Process is up. Use as the liveness probe. |
| `/readyz` | Frontend is built, the memory folder is writable, `OPENAI_API_KEY` is set. 503 with a per-check reason if not. It never calls a paid API. |
| `/metrics` | Prometheus text format. Needs a key. |

Logs are one JSON object per line on stdout, each with a request id that is also
returned in the `X-Request-ID` response header. Query strings are never logged, because
they can carry tokens. Probe requests are logged only when they fail.

## Dependencies and CI

`requirements.lock` pins the Python dependencies for Python 3.11, the image's version.
To regenerate it:

```bash
uv pip compile pyproject.toml --python-version 3.11 -o requirements.lock
```

The frontend is locked by `frontend/pnpm-lock.yaml` and built with `--frozen-lockfile`.

To shrink the image by several GB, build with a CPU-only torch:

```bash
docker build --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu -t voicemem .
```

CI (`.github/workflows/production-check.yml`) runs on every push and pull request to
`main`: `ruff`, the test suite, the frontend build, a Docker build, and a dependency
audit. The audit fails on any known-vulnerable pin except the dated entries in
`security/audit-exceptions.txt`. The one entry today is `transformers==4.52.3`, pinned on
purpose for the Qwen2.5-Omni emotion model. Its exception expires on 2027-01-31, after
which CI fails until someone reviews it. Upgrading it needs a GPU regression run of the
right brain.

## Not built yet

Do these before serving independent customers.

- **Multiple users.** There is one shared set of keys and one active memory space for the whole process. Two people using it at once share memory and switch each other's space. Per-user accounts and per-user memory spaces are the first thing to build.
- **Privacy workflows.** The system learns voiceprints and can archive raw audio. Both are personal data, and voiceprints are biometric. There is no consent step, no delete-my-data and no export. Add these before collecting data from the public, and check which privacy law applies to your users.
- **Turn audio storage.** Voice-turn recordings are written under `results/turn_audio` inside the container, which is not on the data volume, and the app directory is not writable by the non-root user (read from the Dockerfile, not tested in a running container). Expect "play back what I said" not to work and no recordings to be kept. If you want it, point that folder at the volume and decide the retention period first.
- **Scaling out.** Models and memory spaces live in process memory, and so do the rate limits. Run one instance. More than one needs a shared store such as Redis and sticky routing for WebSockets.
- **Background work and storage.** Ingestion runs inline. Recordings are on local disk, not object storage.
- **Backups.** The volume is the only copy of the memory data. Snapshot it on a schedule and test a restore.
- **GPU capacity.** The local models need planning that this repository does not do for you.
- **Model and dataset licenses.** The code is Apache-2.0. Check the licenses of the Qwen models and the ChatMem-400K dataset before selling anything built on them.

## Do not delete

`web/`, `voicemem/`, `models/`, `evaluation/`, `finetune/`, `examples/`, `assets/`,
`docs/` and `tests/` are the research runtime and its supporting material. The
production layer only adds files around them.
