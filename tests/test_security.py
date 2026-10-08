"""Security layer tests. Run without any model: a stub app stands in for web/run.py."""

import asyncio
import time

import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient

from production.security import (
    COOKIE,
    SecurityMiddleware,
    Settings,
    make_token,
    verify_token,
)

KEY = "k" * 32
OTHER = "z" * 32
AUTH = {"Authorization": f"Bearer {KEY}"}


def build(**overrides) -> TestClient:
    legacy = FastAPI()

    @legacy.get("/api/memories")
    def memories():
        return {"left": [], "right": []}

    @legacy.post("/api/spaces")
    def spaces(body: dict):
        return {"ok": True}

    @legacy.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        try:
            while True:
                await sock.send_text(await sock.receive_text())
        except WebSocketDisconnect:
            pass

    app = FastAPI()

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    app.mount("/backend", legacy)
    settings = Settings(api_keys=(KEY,), **overrides)
    return TestClient(SecurityMiddleware(app, settings))


# ── start-up policy ──────────────────────────────────────────────────────────
def test_refuses_to_start_without_keys():
    with pytest.raises(RuntimeError, match="VOICEMEM_API_KEYS"):
        Settings().validate()


def test_refuses_short_keys():
    with pytest.raises(RuntimeError, match="at least"):
        Settings(api_keys=("short",)).validate()


def test_auth_can_be_disabled_explicitly():
    Settings(auth_disabled=True).validate()


# ── HTTP auth ────────────────────────────────────────────────────────────────
def test_public_paths_need_no_credentials():
    c = build()
    assert c.get("/healthz").status_code == 200
    assert c.get("/auth/me").status_code == 401  # reports state, never 404s


def test_protected_path_rejects_missing_and_wrong_credentials():
    c = build()
    assert c.get("/backend/api/memories").status_code == 401
    assert c.get("/backend/api/memories", headers={"Authorization": f"Bearer {OTHER}"}).status_code == 401
    assert c.get("/backend/api/memories", headers={"X-API-Key": OTHER}).status_code == 401


def test_bearer_and_api_key_headers_work():
    c = build()
    assert c.get("/backend/api/memories", headers=AUTH).status_code == 200
    assert c.get("/backend/api/memories", headers={"X-API-Key": KEY}).status_code == 200


def test_login_sets_httponly_cookie_that_grants_access():
    c = build()
    r = c.post("/auth/login", json={"key": KEY})
    assert r.status_code == 200
    set_cookie = r.headers["set-cookie"]
    assert "HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie
    assert c.get("/backend/api/memories").status_code == 200
    assert c.get("/auth/me").json()["authenticated"] is True


def test_login_rejects_wrong_key_and_garbage_body():
    c = build()
    assert c.post("/auth/login", json={"key": OTHER}).status_code == 401
    assert c.post("/auth/login", content=b"not json").status_code == 401
    assert c.post("/auth/login", json={}).status_code == 401


def test_logout_clears_cookie():
    c = build()
    c.post("/auth/login", json={"key": KEY})
    c.post("/auth/logout")
    assert c.get("/backend/api/memories").status_code == 401


def test_tampered_expired_and_revoked_tokens_fail():
    keys = (KEY,)
    good = make_token(KEY)
    assert verify_token(good, keys)
    exp, kid, sig = good.split(".")
    assert not verify_token(f"{exp}.{kid}.{'0' * len(sig)}", keys)
    assert not verify_token(f"{int(exp) + 999}.{kid}.{sig}", keys)          # expiry edited
    assert not verify_token(make_token(KEY, ttl=-1), keys)                   # expired
    assert not verify_token(good, (OTHER,))                                  # key removed = revoked
    assert not verify_token("garbage", keys)


def test_tampered_cookie_is_rejected_by_the_app():
    c = build()
    c.cookies.set(COOKIE, make_token(KEY)[:-4] + "0000")
    assert c.get("/backend/api/memories").status_code == 401


