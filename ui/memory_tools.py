from __future__ import annotations

import gc
from core.redaction import sanitize_text


def set_bounded_log(widget, max_blocks: int = 2500):
    """Limit QTextDocument/QPlainTextEdit history so long batch runs do not eat RAM."""
    try:
        doc = widget.document()
        doc.setMaximumBlockCount(int(max_blocks))
    except Exception:
        pass


def bounded_append(widget, text: str, max_blocks: int = 2500):
    try:
        doc = widget.document()
        if doc.maximumBlockCount() <= 0 or doc.maximumBlockCount() > int(max_blocks):
            doc.setMaximumBlockCount(int(max_blocks))
    except Exception:
        pass
    safe_text = sanitize_text(text)
    try:
        widget.appendPlainText(safe_text)
    except Exception:
        try:
            widget.append(safe_text)
        except Exception:
            pass


def soft_gc():
    try:
        gc.collect()
    except Exception:
        pass
