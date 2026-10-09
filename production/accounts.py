"""User accounts: sign-up, sign-in, consent, password change, delete.

SQLAlchemy Core, so the same code runs on SQLite (development) and Postgres (production,
``DATABASE_URL``). Passwords are hashed with scrypt from the standard library. Sessions are
signed tokens that carry the user's ``token_version``: changing the password or deleting the
account bumps it and every older session stops working.

shortcut: tables are created on start, there are no migrations. Add Alembic before the first
schema change.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Integer, MetaData, String, Table, create_engine, delete, insert, select, text, update,
)
from sqlalchemy.exc import IntegrityError

SESSION_TTL_S = 12 * 3600
MIN_PASSWORD = 10
MAX_PASSWORD = 200          # scrypt is slow on purpose: cap the input so it cannot be abused
SCRYPT_N = 2 ** 15          # tests lower this through the environment
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{2,24}$")

metadata = MetaData()
users = Table(
    "users", metadata,
    Column("id", String(16), primary_key=True),
    Column("email", String(254), nullable=False, unique=True),
    Column("pw_hash", String(300), nullable=False),
    Column("token_version", Integer, nullable=False, default=0),
    Column("consent_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


class AccountError(ValueError):
    """Bad input the caller can show to the user."""


class EmailTaken(AccountError):
    pass


def database_url(env: dict | None = None) -> str:
    env = os.environ if env is None else env
    url = env.get("DATABASE_URL", "").strip()
    if not url:
        data = env.get("VOICEMEM_DATA_DIR", ".")
        return f"sqlite:///{data}/voicemem_accounts.db"
    # Render and Heroku hand out "postgres://"; SQLAlchemy wants a driver name.
    return re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", url)


# ── passwords ────────────────────────────────────────────────────────────────
def _scrypt(password: str, salt: bytes, n: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=8, p=1, dklen=32, maxmem=128 * 1024 * 1024)


def hash_password(password: str) -> str:
    n = int(os.environ.get("VOICEMEM_SCRYPT_N", SCRYPT_N))
    salt = secrets.token_bytes(16)
    return f"scrypt${n}${salt.hex()}${_scrypt(password, salt, n).hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, n, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        return hmac.compare_digest(_scrypt(password, bytes.fromhex(salt), int(n)), bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def _validate(email: str, password: str) -> str:
    email = (email or "").strip().lower()
    if len(email) > 254 or not _EMAIL.match(email):
        raise AccountError("Enter a valid email address.")
    if not MIN_PASSWORD <= len(password or "") <= MAX_PASSWORD:
        raise AccountError(f"Password must be {MIN_PASSWORD} to {MAX_PASSWORD} characters.")
    return email


# ── session tokens ───────────────────────────────────────────────────────────
def make_session(secret: str, uid: str, version: int, ttl: int = SESSION_TTL_S, now: float | None = None) -> str:
    msg = f"{int((now if now is not None else time.time()) + ttl)}.{uid}.{version}"
    return f"{msg}.{hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()}"


def read_session(secret: str, token: str, now: float | None = None) -> tuple[str, int] | None:
    """Return (user id, token version) if the signature and expiry are good."""
    parts = token.split(".")
    if len(parts) != 4 or not parts[0].isdigit() or not parts[2].isdigit():
        return None
    if int(parts[0]) < (now if now is not None else time.time()):
        return None
    want = hmac.new(secret.encode(), ".".join(parts[:3]).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, parts[3]):
        return None
    return parts[1], int(parts[2])


# ── the store ────────────────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(timezone.utc)


class Accounts:
    def __init__(self, url: str | None = None) -> None:
        self.engine = create_engine(url or database_url(), pool_pre_ping=True, future=True)
        metadata.create_all(self.engine)
        self._dummy = hash_password("not-a-real-password")   # equalises timing for unknown emails

    def ping(self) -> None:
        with self.engine.connect() as c:
            c.execute(text("SELECT 1"))

    def create(self, email: str, password: str) -> dict:
        email = _validate(email, password)
        uid = "u" + secrets.token_hex(6)               # also the tenant id: alphanumeric, 13 chars
        try:
            with self.engine.begin() as c:
                c.execute(insert(users).values(
                    id=uid, email=email, pw_hash=hash_password(password), token_version=0, created_at=_now()))
        except IntegrityError:
            raise EmailTaken("An account with this email already exists.") from None
        return self.get(uid)

    def authenticate(self, email: str, password: str) -> dict | None:
        email = (email or "").strip().lower()
        if len(password or "") > MAX_PASSWORD:
            return None
        with self.engine.connect() as c:
            row = c.execute(select(users).where(users.c.email == email)).mappings().first()
        if row is None:
            check_password(password or "", self._dummy)
            return None
        return self._public(row) if check_password(password, row["pw_hash"]) else None

    def get(self, uid: str) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(users).where(users.c.id == uid)).mappings().first()
        return self._public(row) if row else None

    def token_version(self, uid: str) -> int | None:
        with self.engine.connect() as c:
            return c.execute(select(users.c.token_version).where(users.c.id == uid)).scalar()

    def has_consent(self, uid: str) -> bool:
        with self.engine.connect() as c:
            return c.execute(select(users.c.consent_at).where(users.c.id == uid)).scalar() is not None

    def give_consent(self, uid: str) -> None:
        with self.engine.begin() as c:
            c.execute(update(users).where(users.c.id == uid).values(consent_at=_now()))

    def withdraw_consent(self, uid: str) -> None:
        with self.engine.begin() as c:
            c.execute(update(users).where(users.c.id == uid).values(consent_at=None))

    def change_password(self, uid: str, old: str, new: str) -> bool:
        user = self.get(uid)
        with self.engine.connect() as c:
            stored = c.execute(select(users.c.pw_hash).where(users.c.id == uid)).scalar()
        if not user or not stored or not check_password(old or "", stored):
            return False
        _validate(user["email"], new)
        with self.engine.begin() as c:       # bumping the version signs everyone out
            c.execute(update(users).where(users.c.id == uid).values(
                pw_hash=hash_password(new), token_version=users.c.token_version + 1))
        return True

    def delete(self, uid: str) -> None:
        with self.engine.begin() as c:
            c.execute(delete(users).where(users.c.id == uid))

    @staticmethod
    def _public(row) -> dict:
        return {"id": row["id"], "email": row["email"], "consented": row["consent_at"] is not None,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None}
