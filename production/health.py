"""Readiness checks. Cheap and local: a probe must never call a paid API."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def readiness(frontend: Path, memory_root: Path, env: dict[str, str] | None = None) -> tuple[bool, dict[str, str]]:
    """Return (ready, per-check result). ``ok`` or a short reason for each check."""
    env = os.environ if env is None else env
    checks: dict[str, str] = {}

    checks["frontend"] = "ok" if (frontend / "index.html").is_file() else "frontend build is missing"

    try:
        memory_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=memory_root):
            pass
        checks["memory_storage"] = "ok"
    except OSError as exc:
        checks["memory_storage"] = f"not writable: {exc.strerror or exc}"

    checks["llm_credentials"] = "ok" if env.get("OPENAI_API_KEY") else "OPENAI_API_KEY is not set"

    return all(v == "ok" for v in checks.values()), checks
