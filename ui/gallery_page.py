"""Gallery page — SQL-paginated, async thumbnails via ThumbnailService."""
from __future__ import annotations

from pathlib import Path
from collections import Counter, OrderedDict

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel,
    QScrollArea, QGridLayout, QFrame, QComboBox, QCheckBox, QSplitter,
    QListWidget, QSizePolicy, QListWidgetItem, QSpinBox, QListView, QColorDialog,
    QAbstractItemView, QStyledItemDelegate, QStyle, QMenu, QApplication, QMessageBox,
)
from PySide6.QtCore import Qt, QSize, QTimer, QStringListModel, QAbstractListModel, QModelIndex, QRect, QMimeData, QUrl
from PySide6.QtGui import QPixmap, QColor, QBrush, QIcon, QPainter, QPen

from core.library import sort_tag_items
from core.tag_utils import normalize_tag, tag_display_color
from core.search.human_query_parser import parse_query, to_sql_conditions as _num_to_sql
from core.stability import safe_call as _safe_call
from core.database.repository import (
    count_search_items, search_items, enrich_items,
    candidate_tags, candidate_sources, counts, source_unique_image_count,
)
from core.thumb_service import ThumbnailService

GROUP_ORDER = ["artist", "contributor", "character", "copyright", "species", "general", "meta", "lore", "invalid",
               "parody", "language", "category", "pages"]
GROUP_COLORS = {
    "artist": "#ff3838", "contributor": "#e67e22", "character": "#00a000", "copyright": "#ff54a7",
    "species": "#22a6b3", "general": "#004cff", "meta": "#ff9900", "lore": "#9b59b6", "invalid": "#7f8c8d", "parody": "#ff54a7",
    "language": "#cc8800", "category": "#00aaaa", "pages": "#888888",
}


def _global_tag_groups_worker(settings, progress=None, stop_check=None):
    """Build global sidebar tag counters without blocking the GUI thread."""
    if progress:
        progress("Галерея: загрузка общего счётчика тегов…")
    if stop_check and stop_check():
        return None
    from core.database.repository import tag_group_counts
    return tag_group_counts(settings)

def _gallery_facets_worker(settings, progress=None, stop_check=None):
    """Load autocomplete/source counters outside the GUI thread."""
    if progress:
        progress("Галерея: обновление источников и тегов…")
    if stop_check and stop_check():
        return None
    tags = candidate_tags(settings)
    sources = candidate_sources(settings)
    _unused, source_counts, _ = counts(settings)
    source_total = source_unique_image_count(settings)
    return {"tags": tags, "sources": sources, "source_counts": source_counts, "source_total": source_total}

_PH_CACHE: dict[tuple, QPixmap] = {}

def _current_theme_name() -> str:
    try:
        from ui.main_window import _CURRENT_THEME
        return _CURRENT_THEME
    except Exception:
        return "abyss"

def _gallery_item_colors(theme: str | None = None) -> tuple[str, str]:
    theme = theme or _current_theme_name()
    colors = {
        "abyss": ("#111420", "#e8e8e8"),
        "dark": ("#111420", "#e8e8e8"),
        "ember": ("#181408", "#d8c8a0"),
        "slate": ("#1e2028", "#c8ccd8"),
        "sakura": ("#140820", "#e0c8d8"),
        "pornhub": ("#0f0f0f", "#f0f0f0"),
        "ph": ("#0f0f0f", "#f0f0f0"),
        "r34": ("#a8d99f", "#111111"),
        "r34dark": ("#10150f", "#d8e6d5"),
        "win95": ("#c0c0c0", "#000000"),
        "windows95": ("#c0c0c0", "#000000"),
        "light": ("#ffffff", "#1a1c2a"),
    }
    return colors.get(theme, colors["abyss"])


def _placeholder(w: int, h: int) -> QPixmap:
    theme = _current_theme_name()
    key = (w, h, theme)
    if key not in _PH_CACHE:
        p = QPixmap(w, h)
        # Never render a completely blank gallery while thumbnails are being
        # created or a cached thumbnail fails. A visible neutral slot makes it
        # obvious that records exist and leaves the item clickable.
        bg, fg = _gallery_item_colors(theme)
        p.fill(QColor(bg))
        painter = QPainter(p)
        painter.setPen(QPen(QColor("#303645" if theme not in ("r34", "light", "win95", "windows95") else "#788878"), 1))
        painter.drawRect(0, 0, max(0, w - 1), max(0, h - 1))
        painter.setPen(QColor(fg))
        painter.drawText(p.rect(), Qt.AlignCenter, "…")
        painter.end()
        _PH_CACHE[key] = p
    return _PH_CACHE[key]


# ── Image card ────────────────────────────────────────────────────────────────

