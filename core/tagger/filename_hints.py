"""Filename-derived reverse-search hints.

These helpers are intentionally conservative: they only decide whether a
branch that depends on a filename/source key has any useful key to try.  They
must never disable content-based reverse branches such as IQDB, SauceNAO or
TinEye, because generic Telegram/phone filenames can still match by pixels.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Union

PathLike = Union[str, Path]

_HEX40_RE = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{40})(?![0-9a-fA-F])")


def _text_for_path(path: PathLike) -> tuple[str, str, str]:
    try:
        p = Path(path)
        return str(path or ""), p.name, p.stem
    except Exception:
        text = str(path or "")
        return text, text, text.rsplit(".", 1)[0]


def extract_rule34_40hex_key(path: PathLike) -> str:
    """Return a 40-hex rule34 image-key/SHA1-like token from a path, if any."""
    seen = set()
    for text in _text_for_path(path):
        for m in _HEX40_RE.findall(text or ""):
            key = str(m or "").lower()
            if key and key not in seen:
                return key
            seen.add(key)
    return ""
