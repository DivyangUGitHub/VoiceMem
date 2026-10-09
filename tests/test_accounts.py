import pytest

from production import accounts
from production.accounts import Accounts, AccountError, EmailTaken, check_password, hash_password, make_session, read_session

PW = "correct horse battery"


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setenv("VOICEMEM_SCRYPT_N", "1024")


@pytest.fixture
def db(tmp_path):
    return Accounts(f"sqlite:///{tmp_path}/a.db")


def test_password_hash_roundtrip_and_salt():
    h = hash_password(PW)
    assert check_password(PW, h) and not check_password(PW + "x", h)
    assert h != hash_password(PW)                     # fresh salt each time
    assert not check_password(PW, "garbage") and not check_password(PW, "md5$1$aa$bb")


def test_signup_then_login(db):
    user = db.create("Ann@Example.com", PW)
    assert user["email"] == "ann@example.com" and user["id"].startswith("u") and not user["consented"]
    assert db.authenticate("ANN@example.com", PW)["id"] == user["id"]
    assert db.authenticate("ann@example.com", "wrong password!") is None
    assert db.authenticate("nobody@example.com", PW) is None


def test_duplicate_email_and_bad_input(db):
    db.create("a@b.co", PW)
    with pytest.raises(EmailTaken):
        db.create("A@B.CO", PW)
    for email, pw in [("not-an-email", PW), ("a@b.co", "short"), ("x@y.co", "p" * 201), ("", PW)]:
        with pytest.raises(AccountError):
            db.create(email, pw)


def test_huge_password_is_refused_without_hashing(db):
    db.create("a@b.co", PW)
    assert db.authenticate("a@b.co", "p" * 10_000) is None


def test_consent_can_be_given_and_withdrawn(db):
    uid = db.create("a@b.co", PW)["id"]
    assert not db.has_consent(uid)
    db.give_consent(uid)
    assert db.has_consent(uid) and db.get(uid)["consented"]
    db.withdraw_consent(uid)
    assert not db.has_consent(uid)


def test_changing_password_revokes_sessions(db):
    uid = db.create("a@b.co", PW)["id"]
    assert db.token_version(uid) == 0
    assert not db.change_password(uid, "wrong password!", "another long one")
    assert db.change_password(uid, PW, "another long one")
    assert db.token_version(uid) == 1
    assert db.authenticate("a@b.co", "another long one") and not db.authenticate("a@b.co", PW)


def test_delete_removes_the_user(db):
    uid = db.create("a@b.co", PW)["id"]
    db.delete(uid)
    assert db.get(uid) is None and db.token_version(uid) is None
    db.create("a@b.co", PW)                            # the email is free again


def test_session_token_signature_expiry_and_tampering():
    secret = "s" * 40
    tok = make_session(secret, "uabc123def456", 3, now=1000)
    assert read_session(secret, tok, now=1000) == ("uabc123def456", 3)
    assert read_session(secret, tok, now=1000 + accounts.SESSION_TTL_S + 1) is None
    assert read_session("t" * 40, tok, now=1000) is None
    exp, uid, ver, sig = tok.split(".")
    for forged in (f"{exp}.uother0000000.{ver}.{sig}", f"{exp}.{uid}.99.{sig}", f"{exp}.{uid}.{ver}", "a.b.c.d", ""):
        assert read_session(secret, forged, now=1000) is None


def test_database_url_handles_render_style_urls():
    assert accounts.database_url({"DATABASE_URL": "postgres://u:p@h/db"}) == "postgresql+psycopg://u:p@h/db"
    assert accounts.database_url({"DATABASE_URL": "postgresql://u:p@h/db"}) == "postgresql+psycopg://u:p@h/db"
    assert accounts.database_url({"VOICEMEM_DATA_DIR": "/d"}) == "sqlite:////d/voicemem_accounts.db"
