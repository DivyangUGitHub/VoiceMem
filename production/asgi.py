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
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from production.security import SecurityMiddleware, Settings

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
FRONTEND = ROOT / "frontend" / "out"

# Fail in a second on bad configuration, not after the models have loaded.
settings = Settings.from_env()
settings.validate()

# web/run.py intentionally imports its sibling modules as top-level modules.
# Put that directory on sys.path without changing the existing application.
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))

from run import app as legacy_app  # noqa: E402

docs = os.environ.get("VOICEMEM_ENABLE_DOCS", "0") == "1"
app = FastAPI(
    title="VoiceMem",
    version="1.0.0",
    docs_url="/api-docs" if docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if docs else None,
)
app.add_middleware(SecurityMiddleware, settings=settings)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return JSONResponse({"status": "ok", "service": "voicemem"})


@app.get("/readyz", include_in_schema=False)
def readyz():
    if not FRONTEND.is_dir() or not (FRONTEND / "index.html").is_file():
        return JSONResponse(
            {"status": "not_ready", "reason": "frontend build is missing"},
            status_code=503,
        )
    return JSONResponse({"status": "ready"})


# Keep every existing VoiceMem endpoint and WebSocket intact.
# The mount prefix makes the final URLs:
#   /backend/api/...
#   /backend/ws
#   /backend/images/...
app.mount("/backend", legacy_app)

if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