class ImageCard(QFrame):
    def __init__(self, item: dict, index: int, cb, w: int = 240, h: int = 220):
        super().__init__()
        self.index = index
        self.cb = cb
        self._path = str(item.get("path", ""))
        self._destroyed = False
        self.setFixedSize(w, h)
        _CURRENT_THEME = _current_theme_name()
        if _CURRENT_THEME in ("win95", "windows95"):
            self.setStyleSheet(
                "QFrame{background:#c0c0c0;border-top:2px solid #808080;"
                "border-left:2px solid #808080;border-bottom:2px solid #ffffff;"
                "border-right:2px solid #ffffff;border-radius:0px;}"
                "QFrame:hover{background:#c0c0c0;border-top:2px solid #000080;"
                "border-left:2px solid #000080;border-bottom:2px solid #ffffff;"
                "border-right:2px solid #ffffff;}"
            )
        elif _CURRENT_THEME == "r34":
            self.setStyleSheet(
                "QFrame{background:#a8d99f;border:1px solid #6da36b;border-radius:2px;}"
                "QFrame:hover{background:#8cc57d;border:1px solid #3a7a35;}"
            )
        else:
            self.setStyleSheet(
                "QFrame{background:#111420;border:1px solid #1c2030;border-radius:10px;}"
                "QFrame:hover{background:#141828;border:1px solid #7c5cbf;}"
            )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        self.lab = QLabel()
        self.lab.setAlignment(Qt.AlignCenter)
        self.lab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        tw = max(30, w - 14)
        th = max(30, h - 14)
        self.lab.setFixedSize(tw, th)
        self._tw, self._th = tw, th
        if _CURRENT_THEME in ("win95", "windows95"):
            self.lab.setStyleSheet("background:#ffffff;color:#000000;border:0px;border-radius:0px;font-weight:400;")
        lay.addWidget(self.lab)

        self._is_video = bool(item.get("is_video"))
        self.lab.setPixmap(_placeholder(tw, th))
        if self._is_video:
            if _CURRENT_THEME in ("win95", "windows95"):
                self.lab.setStyleSheet("background:#ffffff;color:#000000;border:0px;border-radius:0px;font-weight:700;")
            else:
                self.lab.setStyleSheet("font-weight:900;color:#e8e8e8;")

        svc = ThumbnailService.instance()
        pix = svc.request(self._path, tw, th, self._on_thumb)
        if pix is not None and not pix.isNull():
            self._set_pix(pix)
        elif self._is_video:
            self.lab.setText("▶ VIDEO\n" + Path(self._path).name[:24])

    def _on_thumb(self, path: str, pix: QPixmap) -> None:
        if self._destroyed or path != self._path:
            return
        if pix.isNull():
            self.lab.setText(Path(path).name[:24])
        else:
            self._set_pix(pix)

    def _set_pix(self, pix: QPixmap) -> None:
        scaled = pix.scaled(QSize(self._tw, self._th),
                            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.lab.setPixmap(scaled)
        if getattr(self, "_is_video", False):
            self.lab.setText("")

    def mousePressEvent(self, _):
        self.cb(self.index)

    def hideEvent(self, e):
        super().hideEvent(e)
        self._destroyed = True

    def closeEvent(self, e):
        super().closeEvent(e)
        self._destroyed = True



# ── Virtual gallery model ─────────────────────────────────────────────────────

class GalleryListModel(QAbstractListModel):
    """Virtualized icon model for the gallery.

    QListView asks `data()` mostly for visible indexes, so thousands of items no
    longer mean thousands of QWidget cards. Thumbnails are requested lazily and
    pushed back via dataChanged when ready.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[dict] = []
        self.thumb_w = 220
        self.thumb_h = 200
        self.quality_scale = 2
        # ThumbnailService already owns its own LRU, but the model used to keep
        # a second unbounded QPixmap dictionary for the current result set.
        self._pix_by_path: OrderedDict[str, QPixmap] = OrderedDict()
        self._pix_cache_max = 500
        self._requested: set[str] = set()
        self._path_to_rows: dict[str, list[int]] = {}

    def set_items(self, items: list[dict], thumb_w: int, thumb_h: int) -> None:
        self.beginResetModel()
        # Defensive UI de-duplication: even if an upstream query accidentally
        # returns the same file twice, do not render duplicate cards.  Preserve
        # the first item because it carries the current metadata/order.
        deduped: list[dict] = []
        seen_paths: set[str] = set()
        for item in (items or []):
            raw_path = str((item or {}).get("path", "")).strip()
            if raw_path:
                key = str(Path(raw_path)).replace("\\", "/").casefold()
                if key in seen_paths:
                    continue
                seen_paths.add(key)
            deduped.append(item)
        self.items = deduped
        self.thumb_w = max(32, int(thumb_w or 220))
        self.thumb_h = max(32, int(thumb_h or 200))
        self._pix_by_path.clear()
        self._requested.clear()
        self._path_to_rows = {}
        for i, it in enumerate(self.items):
            self._path_to_rows.setdefault(str(it.get("path", "")), []).append(i)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.items)

    def _cached_pix(self, path: str):
        pix = self._pix_by_path.get(path)
        if pix is not None:
            self._pix_by_path.move_to_end(path)
        return pix

    def _store_pix(self, path: str, pix: QPixmap) -> None:
        if not path or pix is None or pix.isNull():
            return
        self._pix_by_path[path] = pix
        self._pix_by_path.move_to_end(path)
        while len(self._pix_by_path) > self._pix_cache_max:
            self._pix_by_path.popitem(last=False)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self.items):
            return None
        item = self.items[row]
        path = str(item.get("path", ""))
        if role == Qt.UserRole:
            return row
        if role == Qt.UserRole + 1:
            return item
        if role == Qt.ToolTipRole:
            return path
        if role == Qt.DisplayRole:
            # Keep labels extremely short; full path is in tooltip/opened post.
            return "▶" if item.get("is_video") else ""
        if role == Qt.DecorationRole:
            pix = self._cached_pix(path)
            if pix is not None and not pix.isNull():
                return pix
            if path not in self._requested:
                self._requested.add(path)
                svc = ThumbnailService.instance()
                # Generate a larger cached thumbnail and draw it down to the tile.
                # This prevents the blurred look on high-DPI / wide displays.
                quality = max(1, min(3, int(getattr(self, "quality_scale", 2) or 2)))
                req_w = min(768, max(self.thumb_w, self.thumb_w * quality))
                req_h = min(768, max(self.thumb_h, self.thumb_h * quality))
                got = svc.request(path, req_w, req_h, self._on_thumb)
                if got is not None and not got.isNull():
                    self._store_pix(path, got)
                    return self._cached_pix(path)
            return _placeholder(self.thumb_w, self.thumb_h)
        if role == Qt.BackgroundRole:
            return None
        if role == Qt.ForegroundRole:
            _bg, fg = _gallery_item_colors()
            return QColor(fg)
        return None

    def _on_thumb(self, path: str, pix: QPixmap) -> None:
        if not path:
            return
        if pix is not None and not pix.isNull():
            self._store_pix(path, pix)
        rows = self._path_to_rows.get(path, [])
        for r in rows:
            if 0 <= r < len(self.items):
                idx = self.index(r, 0)
                self.dataChanged.emit(idx, idx, [Qt.DecorationRole])



class GalleryCardDelegate(QStyledItemDelegate):
    """Paint square layout cells, but outline only the actual aspect-fit preview.

    The cell is always square to keep the grid stable.  The image is never
    cropped: portrait images are limited by height, landscape images by width.
    """
    def sizeHint(self, option, index):
        # Explicit square allocation keeps every page laid out the same way,
        # independent of the aspect ratios of the files on that page.
        model = index.model()
        side = max(32, int(getattr(model, "thumb_w", 200) or 200))
        gap = 14
        try:
            parent = self.parent()
            gap = max(0, int(parent.gridSize().width()) - side) if parent is not None else 14
        except Exception:
            pass
        return QSize(side + gap, side + gap)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        item = index.data(Qt.UserRole + 1) or {}
        theme = _current_theme_name()
        video = bool(item.get("is_video"))
        selected = bool(option.state & QStyle.State_Selected)

        # Draw from QPixmap directly. QIcon may preserve the intrinsic preview
        # size instead of enlarging it, which made some pages look compressed to
        # their smallest thumbnails even though the grid slot was square.
        model = index.model()
        requested_side = int(getattr(model, "thumb_w", 0) or 0)
        side = max(32, min(
            requested_side or max(32, min(option.rect.width(), option.rect.height()) - 8),
            max(32, option.rect.width() - 8),
            max(32, option.rect.height() - 8),
        ))
        cell = QRect(
            option.rect.x() + (option.rect.width() - side) // 2,
            option.rect.y() + (option.rect.height() - side) // 2,
            side,
            side,
        )

        pix = index.data(Qt.DecorationRole)
        if not isinstance(pix, QPixmap) or pix.isNull():
            painter.restore()
            return

        draw = pix.scaled(cell.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = cell.x() + (cell.width() - draw.width()) // 2
        y = cell.y() + (cell.height() - draw.height()) // 2
        image_rect = QRect(x, y, draw.width(), draw.height())

        painter.drawPixmap(image_rect, draw)

        if theme == "r34":
            border = "#6da36b"
        elif theme == "r34dark":
            border = "#355b35"
        elif theme in ("win95", "windows95"):
            border = "#808080"
        elif theme == "light":
            border = "#c8cce0"
        else:
            border = "#303645"

        width = 1
        if video:
            border = "#004cff" if theme in ("r34", "r34dark") else "#388cff"
            width = 2
        if selected:
            border = "#004cff" if theme in ("r34", "r34dark") else "#5c8dff"
            width = 2

        painter.setPen(QPen(QColor(border), width))
        painter.drawRect(image_rect.adjusted(0, 0, -1, -1))
        painter.restore()

# ── Tag autocomplete ──────────────────────────────────────────────────────────

class _TagCompleteEdit(QLineEdit):
    """LineEdit with per-token tag completion (works in middle of string)."""

    def __init__(self, tag_list_getter, parent=None):
        super().__init__(parent)
        self._get_tags = tag_list_getter
        self._popup = QListWidget()
        self._popup.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self._popup.setFocusPolicy(Qt.NoFocus)
        self._popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._popup.setMouseTracking(True)
        _CURRENT_THEME = _current_theme_name()
        if _CURRENT_THEME in ("win95", "windows95"):
            self._popup.setStyleSheet(
                "QListWidget{background:#ffffff;border:1px solid #000000;color:#000000;font-size:12px;border-radius:0px;}"
                "QListWidget::item{padding:2px 6px;border-radius:0px;}"
                "QListWidget::item:selected{background:#000080;color:#ffffff;}"
            )
        else:
            self._popup.setStyleSheet(
                "QListWidget{background:#0f1118;border:1px solid #2e3347;color:#c9cdd6;font-size:12px;border-radius:8px;}"
                "QListWidget::item{padding:4px 10px;border-radius:4px;}"
                "QListWidget::item:hover{background:#1e2236;color:#e0e4f0;}"
                "QListWidget::item:selected{background:#2d2050;color:#c9a8ff;}"
            )
        self._popup.itemClicked.connect(self._pick)
        self._popup_active = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._update_popup)
        self.textEdited.connect(lambda _: self._timer.start())

    def _current_token(self) -> str:
        text = self.text()
        pos = self.cursorPosition()
        before = text[:pos]
        token = before.split()[-1] if before.split() else ""
        return token.lstrip("-")

    def _update_popup(self):
        token = self._current_token()
        if len(token) < 1:
            self._popup.hide()
            return
        tags = self._get_tags()
        token_l = token.lower()
        matches = [t for t in tags if token_l in t.lower()][:60]
        if not matches:
            self._popup.hide()
            return
        self._popup.clear()
        for m in matches:
            self._popup.addItem(m)
        self._popup.setFixedWidth(max(300, self.width()))
        rows = min(12, len(matches))
        self._popup.setFixedHeight(rows * 22 + 4)
        gp = self.mapToGlobal(self.rect().bottomLeft())
        self._popup.move(gp)
        self._popup.show()
        # Track if mouse is inside popup
        self._popup.setMouseTracking(True)
        self._popup.entered = lambda: setattr(self, "_popup_active", True)
        self._popup.leaveEvent = lambda e: setattr(self, "_popup_active", False)

    def _pick(self, item):
        tag = item.text()
        text = self.text()
        pos = self.cursorPosition()
        before = text[:pos]
        after = text[pos:]
        parts = before.split()
        if parts:
            # Replace the last token
            neg = parts[-1].startswith("-")
            parts[-1] = ("-" if neg else "") + tag
        else:
            parts = [tag]
        new_before = " ".join(parts)
        self.setText(new_before + (" " if not after.startswith(" ") else "") + after.strip())
        self.setCursorPosition(len(new_before) + 1)
        self._popup.hide()

    def keyPressEvent(self, e):
        if self._popup.isVisible():
            if e.key() in (Qt.Key_Down, Qt.Key_Up, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape):
                if e.key() == Qt.Key_Escape:
                    self._popup.hide()
                    return
                if e.key() in (Qt.Key_Return, Qt.Key_Enter):
                    cur = self._popup.currentItem()
                    if cur:
                        self._pick(cur)
                        return
                self._popup.setFocus()
                self._popup.keyPressEvent(e)
                self.setFocus()
                return
        super().keyPressEvent(e)
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._popup.hide()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        # Delay hide so click on popup can register first
        QTimer.singleShot(300, self._maybe_hide_popup)

    def _maybe_hide_popup(self):
        # Only hide if the popup itself doesn't have focus/hover
        if not self.hasFocus() and not self._popup_active:
            self._popup.hide()


# ── Gallery page ──────────────────────────────────────────────────────────────

class GalleryPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._batch: list[dict] = []
        self._sql_total: int = 0
        self._page: int = 1
        self._render_token: int = 0
        # When the post viewer crosses pages, do not rebuild hidden thumbnails
        # underneath it. Render the adopted page only when returning to gallery.
        self._viewer_page_dirty: bool = False
        self._last_filter: dict = {"q": "", "src": "all", "bucket": "all", "order": "path"}
        self._last_extra_where: list = []
        self._last_extra_params: list = []
        self._tag_list: list[str] = []
        self._sources_expanded: bool = False
        self._fav_set: set = set()
        self._global_tag_groups_cache = None
        self._global_tag_task = None
        self._global_tag_generation = 0
        self._prefetch_task = None
        self._facets_task = None
        self._facets_generation = 0
        self._source_hosts: list[str] = []
        self._build_ui()
        self.retranslate()

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        lay = QVBoxLayout(self)

        # Top bar
        row = QHBoxLayout()
        self.search_label = QLabel()
        self.search = _TagCompleteEdit(lambda: self._tag_list)
        self.search.setPlaceholderText("tag1 tag2 -tag3")
        self.clear_btn = QPushButton("×")
        self.clear_btn.setFixedWidth(36)
        self.clear_btn.clicked.connect(lambda: self.search.clear())

        self.file_filter = QComboBox()
        for lbl, dat in [("Все","all"),("Новые","inbox"),("Архив","archive"),("Удалено","trash"),("Найденные","found"),("Не найд.","no_match"),("Скачанные","downloaded")]:
            self.file_filter.addItem(lbl, dat)

        self.source_label = QLabel()
        self.source = QComboBox()
        self.source.setMinimumWidth(120)

        self.sort_label = QLabel()
        self.gallery_sort = QComboBox()
        for m in ["filename","newest","oldest"]:
            self.gallery_sort.addItem(m, m)

        self.fav = QCheckBox()
        self.rating_min = QComboBox()
        self.rating_min.setToolTip("Минимальный рейтинг")
        for label, val in [("★ все","0"),("★","1"),("★★","2"),
                            ("★★★","3"),("★★★★","4"),("★★★★★","5")]:
            self.rating_min.addItem(label, val)
        self.refresh_btn = QPushButton()

        # Sort: compact label
        self.gallery_sort.setFixedWidth(100)
        self.sort_label.setFixedWidth(130)

        # Rating: op + stars
        self.rating_op = QComboBox()
        self.rating_op.setFixedWidth(105)
        for label, val in [("рейтинг",""),("равно","="),("не меньше",">="),("не больше","<=")]:
            self.rating_op.addItem(label, val)
        self.rating_stars = QComboBox()
        self.rating_stars.setFixedWidth(110)
        for label, val in [("—","0"),("★","1"),("★★","2"),("★★★","3"),("★★★★","4"),("★★★★★","5")]:
            self.rating_stars.addItem(label, val)

        row.addWidget(self.search, 4)
        row.addWidget(self.clear_btn)
        row.addWidget(self.file_filter)
        row.addWidget(self.sort_label)
        row.addWidget(self.gallery_sort)
        row.addWidget(self.rating_op)
        row.addWidget(self.rating_stars)
        row.addWidget(self.fav)
        row.addWidget(self.refresh_btn)
        lay.addLayout(row)

        # Live filter preview for human search
        self._filter_preview = QLabel("")
        self._filter_preview.setStyleSheet(
            "color:#6080c0;font-size:11px;padding:1px 4px;")
        self._filter_preview.setVisible(False)
        lay.addWidget(self._filter_preview)

        # Splitter
        splitter = QSplitter(Qt.Horizontal)
        lay.addWidget(splitter, 1)

        # Left panel
        left = QWidget()
        left.setFixedWidth(390)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)

        self.sources_title = QLabel()
        self.sources_list = QListWidget()
        self.sources_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sources_list.setFixedWidth(360)
        self.sources_toggle = QPushButton()
        self.sources_toggle.clicked.connect(self._toggle_sources)
        ll.addWidget(self.sources_title)
        ll.addWidget(self.sources_list)
        ll.addWidget(self.sources_toggle)

        tag_head_w = QWidget()
        tag_head_w.setFixedWidth(360)
        tag_head = QHBoxLayout(tag_head_w)
        tag_head.setContentsMargins(0, 0, 0, 0)
        self.page_tags_title = QLabel()
        self.tag_sort = QComboBox()
        self.tag_sort.setFixedWidth(190)
        for m in ["count_desc","count_asc","alpha","alpha_desc"]:
            self.tag_sort.addItem(m, m)
        self.tag_sort.currentIndexChanged.connect(lambda _: self._render_page_tags())
        tag_head.addWidget(self.page_tags_title, 1, Qt.AlignLeft)
        tag_head.addWidget(self.tag_sort, 0, Qt.AlignRight)
        ll.addWidget(tag_head_w, 0, Qt.AlignLeft)

        self.page_tags = QListWidget()
        self.page_tags.setFixedWidth(360)
        self.page_tags.setContextMenuPolicy(Qt.CustomContextMenu)
        self.page_tags.customContextMenuRequested.connect(self._tag_color_context_menu)
        ll.addWidget(self.page_tags, 1, Qt.AlignLeft)
        splitter.addWidget(left)

        # Right panel
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        self.info = QLabel("")
        rl.addWidget(self.info)
        self.render_warning = QLabel("")
        self.render_warning.setVisible(False)
        self.render_warning.setWordWrap(True)
        self.render_warning.setStyleSheet("color:#e6a23c;padding:4px 8px;background:#251b0f;border:1px solid #6d4b18;border-radius:5px;")
        rl.addWidget(self.render_warning)

        self.view = QListView()
        self.view.setViewMode(QListView.IconMode)
        self.view.setResizeMode(QListView.Adjust)
        self.view.setMovement(QListView.Static)
        self.view.setUniformItemSizes(True)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setWrapping(True)
        self.view.setSpacing(14)
        self.view.setWordWrap(False)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.model = GalleryListModel(self)
        self.view.setModel(self.model)
        self.view.setItemDelegate(GalleryCardDelegate(self.view))
        self.view.setStyleSheet("QListView{background:transparent;border:none;} QListView::item{background:transparent;border:none;}")
        self.view.clicked.connect(lambda idx: self._open_post(idx.row()))
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._show_context_menu)
        rl.addWidget(self.view, 1)

        # Pager
        pager = QHBoxLayout()
        self.prev_btn = QPushButton()
        self.next_btn = QPushButton()
        self.page_label = QLabel("")
        self.page_input = QSpinBox()
        self.page_input.setMinimum(1)
        self.page_input.setMaximum(1)
        self.page_input.setFixedWidth(90)
        self.go_btn = QPushButton()
        pager.addWidget(self.prev_btn)
        pager.addStretch(1)
        pager.addWidget(self.page_label)
        pager.addWidget(self.page_input)
        pager.addWidget(self.go_btn)
        pager.addStretch(1)
        pager.addWidget(self.next_btn)
        rl.addLayout(pager)
        splitter.addWidget(right)
        splitter.setSizes([390, 1100])

        # Signals
        # Search fires on Return; live typing only updates autocomplete
        self.search.returnPressed.connect(self.apply_filter)
        self.rating_op.currentIndexChanged.connect(self.apply_filter)
        self.rating_op.currentIndexChanged.connect(self._on_rating_op_changed)
        self.rating_stars.currentIndexChanged.connect(self.apply_filter)
        # Search intentionally runs only on Enter/Refresh. On very large
        # libraries every typed character should not rebuild the SQL result.

        self.clear_btn.clicked.connect(lambda: (self.search.clear(), self.apply_filter()))
        self.file_filter.currentIndexChanged.connect(self.apply_filter)
        self.source.currentTextChanged.connect(self.apply_filter)
        self.gallery_sort.currentIndexChanged.connect(self.apply_filter)
        self.fav.stateChanged.connect(self._on_fav_changed)
        self.refresh_btn.clicked.connect(self.refresh_force)
        self.prev_btn.clicked.connect(self._prev_page)
        self.next_btn.clicked.connect(self._next_page)
        self.go_btn.clicked.connect(self._go_to_page)
        self.page_tags.itemClicked.connect(self._tag_single)
        self.page_tags.itemDoubleClicked.connect(self._tag_add)
        self.sources_list.itemClicked.connect(self._source_from_list)

    def retranslate(self):
        t = getattr(self.main, "t", lambda x: x)
        self.search_label.setText(t("Search") + ":")
        self.source_label.setText(t("Source") + ":")
        self.sort_label.setText(t("Image sort") + ":")
        self.go_btn.setText(t("Go"))
        self.fav.setText(t("Favorites only"))
        self.refresh_btn.setText(t("Refresh"))
        try:
            from pathlib import Path as _P
            from PySide6.QtGui import QIcon
            from PySide6.QtCore import QSize
            _theme = getattr(self.main, "settings", {}).get("appearance", "dark")
            _sfx = "_dark" if _theme in ("light", "r34", "win95", "windows95") else ""
            _base = _P(__file__).parent.parent / "assets" / "icons"
            for _n in [f"refresh{_sfx}", "refresh"]:
                _p = _base / f"{_n}.ico"
                if _p.exists():
                    _ico = QIcon(str(_p))
                    if not _ico.isNull():
                        self.refresh_btn.setIcon(_ico)
                        self.refresh_btn.setIconSize(QSize(16, 16))
                        break
        except Exception:
            pass
        self.sources_title.setText(t("Sources"))
        self.page_tags_title.setText("Теги на странице (всего)" if getattr(self.main, "settings", {}).get("language", "ru") == "ru" else "Page tags (total)")
        self.page_tags_title.setToolTip("Показаны теги текущей страницы; число — количество во всей галерее.")
        self.prev_btn.setText("← " + t("Prev"))
        self.next_btn.setText(t("Next") + " →")
        self.sources_toggle.setText(t("Show all"))


    def apply_theme_style(self, theme_name: str | None = None):
        """Refresh inline-styled gallery pieces after a runtime theme switch."""
        try:
            global _PH_CACHE
            _PH_CACHE.clear()
        except Exception:
            pass
        try:
            self._filter_preview.setStyleSheet("color:#003c8f;font-size:11px;padding:1px 4px;" if theme_name in ("r34", "win95", "windows95", "light") else "color:#6080c0;font-size:11px;padding:1px 4px;")
        except Exception:
            pass
        # Existing ImageCard instances have inline QSS; rebuild them so card borders,
        # label backgrounds and placeholder colors match the new theme immediately.
        try:
            if getattr(self, "_batch", None):
                QTimer.singleShot(0, self._render_page)
        except Exception:
            pass
        try:
            self.view.viewport().update()
            self.retranslate()
        except Exception:
            pass

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh(self):
        if not self._batch and self._sql_total == 0:
            self.refresh_force()

    def refresh_force(self):
        # Render the SQL page immediately, then update expensive facets in a
        # worker.  At ~10k results these GROUP BY queries were visibly freezing
        # the UI before any cards appeared.
        self._global_tag_groups_cache = None
        self._global_tag_generation += 1
        self.apply_filter()
        self._facets_generation += 1
        generation = self._facets_generation
        if self._facets_task is not None:
            try: self._facets_task.cancel()
            except Exception: pass
        def done(result):
            if generation != self._facets_generation or not result:
                return
            self._tag_list = list(result.get("tags", []))
            src_list = list(result.get("sources", []))
            from core.media_utils import host_from_url
            hosts = []
            seen = set()
            for url in src_list:
                host = host_from_url(url) if "://" in str(url) else str(url)
                if host and host not in seen:
                    seen.add(host); hosts.append(host)
            self._source_hosts = sorted(hosts)
            self._source_counts = dict(result.get("source_counts", {}))
            self._source_unique_total = int(result.get("source_total", 0) or 0)
            cur_src = self.source.currentText() or "all"
            self.source.blockSignals(True); self.source.clear(); self.source.addItem("all")
            for host in self._source_hosts: self.source.addItem(host)
            index = self.source.findText(cur_src)
            self.source.setCurrentIndex(index if index >= 0 else 0); self.source.blockSignals(False)
            self._render_source_list(self._source_hosts)
        def finished():
            if generation == self._facets_generation:
                self._facets_task = None
        self._facets_task = self.main.task_manager.submit(
            _gallery_facets_worker, dict(self.main.settings or {}), name="gallery-facets",
            on_result=done, on_finished=finished,
            on_error=lambda error: print("GALLERY FACETS ERROR:", error),
        )

    def _render_source_list(self, hosts: list):
        self.sources_list.clear()
        sc = getattr(self, "_source_counts", {})
        # Each source count may include the same image. "all" must show unique
        # files, not the sum of file↔source links.
        all_count = int(getattr(self, "_source_unique_total", 0) or 0) if sc else self._sql_total
        items = [("all", all_count)] + sorted((h, sc.get(h, 0)) for h in hosts)
        visible = items if self._sources_expanded else items[:5]
        for h, cnt in visible:
            label = f"{h}  {cnt}" if cnt else h
            self.sources_list.addItem(label)
        h_px = min(300, 10 + max(1, len(visible)) * 22)
        self.sources_list.setFixedHeight(h_px)
        self.sources_toggle.setVisible(len(items) > 5)

    def _toggle_sources(self):
        self._sources_expanded = not self._sources_expanded
        t = getattr(self.main, "t", lambda x: x)
        self.sources_toggle.setText(t("Hide") if self._sources_expanded else t("Show all"))
        # Re-render from cached facets; toggling a list must never trigger a
        # fresh full-table source aggregation query.
        self._render_source_list(list(getattr(self, "_source_hosts", [])))

    def _source_from_list(self, item):
        # Strip count suffix ("host  42" → "host")
        host = item.text().split("  ")[0].strip()
        idx = self.source.findText(host)
        self.source.setCurrentIndex(idx if idx >= 0 else 0)

    # ── Favorites ─────────────────────────────────────────────────────────────

    def _on_fav_changed(self):
        # Favorites are filtered in SQL, before COUNT/LIMIT/OFFSET.
        # The old client-side filter only filtered the visible page: with one
        # favorite on page 2 it produced empty page 1 and kept 79 total pages.
        self._page = 1
        self.apply_filter()

    def favorite_updated(self, path: str, enabled: bool) -> None:
        """Refresh a Favorites-only result when PostPage changes a star."""
        if self.fav.isChecked():
            self._page = 1
            self.apply_filter()

    # ── Filter ────────────────────────────────────────────────────────────────

    def _on_rating_op_changed(self):
        op = self.rating_op.currentData() if hasattr(self, 'rating_op') else ''
        if hasattr(self, 'rating_stars'):
            self.rating_stars.setEnabled(bool(op))
            if not op:
                self.rating_stars.setCurrentIndex(0)

    def apply_filter(self):
        try:
            self._apply_filter_impl()
        except Exception as e:
            import logging
            logging.getLogger("local_booru").error("apply_filter error: %s", e, exc_info=True)

    def _apply_filter_impl(self):
        raw_q = self.search.text().strip()

        src = self.source.currentText() or "all"
        ff = self.file_filter.currentData() or "all"
        mode = self.gallery_sort.currentData() or "filename"
        order = {"newest": "newest", "oldest": "oldest"}.get(mode, "path")

        # Build extra conditions BEFORE count
        _parse_result = parse_query(raw_q)
        _extra_where: list[str] = []
        _extra_params: list = []
        q = raw_q  # default: use full query as tags
        if _parse_result.filters:
            _frags, _params = _num_to_sql(_parse_result.filters)
            _extra_where.extend(_frags)
            _extra_params.extend(_params)
            previews = " | ".join(f.display for f in _parse_result.filters)
            self._filter_preview.setText("→  " + previews)
            self._filter_preview.setVisible(True)
            q = " ".join(_parse_result.tags)  # only tag portion
        else:
            self._filter_preview.setVisible(False)

        _rating_op    = getattr(self, "rating_op", None)
        _rating_stars = getattr(self, "rating_stars", None)
        if _rating_op and _rating_stars:
            op = _rating_op.currentData() or ""
            stars = int(_rating_stars.currentData() or "0")
            if op and stars > 0:
                op_safe = op if op in ("=",">=","<=") else ">="
                _extra_where.append(f"COALESCE(i.rating, 0) {op_safe} ?")
                _extra_params.append(stars)

        # Favorites-only must be part of the database result set, not a Python
        # filter applied after pagination. Otherwise the selected favorite is
        # visible only on its old physical page and every other page is blank.
        if self.fav.isChecked():
            _extra_where.append("COALESCE(i.favorite, 0) = 1")

        total = count_search_items(self.main.settings, q, src, ff,
                                   extra_where=_extra_where, extra_params=_extra_params)
        self._sql_total = int(total or 0)
        self._last_filter = {"q": q, "src": src, "bucket": ff, "order": order}
        self._last_extra_where  = _extra_where   # save for _render_page
        self._last_extra_params = _extra_params
        self._page = 1
        self._render_page()

    def current_result_image_ids(self) -> list[int]:
        """Return IDs in the current full gallery result, not only visible page."""
        items = search_items(
            self.main.settings,
            query=self._last_filter.get("q", ""),
            source=self._last_filter.get("src", "all"),
            bucket=self._last_filter.get("bucket", "all"),
            limit=None,
            offset=0,
            order=self._last_filter.get("order", "path"),
            extra_where=getattr(self, "_last_extra_where", None),
            extra_params=getattr(self, "_last_extra_params", None),
        ) or []
        # Favorites-only is already included in _last_extra_where, so this is
        # the same full SQL result set the gallery paginates over.
        return [int(x["id"]) for x in items if x.get("id") is not None]

    # ── Page ──────────────────────────────────────────────────────────────────

    def _per_page(self) -> int:
        cols = int(self.main.settings.get("columns", 4))
        rows = int(self.main.settings.get("rows_per_page", 4))
        return max(1, cols * rows)

    def _render_page(self):
        self._viewer_page_dirty = False
        self._render_token += 1
        token = self._render_token

        per = self._per_page()
        total = self._sql_total
        maxp = max(1, (total + per - 1) // per)
        self._page = max(1, min(self._page, maxp))
        offset = (self._page - 1) * per

        try:
            batch = search_items(
                self.main.settings,
                query=self._last_filter.get("q", ""),
                source=self._last_filter.get("src", "all"),
                bucket=self._last_filter.get("bucket", "all"),
                limit=per,
                offset=offset,
                order=self._last_filter.get("order", "path"),
                extra_where=getattr(self, "_last_extra_where", None),
                extra_params=getattr(self, "_last_extra_params", None),
            ) or []
        except Exception as exc:
            import logging
            logging.getLogger("local_booru").exception("GALLERY PAGE QUERY FAILED")
            batch = []
            self.render_warning.setText(f"Ошибка выдачи карточек: {exc}. Смотри журнал ошибок.")
            self.render_warning.setVisible(True)
        else:
            if total > 0 and not batch and offset < total:
                self.render_warning.setText("В базе есть файлы, но SQL-страница вернула пустой результат. Обнови галерею и пришли errors.log.")
                self.render_warning.setVisible(True)
            else:
                self.render_warning.setVisible(False)

        # The SQL query already applies the favorites condition before LIMIT
        # and OFFSET, so paging remains dense and the total/page count is true.
        self._batch = batch

        t = getattr(self.main, "t", lambda x: x)
        self.info.setText(f"{t('Images')}: {total}")
        self.page_label.setText(f"Page {self._page}/{maxp}")
        self.page_input.setMaximum(maxp)
        self.page_input.blockSignals(True)
        self.page_input.setValue(self._page)
        self.page_input.blockSignals(False)
        self.prev_btn.setVisible(self._page > 1)
        self.next_btn.setVisible(self._page < maxp)

        QTimer.singleShot(0, lambda b=batch, tk=token: self._render_cards(b, tk))

    def adopt_viewer_page(self, page: int, batch: list[dict]):
        """Record page reached in PostPage without painting a hidden gallery."""
        self._page = max(1, int(page))
        self._batch = list(batch or [])
        self._viewer_page_dirty = True

    def render_after_viewer_navigation(self):
        """Paint the already loaded viewer page once the gallery is visible again."""
        if not self._viewer_page_dirty:
            return
        self._viewer_page_dirty = False
        self._render_token += 1
        token = self._render_token
        per = self._per_page()
        total = self._sql_total
        maxp = max(1, (total + per - 1) // per)
        self._page = max(1, min(self._page, maxp))
        t = getattr(self.main, "t", lambda x: x)
        self.info.setText(f"{t('Images')}: {total}")
        self.page_label.setText(f"Page {self._page}/{maxp}")
        self.page_input.setMaximum(maxp)
        self.page_input.blockSignals(True)
        self.page_input.setValue(self._page)
        self.page_input.blockSignals(False)
        self.prev_btn.setVisible(self._page > 1)
        self.next_btn.setVisible(self._page < maxp)
        batch = list(self._batch)
        QTimer.singleShot(0, lambda b=batch, tk=token: self._render_cards(b, tk))

    def _clear_grid(self):
        try:
            self.model.set_items([], 220, 200)
        except Exception:
            pass

    def _render_cards(self, batch: list, token: int):
        if token != self._render_token:
            return
        cols = max(1, int(self.main.settings.get("columns", 4)))
        max_tile = max(64, int(self.main.settings.get("card_height", 220) or 220))
        theme = _current_theme_name()
        spacing = 7 if theme in ("r34", "r34dark") else 10
        vieww = max(240, self.view.viewport().width() - 8)
        available_tile = max(48, int((vieww - (cols * spacing * 2)) / cols))
        # One fixed square slot per item; the image is aspect-fit inside it.
        # card_height now really caps preview size on wide windows.
        tile = max(48, min(max_tile, available_tile))
        self.view.setSpacing(0)  # padding is accounted for in gridSize
        self.view.setGridSize(QSize(tile + spacing * 2, tile + spacing * 2))
        self.view.setIconSize(QSize(tile, tile))
        self.model.quality_scale = max(1, min(3, int(self.main.settings.get("thumb_quality_scale", 2) or 2)))
        self.model._pix_cache_max = max(50, min(2000, int(self.main.settings.get("thumb_memory_items", 400) or 400)))
        self.model.set_items(batch, tile, tile)
        QTimer.singleShot(30, self._render_page_tags)
        QTimer.singleShot(150, self._prefetch_neighbor_pages)

    def _render_card_batch(self, batch, h, cols, w, token, start, chunk):
        # Compatibility for old calls; QListView/GalleryListModel handles virtual rows now.
        self._render_cards(batch, token)

    # ── Page tags ─────────────────────────────────────────────────────────────

    def _request_global_tag_groups(self):
        if self._global_tag_task is not None:
            return
        generation = self._global_tag_generation
        settings = dict(self.main.settings or {})
        def complete(groups):
            if generation != self._global_tag_generation or groups is None:
                return
            self._global_tag_groups_cache = groups
            QTimer.singleShot(0, self._render_page_tags)
        def finished():
            self._global_tag_task = None
        self._global_tag_task = self.main.task_manager.submit(
            _global_tag_groups_worker, settings, name="gallery-sidebar-tag-counts",
            on_result=complete, on_error=lambda _error: complete({}), on_finished=finished,
        )

    def _prefetch_neighbor_pages(self):
        """Warm thumbnail cache for the pages beside the visible one.

        It is intentionally low-impact: database fetch and thumbnail scheduling
        happen after the visible page was painted, and no metadata is modified.
        """
        if self._prefetch_task is not None or not bool(self.main.settings.get("thumb_prefetch_pages", True)):
            return
        per = self._per_page()
        if per <= 0:
            return
        pages = []
        maxp = max(1, (self._sql_total + per - 1) // per)
        if self._page > 1: pages.append(self._page - 1)
        if self._page < maxp: pages.append(self._page + 1)
        if not pages:
            return
        settings = dict(self.main.settings or {})
        filt = dict(self._last_filter); extra_where = list(self._last_extra_where); extra_params = list(self._last_extra_params)
        def load_neighbor_paths():
            paths = []
            for page in pages:
                rows = search_items(settings, query=filt.get("q", ""), source=filt.get("src", "all"), bucket=filt.get("bucket", "all"), limit=per, offset=(page - 1) * per, order=filt.get("order", "path"), extra_where=extra_where, extra_params=extra_params)
                paths.extend(str(x.get("path", "")) for x in rows if x.get("path"))
            return paths
        def warm(paths):
            svc = ThumbnailService.instance()
            tile = max(48, min(int(self.main.settings.get("card_height", 220) or 220), 512))
            quality = max(1, min(3, int(self.main.settings.get("thumb_quality_scale", 2) or 2)))
            for path in paths or []:
                svc.request(path, min(768, tile * quality), min(768, tile * quality))
        def finished():
            self._prefetch_task = None
        self._prefetch_task = self.main.task_manager.submit(load_neighbor_paths, name="gallery-thumb-prefetch", on_result=warm, on_finished=finished)

    def _render_page_tags(self):
        batch = list(self._batch)
        if not batch:
            self.page_tags.clear()
            return
        try:
            enrich_items(self.main.settings, batch)
        except Exception:
            pass
        # Load GLOBAL tag counts from DB (not just current page). Cache the
        # aggregation: changing page/source must not re-run a GROUP BY across
        # ten thousand+ indexed posts every time. Refresh clears this cache.
        global_groups = getattr(self, "_global_tag_groups_cache", None)
        if global_groups is None:
            self.page_tags.clear()
            self.page_tags.addItem("Загрузка общего списка тегов в фоне…")
            self._request_global_tag_groups()
            return
        # Build page-local tag set to filter which tags appear on this page
        page_tags_set: set[str] = set()
        for item in batch:
            groups = item.get("tag_groups") or {"general": item.get("tags", [])}
            for tags in groups.values():
                for tag in tags:
                    nt = normalize_tag(tag)
                    if nt:
                        page_tags_set.add(nt)
        self.page_tags.clear()
        mode = self.tag_sort.currentData() or "count_desc"
        group_order = list(self.main.settings.get("tag_group_order") or GROUP_ORDER)
        for _group in GROUP_ORDER:
            if _group not in group_order: group_order.append(_group)
        colors = dict(GROUP_COLORS); colors.update(self.main.settings.get("tag_group_colors") or {})
        for group in group_order:
            global_g = global_groups.get(group, {})
            # Show only tags present on current page, with global counts
            filtered = {t: c for t, c in global_g.items() if normalize_tag(t) in page_tags_set}
            if not filtered:
                continue
            self._add_header(group)
            for tag, count in sort_tag_items(filtered.items(), mode)[:500]:
                it = QListWidgetItem(f"    {tag}    {count}")
                it.setData(Qt.UserRole, tag)
                it.setToolTip(f"{group}: {tag} (всего в базе)")
                it.setForeground(QBrush(QColor(tag_display_color(tag, group, self.main.settings, colors))))
                self.page_tags.addItem(it)

    def _add_header(self, group: str):
        t = getattr(self.main, "t", lambda x: x)
        it = QListWidgetItem(t(group))
        it.setFlags(Qt.NoItemFlags)
        colors = dict(GROUP_COLORS); colors.update(self.main.settings.get("tag_group_colors") or {})
        it.setForeground(QBrush(QColor(colors.get(group, "#888"))))
        f = it.font(); f.setBold(True); f.setPointSize(max(f.pointSize(), 12)); it.setFont(f)
        self.page_tags.addItem(it)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _prev_page(self):
        if self._page > 1:
            self._page -= 1
            self._clear_grid()
            self.page_tags.clear()
            QTimer.singleShot(0, self._render_page)

    def _next_page(self):
        per = self._per_page()
        maxp = max(1, (self._sql_total + per - 1) // per)
        if self._page < maxp:
            self._page += 1
            self._clear_grid()
            self.page_tags.clear()
            QTimer.singleShot(0, self._render_page)

    def _go_to_page(self):
        self._page = int(self.page_input.value())
        self._clear_grid()
        self.page_tags.clear()
        QTimer.singleShot(0, self._render_page)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        batch = getattr(self, "_batch", None) or (self.model.items if hasattr(self, "model") else None)
        if self.isVisible() and batch:
            QTimer.singleShot(150, self._render_page)

    # ── Open post ─────────────────────────────────────────────────────────────

    def _open_post(self, idx: int):
        if 0 <= idx < len(self._batch):
            try:
                enrich_items(self.main.settings, [self._batch[idx]])
            except Exception:
                pass
        self.main.open_post(idx, self._batch)

    def open_post(self, idx: int):
        self._open_post(idx)

    def random_post(self):
        """Pick a random item from the ENTIRE filtered result set via SQL."""
        from core.database.connection import db as db_ctx
        import random
        try:
            q = self._last_filter.get("q", "")
            src = self._last_filter.get("src", "all")
            bucket = self._last_filter.get("bucket", "all")
            total = self._sql_total
            if total <= 0:
                return
            # Pick a random offset and fetch 1 item
            rand_offset = random.randint(0, max(0, total - 1))
            items = search_items(
                self.main.settings,
                query=q, source=src, bucket=bucket,
                limit=1, offset=rand_offset,
                order=self._last_filter.get("order", "path"),
            )
            if not items:
                return
            item = items[0]
            try:
                enrich_items(self.main.settings, [item])
            except Exception:
                pass
            self.main.open_post(0, [item])
        except Exception as e:
            print("RANDOM ERROR:", e)
            # Fallback to current page
            if self._batch:
                idx = random.randrange(len(self._batch))
                self.main.open_post(idx, self._batch)

    def _show_context_menu(self, point):
        idx = self.view.indexAt(point)
        if not idx.isValid() or idx.row() >= len(self._batch):
            return
        item = self._batch[idx.row()]
        path = Path(str(item.get("path", "")))
        menu = QMenu(self)
        open_action = menu.addAction("Открыть")
        folder_action = menu.addAction("Открыть папку файла")
        copy_file_action = menu.addAction("Копировать файл")
        menu.addSeparator()
        copy_path_action = menu.addAction("Копировать путь")
        copy_md5_action = menu.addAction("Копировать MD5")
        copy_source_action = menu.addAction("Копировать источники")
        copy_tags_action = menu.addAction("Копировать теги")
        menu.addSeparator()
        delete_action = menu.addAction("Удалить (в корзину)")
        chosen = menu.exec(self.view.viewport().mapToGlobal(point))
        if chosen is None:
            return
        try:
            if chosen == open_action:
                self._open_post(idx.row())
            elif chosen == folder_action:
                import os
                os.startfile(str(path.parent)) if hasattr(os, "startfile") else __import__("subprocess").Popen(["xdg-open", str(path.parent)])
            elif chosen == copy_file_action:
                mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(path))]); QApplication.clipboard().setMimeData(mime)
            elif chosen == copy_path_action:
                QApplication.clipboard().setText(str(path))
            elif chosen == copy_md5_action:
                QApplication.clipboard().setText(str(item.get("hash_md5") or ""))
            elif chosen == copy_source_action:
                enrich_items(self.main.settings, [item]); QApplication.clipboard().setText("\n".join(s.get("url", "") for s in item.get("sources", []) if s.get("url")))
            elif chosen == copy_tags_action:
                enrich_items(self.main.settings, [item]); QApplication.clipboard().setText(" ".join(item.get("tags", [])))
            elif chosen == delete_action:
                answer = QMessageBox.question(
                    self, "Удалить файл",
                    f"Переместить в корзину?\n\n{path.name}\n\nФайл можно будет восстановить в разделе «Удалено».",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if answer == QMessageBox.Yes:
                    from core.library_lifecycle import trash_media_paths
                    result = trash_media_paths(self.main.settings, [path], reason="gallery_context_delete", make_backup=True)
                    if result.get("error"):
                        raise RuntimeError(result.get("error"))
                    self.refresh_force()
                    try:
                        self.main.trash_page.refresh()
                    except Exception:
                        pass
        except Exception as e:
            QMessageBox.warning(self, "Галерея", str(e))

    # ── Tag interactions ──────────────────────────────────────────────────────

    def _tag_color_context_menu(self, point):
        item = self.page_tags.itemAt(point)
        if item is None or not (item.flags() & Qt.ItemIsEnabled):
            return
        tag = str(item.data(Qt.UserRole) or "").strip()
        if not tag:
            return
        menu = QMenu(self)
        set_color = menu.addAction("Выбрать цвет тега...")
        clear_color = menu.addAction("Сбросить цвет тега")
        action = menu.exec(self.page_tags.viewport().mapToGlobal(point))
        if action == set_color:
            current = QColor(tag_display_color(tag, "general", self.main.settings, GROUP_COLORS))
            selected = QColorDialog.getColor(current, self, f"Цвет тега: {tag}")
            if selected.isValid():
                colors = dict(self.main.settings.get("tag_colors") or {})
                colors[normalize_tag(tag).lower()] = selected.name()
                self.main.settings["tag_colors"] = colors
                self.main.save_settings()
                self._render_page_tags()
                try: self.main.tags_page.render_all(); self.main.tags_page.render_group()
                except Exception: pass
        elif action == clear_color:
            colors = dict(self.main.settings.get("tag_colors") or {})
            colors.pop(normalize_tag(tag).lower(), None)
            self.main.settings["tag_colors"] = colors
            self.main.save_settings()
            self._render_page_tags()
            try: self.main.tags_page.render_all(); self.main.tags_page.render_group()
            except Exception: pass

    def _tag_single(self, item):
        tag = item.data(Qt.UserRole)
        if tag:
            try: self.main.open_tag_single(tag)
            except Exception: pass

    def _tag_add(self, item):
        tag = item.data(Qt.UserRole)
        if tag:
            try: self.main.open_tag_add(tag)
            except Exception: pass
