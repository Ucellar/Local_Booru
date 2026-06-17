"""Small UI normalisation pass for Local Booru.

The project has many pages that were built feature-by-feature.  This helper does
not move controls around and does not touch parser logic; it only applies a
consistent visual hierarchy after pages are constructed: button roles, sane
minimum heights, alternating table rows and tab/list behaviour.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QLineEdit,
    QListView,
    QListWidget,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QTabBar,
    QTableView,
    QTableWidget,
    QTextEdit,
    QPlainTextEdit,
    QWidget,
)

_DANGER_WORDS = (
    "удал", "delete", "сброс", "reset", "очист", "nuke", "trash",
    "корзин", "purge", "стереть",
    "стоп", "stop",
)
_PRIMARY_WORDS = (
    "старт", "start", "сохран", "save", "примен", "apply", "создать",
    "backup", "копию", "подключ", "повторить", "retry", "обновить", "refresh",
    "импорт", "экспорт", "открыть", "выбрать",
    # Running controls must be visually active only while enabled.  QSS handles
    # the disabled state; role assignment only says what kind of action this is.
    "пауза", "pause", "продолжить", "resume",
)
_SUBTLE_WORDS = (
    "отмена", "cancel", "назад", "back", "закрыть", "close", "скрыть",
)


def _button_role(text: str) -> str | None:
    t = (text or "").lower().replace("&", "")
    if not t:
        return None
    if any(word in t for word in _DANGER_WORDS):
        return "danger"
    if any(word in t for word in _PRIMARY_WORDS):
        return "primary"
    if any(word in t for word in _SUBTLE_WORDS):
        return "subtle"
    return None


def _set_role(btn: QPushButton) -> None:
    # Navigation and post-control buttons already have dedicated QSS.  Do not
    # overwrite their visual language with role colors.
    if btn.objectName() in {"NavBtn", "PostCtrl", "ModeBtn"}:
        return
    name = btn.objectName() or ""
    if name in {"ParserStartButton", "ParserPauseButton"}:
        role = "primary"
    elif name in {"ParserStopButton", "DownloaderStopButton", "SubscriptionStopButton"}:
        role = "danger"
    else:
        role = _button_role(btn.text())
    if role:
        btn.setProperty("role", role)
    else:
        btn.setProperty("role", "subtle")
    try:
        if btn.minimumHeight() < 30:
            btn.setMinimumHeight(30)
    except Exception:
        pass


def apply_visual_polish(root: QWidget) -> None:
    """Apply safe visual polish to an already-built widget tree."""
    if root is None:
        return

    for btn in root.findChildren(QPushButton):
        _set_role(btn)
        try:
            btn.setCursor(Qt.PointingHandCursor)
        except Exception:
            pass

    for cls in (QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit):
        for edit in root.findChildren(cls):
            try:
                if edit.minimumHeight() < 30:
                    edit.setMinimumHeight(30)
            except Exception:
                pass
            if isinstance(edit, QComboBox):
                try:
                    if edit.view() is None or not isinstance(edit.view(), QListView):
                        edit.setView(QListView(edit))
                    edit.setMaxVisibleItems(12)
                except Exception:
                    pass

    for box in root.findChildren(QGroupBox):
        try:
            box.setProperty("uxPanel", True)
        except Exception:
            pass

    for cls in (QTableWidget, QTableView):
        for view in root.findChildren(cls):
            try:
                view.setAlternatingRowColors(True)
                view.setSelectionBehavior(QAbstractItemView.SelectRows)
            except Exception:
                pass
            try:
                view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
                view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
            except Exception:
                pass

    # Do not force alternating rows on QListWidget.  Qt uses the system
    # AlternateBase palette for many custom list widgets; on Windows this can
    # turn source/tag lists into unrelated blue stripes even on amber/green
    # themes.  Lists keep theme-defined selected/hover colors only.
    for view in root.findChildren(QListWidget):
        try:
            view.setAlternatingRowColors(False)
            view.setSelectionBehavior(QAbstractItemView.SelectRows)
        except Exception:
            pass
        try:
            view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
            view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        except Exception:
            pass

    for tab in root.findChildren(QTabBar):
        try:
            tab.setElideMode(Qt.ElideRight)
            tab.setUsesScrollButtons(True)
            tab.setExpanding(False)
        except Exception:
            pass

    # Dynamic properties require a repolish to take effect if the stylesheet was
    # already applied before this helper ran.
    try:
        root.style().unpolish(root)
        root.style().polish(root)
    except Exception:
        pass