# ── CSRF / origin ────────────────────────────────────────────────────────────
def test_cross_origin_post_is_blocked_even_with_valid_credentials():
    c = build()
    r = c.post("/backend/api/spaces", json={}, headers={**AUTH, "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_same_origin_and_allowlisted_origin_posts_pass():
    c = build(allowed_origins=frozenset({"https://app.example.com"}))
    assert c.post("/backend/api/spaces", json={}, headers={**AUTH, "Origin": "http://testserver"}).status_code == 200
    assert c.post("/backend/api/spaces", json={}, headers={**AUTH, "Origin": "https://app.example.com"}).status_code == 200


def test_null_origin_is_blocked():
    c = build()
    assert c.post("/backend/api/spaces", json={}, headers={**AUTH, "Origin": "null"}).status_code == 403


def test_login_from_foreign_origin_is_blocked():
    c = build()
    assert c.post("/auth/login", json={"key": KEY}, headers={"Origin": "https://evil.example"}).status_code == 403


# ── limits ───────────────────────────────────────────────────────────────────
def test_rate_limit_returns_429_with_retry_after():
    c = build(rate_per_min=3)
    codes = [c.get("/backend/api/memories", headers=AUTH).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    assert c.get("/backend/api/memories", headers=AUTH).headers["retry-after"] == "60"


def test_login_attempts_are_rate_limited():
    c = build(login_per_min=3)
    codes = [c.post("/auth/login", json={"key": OTHER}).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]


def test_repeated_bad_credentials_get_throttled():
    c = build(authfail_per_min=3)
    codes = [c.get("/backend/api/memories").status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]


def test_oversized_body_is_rejected():
    c = build(max_body_bytes=100)
    assert c.post("/backend/api/spaces", json={"x": "a" * 500}, headers=AUTH).status_code == 413


def test_oversized_chunked_body_is_rejected():
    c = build(max_body_bytes=100)

    def chunks():
        for _ in range(10):
            yield b"a" * 50

    assert c.post("/backend/api/spaces", content=chunks(), headers={**AUTH, "content-type": "application/json"}).status_code == 413


# ── headers ──────────────────────────────────────────────────────────────────
def test_security_headers_on_every_response():
    r = build().get("/healthz")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    csp = r.headers["content-security-policy"]
    assert "object-src 'none'" in csp and "frame-ancestors 'self'" in csp
    assert "ws://testserver" in csp and "wss://testserver" in csp
    assert "strict-transport-security" not in r.headers          # plain http: no HSTS


def test_hsts_only_when_https():
    r = build().get("/healthz", headers={"X-Forwarded-Proto": "https"})
    assert "max-age=31536000" in r.headers["strict-transport-security"]


def test_api_responses_are_not_cached():
    assert build().get("/backend/api/memories", headers=AUTH).headers["cache-control"] == "no-store"


def test_hostile_host_header_cannot_inject_into_csp():
    r = build().get("/healthz", headers={"Host": "a.com; script-src *"})
    assert "script-src *" not in r.headers["content-security-policy"]


# ── path tricks ──────────────────────────────────────────────────────────────
def _raw_get(app, path: str) -> int:
    """Send a request with the path exactly as given (HTTP clients normalise '..')."""
    status = []

    async def run():
        scope = {"type": "http", "method": "GET", "path": path, "raw_path": path.encode(),
                 "headers": [(b"host", b"testserver")], "client": ("1.2.3.4", 1), "scheme": "http",
                 "query_string": b"", "root_path": ""}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            if msg["type"] == "http.response.start":
                status.append(msg["status"])

        await app(scope, receive, send)

    asyncio.run(run())
    return status[0]


@pytest.mark.parametrize("path", ["//backend/api/memories", "/x/../backend/api/memories", "/backend/./api/memories"])
def test_dot_segments_and_double_slashes_are_refused(path):
    assert _raw_get(build().app, path) == 400


# ── WebSocket ────────────────────────────────────────────────────────────────
def test_ws_without_credentials_is_refused():
    c = build()
    with pytest.raises(WebSocketDisconnect), c.websocket_connect("/backend/ws"):
        pass


def test_ws_with_credentials_echoes():
    c = build()
    with c.websocket_connect("/backend/ws", headers=AUTH) as ws:
        ws.send_text("hello")
        assert ws.receive_text() == "hello"


def test_ws_with_session_cookie_works():
    c = build()
    c.post("/auth/login", json={"key": KEY})
    with c.websocket_connect("/backend/ws") as ws:
        ws.send_text("hi")
        assert ws.receive_text() == "hi"


def test_ws_cross_site_hijack_is_blocked_even_with_cookie():
    c = build()
    c.post("/auth/login", json={"key": KEY})
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/backend/ws", headers={"Origin": "https://evil.example"}):
            pass


def test_ws_oversized_message_closes_with_1009():
    c = build(max_ws_message_bytes=10)
    with c.websocket_connect("/backend/ws", headers=AUTH) as ws:
        ws.send_text("x" * 50)
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
        assert e.value.code == 1009


def test_ws_connection_cap_per_client():
    c = build(max_ws_per_client=2)
    with c.websocket_connect("/backend/ws", headers=AUTH), c.websocket_connect("/backend/ws", headers=AUTH):
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/backend/ws", headers=AUTH):
                pass
    # slots are released when sockets close
    with c.websocket_connect("/backend/ws", headers=AUTH) as ws:
        ws.send_text("again")
        assert ws.receive_text() == "again"


def test_token_helpers_are_fast_enough_not_to_dos():
    t = time.perf_counter()
    for _ in range(200):
        verify_token(make_token(KEY), (KEY,))
    assert time.perf_counter() - t < 2


# ── production/asgi.py wiring ────────────────────────────────────────────────
def _load_asgi(monkeypatch, **env):
    """Import production.asgi with a stub in place of the heavy web/run.py runtime."""
    import importlib
    import sys
    import types

    legacy = FastAPI()

    @legacy.get("/api/memories")
    def memories():
        return {"left": [], "right": []}

    stub = types.ModuleType("run")
    stub.app = legacy
    monkeypatch.setitem(sys.modules, "run", stub)
    for name in ("VOICEMEM_API_KEYS", "VOICEMEM_AUTH_DISABLED", "VOICEMEM_ENABLE_DOCS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    sys.modules.pop("production.asgi", None)
    return importlib.import_module("production.asgi")


def test_asgi_fails_before_loading_the_runtime_when_unconfigured(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "run", None)  # importing it would raise ImportError instead
    for name in ("VOICEMEM_API_KEYS", "VOICEMEM_AUTH_DISABLED"):
        monkeypatch.delenv(name, raising=False)
    sys.modules.pop("production.asgi", None)
    import importlib

    with pytest.raises(RuntimeError, match="VOICEMEM_API_KEYS"):
        importlib.import_module("production.asgi")


def test_asgi_protects_the_legacy_runtime(monkeypatch):
    mod = _load_asgi(monkeypatch, VOICEMEM_API_KEYS=KEY)
    c = TestClient(mod.app)
    assert c.get("/healthz").status_code == 200
    assert c.get("/backend/api/memories").status_code == 401
    assert c.get("/backend/api/memories", headers=AUTH).status_code == 200


def test_asgi_docs_are_off_unless_enabled(monkeypatch):
    c = TestClient(_load_asgi(monkeypatch, VOICEMEM_API_KEYS=KEY).app)
    assert c.get("/api-docs", headers=AUTH).status_code == 404
    assert c.get("/openapi.json", headers=AUTH).status_code == 404
    c = TestClient(_load_asgi(monkeypatch, VOICEMEM_API_KEYS=KEY, VOICEMEM_ENABLE_DOCS="1").app)
    assert c.get("/openapi.json", headers=AUTH).status_code == 200
    assert c.get("/openapi.json").status_code == 401
