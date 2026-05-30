from pathlib import Path
import json, re, os, html, zipfile
from collections import Counter
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QGridLayout, QFrame, QSplitter, QListWidget,
    QListWidgetItem, QComboBox, QToolButton, QMenu, QFileDialog,
    QInputDialog, QMessageBox, QStackedWidget
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QImage, QColor

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
PDF_EXTS = {".pdf"}
ARCHIVE_EXTS = {".zip", ".cbz"}
DOC_EXTS = IMG_EXTS | PDF_EXTS
MANGA_META_NAMES = {"meta.json", "info.json", "metadata.json", "tags.json", "gallery.tags.json"}

GROUP_ORDER = ["artist", "character", "parody", "copyright", "general", "language", "category", "group", "pages"]
GROUP_TITLES_RU = {
    "artist": "Авторы",
    "character": "Персонажи",
    "parody": "Произведения",
    "copyright": "Копирайт",
    "general": "Общие",
    "language": "Языки",
    "category": "Категории",
    "group": "Группы",
    "pages": "Страницы",
}
GROUP_COLORS = {
    "artist": "#ff3838",
    "character": "#55dd55",
    "parody": "#ff54a7",
    "copyright": "#ff54a7",
    "general": "#6699ff",
    "language": "#ffbb55",
    "category": "#55dddd",
    "group": "#ffaa77",
    "pages": "#aaaaaa",
}
GROUP_ALIASES = {
    "tag": "general",
    "tags": "general",
    "general": "general",
    "meta": "category",
    "metadata": "category",
    "circle": "group",
}


from ui.manga.helpers import *

class MangaCard(QFrame):
    def __init__(self, item, cb, w=220, h=330):
        super().__init__()
        self.item = item; self.cb = cb
        self.setFixedSize(w, h)
        self.setStyleSheet("QFrame{background:#1f1f1f;border:1px solid #3a3a3a;border-radius:0px;}QFrame:hover{border:2px solid #ff54a7;border-radius:0px;}")
        lay = QVBoxLayout(self); lay.setContentsMargins(6,6,6,6)
        img = QLabel(); img.setAlignment(Qt.AlignCenter); img.setFixedSize(w-14, h-70)
        cover = item.get("cover", "")
        if cover:
            pix = pixmap_from_file(cover, img.size())
            if not pix.isNull(): img.setPixmap(pix)
        if img.pixmap() is None:
            img.setText("PDF" if cover.lower().endswith(".pdf") else "NO COVER")
        title = QLabel(item.get("title", "")); title.setWordWrap(True); title.setStyleSheet("font-weight:900;color:#eee")
        lay.addWidget(img,1); lay.addWidget(title)
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.cb(self.item)


class ChapterSelection(QWidget):
    def __init__(self, main, series_item, owner):
        super().__init__(owner)
        self.main=main; self.series_item=series_item; self.owner=owner
        lay=QVBoxLayout(self)
        top=QHBoxLayout(); back=QPushButton("Назад к манге"); title=QLabel(series_item.get("title", "")); title.setStyleSheet("font-size:22px;font-weight:900")
        top.addWidget(back); top.addWidget(title,1); lay.addLayout(top)
        area=QScrollArea(); area.setWidgetResizable(True); w=QWidget(); self.grid=QGridLayout(w); area.setWidget(w); lay.addWidget(area,1)
        back.clicked.connect(lambda: owner.close_reader())
        chapters=series_item.get("chapters", []) or []
        for i,ch in enumerate(chapters):
            card=MangaCard(ch, lambda x=ch: owner.open_manga(x), 190, 280)
            self.grid.addWidget(card, i//5, i%5)
