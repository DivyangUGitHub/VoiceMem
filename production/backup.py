"""Consistent copies of a memory folder: used for user export and for backups.

Memory spaces hold SQLite files that are being written while the app runs. Copying such a
file with ``cp`` can produce a broken copy, so SQLite files go through SQLite's own backup API.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

_MAGIC = b"SQLite format 3\x00"


def is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == _MAGIC
    except OSError:
        return False


def copy_consistent(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if is_sqlite(src):
        try:
            source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            target = sqlite3.connect(dst)
            with target:
                source.backup(target)
            source.close()
            target.close()
            return
        except sqlite3.Error:
            pass                       # e.g. a read-only filesystem: fall back to a plain copy
    shutil.copy2(src, dst)


def copy_tree(src_dir: Path, dst_dir: Path) -> int:
    """Copy a folder with consistent SQLite files. Returns the number of files."""
    n = 0
    for f in sorted(src_dir.rglob("*")):
        if f.is_file() and not f.name.endswith(("-wal", "-shm", "-journal")):
            copy_consistent(f, dst_dir / f.relative_to(src_dir))
            n += 1
    return n


def export_zip(spaces: list[Path], out: Path, account: dict) -> None:
    """Everything stored about one user: account facts plus a consistent copy of each space."""
    import tempfile

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z, tempfile.TemporaryDirectory() as tmp:
        z.writestr("account.json", json.dumps(
            {**account, "exported_at": datetime.now(timezone.utc).isoformat()}, indent=2))
        for space in spaces:
            copy_tree(space, Path(tmp) / space.name)
        for f in sorted(Path(tmp).rglob("*")):
            if f.is_file():
                z.write(f, Path("spaces") / f.relative_to(tmp))


# ── scheduled backups ────────────────────────────────────────────────────────
STAMP = "voicemem-%Y%m%dT%H%M%SZ"


def _sqlite_path(url: str) -> Path | None:
    return Path(url.split("sqlite:///", 1)[1]) if url.startswith("sqlite:///") else None


def make_backup(out_root: Path, memory_root: Path, database_url: str, pg_dump: str | None = None) -> Path:
    """Write one backup folder: all memory spaces plus the accounts database. Returns its path."""
    dest = Path(out_root) / datetime.now(timezone.utc).strftime(STAMP)
    dest.mkdir(parents=True)
    (dest / "memory").mkdir()                  # exists even before the first user has stored anything
    copy_tree(Path(memory_root), dest / "memory")
    sqlite_file = _sqlite_path(database_url)
    if sqlite_file is not None:
        if sqlite_file.exists():
            copy_consistent(sqlite_file, dest / "accounts.db")
    else:
        url = database_url.replace("+psycopg", "", 1)
        result = subprocess.run([pg_dump or os.environ.get("PG_DUMP", "pg_dump"), "--no-owner", "--dbname", url,
                                 "--file", str(dest / "accounts.sql")], capture_output=True, text=True)
        if result.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            # The error can echo the connection string, so never include the URL.
            host = urlsplit(url).hostname
            raise RuntimeError(f"pg_dump failed for host {host}: {result.stderr.strip().splitlines()[-1:]}")
    return dest


def verify_backup(path: Path) -> list[str]:
    """Problems found in a backup folder. An empty list means it is usable."""
    path = Path(path)
    problems: list[str] = []
    if not (path / "memory").is_dir():
        problems.append("memory/ is missing")
    for f in sorted(path.rglob("*")):
        if f.is_file() and is_sqlite(f):
            try:
                conn = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
                try:
                    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
                finally:
                    conn.close()               # a damaged file raises; an open handle blocks deletion on Windows
                if result != "ok":
                    problems.append(f"{f.relative_to(path)}: {result}")
            except sqlite3.Error as exc:
                problems.append(f"{f.relative_to(path)}: {exc}")
    sql = path / "accounts.sql"
    if sql.exists() and "PostgreSQL database dump" not in sql.read_text(errors="ignore")[:2000]:
        problems.append("accounts.sql does not look like a pg_dump file")
    if not sql.exists() and not (path / "accounts.db").exists():
        problems.append("no accounts database in the backup")
    return problems


def prune(out_root: Path, keep: int) -> list[Path]:
    """Delete all but the newest ``keep`` backups (only folders this tool created)."""
    folders = sorted(p for p in Path(out_root).glob("voicemem-*T*Z") if p.is_dir())
    old = folders[:-keep] if keep > 0 else []
    for p in old:
        shutil.rmtree(p, ignore_errors=True)
    return old
