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

class MangaReader(QWidget):
    def __init__(self, main, item, all_items, owner):
        super().__init__(owner)
        self.main = main; self.owner = owner; self.item = item; self.all_items = all_items
        self.pages = [str(p) for p in item.get("pages", [])]
        self.index = 0
        self._resize_pending = False
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0)
        top = QHBoxLayout()
        self.back_btn = QPushButton("Назад к манге")
        self.prev_btn = QPushButton("<")
        self.page_btn = QToolButton(); self.page_btn.setPopupMode(QToolButton.InstantPopup)
        self.next_btn = QPushButton(">")
        self.layout_btn = QPushButton("Снизу/сбоку")
        top.addWidget(self.back_btn); top.addStretch(1); top.addWidget(self.prev_btn); top.addWidget(self.page_btn); top.addWidget(self.next_btn); top.addWidget(self.layout_btn)
        lay.addLayout(top)
        self.mid = QSplitter(Qt.Vertical)
        lay.addWidget(self.mid, 1)
        self.img = QLabel(); self.img.setAlignment(Qt.AlignCenter); self.img.setMinimumHeight(420); self.img.setStyleSheet("background:#111;border:1px solid #333;border-radius:8px")
        self.mid.addWidget(self.img)
        self.thumbs_area = QScrollArea(); self.thumbs_area.setWidgetResizable(True)
        self.thumbs = QWidget(); self.thumb_lay = QGridLayout(self.thumbs); self.thumbs_area.setWidget(self.thumbs)
        self.mid.addWidget(self.thumbs_area)
        self.related_title = QLabel("Другие главы этого тайтла"); self.related_title.setStyleSheet("font-weight:900;font-size:18px")
        lay.addWidget(self.related_title)
        self.related = QHBoxLayout(); lay.addLayout(self.related)
        self.back_btn.clicked.connect(self.close_reader)
        self.prev_btn.clicked.connect(self.prev_page); self.next_btn.clicked.connect(self.next_page); self.layout_btn.clicked.connect(self.toggle_layout)
        self.build_thumbs(); self.build_related(); self.show_page(0)
    def close_reader(self):
        try: self.owner.close_reader()
        except Exception: self.hide()
    def _clear_layout(self, layout):
        while layout.count():
            w = layout.takeAt(0).widget()
            if w: w.deleteLater()
    def build_thumbs(self):
        self._clear_layout(self.thumb_lay)
        bottom = self.main.settings.get("manga_reader_layout", "bottom") == "bottom"
        self.mid.setOrientation(Qt.Vertical if bottom else Qt.Horizontal)
        if bottom:
            self.thumbs_area.setMinimumWidth(0); self.thumbs_area.setMaximumWidth(16777215)
            self.mid.setSizes([700,260])
            cols = 8
        else:
            self.thumbs_area.setMinimumWidth(260); self.thumbs_area.setMaximumWidth(360)
            self.mid.setSizes([900,300])
            cols = 2
        for i,p in enumerate(self.pages):
            lab = QLabel(str(i+1)); lab.setAlignment(Qt.AlignCenter); lab.setFixedSize(120,160)
            pix = pixmap_from_file(p, lab.size())
            if not pix.isNull(): lab.setPixmap(pix)
            lab.setStyleSheet("border:1px solid #444;background:#222")
            lab.mousePressEvent = lambda ev, ix=i: self.show_page(ix)
            self.thumb_lay.addWidget(lab, i//cols, i%cols)
    def build_related(self):
        self._clear_layout(self.related)
        key = self.item.get("chapter_key"); count = 0
        for it in self.all_items:
            if it is self.item: continue
            if it.get("chapter_key") == key:
                self.related.addWidget(MangaCard(it, lambda x=it: self.open_related(x), 140, 210)); count += 1
        self.related_title.setVisible(count > 0)
    def open_related(self, item):
        self.item = item; self.pages = [str(p) for p in item.get("pages", [])]; self.index = 0
        self.build_thumbs(); self.build_related(); self.show_page(0)
    def build_page_menu(self):
        menu = QMenu(self)
        for i in range(len(self.pages)):
            act = menu.addAction(f"{i+1}/{len(self.pages)}")
            act.triggered.connect(lambda checked=False, ix=i: self.show_page(ix))
        self.page_btn.setMenu(menu)
    def show_page(self, idx):
        if not self.pages:
            self.img.setText("Нет страниц"); return
        idx = max(0, min(idx, len(self.pages)-1)); self.index = idx; p = self.pages[idx]
        self.page_btn.setText(f"{idx+1}/{len(self.pages)} ▼")
        self.prev_btn.setEnabled(True); self.next_btn.setEnabled(True)
        self.build_page_menu()
        pix = pixmap_from_file(p, self.img.size())
        if not pix.isNull():
            self.img.setPixmap(pix); return
        self.img.setPixmap(QPixmap()); self.img.setText((Path(str(p)).name if not _is_virtual_archive_path(p) else Path(_split_virtual_archive_path(p)[1]).name) + ("\nPDF preview needs PyMuPDF" if str(p).lower().endswith(".pdf") else "\nunsupported preview"))
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._resize_pending: return
        self._resize_pending = True
        QTimer.singleShot(120, self._delayed_resize)
    def _delayed_resize(self):
        self._resize_pending = False
        self.show_page(self.index)
    def prev_page(self):
        if self.index <= 0: self.close_reader()
        else: self.show_page(self.index-1)
    def next_page(self):
        if self.index >= len(self.pages)-1: self.close_reader()
        else: self.show_page(self.index+1)
    def toggle_layout(self):
        cur = self.main.settings.get("manga_reader_layout", "bottom")
        self.main.settings["manga_reader_layout"] = "side" if cur == "bottom" else "bottom"
        self.main.save_settings(); self.build_thumbs(); self.show_page(self.index)
