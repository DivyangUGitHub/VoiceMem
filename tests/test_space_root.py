"""web/run.py loads the models on import, so guard its wiring by reading the source.

A hardcoded `_ROOT / "voicemem_memoryspace"` ignores VOICEMEM_MEMORYSPACE_ROOT: in the
container that points at a folder that does not exist and the non-root user cannot create.
"""

import re
from pathlib import Path

RUN = Path(__file__).resolve().parents[1] / "web" / "run.py"


def test_space_paths_follow_the_configured_root():
    hardcoded = [
        line for line in RUN.read_text(encoding="utf-8").splitlines()
        if re.search(r'_ROOT\s*/\s*"voicemem_memoryspace"', line) and "setdefault" not in line
    ]
    assert hardcoded == []
