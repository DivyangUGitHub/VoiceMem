# VoiceMem production deployment

This layer is additive. It does not delete or replace the existing VoiceMem
runtime under `web/`.

## Architecture

```
Browser
  |
  | HTTPS / WSS
  v
production.asgi:app
  |
  +-- /                Next.js static export
  |
  +-- /backend         existing VoiceMem FastAPI + WebSocket runtime
  |
  +-- /healthz
  +-- /readyz
  +-- /api-docs
```

The new frontend talks to the existing runtime through the same origin, so
there is no browser CORS dependency and no frontend copy of the Python memory
logic.

## Local production-like run

1. Copy `.env.production.example` to `.env.production`.
2. Put the required provider credentials in the new file.
3. Build and start:

```powershell
docker compose -f docker-compose.production.yml up --build
```

Open:

```
http://localhost:8787
```

Health:

```
http://localhost:8787/healthz
```

Existing voice console:

```
http://localhost:8787/backend/
```

## Render

The repository contains `render.yaml` and a multi-stage Dockerfile. In
Render, create a Blueprint from this repository and deploy the Blueprint.
Render will prompt for `OPENAI_API_KEY` because it is intentionally marked
`sync: false`.

The attached persistent disk stores VoiceMem memory-space data. The service
is intentionally single-instance because the current VoiceMem runtime keeps
active model instances and memory-space objects in process memory.

## Important production limits

This is a production deployment foundation, not a claim that the underlying
research runtime has become a horizontally scalable SaaS overnight.

Before serving multiple independent customers, add:
- real user authentication and authorization;
- per-user memory-space ownership and isolation;
- a database-backed account/session layer;
- rate limiting and abuse protection;
- background workers for expensive ingestion;
- external object storage for recordings;
- centralized logs/metrics/traces;
- model-serving/GPU capacity planning;
- automated backup/restore for memory data;
- privacy/export/delete workflows.

Do not put API keys in frontend `NEXT_PUBLIC_*` variables. Client-visible
Next.js environment variables are embedded into browser code.

## Existing files that must NOT be deleted

Keep the current:
- `web/`
- `voicemem/`
- `models/`
- `evaluation/`
- `finetune/`
- `examples/`
- `assets/`
- `docs/`
- `tests/`

The production layer only adds deployment/runtime files around them.
