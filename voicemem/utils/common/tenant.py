"""Who is this request for?

The web layer sets ``current`` once per request or WebSocket (a ContextVar, so it follows
asyncio tasks and ``asyncio.to_thread``, and never leaks between concurrent users). The
runtime reads it to pick that user's memory space. ``None`` means single-user mode
(``python web/run.py``), where everything behaves as before.

Plain ``threading.Thread`` does not inherit it: pass what a thread needs as arguments.
"""

from __future__ import annotations

import contextvars
import re
import threading
from contextvars import ContextVar, Token

current: ContextVar[str | None] = ContextVar("voicemem_tenant", default=None)

# Becomes part of a folder name, so keep it boring.
_ID = re.compile(r"^[A-Za-z0-9]{4,24}$")


def bind(tenant_id: str) -> Token:
    if not _ID.match(tenant_id or ""):
        raise ValueError("invalid tenant id")
    return current.set(tenant_id)


def reset(token: Token) -> None:
    current.reset(token)


def thread(target, *args, **kwargs) -> threading.Thread:
    """A daemon thread that keeps the caller's tenant (and other context)."""
    ctx = contextvars.copy_context()
    return threading.Thread(target=ctx.run, args=(target, *args), kwargs=kwargs, daemon=True)


def submit(pool, fn, *args, **kwargs):
    """``pool.submit`` that keeps the caller's tenant."""
    return pool.submit(contextvars.copy_context().run, fn, *args, **kwargs)
