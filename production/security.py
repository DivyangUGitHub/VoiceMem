"""Security layer for the production app: auth, origin checks, limits, headers.

One pure-ASGI middleware, so it covers HTTP *and* WebSocket (the old
``@app.middleware("http")`` never saw WebSocket traffic). It knows nothing about
the research runtime under ``web/`` and can be tested without loading any model.

Policy
------
* ``/backend/**``, ``/metrics``, ``/api-docs`` and ``/openapi.json`` need a credential.
  Everything else (static frontend, ``/healthz``, ``/readyz``, ``/auth/*``) is public.
* Credential = signed session cookie (browser login), ``Authorization: Bearer <key>``
  or ``X-API-Key`` (scripts). Keys come from ``VOICEMEM_API_KEYS`` (comma separated).
* Users sign up and sign in with email and password (needs ``DATABASE_URL`` or the default
  SQLite file, and ``VOICEMEM_SECRET_KEY``). Each user's session is bound to their own memory.
  Every API key is its own isolated user too.
* The app refuses to start with no keys and no secret unless ``VOICEMEM_AUTH_DISABLED=1``.
* State is in memory: correct for the single-instance deployment documented in
  ``docs/PRODUCTION.md``. Put a shared store (Redis) behind it before scaling out.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.requests import cookie_parser
from starlette.responses import JSONResponse

from production.accounts import AccountError, EmailTaken, make_session, read_session
from voicemem.utils.common import tenant

log = logging.getLogger("voicemem.security")

COOKIE = "vm_session"
SESSION_TTL_S = 12 * 3600
MIN_KEY_LEN = 16
PROTECTED = ("/backend", "/account", "/api-docs", "/openapi.json", "/metrics")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,255}$")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        raise RuntimeError(f"{name} must be an integer") from None


@dataclass(frozen=True)
class Settings:
    api_keys: tuple[str, ...] = ()
    secret_key: str = ""            # signs user sessions; enables accounts
    allow_signup: bool = False
    auth_disabled: bool = False
    allowed_origins: frozenset[str] = frozenset()
    rate_per_min: int = 120          # protected HTTP requests per client
    login_per_min: int = 10          # login attempts per client
    authfail_per_min: int = 30       # rejected credentials per client
    ws_connects_per_min: int = 30
    max_ws_per_client: int = 4
    max_body_bytes: int = 1_000_000
    max_ws_message_bytes: int = 1_000_000

    @classmethod
    def from_env(cls) -> "Settings":
        keys = tuple(k.strip() for k in os.environ.get("VOICEMEM_API_KEYS", "").split(",") if k.strip())
        origins = frozenset(
            o.strip().rstrip("/") for o in os.environ.get("VOICEMEM_ALLOWED_ORIGINS", "").split(",") if o.strip()
        )
        return cls(
            api_keys=keys,
            secret_key=os.environ.get("VOICEMEM_SECRET_KEY", "").strip(),
            allow_signup=os.environ.get("VOICEMEM_ALLOW_SIGNUP", "0") == "1",
            auth_disabled=os.environ.get("VOICEMEM_AUTH_DISABLED", "0") == "1",
            allowed_origins=origins,
            rate_per_min=_env_int("VOICEMEM_RATE_LIMIT_PER_MIN", 120),
            login_per_min=_env_int("VOICEMEM_LOGIN_LIMIT_PER_MIN", 10),
            max_ws_per_client=_env_int("VOICEMEM_MAX_WS_PER_CLIENT", 4),
            max_body_bytes=_env_int("VOICEMEM_MAX_BODY_BYTES", 1_000_000),
            max_ws_message_bytes=_env_int("VOICEMEM_MAX_WS_MESSAGE_BYTES", 1_000_000),
        )

    def validate(self) -> None:
        """Fail closed: a public deployment must not start without credentials."""
        if self.auth_disabled:
            log.warning("VOICEMEM_AUTH_DISABLED=1: authentication is OFF. Local development only.")
            return
        if self.secret_key and len(self.secret_key) < 32:
            raise RuntimeError("VOICEMEM_SECRET_KEY must be at least 32 characters.")
        if not self.api_keys and not self.secret_key:
            raise RuntimeError(
                "VOICEMEM_API_KEYS is empty and VOICEMEM_SECRET_KEY is not set. Set one or both, e.g.\n"
                "  python -c \"import secrets; print(secrets.token_urlsafe(32))\"\n"
                "or set VOICEMEM_AUTH_DISABLED=1 for local development."
            )
        short = [k for k in self.api_keys if len(k) < MIN_KEY_LEN]
        if short:
            raise RuntimeError(f"Every key in VOICEMEM_API_KEYS must be at least {MIN_KEY_LEN} characters.")


# ── session tokens ───────────────────────────────────────────────────────────
# token = "<expiry>.<key id>.<hmac>", signed with the key itself. No extra secret
# to manage, sessions survive a restart, and removing a key revokes its sessions.

def _key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def make_token(key: str, ttl: int = SESSION_TTL_S, now: float | None = None) -> str:
    msg = f"{int((now if now is not None else time.time()) + ttl)}.{_key_id(key)}"
    return f"{msg}.{hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()}"


def verify_token(token: str, keys: tuple[str, ...], now: float | None = None) -> bool:
    parts = token.split(".")
    if len(parts) != 3 or not parts[0].isdigit():
        return False
    if int(parts[0]) < (now if now is not None else time.time()):
        return False
    msg = f"{parts[0]}.{parts[1]}"
    for key in keys:
        if _key_id(key) == parts[1]:
            want = hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()
            return hmac.compare_digest(want, parts[2])
    return False


def key_matches(candidate: str, keys: tuple[str, ...]) -> bool:
    ok = False
    for key in keys:                      # no early exit: same work for every guess
        ok |= hmac.compare_digest(candidate.encode(), key.encode())
    return ok


class RateLimiter:
    """Sliding window per (bucket, client). In memory, single instance."""

    MAX_KEYS = 20_000

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def allow(self, bucket: str, client: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        if len(self._hits) > self.MAX_KEYS:
            self._hits = defaultdict(deque, {k: v for k, v in self._hits.items() if v and now - v[-1] < window})
        q = self._hits[(bucket, client)]
        while q and now - q[0] >= window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


class SecurityMiddleware:
    def __init__(self, app, settings: Settings | None = None, accounts=None) -> None:
        self.app = app
        self.s = settings or Settings.from_env()
        self.s.validate()
        self.accounts = accounts
        if self.s.secret_key and accounts is None and not self.s.auth_disabled:
            raise RuntimeError("VOICEMEM_SECRET_KEY is set but no accounts store was given.")
        self.limiter = RateLimiter()
        self._ws_active: dict[str, int] = defaultdict(int)

    # ── entry ────────────────────────────────────────────────────────────────
    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind == "http":
            await self._http(scope, receive, send)
        elif kind == "websocket":
            await self._ws(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _headers(scope) -> dict[str, str]:
        out: dict[str, str] = {}
        for k, v in scope["headers"]:
            out.setdefault(k.decode("latin-1").lower(), v.decode("latin-1"))
        return out

    @staticmethod
    def _client(scope) -> str:
        c = scope.get("client")
        return c[0] if c else "unknown"

    @staticmethod
    def _is_protected(path: str) -> bool:
        return any(path == p or path.startswith(p + "/") for p in PROTECTED)

    @staticmethod
    def _is_https(scope, h) -> bool:
        return scope.get("scheme") in ("https", "wss") or h.get("x-forwarded-proto") == "https"

    def _origin_ok(self, h) -> bool:
        origin = h.get("origin")
        if not origin:                    # non-browser client; browsers always send it on WS and POST
            return True
        if origin.rstrip("/") in self.s.allowed_origins:
            return True
        host = h.get("host", "")
        return bool(host) and urlsplit(origin).netloc.lower() == host.lower()

    def _identify(self, h) -> str | None:
        """Who is calling: ``u<id>`` for a signed-in user, ``k<keyid>`` for an API key, else None."""
        keys = self.s.api_keys
        auth = h.get("authorization", "")
        for cand in (auth[7:].strip() if auth[:7].lower() == "bearer " else "", h.get("x-api-key", "").strip()):
            if cand and key_matches(cand, keys):
                return "k" + _key_id(cand)
        token = cookie_parser(h.get("cookie", "")).get(COOKIE)
        if not token:
            return None
        if self.accounts and self.s.secret_key:
            got = read_session(self.s.secret_key, token)
            if got:
                uid, version = got
                return uid if self.accounts.token_version(uid) == version else None
        if verify_token(token, keys):
            return "k" + token.split(".")[1]
        return None

    def _authenticated(self, h) -> bool:
        return self.s.auth_disabled or self._identify(h) is not None

    def _response_headers(self, scope, h, path: str) -> list[tuple[bytes, bytes]]:
        host = h.get("host", "")
        host = host if _HOST_RE.match(host) else ""
        ws = f" ws://{host} wss://{host}" if host else ""
        csp = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self' data:; "
            f"connect-src 'self'{ws}; worker-src 'self' blob:; object-src 'none'; "
            "base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
        )
        out = {
            "Content-Security-Policy": csp,
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "SAMEORIGIN",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "camera=(self), microphone=(self), geolocation=(), payment=()",
            "Cross-Origin-Opener-Policy": "same-origin",
        }
        if self._is_https(scope, h):
            out["Strict-Transport-Security"] = "max-age=31536000"
        if path.startswith("/auth/") or path.startswith("/backend/api/"):
            out["Cache-Control"] = "no-store"
        return [(k.lower().encode(), v.encode()) for k, v in out.items()]

    def _wrap_send(self, send, extra):
        async def _send(msg):
            if msg["type"] == "http.response.start":
                have = {k.lower() for k, _ in msg.get("headers", [])}
                msg = {**msg, "headers": list(msg.get("headers", [])) + [x for x in extra if x[0] not in have]}
            await send(msg)
        return _send

    # ── HTTP ─────────────────────────────────────────────────────────────────
    async def _http(self, scope, receive, send):
        h = self._headers(scope)
        path = scope["path"]
        send = self._wrap_send(send, self._response_headers(scope, h, path))
        client = self._client(scope)

        if "//" in path or any(seg in (".", "..") for seg in path.split("/")):
            return await JSONResponse({"error": "bad_path"}, status_code=400)(scope, receive, send)

        if path.startswith("/auth/"):
            return await self._auth(scope, receive, send, h, client, path)

        who = None
        if self._is_protected(path):
            who = self._identify(h)
            if who is None and not self.s.auth_disabled:
                log.warning("auth rejected client=%s path=%s", client, path)
                limited = not self.limiter.allow("authfail", client, self.s.authfail_per_min)
                resp = JSONResponse({"error": "rate_limited" if limited else "unauthorized"},
                                    status_code=429 if limited else 401)
                return await resp(scope, receive, send)
            if scope["method"] not in SAFE_METHODS and not self._origin_ok(h):
                return await JSONResponse({"error": "bad_origin"}, status_code=403)(scope, receive, send)
            if not self.limiter.allow("api", client, self.s.rate_per_min):
                return await JSONResponse({"error": "rate_limited"}, status_code=429,
                                          headers={"Retry-After": "60"})(scope, receive, send)

        declared = h.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.s.max_body_bytes:
            return await JSONResponse({"error": "body_too_large"}, status_code=413)(scope, receive, send)

        seen = 0
        started = False
        too_large = False

        async def capped_receive():
            nonlocal seen, too_large
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.s.max_body_bytes:
                    too_large = True
                    raise _BodyTooLarge
            return msg

        async def tracking_send(msg):
            nonlocal started
            if too_large and not started:
                return                      # the app answered 400 to our abort; we send 413 below
            started = started or msg["type"] == "http.response.start"
            await send(msg)

        bound = tenant.bind(who) if who else None
        try:
            with contextlib.suppress(_BodyTooLarge):
                await self.app(scope, capped_receive, tracking_send)
        finally:
            if bound is not None:
                tenant.reset(bound)
        if too_large and not started:
            await JSONResponse({"error": "body_too_large"}, status_code=413)(scope, receive, send)

    @staticmethod
    async def _read_json(receive) -> dict | None:
        """Small JSON object from the body; None if it is too big, {} if it is not an object."""
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if len(body) > 4096:
                return None
            if not msg.get("more_body"):
                break
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    async def _auth(self, scope, receive, send, h, client, path):
        method = scope["method"]
        if path == "/auth/me" and method == "GET":
            who = self._identify(h)
            ok = self.s.auth_disabled or who is not None
            body = {"authenticated": ok, "auth_required": not self.s.auth_disabled,
                    "accounts": self.accounts is not None,
                    "signup_allowed": bool(self.accounts and self.s.allow_signup)}
            if who and who.startswith("u") and self.accounts:
                user = self.accounts.get(who)
                if user:
                    body["user"] = {"email": user["email"], "consented": user["consented"]}
            return await JSONResponse(body, status_code=200 if ok else 401)(scope, receive, send)
        if method != "POST" or path not in ("/auth/login", "/auth/logout", "/auth/signup"):
            return await JSONResponse({"error": "not_found"}, status_code=404)(scope, receive, send)
        if not self._origin_ok(h):
            return await JSONResponse({"error": "bad_origin"}, status_code=403)(scope, receive, send)

        secure = "; Secure" if self._is_https(scope, h) else ""

        def cookie(token: str, max_age: int) -> dict:
            return {"Set-Cookie": f"{COOKIE}={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict{secure}"}

        if path == "/auth/logout":
            return await JSONResponse({"ok": True}, headers=cookie("", 0))(scope, receive, send)

        if not self.limiter.allow("login", client, self.s.login_per_min):
            return await JSONResponse({"error": "rate_limited"}, status_code=429,
                                      headers={"Retry-After": "60"})(scope, receive, send)
        data = await self._read_json(receive)
        if data is None:
            return await JSONResponse({"error": "body_too_large"}, status_code=413)(scope, receive, send)

        if path == "/auth/signup":
            if not (self.accounts and self.s.allow_signup):
                return await JSONResponse({"error": "signup_closed"}, status_code=403)(scope, receive, send)
            try:
                user = await asyncio.to_thread(
                    self.accounts.create, str(data.get("email", "")), str(data.get("password", "")))
            except EmailTaken:
                return await JSONResponse({"error": "email_taken"}, status_code=409)(scope, receive, send)
            except AccountError as exc:
                return await JSONResponse({"error": "invalid", "message": str(exc)},
                                          status_code=400)(scope, receive, send)
            token = make_session(self.s.secret_key, user["id"], 0)
            return await JSONResponse({"ok": True}, status_code=201,
                                      headers=cookie(token, SESSION_TTL_S))(scope, receive, send)

        if self.accounts and "email" in data:
            user = await asyncio.to_thread(
                self.accounts.authenticate, str(data.get("email", "")), str(data.get("password", "")))
            if not user:
                log.warning("login failed client=%s", client)
                return await JSONResponse({"error": "invalid_credentials"}, status_code=401)(scope, receive, send)
            token = make_session(self.s.secret_key, user["id"], self.accounts.token_version(user["id"]))
            return await JSONResponse({"ok": True}, headers=cookie(token, SESSION_TTL_S))(scope, receive, send)

        key = str(data.get("key", "")).strip()
        if self.s.auth_disabled or not key_matches(key, self.s.api_keys):
            log.warning("login failed client=%s", client)
            return await JSONResponse({"error": "invalid_key"}, status_code=401)(scope, receive, send)
        return await JSONResponse({"ok": True}, headers=cookie(make_token(key), SESSION_TTL_S))(scope, receive, send)

    # ── WebSocket ────────────────────────────────────────────────────────────
    async def _ws(self, scope, receive, send):
        path = scope["path"]
        if not self._is_protected(path):
            return await self.app(scope, receive, send)
        h = self._headers(scope)
        client = self._client(scope)

        async def deny(why: str, code: int = 1008):
            log.warning("ws rejected client=%s path=%s reason=%s", client, path, why)
            await send({"type": "websocket.close", "code": code})   # before accept -> HTTP 403

        if "//" in path or any(seg in (".", "..") for seg in path.split("/")):
            return await deny("bad_path")
        if not self._origin_ok(h):
            return await deny("bad_origin")
        who = self._identify(h)
        if who is None and not self.s.auth_disabled:
            self.limiter.allow("authfail", client, self.s.authfail_per_min)
            return await deny("unauthorized")
        # Voice and voiceprints are personal data: a signed-in user must agree first.
        if who and who.startswith("u") and self.accounts and not self.accounts.has_consent(who):
            return await deny("consent_required")
        if not self.limiter.allow("ws", client, self.s.ws_connects_per_min):
            return await deny("rate_limited")
        if self._ws_active[client] >= self.s.max_ws_per_client:
            return await deny("too_many_connections")

        oversized = False

        async def guarded_receive():
            nonlocal oversized
            if oversized:
                return {"type": "websocket.disconnect", "code": 1009}
            msg = await receive()
            if msg["type"] == "websocket.receive":
                size = len(msg.get("bytes") or b"") or len((msg.get("text") or "").encode())
                if size > self.s.max_ws_message_bytes:
                    oversized = True
                    log.warning("ws message too large client=%s size=%d", client, size)
                    with contextlib.suppress(Exception):  # peer may already be gone
                        await send({"type": "websocket.close", "code": 1009})
                    return {"type": "websocket.disconnect", "code": 1009}
            return msg

        self._ws_active[client] += 1
        bound = tenant.bind(who) if who else None
        try:
            await self.app(scope, guarded_receive, send)
        finally:
            if bound is not None:
                tenant.reset(bound)
            self._ws_active[client] -= 1
            if self._ws_active[client] <= 0:
                self._ws_active.pop(client, None)


class _BodyTooLarge(Exception):
    pass
