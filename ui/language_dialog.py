"""Startup language selection dialog."""

from __future__ import annotations

from core.language_bootstrap import DEFAULT_LANGUAGE, normalize_language


def choose_startup_language(current: str = DEFAULT_LANGUAGE, parent=None) -> str:
    """Show a small modal startup dialog and return 'ru' or 'en'.

    The import of PySide6 stays inside the function so non-GUI tests can import
    this module without requiring Qt.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QDialog,
        QFrame,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    selected = {"value": normalize_language(current)}

    dialog = QDialog(parent)
    dialog.setWindowTitle("Choose language / Выбор языка")
    dialog.setModal(True)
    dialog.setMinimumWidth(420)
    dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

    root = QVBoxLayout(dialog)
    root.setContentsMargins(22, 20, 22, 20)
    root.setSpacing(14)

    title = QLabel("Choose interface language\nВыберите язык интерфейса")
    title.setAlignment(Qt.AlignCenter)
    title.setObjectName("StartupLanguageTitle")
    title.setStyleSheet("font-size: 18px; font-weight: 700;")
    root.addWidget(title)

    hint = QLabel("You can change this later in Settings.\nПозже это можно изменить в настройках.")
    hint.setAlignment(Qt.AlignCenter)
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #9aa0aa;")
    root.addWidget(hint)

    frame = QFrame()
    frame.setFrameShape(QFrame.NoFrame)
    row = QHBoxLayout(frame)
    row.setContentsMargins(0, 4, 0, 0)
    row.setSpacing(12)

    def _make_button(text: str, lang: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setMinimumHeight(52)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setProperty("lang", lang)
        btn.setStyleSheet(
            "QPushButton { font-size: 16px; font-weight: 600; padding: 10px 18px; "
            "border-radius: 10px; border: 1px solid #4a4f5a; }"
            "QPushButton:hover { border-color: #7d8494; }"
        )

        def _choose() -> None:
            selected["value"] = lang
            dialog.accept()

        btn.clicked.connect(_choose)
        return btn

    row.addWidget(_make_button("Русский", "ru"))
    row.addWidget(_make_button("English", "en"))
    root.addWidget(frame)

    # Closing the dialog should not block the app. Use the current/default
    # language and let startup continue.
    dialog.exec()
    return normalize_language(selected.get("value"))
