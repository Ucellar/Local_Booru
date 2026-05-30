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
from ui.manga.widgets import MangaCard, ChapterSelection
from ui.manga.reader import MangaReader

class MangaPage(QWidget):
    def __init__(self, main):
        super().__init__(); self.main = main; self.items = []; self.filtered = []; self.tag_counts = Counter(); self.reader = None
        root_lay = QVBoxLayout(self)
        self.stack = QStackedWidget(); root_lay.addWidget(self.stack,1)
        self.library = QWidget(); lay = QVBoxLayout(self.library); self.stack.addWidget(self.library)
        top = QHBoxLayout()
        self.search = QLineEdit(); self.search.setClearButtonEnabled(True); self.search.setPlaceholderText("Поиск по названию или тегам")
        self.sort = QComboBox(); self.sort.addItem("Название", "title"); self.sort.addItem("Новые", "new"); self.sort.addItem("Старые", "old"); self.sort.addItem("Страниц больше", "pages_desc")
        self.refresh_btn = QPushButton("Обновить")
        top.addWidget(QLabel("Манга")); top.addWidget(self.search,1); top.addWidget(self.sort); top.addWidget(self.refresh_btn); lay.addLayout(top)
        split = QSplitter(Qt.Horizontal); lay.addWidget(split,1)
        tag_side = QWidget(); tl = QVBoxLayout(tag_side); tl.addWidget(QLabel("Теги")); self.tags_list = QListWidget(); tl.addWidget(self.tags_list,1); split.addWidget(tag_side)
        self.area = QScrollArea(); self.area.setWidgetResizable(True); self.gridw = QWidget(); self.grid = QGridLayout(self.gridw); self.area.setWidget(self.gridw); split.addWidget(self.area)

        split.setSizes([260, 1000])
        self.current_manga_item = None

        self.search.textChanged.connect(self.apply_filter)
        self.sort.currentIndexChanged.connect(self.apply_filter)
        self.refresh_btn.clicked.connect(self.refresh)
        self.tags_list.itemClicked.connect(self.tag_clicked)
    def retranslate(self):
        pass
    def refresh(self):
        root = self.main.settings.get("manga_root") or self.main.settings.get("root")
        self.items = scan_manga(root); self.recount_tags(); self.apply_filter()
    def recount_tags(self):
        self.tag_counts = Counter()
        self.tag_group_counts = {g: Counter() for g in GROUP_ORDER}
        for it in self.items:
            groups = it.get("groups") or {}
            has_grouped = False
            for raw_group, arr in groups.items():
                group = normalize_group_name(raw_group)
                clean_arr = [norm_tag(x) for x in (arr or []) if norm_tag(x)]
                if clean_arr:
                    has_grouped = True
                    self.tag_group_counts[group].update(clean_arr)
                    self.tag_counts.update(clean_arr)
            if not has_grouped:
                fallback = [norm_tag(x) for x in it.get("tags", []) if norm_tag(x)]
                self.tag_group_counts["general"].update(fallback)
                self.tag_counts.update(fallback)
        self.tags_list.clear()
        shown = 0
        for group in GROUP_ORDER:
            counter = self.tag_group_counts.get(group) or Counter()
            if not counter:
                continue
            header = QListWidgetItem(GROUP_TITLES_RU.get(group, group))
            header.setData(Qt.UserRole, None)
            header.setFlags(header.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsEnabled)
            header.setForeground(QColor(GROUP_COLORS.get(group, "#dddddd")))
            self.tags_list.addItem(header)
            for tag, count in counter.most_common(80):
                li = QListWidgetItem(f"  {tag}    {count}")
                li.setData(Qt.UserRole, tag)
                li.setData(Qt.UserRole + 1, group)
                li.setForeground(QColor(GROUP_COLORS.get(group, GROUP_COLORS.get("general", "#6699ff"))))
                self.tags_list.addItem(li)
                shown += 1
                if shown >= 350:
                    return
    def tag_clicked(self, item):
        tag = item.data(Qt.UserRole)
        if tag: self.search.setText(tag)
    def apply_filter(self):
        q = self.search.text().lower().strip(); arr = []
        for it in self.items:
            hay = (it.get("title", "") + " " + " ".join(it.get("tags", []))).lower()
            if q and q not in hay: continue
            arr.append(it)
        mode = self.sort.currentData()
        if mode == "new": arr.sort(key=lambda x:x.get("mtime",0), reverse=True)
        elif mode == "old": arr.sort(key=lambda x:x.get("mtime",0))
        elif mode == "pages_desc": arr.sort(key=lambda x:len(x.get("pages",[])), reverse=True)
        else: arr.sort(key=lambda x:x.get("title", "").lower())
        self.filtered = arr; self.render()
    def render(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w: w.deleteLater()
        try: cols = int(self.main.settings.get("manga_columns",5))
        except Exception: cols = 5
        for i,it in enumerate(self.filtered):
            self.grid.addWidget(MangaCard(it, self.open_manga), i//cols, i%cols)
        self.grid.setRowStretch((len(self.filtered)//max(1,cols))+1,1)

    def open_manga(self, item):
        if self.reader is not None:
            try: self.stack.removeWidget(self.reader); self.reader.deleteLater()
            except Exception: pass
            self.reader = None
        if item.get("is_series") and item.get("chapters"):
            self.reader = ChapterSelection(self.main, item, self)
        else:
            self.reader = MangaReader(self.main, item, self.items, self)
        self.stack.addWidget(self.reader); self.stack.setCurrentWidget(self.reader)
    def close_reader(self):
        self.stack.setCurrentWidget(self.library)
        if self.reader is not None:
            old = self.reader; self.reader = None
            self.stack.removeWidget(old); old.deleteLater()

    def open_random_manga(self):
        import random

        items = list(getattr(self, "filtered", None) or getattr(self, "items", None) or [])
        if not items:
            try:
                self.refresh()
                items = list(getattr(self, "filtered", None) or getattr(self, "items", None) or [])
            except Exception:
                pass

        if not items:
            return

        item = random.choice(items)

        chapters = item.get("chapters", []) if isinstance(item, dict) else []
        if chapters:
            item = chapters[0]

        if hasattr(self, "open_manga"):
            self.open_manga(item)
        elif hasattr(self, "open_reader"):
            self.open_reader(item)
