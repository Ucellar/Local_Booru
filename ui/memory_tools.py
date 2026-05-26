from __future__ import annotations

import gc


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
    try:
        widget.appendPlainText(str(text))
    except Exception:
        try:
            widget.append(str(text))
        except Exception:
            pass


def soft_gc():
    try:
        gc.collect()
    except Exception:
        pass
