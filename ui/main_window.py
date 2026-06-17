"""Main window — sidebar navigation, clean layout."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QIcon, QPixmap, QColor, QPainter, QFont
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QMenu, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget, QFrame, QSizePolicy, QSpacerItem, QComboBox,
    QToolButton,
)

from core.app_context import AppContext
from core.task_manager import TaskManager
from core.i18n import tr
from core.paths import APP_ICON_FILE
from ui.modules.registry import PAGE_BY_KEY, PAGE_SPECS, WORKSPACE_DEFAULT_PAGE, WORKSPACE_TITLES
from ui.styles.themes import stylesheet_for
from ui.visual_polish import apply_visual_polish

# Icons: emoji fallback — looks fine with Segoe UI Emoji on Windows
NAV_ICONS = {
    "Tagger":     ("🔍", "parser"),
    "ParserBlueprint": ("🧩", "parser"),
    "Tags":       ("🏷", "tags"),
    "Trash":      ("", "action_delete"),
    "Diagnostics": ("", "diagnostics"),
    "NO_MATCH":   ("❓", "nomatch"),
    "Overview":   ("📊", "home"),
    "Gallery":    ("🖼", "gallery"),
    "Manga":      ("📖", "manga"),
    "Games":      ("🎮", "games"),
    "DLER":       ("⬇", "grabber"),
    "Subs":       ("🔄", "subs"),
    "Duplicates": ("♊", "dupes"),
    "Post":       ("🔎", "gallery"),
    "Settings":   ("⚙", "settings"),
}

_ICON_CACHE: dict = {}
_CURRENT_THEME: str = "dark"

def _is_light_theme(theme_name: str) -> bool:
    """Themes that need dark icon variants on light backgrounds."""
    return theme_name in ("light", "r34", "win95", "windows95")

def _load_icon(name: str, light_theme: bool = False) -> "QIcon | None":
    """Load QIcon from assets/icons/. White icons for dark themes, dark for light."""
    suffix = "_dark" if light_theme else ""
    cache_key = f"{name}{suffix}"
    if cache_key in _ICON_CACHE:
        return _ICON_CACHE[cache_key]
    try:
        base = Path(__file__).parent.parent / "assets" / "icons"
        # Try themed variant first, fallback to base
        candidates = [
            base / f"{name}{suffix}.ico",
            base / f"{name}{suffix}.png",
            base / f"{name}.ico",
            base / f"{name}.png",
        ]
        for p in candidates:
            if p.exists():
                icon = QIcon(str(p))
                if not icon.isNull():
                    _ICON_CACHE[cache_key] = icon
                    return icon
    except Exception:
        pass
    _ICON_CACHE[cache_key] = None
    return None


def _make_nav_btn(text: str, emoji: str = "", icon_name: str = "", light: bool = False) -> QPushButton:
    btn = QPushButton()
    btn.setObjectName("NavBtn")
    btn.setCheckable(True)
    btn.setMinimumHeight(40)
    btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    btn.setFocusPolicy(Qt.NoFocus)
    icon = _load_icon(icon_name, light) if icon_name else None
    if icon:
        btn.setIcon(icon)
        btn.setIconSize(QSize(18, 18))
        btn.setText(f"  {text}")
    else:
        btn.setText(f"  {emoji}  {text}" if emoji else f"  {text}")
    return btn


def _separator() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("border: none; background: #1e2130; max-height: 1px; margin: 4px 12px;")
    return f


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.context = AppContext()
        self.settings = self.context.settings
        try:
            from core.local_parallel import local_workers
            _task_workers = local_workers(self.settings, "task_max_workers", int(self.settings.get("local_background_workers", 4) or 4), maximum=16)
        except Exception:
            _task_workers = int(self.settings.get("task_max_workers", 4) or 4)
        self.task_manager = TaskManager(self, max_workers=_task_workers)
        self.pages: dict = {}
        self.page_buttons: dict = {}
        self.setWindowTitle("Local Booru")
        # Restore window size, clamped to available screen
        try:
            from PySide6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()
            avail = screen.availableGeometry() if screen else None
            if avail:
                max_w = avail.width()
                max_h = avail.height()
            else:
                max_w, max_h = 1920, 1080
        except Exception:
            max_w, max_h = 1920, 1080
        w = min(int(self.settings.get("window_w", 1440)), max_w)
        h = min(int(self.settings.get("window_h", 880)), max_h)
        self.resize(w, h)
        self.setMinimumSize(800, 500)  # allow window to be resized smaller

        if APP_ICON_FILE.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_FILE)))

        # ── Root layout: sidebar + content ───────────────────────────────────
        root = QWidget()
        root_lay = QHBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ── Sidebar ───────────────────────────────────────────────────────────
        self._sidebar = QWidget()
        self._sidebar.setObjectName("Sidebar")
        self._sidebar.setFixedWidth(216)
        self._sidebar.setObjectName("Sidebar")
        # Background set by QSS theme via #Sidebar selector
        sidebar_lay = QVBoxLayout(self._sidebar)
        sidebar_lay.setContentsMargins(10, 18, 10, 12)
        sidebar_lay.setSpacing(2)

        # Logo / title
        self.logo = QLabel()
        self.logo.setObjectName("Logo")
        self.logo.setAlignment(Qt.AlignCenter)
        self.logo.setFixedHeight(52)
        self.logo.setWordWrap(False)
        sidebar_lay.addWidget(self.logo)

        # Workspace switcher (compact)
        self.mode_btn = QPushButton("  Парсер")
        self.mode_btn.setObjectName("ModeBtn")
        self.mode_btn.setFixedHeight(32)
        self.mode_menu = QMenu(self)
        self.mode_btn.setMenu(self.mode_menu)
        sidebar_lay.addWidget(self.mode_btn)
        sidebar_lay.addSpacing(6)

        # Nav buttons are reusable widgets; their order and the collapsible
        # «Дополнительно» group are controlled from Settings by drag&drop.
        for spec in PAGE_SPECS:
            if not spec.button_attr or spec.key == "Settings":
                continue  # Settings is pinned at bottom separately
            em, ico = NAV_ICONS.get(spec.key, ("•", ""))
            btn = _make_nav_btn(spec.button_text, em, ico)
            setattr(self, spec.button_attr, btn)
            self.page_buttons[spec.key] = btn
            btn.clicked.connect(lambda _=False, key=spec.key: self.go(key))

        self._nav_primary_widget = QWidget()
        self._nav_primary_layout = QVBoxLayout(self._nav_primary_widget)
        self._nav_primary_layout.setContentsMargins(0, 0, 0, 0); self._nav_primary_layout.setSpacing(2)
        sidebar_lay.addWidget(self._nav_primary_widget)
        self._nav_extra_toggle = QPushButton("Дополнительно  ▸")
        self._nav_extra_toggle.setObjectName("ModeBtn")
        self._nav_extra_toggle.setFixedHeight(30); self._nav_extra_toggle.setFocusPolicy(Qt.NoFocus)
        self._nav_extra_toggle.clicked.connect(self._toggle_extra_navigation)
        sidebar_lay.addWidget(self._nav_extra_toggle)
        self._nav_extra_widget = QWidget()
        self._nav_extra_layout = QVBoxLayout(self._nav_extra_widget)
        self._nav_extra_layout.setContentsMargins(8, 0, 0, 0); self._nav_extra_layout.setSpacing(2)
        sidebar_lay.addWidget(self._nav_extra_widget)
        self._extra_navigation_expanded = not bool(self.settings.get("interface_extra_collapsed", True))
        self._rebuild_navigation_layout()

        sidebar_lay.addStretch(1)
        sidebar_lay.addWidget(_separator())

        # Random button at bottom
        self.btn_random = _make_nav_btn("Рандом", "🎲", "random")
        self.btn_random.setCheckable(False)
        self.btn_random.setFocusPolicy(Qt.NoFocus)
        self.btn_random.clicked.connect(self.open_random_post)
        sidebar_lay.addWidget(self.btn_random)

        # Settings — always pinned at very bottom
        self.btn_settings_nav = _make_nav_btn("Настройки", "⚙", "settings")
        self.btn_settings_nav.setFocusPolicy(Qt.NoFocus)
        self.btn_settings_nav.setCheckable(True)
        self.btn_settings_nav.clicked.connect(lambda: self.go("Settings"))
        sidebar_lay.addWidget(self.btn_settings_nav)

        root_lay.addWidget(self._sidebar)

        # ── Content area ──────────────────────────────────────────────────────
        content = QWidget()
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)

        # Top bar
        self._topbar = QWidget()
        self._topbar.setFixedHeight(54)
        self._topbar.setObjectName("TopBar")
        tb_lay = QHBoxLayout(self._topbar)
        tb_lay.setContentsMargins(22, 0, 18, 0)
        self.title = QLabel("")
        self.title.setObjectName("Title")
        tb_lay.addWidget(self.title)
        tb_lay.addStretch(1)
        content_lay.addWidget(self._topbar)

        # Stack
        self.stack = QStackedWidget()
        content_lay.addWidget(self.stack, 1)

        # Persistent compact status strip: no database queries, safe during live parsing.
        self._status_strip = QWidget()
        self._status_strip.setObjectName("StatusStrip")
        status_lay = QHBoxLayout(self._status_strip)
        status_lay.setContentsMargins(16, 4, 16, 4)
        self._status_protection = QLabel("🛡 Исходный архив: только чтение")
        self._status_tasks = QLabel("Фоновые задачи: 0")
        self._status_hint = QLabel("Рабочую библиотеку можно пересобирать")
        status_lay.addWidget(self._status_protection); status_lay.addStretch(1); status_lay.addWidget(self._status_hint); status_lay.addSpacing(20); status_lay.addWidget(self._status_tasks)
        content_lay.addWidget(self._status_strip)

        root_lay.addWidget(content, 1)
        self.setCentralWidget(root)

        self._build_pages()
        # Defer theme application until all widgets are shown (fixes startup theme bug)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.apply_theme)
        self.retranslate()
        self.apply_interface_modules()
        try:
            apply_visual_polish(self)
        except Exception:
            pass

        # Keep Inbox/Trash lifecycle accurate while the app remains open for
        # long downloads; startup-only maintenance is not enough for 24h rules.
        self._lifecycle_timer = QTimer(self)
        self._lifecycle_timer.setInterval(5 * 60 * 1000)
        self._lifecycle_timer.timeout.connect(self._run_lifecycle_maintenance)
        self._lifecycle_timer.start()
        QTimer.singleShot(1500, self._run_lifecycle_maintenance)
        self._task_status_timer = QTimer(self)
        self._task_status_timer.setInterval(1000)
        self._task_status_timer.timeout.connect(self._update_task_status_strip)
        self._task_status_timer.start()
        self._update_task_status_strip()
        QTimer.singleShot(2500, self.ensure_local_clip_model_download)

    # ── Local AI model bootstrap ──────────────────────────────────────────────

    def ensure_local_clip_model_download(self, force: bool = False):
        """Download the local CLIP model in background when the small build ships without weights."""
        try:
            if getattr(self, "_clip_model_download_active", False):
                return None
            settings = self.settings or {}
            if not bool(settings.get("visual_nomatch_classify_enabled", True)):
                return None
            backend = str(settings.get("visual_nomatch_backend", "clip_local") or "clip_local").strip().lower()
            if backend not in ("clip_local", "clip", "ai"):
                return None
            if not force and not bool(settings.get("visual_nomatch_auto_download_model", True)):
                return None
            from core.visual_status import local_clip_model_state, download_clip_model
            state = local_clip_model_state(settings)
            if state.get("available"):
                return None

            self._clip_model_download_active = True

            def _progress(msg):
                try:
                    self._status_hint.setText(str(msg)[:180])
                except Exception:
                    pass
                try:
                    page = self.pages.get("NO_MATCH") or getattr(self, "nomatch_page", None)
                    if page is not None and hasattr(page, "on_ai_model_download_progress"):
                        page.on_ai_model_download_progress(str(msg))
                except Exception:
                    pass

            def _done(res):
                try:
                    self._status_hint.setText("NO_MATCH AI-модель скачана")
                except Exception:
                    pass
                try:
                    page = self.pages.get("NO_MATCH") or getattr(self, "nomatch_page", None)
                    if page is not None and hasattr(page, "update_ai_status"):
                        page.update_ai_status()
                        page.refresh()
                except Exception:
                    pass

            def _err(err):
                try:
                    self._status_hint.setText("Ошибка скачивания AI-модели; откройте Брак → Что с AI?")
                except Exception:
                    pass
                try:
                    page = self.pages.get("NO_MATCH") or getattr(self, "nomatch_page", None)
                    if page is not None and hasattr(page, "update_ai_status"):
                        page.update_ai_status()
                except Exception:
                    pass

            def _finished():
                self._clip_model_download_active = False

            return self.task_manager.submit(
                download_clip_model, settings,
                name="download-no-match-ai-model",
                on_progress=_progress,
                on_result=_done,
                on_error=_err,
                on_finished=_finished,
            )
        except Exception:
            try:
                self._clip_model_download_active = False
            except Exception:
                pass
            return None


    # ── Page construction ─────────────────────────────────────────────────────

    def _build_pages(self):
        for spec in PAGE_SPECS:
            page = spec.factory(self)
            setattr(self, spec.attr, page)
            self.pages[spec.key] = page
            self.stack.addWidget(page)

    def _update_task_status_strip(self):
        try:
            tasks = self.task_manager.active_snapshot()
            self._status_tasks.setText(f"Фоновые задачи: {len(tasks)}")
            if tasks:
                current = tasks[0]
                self._status_tasks.setToolTip(f"{current.get('name', '')}: {current.get('progress', '')}")
            else:
                self._status_tasks.setToolTip("")
        except Exception:
            pass

    # ── Helpers ───────────────────────────────────────────────────────────────

    def t(self, key):
        return tr(self.settings, key)

    def save_settings(self):
        self.context.save_settings()

    def apply_theme(self):
        theme_name = self.settings.get("appearance", "dark")
        qss = stylesheet_for(theme_name)
        # Styled backgrounds only need to be enabled once. Doing it on every theme
        # switch is expensive on galleries with many visible widgets.
        if not getattr(self, "_theme_widgets_prepared", False):
            try:
                for _w in [self] + self.findChildren(QWidget):
                    _w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                self._theme_widgets_prepared = True
            except Exception:
                pass
        # A single global QSS apply is enough; clearing + applying both globally and
        # on MainWindow caused a visible stall when changing themes.
        try:
            from PySide6.QtWidgets import QApplication
            qapp = QApplication.instance()
            if qapp is not None:
                qapp.setStyleSheet(qss)
        except Exception:
            self.setStyleSheet(qss)
        # Sync QPalette so palette(highlight) and palette(text) work in labels
        try:
            from PySide6.QtGui import QPalette, QColor
            from PySide6.QtCore import Qt
            _accent_map = {
                "dark": ("#6c85e0", "#c0c8e0", "#0d0f16", "#11131c", "#0d0f16"),
                "abyss": ("#6c85e0", "#c0c8e0", "#0d0f16", "#11131c", "#0d0f16"),
                "ember": ("#c87040", "#c8b090", "#120f09", "#171307", "#120f09"),
                "slate": ("#5a8a9f", "#b0c8d0", "#16181e", "#222630", "#16181e"),
                "sakura": ("#d060a0", "#e0b0d0", "#10070d", "#170a11", "#10070d"),
                "ph": ("#ff9000", "#f5f5f5", "#1b1b1b", "#19130b", "#1b1b1b"),
                "pornhub": ("#ff9000", "#f5f5f5", "#1b1b1b", "#19130b", "#1b1b1b"),
                "r34":   ("#3a7a35", "#111111", "#a8d99f", "#aedca7", "#a8d99f"),
                "r34dark": ("#7fb06f", "#d6e4d3", "#10150f", "#121a10", "#10150f"),
                "win95": ("#000080", "#000000", "#c0c0c0", "#c0c0c0", "#c0c0c0"),
                "windows95": ("#000080", "#000000", "#c0c0c0", "#c0c0c0", "#c0c0c0"),
                "light": ("#5060d0", "#1a1c2a", "#f4f5f8", "#f7f5ff", "#f4f5f8"),
            }
            accent, text, bg, alt, base = _accent_map.get(theme_name, ("#6c85e0", "#c0c8e0", "#0d0f16", "#11131c", "#0d0f16"))
            pal = self.palette()
            pal.setColor(QPalette.ColorRole.Highlight, QColor(accent))
            pal.setColor(QPalette.ColorRole.HighlightedText, QColor(bg))
            pal.setColor(QPalette.ColorRole.Text, QColor(text))
            pal.setColor(QPalette.ColorRole.WindowText, QColor(text))
            pal.setColor(QPalette.ColorRole.ButtonText, QColor(text))
            pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(text))
            pal.setColor(QPalette.ColorRole.Window, QColor(bg))
            pal.setColor(QPalette.ColorRole.Base, QColor(base))
            pal.setColor(QPalette.ColorRole.AlternateBase, QColor(alt))
            pal.setColor(QPalette.ColorRole.Button, QColor(bg))
            self.setPalette(pal)
            from PySide6.QtWidgets import QApplication
            qapp = QApplication.instance()
            if qapp is not None:
                qapp.setPalette(pal)
            # Push the new palette into already-created widgets; otherwise labels/buttons
            # may keep old black/white text until the next full restart.
            for _w in [self] + self.findChildren(QWidget):
                try:
                    _w.setPalette(pal)
                except Exception:
                    pass
        except Exception:
            pass
        import ui.main_window as _mw
        _mw._ICON_CACHE.clear()
        _mw._CURRENT_THEME = theme_name
        self._update_logo()
        self._reload_nav_icons()
        try:
            sep_color = ("#6da36b" if theme_name == "r34" else ("#345032" if theme_name == "r34dark" else ("#808080" if theme_name in ("win95", "windows95") else "#1e2130")))
            for frame in self.findChildren(QFrame):
                if frame.frameShape() == QFrame.HLine:
                    frame.setStyleSheet(f"border: none; background: {sep_color}; max-height: 1px; margin: 4px 12px;")
        except Exception:
            pass
        # Refresh settings page background when theme changes
        try:
            sp = self.settings_page
            if hasattr(sp, "retranslate"):
                sp.retranslate()
        except Exception:
            pass
        # Keep old Win95/R34 controls from using enormous native drop-downs.
        try:
            for cb in self.findChildren(QComboBox):
                cb.setMaxVisibleItems(12)
        except Exception:
            pass
        # Let custom widgets with inline styles refresh after theme switching.
        try:
            for w in self.findChildren(QWidget):
                fn = getattr(w, "apply_theme_style", None)
                if callable(fn):
                    fn(theme_name)
        except Exception:
            pass
        try:
            apply_visual_polish(self)
        except Exception:
            pass
        # Global QSS and palette application above already trigger repaint; avoid
        # an additional full unpolish/polish pass over the whole gallery.
        self.update()
        try:
            from PySide6.QtWidgets import QApplication
            qapp = QApplication.instance()
            if qapp is not None:
                qapp.processEvents()
        except Exception:
            pass

    def _update_logo(self):
        p = Path(self.settings.get("logo_path", ""))
        if p.exists():
            pix = QPixmap(str(p))
            if not pix.isNull():
                max_w = 184   # sidebar 200 - 16px margins
                max_h = 52
                scaled = pix.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                # Paint rounded pixmap
                from PySide6.QtGui import QPainter, QColor, QPainterPath
                rounded = QPixmap(scaled.size())
                rounded.fill(QColor(0, 0, 0, 0))
                painter = QPainter(rounded)
                painter.setRenderHint(QPainter.Antialiasing, True)
                painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
                path = QPainterPath()
                r = min(10.0, scaled.width() / 4.0, scaled.height() / 4.0)
                path.addRoundedRect(0, 0, scaled.width(), scaled.height(), r, r)
                painter.setClipPath(path)
                painter.drawPixmap(0, 0, scaled)
                painter.end()
                self.logo.setAlignment(Qt.AlignCenter)
                self.logo.setPixmap(rounded)
                return
        self.logo.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.logo.setText(self.settings.get("theme_title", "Local Booru"))

    # ── Workspace ─────────────────────────────────────────────────────────────

    def workspace_title(self, ws):
        titles = WORKSPACE_TITLES.get(ws, ws)
        if isinstance(titles, dict):
            lang = self.settings.get("language", "ru")
            return titles.get(lang) or titles.get("ru") or titles.get("en") or ws
        return str(titles)

    def _free_navigation_enabled(self):
        # v326: full interface layout freedom.  When enabled, the sidebar is a
        # plain user-built tree instead of four hard-coded workspaces.
        return bool(self.settings.get("interface_free_navigation", True))

    def _ordered_navigation_specs(self):
        available = [spec for spec in PAGE_SPECS if spec.button_attr and spec.key != "Settings"]
        keys = [spec.key for spec in available]
        requested = self.settings.get("interface_module_order") or []
        order = [key for key in requested if key in keys] + [key for key in keys if key not in requested]
        mapping = {spec.key: spec for spec in available}
        return [mapping[key] for key in order]

    def _page_is_extra(self, spec):
        # Группа «Дополнительно» применяется только к верхнему уровню дерева.
        # Вложенная страница всегда живёт под родителем, иначе она снова
        # вылезет отдельной кнопкой и смысл сворачивания пропадёт.
        return bool(self._interface_module_config(spec.key).get("extra", False))

    def _nav_clear_layout(self, layout):
        # Rebuilding the tree must not delete reusable page buttons.  Pull all
        # buttons out first; disposable row/container widgets may be deleted.
        for btn in getattr(self, "page_buttons", {}).values():
            try:
                btn.setParent(None)
            except Exception:
                pass
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None and w not in getattr(self, "page_buttons", {}).values():
                try:
                    w.setParent(None)
                    w.deleteLater()
                except Exception:
                    pass

    def _nav_has_children(self, key, children_by_parent):
        return bool(children_by_parent.get(key))

    def _make_nav_row(self, spec, has_children):
        row = QWidget()
        row.setObjectName("NavTreeRow")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        if has_children:
            toggle = QPushButton("▸")
            toggle.setObjectName("NavTreeToggle")
            toggle.setFixedSize(24, 34)
            toggle.setFocusPolicy(Qt.NoFocus)
            toggle.clicked.connect(lambda _=False, key=spec.key: self._toggle_page_children(key))
            self._nav_tree_toggles[spec.key] = toggle
            lay.addWidget(toggle)
        btn = self.page_buttons.get(spec.key)
        if btn is not None:
            lay.addWidget(btn, 1)
        return row

    def _add_nav_node(self, layout, spec, children_by_parent, depth=0):
        has_children = self._nav_has_children(spec.key, children_by_parent)
        row = self._make_nav_row(spec, has_children)
        if depth > 0:
            try:
                row.layout().setContentsMargins(18, 0, 0, 0)
            except Exception:
                pass
        layout.addWidget(row)
        self._nav_rows[spec.key] = row
        if has_children:
            box = QWidget()
            box.setObjectName("NavTreeChildren")
            child_lay = QVBoxLayout(box)
            child_lay.setContentsMargins(18, 0, 0, 0)
            child_lay.setSpacing(2)
            for child in children_by_parent.get(spec.key, []):
                self._add_nav_node(child_lay, child, children_by_parent, depth + 1)
            layout.addWidget(box)
            self._nav_child_containers[spec.key] = box

    def _rebuild_navigation_layout(self):
        if not hasattr(self, "_nav_primary_layout"):
            return
        self._nav_rows = {}
        self._nav_child_containers = {}
        self._nav_tree_toggles = {}
        for layout in (self._nav_primary_layout, self._nav_extra_layout):
            self._nav_clear_layout(layout)

        ordered = self._ordered_navigation_specs()
        by_key = {spec.key: spec for spec in ordered}
        children_by_parent = {key: [] for key in by_key}
        roots = []
        for spec in ordered:
            parent = self._effective_parent_key(spec.key)
            if parent and parent in by_key:
                children_by_parent.setdefault(parent, []).append(spec)
            else:
                roots.append(spec)

        for spec in roots:
            target_layout = self._nav_extra_layout if self._page_is_extra(spec) else self._nav_primary_layout
            self._add_nav_node(target_layout, spec, children_by_parent, 0)
        self._sync_extra_navigation_visibility()

    def _toggle_extra_navigation(self):
        self._extra_navigation_expanded = not bool(getattr(self, "_extra_navigation_expanded", False))
        self.settings["interface_extra_collapsed"] = not self._extra_navigation_expanded
        try:
            self.save_settings()
        except Exception:
            pass
        self._sync_extra_navigation_visibility()

    def _sync_extra_navigation_visibility(self):
        if not hasattr(self, "_nav_extra_widget"):
            return
        if self._free_navigation_enabled():
            # Free navigation: there are no fixed modes.  All visible root pages
            # belong to one user-defined tree, and «Дополнительно» is just an
            # optional collapsible bucket.
            has_extra = any(
                self._page_visible(spec)
                and self._page_is_extra(spec)
                and not self._effective_parent_key(spec.key)
                for spec in self._ordered_navigation_specs()
            )
        else:
            ws = self.settings.get("workspace", "gallery")
            if ws == "tagger": ws = "apt"
            elif ws == "downloader": ws = "adp"
            # Only top-level extra pages make the global «Дополнительно» group appear.
            has_extra = any(
                self._page_visible(spec)
                and self._page_workspace(spec) == ws
                and self._page_is_extra(spec)
                and not self._effective_parent_key(spec.key)
                for spec in self._ordered_navigation_specs()
            )
        self._nav_extra_toggle.setVisible(has_extra)
        expanded = bool(getattr(self, "_extra_navigation_expanded", False))
        self._nav_extra_widget.setVisible(has_extra and expanded)
        self._nav_extra_toggle.setText("Дополнительно  ▾" if expanded else "Дополнительно  ▸")
        self._sync_nav_tree_visibility()

    def _page_children_collapsed(self, key):
        cfg = self.settings.get("interface_page_collapsed") or {}
        return bool(cfg.get(key, True)) if isinstance(cfg, dict) else True

    def _set_page_children_collapsed(self, key, collapsed):
        cfg = dict(self.settings.get("interface_page_collapsed") or {})
        cfg[key] = bool(collapsed)
        self.settings["interface_page_collapsed"] = cfg

    def _toggle_page_children(self, key):
        self._set_page_children_collapsed(key, not self._page_children_collapsed(key))
        try:
            self.save_settings()
        except Exception:
            pass
        self._sync_nav_tree_visibility()

    def _sync_nav_tree_visibility(self):
        if not hasattr(self, "_nav_rows"):
            return
        free_nav = self._free_navigation_enabled()
        ws = self.settings.get("workspace", "gallery")
        if ws == "tagger": ws = "apt"
        elif ws == "downloader": ws = "adp"
        for spec in self._ordered_navigation_specs():
            row = self._nav_rows.get(spec.key)
            if row is None:
                continue
            visible = self._page_visible(spec) and (free_nav or self._page_workspace(spec) == ws)
            row.setVisible(visible)
            btn = self.page_buttons.get(spec.key)
            if btn is not None:
                btn.setVisible(visible)
        for key, toggle in getattr(self, "_nav_tree_toggles", {}).items():
            spec = PAGE_BY_KEY.get(key)
            visible = bool(spec and self._page_visible(spec) and (free_nav or self._page_workspace(spec) == ws))
            collapsed = self._page_children_collapsed(key)
            toggle.setVisible(visible)
            toggle.setText("▸" if collapsed else "▾")
            toggle.setToolTip("Развернуть вложенные страницы" if collapsed else "Свернуть вложенные страницы")
        for key, box in getattr(self, "_nav_child_containers", {}).items():
            spec = PAGE_BY_KEY.get(key)
            visible = bool(spec and self._page_visible(spec) and (free_nav or self._page_workspace(spec) == ws) and not self._page_children_collapsed(key))
            box.setVisible(visible)

    def _interface_module_config(self, key):
        cfg = self.settings.get("interface_modules") or {}
        value = cfg.get(key, {}) if isinstance(cfg, dict) else {}
        return value if isinstance(value, dict) else {}

    def _configured_parent_key(self, key):
        parent = str(self._interface_module_config(key).get("parent", "") or "").strip()
        return parent if parent and parent != key else ""

    def _effective_parent_key(self, key, _seen=None):
        # A child can be assigned to any other visible module in settings.  Broken
        # configs, cycles, hidden parents and system pages are treated as no parent.
        _seen = set(_seen or [])
        if key in _seen:
            return ""
        _seen.add(key)
        parent = self._configured_parent_key(key)
        if not parent:
            return ""
        pspec = PAGE_BY_KEY.get(parent)
        if not pspec or pspec.workspace == "system" or not pspec.button_attr:
            return ""
        # Keep parent links even if the parent page is hidden: a hidden parent
        # should hide its subtree, not leak children back to the root.
        if self._configured_parent_key(parent) in _seen:
            return ""
        return parent

    def _page_workspace(self, spec):
        if spec.workspace == "system":
            return "system"
        parent = self._effective_parent_key(spec.key)
        if parent:
            pspec = PAGE_BY_KEY.get(parent)
            if pspec:
                return self._page_workspace(pspec)
        return str(self._interface_module_config(spec.key).get("workspace", spec.workspace) or spec.workspace)

    def _page_visible(self, spec):
        if spec.workspace == "system":
            return True
        return bool(self._interface_module_config(spec.key).get("visible", True))

    def _visible_workspace_pages(self):
        out = {}
        if self._free_navigation_enabled():
            for spec in PAGE_SPECS:
                if spec.workspace == "system" or not spec.button_attr or not self._page_visible(spec):
                    continue
                out.setdefault("free", []).append(spec.key)
            return out
        for spec in PAGE_SPECS:
            if spec.workspace == "system" or not spec.button_attr or not self._page_visible(spec):
                continue
            out.setdefault(self._page_workspace(spec), []).append(spec.key)
        return out

    def _rebuild_workspace_menu(self):
        self.mode_menu.clear()
        visible = self._visible_workspace_pages()
        if self._free_navigation_enabled():
            self.mode_btn.setVisible(False)
            return visible
        for workspace, title in WORKSPACE_TITLES.items():
            if workspace not in visible:
                continue
            label = title.get(self.settings.get("language", "ru"), next(iter(title.values()))) if isinstance(title, dict) else str(title)
            action = self.mode_menu.addAction(label)
            action.triggered.connect(lambda _=False, ws=workspace: self.set_workspace(ws))
        self.mode_btn.setVisible(not (bool(self.settings.get("auto_hide_single_workspace", True)) and len(visible) <= 1))
        return visible

    def open_interface_modules(self):
        """Open sidebar configuration from an obvious, always-available entry."""
        try:
            self.settings_page.configure_interface_modules()
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Модули интерфейса", str(exc))

    def _first_visible_page(self, workspace=None):
        if self._free_navigation_enabled():
            for spec in self._ordered_navigation_specs():
                if spec.button_attr and self._page_visible(spec) and not self._effective_parent_key(spec.key):
                    return spec.key
            for spec in self._ordered_navigation_specs():
                if spec.button_attr and self._page_visible(spec):
                    return spec.key
            return "Gallery"
        preferred = WORKSPACE_DEFAULT_PAGE.get(workspace)
        if preferred:
            spec = PAGE_BY_KEY.get(preferred)
            if spec and self._page_visible(spec) and self._page_workspace(spec) == workspace:
                return preferred
        for spec in PAGE_SPECS:
            if spec.button_attr and self._page_visible(spec) and self._page_workspace(spec) == workspace:
                return spec.key
        return "Gallery"

    def apply_interface_modules(self):
        self._extra_navigation_expanded = not bool(self.settings.get("interface_extra_collapsed", True))
        self._rebuild_navigation_layout()
        visible = self._rebuild_workspace_menu()
        if not visible:
            # Safety fallback: a broken/empty config must never hide the whole UI.
            modules = dict(self.settings.get("interface_modules") or {})
            modules["Gallery"] = {"visible": True, "workspace": "gallery"}
            self.settings["interface_modules"] = modules
            visible = self._rebuild_workspace_menu()
        if self._free_navigation_enabled():
            self._update_workspace_buttons()
            current = self.stack.currentWidget() if hasattr(self, "stack") else None
            current_key = next((k for k, page in self.pages.items() if page is current), "")
            current_spec = PAGE_BY_KEY.get(current_key)
            if not current_spec or (current_spec.workspace != "system" and not self._page_visible(current_spec)):
                self.go(self._first_visible_page())
            return
        ws = self.settings.get("workspace", "gallery")
        if ws == "tagger": ws = "apt"
        elif ws == "downloader": ws = "adp"
        if ws not in visible:
            ws = next(iter(visible.keys()), "gallery")
        self.settings["workspace"] = ws
        self._update_workspace_buttons()
        current = self.stack.currentWidget() if hasattr(self, "stack") else None
        current_key = next((k for k, page in self.pages.items() if page is current), "")
        current_spec = PAGE_BY_KEY.get(current_key)
        if not current_spec or (current_spec.workspace != "system" and (not self._page_visible(current_spec) or self._page_workspace(current_spec) != ws)):
            self.go(self._first_visible_page(ws))

    def set_workspace(self, name):
        if self._free_navigation_enabled():
            # Workspaces are disabled in free navigation.  Keep old calls from
            # gallery/tag helpers harmless: they should just open the requested page.
            self._rebuild_workspace_menu()
            self._update_workspace_buttons()
            return
        if name == "tagger":   name = "apt"
        elif name == "downloader": name = "adp"
        visible = self._rebuild_workspace_menu()
        if name not in visible:
            name = next(iter(visible.keys()), "gallery")
        self.settings["workspace"] = name
        self.save_settings()
        self._update_workspace_buttons()
        self.go(self._first_visible_page(name))

    def _update_workspace_buttons(self):
        if self._free_navigation_enabled():
            self.mode_btn.setVisible(False)
            for spec in PAGE_SPECS:
                btn = self.page_buttons.get(spec.key)
                if btn:
                    btn.setVisible(self._page_visible(spec))
            self._sync_extra_navigation_visibility()
            return
        ws = self.settings.get("workspace", "gallery")
        if ws == "tagger":     ws = "apt"
        elif ws == "downloader": ws = "adp"
        self.mode_btn.setText(f"  {self.workspace_title(ws)}")
        for spec in PAGE_SPECS:
            btn = self.page_buttons.get(spec.key)
            if btn:
                btn.setVisible(self._page_visible(spec) and self._page_workspace(spec) == ws)
        self._sync_extra_navigation_visibility()

    # ── Retranslate ───────────────────────────────────────────────────────────

    def retranslate(self):
        for spec in PAGE_SPECS:
            btn = self.page_buttons.get(spec.key)
            if btn:
                em, ico = NAV_ICONS.get(spec.key, ("•",""))
                label = self.t(spec.key) if spec.key in ("Overview","Tagger","Gallery","Manga","Games","Duplicates","DLER","Tags") else spec.button_text
                icon = _load_icon(ico, _is_light_theme(self.settings.get("appearance", "dark")))
                if icon:
                    btn.setIcon(icon); btn.setIconSize(QSize(18,18)); btn.setText(f"  {label}")
                else:
                    btn.setText(f"  {em}  {label}")
            page = self.pages.get(spec.key)
            if page and hasattr(page, "retranslate"):
                page.retranslate()
        self._update_logo()
        self._rebuild_workspace_menu()
        self._update_workspace_buttons()

    # ── Navigation ────────────────────────────────────────────────────────────

    def _title_for(self, page):
        spec = PAGE_BY_KEY.get(page)
        return self.t(spec.title_key) if spec else page

    def _expand_ancestors_for_page(self, page):
        changed = False
        seen = set()
        parent = self._effective_parent_key(page)
        while parent and parent not in seen:
            seen.add(parent)
            if self._page_children_collapsed(parent):
                self._set_page_children_collapsed(parent, False)
                changed = True
            parent = self._effective_parent_key(parent)
        if changed:
            try:
                self.save_settings()
            except Exception:
                pass
            self._sync_nav_tree_visibility()

    def go(self, page):
        try:
            if self.stack.currentWidget() is getattr(self, "post_page", None) and page != "Post":
                self.release_open_media_handles()
        except Exception:
            pass

        spec = PAGE_BY_KEY.get(page)
        target = self.pages.get(page)
        if not spec or not target:
            return

        if spec.workspace != "system":
            # Opening a nested page from code/hotkey/search should also reveal it
            # in the sidebar.  In legacy mode it also switches workspace; in free
            # navigation there are no hard-coded modes to switch.
            if not self._free_navigation_enabled():
                ws = self._page_workspace(spec)
                if ws and ws != "system":
                    self.settings["workspace"] = ws
            self._expand_ancestors_for_page(page)
            self._update_workspace_buttons()

        self.title.setText(self._title_for(page))
        
        for key, btn in self.page_buttons.items():
            btn.setChecked(key == page)
        if hasattr(self, "btn_settings_nav"):
            self.btn_settings_nav.setChecked(page == "Settings")
        # Random button never stays "checked"
        if hasattr(self, "btn_random"):
            self.btn_random.setChecked(False)

        if page == "Settings" and hasattr(target, "load_values"):
            target.load_values()
        elif spec.refresh_on_open and hasattr(target, "refresh"):
            target.refresh()

        self.stack.setCurrentWidget(target)

    # ── Random ────────────────────────────────────────────────────────────────

    def open_random_post(self):
        try:
            current = self.stack.currentWidget()
            if current is getattr(self, "manga_page", None):
                self.manga_page.open_random_manga()
                return
            # Gallery or post — both use gallery SQL random
            self.gallery_page.random_post()
        except Exception as e:
            print("RANDOM ERROR:", e)

    # ── Post helpers ──────────────────────────────────────────────────────────

    def release_open_media_handles(self):
        """Detach active GIF/video readers before a file operation on Windows."""
        try:
            page = getattr(self, "post_page", None)
            if page is not None and hasattr(page, "release_media_handles"):
                page.release_media_handles()
            elif page is not None:
                page.stop_video()
        except Exception:
            pass

    def open_post(self, index, context=None, tag_source=None):
        self.post_page.set_post(index, context, tag_source=tag_source)
        self.title.setText(self.t("Post"))
        self.stack.setCurrentWidget(self.post_page)

    def open_tag_single(self, tag):
        # A tag is a gallery filter, not a parser action.  Always route to Gallery
        # even when the Tags module was moved between interface workspaces.
        self.set_workspace("gallery")
        self.go("Gallery")
        self.gallery_page.search.setText(tag)
        self.gallery_page.apply_filter()

    def open_tag_add(self, tag):
        self.set_workspace("gallery")
        self.go("Gallery")
        parts = self.gallery_page.search.text().split()
        if tag not in parts:
            cur = self.gallery_page.search.text().strip()
            self.gallery_page.search.setText((cur + " " + tag).strip())
        self.gallery_page.apply_filter()

    def _reload_nav_icons(self):
        """Reload all nav button icons after theme change."""
        import ui.main_window as _mw
        light = _is_light_theme(self.settings.get("appearance", "dark"))
        for spec in PAGE_SPECS:
            btn = self.page_buttons.get(spec.key)
            if not btn:
                continue
            em, ico = NAV_ICONS.get(spec.key, ("•", ""))
            if ico:
                icon = _load_icon(ico, light)
                if icon:
                    btn.setIcon(icon)
                    btn.setIconSize(QSize(18, 18))
        # Random and settings buttons
        for btn, ico_name in [
            (getattr(self, "btn_random", None), "random"),
            (getattr(self, "btn_settings_nav", None), "settings"),
        ]:
            if btn:
                icon = _load_icon(ico_name, light)
                if icon:
                    btn.setIcon(icon)
                    btn.setIconSize(QSize(18, 18))

    def showEvent(self, event):
        super().showEvent(event)
        # Clamp after the event loop has processed, so geometry is final
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._clamp_to_screen)

    def _clamp_to_screen(self):
        """Ensure window is fully visible on the current screen."""
        try:
            from PySide6.QtGui import QGuiApplication
            # Use window center to find which screen it's on
            center = self.geometry().center()
            screen = QGuiApplication.screenAt(center)
            if screen is None:
                screen = QGuiApplication.primaryScreen()
            if screen is None:
                return
            avail = screen.availableGeometry()
            geo = self.frameGeometry()
            w = min(geo.width(), avail.width())
            h = min(geo.height(), avail.height())
            x = max(avail.left(), min(geo.x(), avail.right() - w))
            y = max(avail.top(), min(geo.y(), avail.bottom() - h))
            if geo.x() != x or geo.y() != y:
                self.move(x, y)
            if geo.width() != w or geo.height() != h:
                self.resize(w, h)
        except Exception:
            pass

    def _run_lifecycle_maintenance(self):
        try:
            from core.library_lifecycle import archive_expired_inbox, purge_expired_trash
            archived = int(archive_expired_inbox(self.settings) or 0)
            purged = purge_expired_trash(self.settings)
            removed = int((purged or {}).get("removed_records", 0) or 0)
            if archived or removed:
                gallery = getattr(self, "gallery_page", None)
                if gallery is not None and hasattr(gallery, "refresh_force"):
                    gallery.refresh_force()
                trash = getattr(self, "trash_page", None)
                if trash is not None and hasattr(trash, "refresh"):
                    trash.refresh()
        except Exception:
            pass

    def changeEvent(self, event):
        super().changeEvent(event)
        try:
            from PySide6.QtCore import QEvent, QTimer
            # WindowStateChange covers maximize/restore/minimize
            if event.type() == QEvent.Type.WindowStateChange:
                if not (int(self.windowState()) & 0x2):  # not minimized
                    QTimer.singleShot(150, self._clamp_to_screen)
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.isMaximized() and not self.isMinimized():
            try:
                from PySide6.QtGui import QGuiApplication
                screen = QGuiApplication.screenAt(self.geometry().center())
                if screen is None:
                    screen = QGuiApplication.primaryScreen()
                if screen:
                    avail = screen.availableGeometry()
                    # Only save if fits on screen
                    if self.width() <= avail.width() and self.height() <= avail.height():
                        self.settings["window_w"] = self.width()
                        self.settings["window_h"] = self.height()
            except Exception:
                pass

    def moveEvent(self, event):
        super().moveEvent(event)
        # Don't clamp during move — user is dragging the window

    def _request_pages_shutdown(self):
        """Ask long-running page workers to stop without joining them."""
        for page in list(getattr(self, "pages", {}) .values()):
            for method_name in ("shutdown_fast", "stop_worker", "stop", "_stop"):
                try:
                    method = getattr(page, method_name, None)
                    if callable(method):
                        method()
                        break
                except Exception:
                    break

    def closeEvent(self, event):
        """Close the window without running heavyweight shutdown work in Qt UI thread.

        The app used to run task shutdown, thumbnail cleanup, light backup and
        SQLite checkpoint synchronously from closeEvent().  On a large archive,
        or when an external backup drive / SQLite lock / thumbnail worker was
        slow, Windows reported the window as "not responding" after pressing
        the title-bar X.

        Keep this handler best-effort and non-blocking.  Heavier maintenance is
        handled after the Qt event loop has already exited, so the visible
        window disappears immediately instead of freezing.
        """
        if getattr(self, "_close_in_progress", False):
            event.accept()
            return
        self._close_in_progress = True

        try:
            self.setEnabled(False)
            self.hide()
        except Exception:
            pass

        # Cooperative cancellation only.  Do not wait here.
        try:
            self._request_pages_shutdown()
        except Exception:
            pass
        try:
            if hasattr(self, "task_manager"):
                self.task_manager.cancel_all()
        except Exception:
            pass
        try:
            from core.shutdown import begin_shutdown
            begin_shutdown()
        except Exception:
            pass

        # Persist cheap UI state if possible, but never block close on it.
        try:
            self.save_settings()
        except Exception:
            pass

        event.accept()
