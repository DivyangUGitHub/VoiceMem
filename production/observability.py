"""Request IDs, JSON logs, an access log and Prometheus-style metrics.

Pure ASGI, like ``security.py``. Metrics live in process memory, which matches the
single-instance deployment in ``docs/PRODUCTION.md``. Query strings are never
logged (they can carry tokens).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
_RID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
QUIET_PATHS = {"/healthz", "/readyz", "/metrics"}   # probes: logged only when they fail

access_log = logging.getLogger("voicemem.access")
app_log = logging.getLogger("voicemem.app")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        out.update(getattr(record, "fields", None) or {})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging() -> None:
    """JSON lines on stdout. ``VOICEMEM_LOG_FORMAT=text`` keeps uvicorn's default output."""
    if os.environ.get("VOICEMEM_LOG_FORMAT", "json") != "json":
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(_RequestIdFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.environ.get("VOICEMEM_LOG_LEVEL", "INFO").upper())
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
    logging.getLogger("uvicorn.access").disabled = True     # replaced by voicemem.access


class Metrics:
    def __init__(self) -> None:
        self.started = time.time()
        self.requests: dict[str, int] = {}
        self.seconds_sum = 0.0
        self.seconds_count = 0
        self.ws_active = 0
        self.ws_total = 0

    def observe(self, status: int, seconds: float) -> None:
        key = f"{status // 100}xx"
        self.requests[key] = self.requests.get(key, 0) + 1
        self.seconds_sum += seconds
        self.seconds_count += 1

    def render(self) -> str:
        lines = [
            "# TYPE voicemem_http_requests_total counter",
            *(f'voicemem_http_requests_total{{status="{k}"}} {v}' for k, v in sorted(self.requests.items())),
            "# TYPE voicemem_http_request_seconds summary",
            f"voicemem_http_request_seconds_sum {self.seconds_sum:.6f}",
            f"voicemem_http_request_seconds_count {self.seconds_count}",
            "# TYPE voicemem_ws_active gauge",
            f"voicemem_ws_active {self.ws_active}",
            "# TYPE voicemem_ws_connections_total counter",
            f"voicemem_ws_connections_total {self.ws_total}",
            "# TYPE voicemem_process_start_time_seconds gauge",
            f"voicemem_process_start_time_seconds {self.started:.0f}",
        ]
        return "\n".join(lines) + "\n"


class Observability:
    def __init__(self, app, metrics: Metrics | None = None) -> None:
        self.app = app
        self.metrics = metrics or Metrics()

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        incoming = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"x-request-id"), "")
        rid = incoming if _RID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        is_ws = scope["type"] == "websocket"
        status = 500
        accepted = False
        start = time.perf_counter()

        async def tracking_send(msg):
            nonlocal status, accepted
            if msg["type"] == "http.response.start":
                status = msg["status"]
                msg = {**msg, "headers": [*msg.get("headers", []), (b"x-request-id", rid.encode())]}
            elif msg["type"] == "websocket.accept":
                status, accepted = 101, True
            elif msg["type"] == "websocket.close" and not accepted:
                status = 403                                  # refused during the handshake
            await send(msg)

        if is_ws:
            self.metrics.ws_total += 1
            self.metrics.ws_active += 1
        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            app_log.exception("unhandled error", extra={"fields": {"path": scope["path"]}})
            raise
        finally:
            seconds = time.perf_counter() - start
            if is_ws:
                self.metrics.ws_active -= 1
            else:
                self.metrics.observe(status, seconds)
            if scope["path"] not in QUIET_PATHS or status >= 400:
                client = scope.get("client")
                access_log.info(
                    "ws" if is_ws else "request",
                    extra={"fields": {
                        "method": scope.get("method", "WS"),
                        "path": scope["path"],
                        "status": status,
                        "ms": round(seconds * 1000, 1),
                        "client": client[0] if client else "-",
                    }},
                )
            request_id_var.reset(token)
