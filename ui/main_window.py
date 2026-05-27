"""Main window — sidebar navigation, clean layout."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QPixmap, QColor, QPainter, QFont
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QMenu, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget, QFrame, QSizePolicy, QSpacerItem,
)

from core.app_context import AppContext
from core.task_manager import TaskManager
from core.i18n import tr
from core.paths import APP_ICON_FILE
from ui.modules.registry import PAGE_BY_KEY, PAGE_SPECS, WORKSPACE_DEFAULT_PAGE, WORKSPACE_TITLES
from ui.styles.themes import stylesheet_for

# Icons: emoji fallback — looks fine with Segoe UI Emoji on Windows
NAV_ICONS = {
    "Tagger":     ("🔍", "parser"),
    "Tags":       ("🏷", "tags"),
    "NO_MATCH":   ("❓", "nomatch"),
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
    return theme_name in ("light", "r34")

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
            base / f"{name}.ico",
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
        self.task_manager = TaskManager(self, max_workers=int(self.settings.get("task_workers", 2)))
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
        self._sidebar.setFixedWidth(200)
        self._sidebar.setObjectName("Sidebar")
        # Background set by QSS theme via #Sidebar selector
        sidebar_lay = QVBoxLayout(self._sidebar)
        sidebar_lay.setContentsMargins(8, 16, 8, 12)
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
        for workspace, title in WORKSPACE_TITLES.items():
            label = title.get(self.settings.get("language", "ru"), next(iter(title.values()))) if isinstance(title, dict) else str(title)
            action = self.mode_menu.addAction(label)
            action.triggered.connect(lambda _=False, ws=workspace: self.set_workspace(ws))
        self.mode_btn.setMenu(self.mode_menu)
        sidebar_lay.addWidget(self.mode_btn)
        sidebar_lay.addSpacing(6)

        # Nav buttons (built from PAGE_SPECS)
        for spec in PAGE_SPECS:
            if not spec.button_attr or spec.key == "Settings":
                continue  # Settings is pinned at bottom separately
            em, ico = NAV_ICONS.get(spec.key, ("•", ""))
            btn = _make_nav_btn(spec.button_text, em, ico)
            setattr(self, spec.button_attr, btn)
            self.page_buttons[spec.key] = btn
            btn.clicked.connect(lambda _=False, key=spec.key: self.go(key))
            sidebar_lay.addWidget(btn)

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
        self._topbar.setFixedHeight(48)
        self._topbar.setObjectName("TopBar")
        tb_lay = QHBoxLayout(self._topbar)
        tb_lay.setContentsMargins(20, 0, 16, 0)
        self.title = QLabel("")
        self.title.setObjectName("Title")
        tb_lay.addWidget(self.title)
        tb_lay.addStretch(1)
        content_lay.addWidget(self._topbar)

        # Stack
        self.stack = QStackedWidget()
        content_lay.addWidget(self.stack, 1)

        root_lay.addWidget(content, 1)
        self.setCentralWidget(root)

        self._build_pages()
        # Defer theme application until all widgets are shown (fixes startup theme bug)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.apply_theme)
        self.retranslate()
        self.set_workspace(self.settings.get("workspace", "apt"))

    # ── Page construction ─────────────────────────────────────────────────────

    def _build_pages(self):
        for spec in PAGE_SPECS:
            page = spec.factory(self)
            setattr(self, spec.attr, page)
            self.pages[spec.key] = page
            self.stack.addWidget(page)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def t(self, key):
        return tr(self.settings, key)

    def save_settings(self):
        self.context.save_settings()

    def apply_theme(self):
        theme_name = self.settings.get("appearance", "dark")
        self.setStyleSheet(stylesheet_for(theme_name))
        # Sync QPalette so palette(highlight) and palette(text) work in labels
        try:
            from PySide6.QtGui import QPalette, QColor
            from PySide6.QtCore import Qt
            _accent_map = {
                "dark": ("#6c85e0", "#c0c8e0", "#0d0f16"),
                "abyss": ("#6c85e0", "#c0c8e0", "#0d0f16"),
                "ember": ("#c87040", "#c8b090", "#14141e"),
                "slate": ("#5a8a9f", "#b0c8d0", "#16181e"),
                "sakura": ("#d060a0", "#e0b0d0", "#140820"),
                "ph":    ("#ff9000", "#f5f5f5", "#0f0f0f"),
                "ph": ("#ff9000", "#f5f5f5", "#1b1b1b"),
                "r34":   ("#7b2fff", "#e0d4f5", "#1a0a2e"),
                "light": ("#5060d0", "#1a1c2a", "#f4f5f8"),
            }
            accent, text, bg = _accent_map.get(theme_name, ("#6c85e0", "#c0c8e0", "#0d0f16"))
            pal = self.palette()
            pal.setColor(QPalette.ColorRole.Highlight, QColor(accent))
            pal.setColor(QPalette.ColorRole.HighlightedText, QColor(bg))
            pal.setColor(QPalette.ColorRole.Text, QColor(text))
            pal.setColor(QPalette.ColorRole.WindowText, QColor(text))
            pal.setColor(QPalette.ColorRole.Window, QColor(bg))
            pal.setColor(QPalette.ColorRole.Base, QColor(bg))
            self.setPalette(pal)
        except Exception:
            pass
        import ui.main_window as _mw
        _mw._ICON_CACHE.clear()
        _mw._CURRENT_THEME = theme_name
        self._update_logo()
        self._reload_nav_icons()
        # Refresh settings page background when theme changes
        try:
            sp = self.settings_page
            if hasattr(sp, "retranslate"):
                sp.retranslate()
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

    def set_workspace(self, name):
        if name == "tagger":   name = "apt"
        elif name == "downloader": name = "adp"
        self.settings["workspace"] = name
        self.save_settings()
        self._update_workspace_buttons()
        self.go(WORKSPACE_DEFAULT_PAGE.get(name, "Tagger"))

    def _update_workspace_buttons(self):
        ws = self.settings.get("workspace", "apt")
        if ws == "tagger":     ws = "apt"
        elif ws == "downloader": ws = "adp"
        self.mode_btn.setText(f"  {self.workspace_title(ws)}")
        for spec in PAGE_SPECS:
            btn = self.page_buttons.get(spec.key)
            if btn:
                btn.setVisible(spec.workspace in (ws, "system"))

    # ── Retranslate ───────────────────────────────────────────────────────────

    def retranslate(self):
        for spec in PAGE_SPECS:
            btn = self.page_buttons.get(spec.key)
            if btn:
                em, ico = NAV_ICONS.get(spec.key, ("•",""))
                label = self.t(spec.key) if spec.key in ("Tagger","Gallery","Manga","Games","Duplicates","DLER","Tags") else spec.button_text
                icon = _load_icon(ico)
                if icon:
                    btn.setIcon(icon); btn.setIconSize(QSize(18,18)); btn.setText(f"  {label}")
                else:
                    btn.setText(f"  {em}  {label}")
            page = self.pages.get(spec.key)
            if page and hasattr(page, "retranslate"):
                page.retranslate()
        self._update_logo()
        self._update_workspace_buttons()

    # ── Navigation ────────────────────────────────────────────────────────────

    def _title_for(self, page):
        spec = PAGE_BY_KEY.get(page)
        return self.t(spec.title_key) if spec else page

    def go(self, page):
        try:
            if self.stack.currentWidget() is getattr(self, "post_page", None) and page != "Post":
                self.post_page.stop_video()
        except Exception:
            pass

        spec = PAGE_BY_KEY.get(page)
        target = self.pages.get(page)
        if not spec or not target:
            return

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

    def open_post(self, index, context=None):
        self.post_page.set_post(index, context)
        self.title.setText(self.t("Post"))
        self.stack.setCurrentWidget(self.post_page)

    def open_tag_single(self, tag):
        self.set_workspace(self.settings.get("workspace", "apt"))
        self.gallery_page.search.setText(tag)
        self.gallery_page.apply_filter()

    def open_tag_add(self, tag):
        self.set_workspace(self.settings.get("workspace", "apt"))
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

    def closeEvent(self, event):
        try: self.task_manager.shutdown()
        except Exception: pass
        try:
            from core.thumb_service import ThumbnailService
            ThumbnailService.instance().stop()
        except Exception: pass
        super().closeEvent(event)
