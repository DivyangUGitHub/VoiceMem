"""Sign-up to delete-my-data, through the real production app with a stub runtime."""

import importlib
import os
import shutil
import sys
import types
import zipfile

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voicemem.utils.common import tenant

KEY = "k" * 32
SECRET = "s" * 40
PW = "correct horse battery"


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Returns (module, make_client). Each client has its own cookie jar, like a separate browser."""
    root = tmp_path / "spaces"
    root.mkdir()
    legacy = FastAPI()

    @legacy.get("/api/whoami")
    def whoami():
        return {"tenant": tenant.current.get()}

    @legacy.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        await sock.send_json({"tenant": tenant.current.get()})
        await sock.close()

    stub = types.ModuleType("run")
    stub.app = legacy

    def purge_tenant(tenant_id):
        gone = [d for d in root.glob(tenant_id + "_*") if d.is_dir()]
        for d in gone:
            shutil.rmtree(d)
        return len(gone)

    stub.purge_tenant = purge_tenant
    monkeypatch.setitem(sys.modules, "run", stub)
    for name, value in {
        "VOICEMEM_API_KEYS": KEY, "VOICEMEM_SECRET_KEY": SECRET, "VOICEMEM_ALLOW_SIGNUP": "1",
        "DATABASE_URL": os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path}/accounts.db",
        "VOICEMEM_MEMORYSPACE_ROOT": str(root),
        "VOICEMEM_SCRYPT_N": "1024", "OPENAI_API_KEY": "sk-test",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("VOICEMEM_AUTH_DISABLED", raising=False)
    sys.modules.pop("production.asgi", None)
    mod = importlib.import_module("production.asgi")
    mod.root = root
    yield mod, lambda: TestClient(mod.app)
    if mod.accounts:
        from production.accounts import metadata
        metadata.drop_all(mod.accounts.engine)       # clean slate on a shared Postgres
        mod.accounts.engine.dispose()


def signup(client, email="ann@example.com", password=PW):
    r = client.post("/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return client.get("/auth/me").json()["user"]


def make_space(mod, uid, name="main"):
    d = mod.root / f"{uid}_{name}"
    d.mkdir()
    (d / "note.txt").write_text(f"secret of {uid}")
    return d


def uid_of(client):
    return client.get("/backend/api/whoami").json()["tenant"]


def test_signup_is_closed_unless_enabled(world, monkeypatch):
    mod, client = world
    monkeypatch.setenv("VOICEMEM_ALLOW_SIGNUP", "0")
    sys.modules.pop("production.asgi", None)
    c = TestClient(importlib.import_module("production.asgi").app)
    assert c.post("/auth/signup", json={"email": "a@b.co", "password": PW}).json() == {"error": "signup_closed"}


def test_signup_signs_you_in_and_each_user_gets_their_own_tenant(world):
    mod, client = world
    ann, bob = client(), client()
    signup(ann)
    signup(bob, "bob@example.com")
    a, b = uid_of(ann), uid_of(bob)
    assert a.startswith("u") and b.startswith("u") and a != b
    assert client().get("/backend/api/whoami").status_code == 401           # no cookie, no access
    keyed = client().get("/backend/api/whoami", headers={"Authorization": f"Bearer {KEY}"}).json()["tenant"]
    assert keyed.startswith("k") and keyed not in (a, b)


def test_bad_signups_and_duplicate_email(world):
    _, client = world
    c = client()
    assert c.post("/auth/signup", json={"email": "nope", "password": PW}).status_code == 400
    assert c.post("/auth/signup", json={"email": "a@b.co", "password": "short"}).status_code == 400
    assert c.post("/auth/signup", json={"email": "a@b.co", "password": PW}).status_code == 201
    assert client().post("/auth/signup", json={"email": "A@B.co", "password": PW}).status_code == 409


def test_login_wrong_password_and_unknown_email_look_the_same(world):
    _, client = world
    signup(client())
    c = client()
    bad = c.post("/auth/login", json={"email": "ann@example.com", "password": "wrong password!"})
    unknown = c.post("/auth/login", json={"email": "who@example.com", "password": PW})
    assert bad.status_code == unknown.status_code == 401 and bad.json() == unknown.json()
    assert c.post("/auth/login", json={"email": "ann@example.com", "password": PW}).status_code == 200
    assert c.get("/backend/api/whoami").status_code == 200


def test_voice_needs_consent_first(world):
    _, client = world
    c = client()
    signup(c)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/backend/ws"):
            pass
    assert c.post("/account/consent", json={"agree": True}).json() == {"consented": True}
    with c.websocket_connect("/backend/ws") as ws:
        assert ws.receive_json()["tenant"] == uid_of(c)
    c.post("/account/consent", json={"agree": False})
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/backend/ws"):
            pass


def test_changing_password_signs_out_old_sessions(world):
    _, client = world
    old = client()
    signup(old)
    assert old.post("/account/password", json={"old": "wrong password!", "new": "another long one"}).status_code == 401
    assert old.post("/account/password", json={"old": PW, "new": "short"}).status_code == 400
    assert old.post("/account/password", json={"old": PW, "new": "another long one"}).json()["signed_out"]
    assert old.get("/backend/api/whoami").status_code == 401
    other = client()
    assert other.post("/auth/login", json={"email": "ann@example.com", "password": PW}).status_code == 401
    assert other.post("/auth/login", json={"email": "ann@example.com", "password": "another long one"}).status_code == 200


def test_a_stolen_session_dies_when_password_changes(world):
    _, client = world
    victim = client()
    signup(victim)
    stolen = victim.cookies.get("vm_session")
    victim.post("/account/password", json={"old": PW, "new": "another long one"})
    thief = client()
    thief.cookies.set("vm_session", stolen)
    assert thief.get("/backend/api/whoami").status_code == 401


def test_export_contains_only_your_own_data(world):
    mod, client = world
    ann, bob = client(), client()
    a = signup(ann)["email"]
    signup(bob, "bob@example.com")
    ua, ub = uid_of(ann), uid_of(bob)
    make_space(mod, ua)
    make_space(mod, ub)
    r = ann.get("/account/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    zpath = mod.root.parent / "out.zip"
    zpath.write_bytes(r.content)
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        assert "account.json" in names and a in z.read("account.json").decode()
        assert any(n.endswith("note.txt") and ua in n for n in names)
        assert not any(ub in n for n in names)
        assert not any(ub in z.read(n).decode(errors="ignore") for n in names if n.endswith(".txt"))


def test_delete_removes_everything_and_only_yours(world):
    mod, client = world
    ann, bob = client(), client()
    signup(ann)
    signup(bob, "bob@example.com")
    ua, ub = uid_of(ann), uid_of(bob)
    mine, theirs = make_space(mod, ua), make_space(mod, ub)
    assert ann.post("/account/delete", json={"password": "wrong password!"}).status_code == 401
    assert mine.exists()
    r = ann.post("/account/delete", json={"password": PW})
    assert r.json() == {"deleted": True, "spaces_removed": 1}
    assert not mine.exists() and theirs.exists()
    assert ann.get("/backend/api/whoami").status_code == 401        # cookie is dead
    assert client().post("/auth/login", json={"email": "ann@example.com", "password": PW}).status_code == 401
    assert bob.get("/backend/api/whoami").status_code == 200        # Bob is untouched


def test_key_holders_have_no_account_endpoints(world):
    _, client = world
    c = client()
    h = {"Authorization": f"Bearer {KEY}"}
    assert c.get("/account/", headers=h).status_code == 403
    assert c.get("/account/export", headers=h).status_code == 403
    assert c.post("/account/delete", json={"password": PW}, headers=h).status_code == 403


def test_cross_site_requests_cannot_delete_or_change_anything(world):
    mod, client = world
    c = client()
    signup(c)
    evil = {"Origin": "https://evil.example"}
    assert c.post("/account/delete", json={"password": PW}, headers=evil).status_code == 403
    assert c.post("/account/consent", json={"agree": True}, headers=evil).status_code == 403
    assert c.post("/auth/login", json={"email": "ann@example.com", "password": PW}, headers=evil).status_code == 403
    assert c.get("/account/").status_code == 200                    # the account still exists


def test_a_signed_out_browser_tab_is_sent_to_the_sign_in_page(world):
    _, client = world
    c = client()
    r = c.get("/backend/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"
    assert c.get("/backend/api/whoami").status_code == 401                  # scripts still get a plain 401
    assert r.headers["cache-control"] == "no-store"                         # a cached console must not outlive sign-out


def test_account_endpoints_need_sign_in(world):
    _, client = world
    c = client()
    for method, path in [("get", "/account/"), ("get", "/account/export"), ("post", "/account/delete")]:
        assert getattr(c, method)(path).status_code == 401


def test_readyz_checks_the_database(world):
    mod, client = world
    mod.accounts.engine.dispose()
    body = client().get("/readyz").json()
    assert body["checks"]["database"] == "ok"


def test_start_refuses_a_short_secret(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICEMEM_SECRET_KEY", "too short")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/a.db")
    monkeypatch.setitem(sys.modules, "run", None)
    sys.modules.pop("production.asgi", None)
    with pytest.raises(RuntimeError, match="at least 32"):
        importlib.import_module("production.asgi")
