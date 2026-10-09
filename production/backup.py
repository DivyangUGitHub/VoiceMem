"""Consistent copies of a memory folder: used for user export and for backups.

Memory spaces hold SQLite files that are being written while the app runs. Copying such a
file with ``cp`` can produce a broken copy, so SQLite files go through SQLite's own backup API.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

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
