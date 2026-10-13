import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from production import accounts as accounts_mod
from production.backup import copy_consistent, make_backup, prune, verify_backup

ROOT = Path(__file__).resolve().parents[1]


def sqlite_with_rows(path: Path, rows: int = 50) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, text TEXT)")
    c.executemany("INSERT INTO memories (text) VALUES (?)", [(f"m{i}",) for i in range(rows)])
    c.commit()
    c.close()


@pytest.fixture
def site(tmp_path):
    memory = tmp_path / "memory"
    sqlite_with_rows(memory / "u1_main" / "main.db")
    (memory / "u1_main" / "notes.txt").write_text("hello")
    (memory / "u2_main").mkdir()
    (memory / "u2_main" / "notes.txt").write_text("other user")
    db = tmp_path / "accounts.db"
    sqlite_with_rows(db, 3)
    return memory, f"sqlite:///{db}", tmp_path / "backups"


def test_backup_contains_everything_and_verifies(site):
    memory, url, out = site
    dest = make_backup(out, memory, url)
    assert (dest / "memory" / "u1_main" / "notes.txt").read_text() == "hello"
    assert (dest / "memory" / "u2_main" / "notes.txt").exists() and (dest / "accounts.db").exists()
    assert verify_backup(dest) == []


def test_an_empty_deployment_still_makes_a_valid_backup(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    db = tmp_path / "accounts.db"
    sqlite_with_rows(db, 0)
    assert verify_backup(make_backup(tmp_path / "out", memory, f"sqlite:///{db}")) == []


def test_a_live_database_is_copied_consistently(tmp_path):
    src, dst = tmp_path / "live.db", tmp_path / "copy.db"
    sqlite_with_rows(src, 10)
    stop = threading.Event()

    def writer():
        c = sqlite3.connect(src, timeout=10)
        i = 0
        while not stop.is_set():
            c.execute("INSERT INTO memories (text) VALUES (?)", (f"w{i}",))
            c.commit()
            i += 1
            time.sleep(0.002)
        c.close()

    t = threading.Thread(target=writer)
    t.start()
    try:
        for _ in range(8):
            copy_consistent(src, dst)
            conn = sqlite3.connect(dst)
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] >= 10
            conn.close()
    finally:
        stop.set()
        t.join()


def test_restore_round_trip(site, tmp_path):
    memory, url, out = site
    dest = make_backup(out, memory, url)
    shutil.rmtree(memory)                                   # disaster
    shutil.copytree(dest / "memory", memory)                # restore, as documented
    conn = sqlite3.connect(memory / "u1_main" / "main.db")
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 50
    assert (memory / "u2_main" / "notes.txt").read_text() == "other user"


def test_verify_catches_damage(site):
    memory, url, out = site
    dest = make_backup(out, memory, url)
    damaged = dest / "memory" / "u1_main" / "main.db"
    data = bytearray(damaged.read_bytes())
    for i in range(2000, min(len(data), 6000)):
        data[i] = 0xFF
    damaged.write_bytes(bytes(data))
    assert verify_backup(dest) != []
    shutil.rmtree(dest / "memory")
    assert "memory/ is missing" in verify_backup(dest)


def test_prune_keeps_the_newest_and_ignores_other_folders(tmp_path):
    for name in ("voicemem-20260101T000000Z", "voicemem-20260102T000000Z", "voicemem-20260103T000000Z", "photos"):
        (tmp_path / name).mkdir()
    removed = prune(tmp_path, 2)
    assert [p.name for p in removed] == ["voicemem-20260101T000000Z"]
    assert (tmp_path / "photos").exists() and (tmp_path / "voicemem-20260103T000000Z").exists()


def test_command_line_tool_makes_a_verified_backup_and_fails_loudly_on_a_bad_one(site, tmp_path):
    memory, url, out = site
    env = {**os.environ, "VOICEMEM_MEMORYSPACE_ROOT": str(memory), "DATABASE_URL": url, "PYTHONPATH": str(ROOT)}
    ok = subprocess.run([sys.executable, str(ROOT / "scripts" / "backup.py"), str(out), "--keep", "1"],
                        env=env, capture_output=True, text=True)
    assert ok.returncode == 0 and "backup OK" in ok.stdout
    backup = next(out.glob("voicemem-*"))
    good = subprocess.run([sys.executable, str(ROOT / "scripts" / "backup.py"), "--verify", str(backup)],
                          env=env, capture_output=True, text=True)
    assert good.returncode == 0 and "backup OK" in good.stdout
    shutil.rmtree(backup / "memory")
    bad = subprocess.run([sys.executable, str(ROOT / "scripts" / "backup.py"), "--verify", str(backup)],
                         env=env, capture_output=True, text=True)
    assert bad.returncode == 1 and "memory/ is missing" in bad.stdout


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL") or not (shutil.which("pg_dump") or os.environ.get("PG_DUMP")),
                    reason="needs a real Postgres (TEST_DATABASE_URL) and pg_dump")
def test_postgres_backup_has_the_users(tmp_path):
    url = accounts_mod.database_url({"DATABASE_URL": os.environ["TEST_DATABASE_URL"]})
    store = accounts_mod.Accounts(url)
    os.environ.setdefault("VOICEMEM_SCRYPT_N", "1024")
    try:
        store.create("backup@example.com", "correct horse battery")
        memory = tmp_path / "memory"
        memory.mkdir()
        dest = make_backup(tmp_path / "out", memory, url, pg_dump=os.environ.get("PG_DUMP"))
        assert verify_backup(dest) == []
        assert "backup@example.com" in (dest / "accounts.sql").read_text()
    finally:
        accounts_mod.metadata.drop_all(store.engine)
        store.engine.dispose()
