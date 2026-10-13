"""Single-origin production ASGI application.

The existing VoiceMem web runtime is mounted under /backend so the new
frontend can share one origin with the existing WebSocket and API stack.
Nothing in the existing web application is removed or replaced; the security
layer in ``production/security.py`` wraps it from the outside.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from production.account_api import build_account_app
from production.accounts import Accounts
from production.health import readiness
from production.observability import Metrics, Observability, configure_logging
from production.security import COOKIE, SecurityMiddleware, Settings

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
FRONTEND = ROOT / "frontend" / "out"

configure_logging()

# Fail in a second on bad configuration, not after the models have loaded.
settings = Settings.from_env()
settings.validate()
# Accounts need the database: connect now, so a bad DATABASE_URL stops the start, not the first sign-up.
accounts = Accounts() if settings.secret_key else None

# web/run.py intentionally imports its sibling modules as top-level modules.
# Put that directory on sys.path without changing the existing application.
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))

import run as run_module  # noqa: E402

legacy_app = run_module.app

docs = os.environ.get("VOICEMEM_ENABLE_DOCS", "0") == "1"
app = FastAPI(
    title="VoiceMem",
    version="1.0.0",
    docs_url="/api-docs" if docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if docs else None,
)
metrics = Metrics()
# Last added = outermost: observability sees the final status of everything, including 401/429.
app.add_middleware(SecurityMiddleware, settings=settings, accounts=accounts)
app.add_middleware(Observability, metrics=metrics)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return JSONResponse({"status": "ok", "service": "voicemem"})


@app.get("/readyz", include_in_schema=False)
def readyz():
    memory_root = Path(os.environ.get("VOICEMEM_MEMORYSPACE_ROOT", ROOT / "voicemem_memoryspace"))
    ready, checks = readiness(FRONTEND, memory_root, database=accounts.ping if accounts else None)
    body = {"status": "ready" if ready else "not_ready", "checks": checks}
    return JSONResponse(body, status_code=200 if ready else 503)


@app.get("/metrics", include_in_schema=False)
def metrics_endpoint():
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")


# Keep every existing VoiceMem endpoint and WebSocket intact.
# The mount prefix makes the final URLs:
#   /backend/api/...
#   /backend/ws
#   /backend/images/...
app.mount("/backend", legacy_app)
if accounts:
    purge = getattr(run_module, "purge_tenant", lambda tenant_id: 0)
    app.mount("/account", build_account_app(accounts, purge, COOKIE))

if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
