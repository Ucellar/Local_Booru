"""Launcher for protected sites in the user's ordinary browser.

v453 removed the embedded browser engine.  The companion extension runs in
Chrome/Edge/Brave/Firefox (or another selected/default browser) and reports only
bounded results to Local Booru through localhost.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton

from core.external_browser import open_external_browser_url

_BR34_WINDOW = None


def _normalize_url(url: str, allow_blank: bool = False):
    raw = str(url or "").strip().strip('"\'')
    if not raw:
        return "about:blank" if allow_blank else None
    if raw.lower() == "about:blank":
        return "about:blank" if allow_blank else None
    if raw.startswith(("http://", "https://")):
        return raw
    if raw.startswith("//"):
        return "https:" + raw
    if "." in raw and not any(ch.isspace() for ch in raw):
        return "https://" + raw
    return None


class LoginBrowserDialog(QDialog):
    """Small control surface; it never embeds or owns a browser process."""

    def __init__(self, url: str = "https://example.com", parent=None, log_func=None, settings=None):
        super().__init__(parent)
        self.log_func = log_func or (lambda _m: None)
        self.settings = settings if isinstance(settings, dict) else getattr(parent, "settings", {}) or {}
        self.setWindowTitle("External Browser Resolver — Local Booru")
        self.resize(720, 210)

        layout = QVBoxLayout(self)
        note = QLabel(
            "Local Booru no longer embeds Chromium. Open the page in your normal browser, "
            "pass Cloudflare/login there, and keep the Local Booru Companion extension enabled."
        )
        note.setWordWrap(True)
        note.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(note)

        row = QHBoxLayout()
        self.url_edit = QLineEdit(_normalize_url(url) or "https://example.com")
        self.open_btn = QPushButton("Open in browser")
        row.addWidget(self.url_edit, 1)
        row.addWidget(self.open_btn)
        layout.addLayout(row)

        self.status = QLabel("Browser cookies and profile remain outside Local Booru.")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)

        close_btn = QPushButton("Close")
        layout.addWidget(close_btn)
        self.open_btn.clicked.connect(self.open_external)
        self.url_edit.returnPressed.connect(self.open_external)
        close_btn.clicked.connect(self.close)

    def load_url(self, url: str, switch: bool = True):
        norm = _normalize_url(url)
        if not norm:
            return False
        self.url_edit.setText(norm)
        if switch:
            return self.open_external()
        return True

    def add_tab(self, url: str, switch: bool = True, popup: bool = False):
        del popup
        return self.load_url(url, switch=switch)

    def open_external(self):
        norm = _normalize_url(self.url_edit.text())
        if not norm:
            self.status.setText("Invalid URL")
            return False
        ok = open_external_browser_url(norm, self.settings, log_fn=self.log_func)
        self.status.setText(
            "Opened in selected/default browser. Complete the challenge there; "
            "the companion extension will return the result."
            if ok else "Could not open the browser. Check External Browser settings."
        )
        return ok

    def release_and_hide(self):
        self.close()


def open_br34(url: str = "https://example.com", parent=None, log_func=None, switch=True):
    global _BR34_WINDOW
    norm = _normalize_url(url)
    if not norm:
        return None
    settings = getattr(parent, "settings", {}) if parent is not None else {}
    if switch:
        open_external_browser_url(norm, settings, log_fn=log_func)
    try:
        if _BR34_WINDOW is None:
            _BR34_WINDOW = LoginBrowserDialog(norm, parent, log_func=log_func, settings=settings)
        else:
            _BR34_WINDOW.settings = settings or _BR34_WINDOW.settings
            _BR34_WINDOW.url_edit.setText(norm)
        _BR34_WINDOW.show()
        _BR34_WINDOW.raise_()
        _BR34_WINDOW.activateWindow()
    except Exception:
        _BR34_WINDOW = None
    return _BR34_WINDOW


def open_br34_multi(urls: list, parent=None, log_func=None):
    normed = []
    for url in urls or []:
        norm = _normalize_url(url)
        if norm and norm not in normed:
            normed.append(norm)
    if not normed:
        return None
    settings = getattr(parent, "settings", {}) if parent is not None else {}
    for url in normed:
        open_external_browser_url(url, settings, dedupe_seconds=0.0, log_fn=log_func)
    return open_br34(normed[0], parent=parent, log_func=log_func, switch=False)
