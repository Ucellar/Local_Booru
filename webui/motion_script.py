"""(v5.44.55) ``static/motion.js`` is written into every page itself, not fetched: it has to run before the page is first
drawn — it says how the page comes in (``data-turned``) and catches the «load» of every picture from the first one.
The pages' Content-Security-Policy lets in exactly this text (its SHA-256), read once — the same text in the page and in
the policy until the program restarts.

(v5.44.56) It stands BEFORE the stylesheets: a script after them waits for them, and the rest of the page is not read
meanwhile.  (In 5.44.55 it stood after them — Chromium's view transitions between pages needed that, and they are gone.)

(v5.44.58) It also lets the pictures still coming go when the reader leaves for another page of the program: a browser
has six connections to the program, a page of slow pictures held them all, and the next page waited for one.
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

_PATH = Path(__file__).with_name("static") / "motion.js"
try:
    TEXT = _PATH.read_text(encoding="utf-8")
except OSError:                                   # no file: pages without softness, nothing else changes
    TEXT = ""
if "</script" in TEXT.lower():                    # it would end the page's <script> early
    TEXT = ""
SHA256 = base64.b64encode(hashlib.sha256(TEXT.encode("utf-8")).digest()).decode("ascii") if TEXT else ""


def tag() -> str:
    """The script for the page's <head>."""
    return f"<script>{TEXT}</script>" if TEXT else ""


def csp_source() -> str:
    """Its place in ``script-src`` of the policy."""
    return f" 'sha256-{SHA256}'" if SHA256 else ""
