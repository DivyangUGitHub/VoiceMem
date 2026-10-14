"""The voice console is served under /backend/ in production, so it must not use absolute URLs."""

import re
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "web" / "voicemem.html").read_text(encoding="utf-8")


# /auth/* lives at the site root on purpose: it is the production app's sign-in, not part of the runtime.
def test_console_urls_follow_the_page_base_path():
    assert "const BASE=" in HTML
    absolute = re.findall(r"""(?:fetch\(|addModule\()\s*['"]/(?!auth/)[^'"]*|WS_HOST\s*\+\s*['"]/ws""", HTML)
    assert absolute == []
