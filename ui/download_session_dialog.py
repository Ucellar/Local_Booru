"""Per-launch media-download permission dialog."""
from __future__ import annotations


def choose_session_media_downloads(default_enabled: bool = True, parent=None) -> bool:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

    selected = {"enabled": bool(default_enabled)}
    dialog = QDialog(parent)
    dialog.setWindowTitle("Медиа-загрузки / Media downloads")
    dialog.setModal(True)
    dialog.setMinimumWidth(520)
    dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

    root = QVBoxLayout(dialog)
    root.setContentsMargins(22, 20, 22, 20)
    root.setSpacing(14)
    title = QLabel("Разрешить скачивание новых медиа в этой сессии?")
    title.setAlignment(Qt.AlignCenter)
    title.setStyleSheet("font-size: 18px; font-weight: 700;")
    root.addWidget(title)
    hint = QLabel(
        "При запрете Local Booru продолжит работать с локальным архивом, тегами, "
        "метаданными, источниками и расширением, но не скачает ни одного нового файла."
    )
    hint.setWordWrap(True)
    hint.setAlignment(Qt.AlignCenter)
    root.addWidget(hint)
    row = QHBoxLayout()
    yes = QPushButton("Разрешить скачивание")
    no = QPushButton("Только локальная работа")
    for button in (yes, no):
        button.setMinimumHeight(48)
        button.setCursor(Qt.PointingHandCursor)
    yes.clicked.connect(lambda: (selected.__setitem__("enabled", True), dialog.accept()))
    no.clicked.connect(lambda: (selected.__setitem__("enabled", False), dialog.accept()))
    row.addWidget(yes); row.addWidget(no)
    root.addLayout(row)
    dialog.exec()
    return bool(selected["enabled"])
