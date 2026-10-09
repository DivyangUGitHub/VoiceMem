"""Fail CI on vulnerable dependencies, except the time-limited ones in security/audit-exceptions.txt.

    pip-audit -r requirements.lock --no-deps --disable-pip -f json | python scripts/audit_gate.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

EXCEPTIONS = Path(__file__).resolve().parents[1] / "security" / "audit-exceptions.txt"


def load_exceptions(path: Path = EXCEPTIONS) -> dict[str, date]:
    out: dict[str, date] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            name, until, *_ = (part.strip() for part in line.split("|"))
            out[name.lower()] = date.fromisoformat(until)
    return out


def check(report: dict, exceptions: dict[str, date], today: date) -> tuple[list[str], list[str]]:
    """Return (failures, accepted-with-warning)."""
    failures: list[str] = []
    accepted: list[str] = []
    for dep in report.get("dependencies", []):
        vulns = dep.get("vulns") or []
        if not vulns:
            continue
        name = dep["name"].lower()
        label = f"{dep['name']}=={dep['version']} ({len(vulns)} advisories)"
        if name in exceptions and today <= exceptions[name]:
            accepted.append(f"{label}, accepted until {exceptions[name]}")
        elif name in exceptions:
            failures.append(f"{label}: exception expired on {exceptions[name]}")
        else:
            failures.append(label)
    return failures, accepted


if __name__ == "__main__":
    failures, accepted = check(json.load(sys.stdin), load_exceptions(), date.today())
    for line in accepted:
        print(f"::warning::{line}")
    for line in failures:
        print(f"::error::{line}")
    sys.exit(1 if failures else 0)
