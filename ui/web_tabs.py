"""(v5.44.117) The program's second window — its pages as tabs, as a browser has them.

The owner (7.11.2026): «если нажал скм то тайтл открывается в отдельном окне как браузер с вкладками (можно будет сразу
несколько грузить одновременно) и так с каждой хуйней в проге … а закрытие такого окна не как не будет сказываться на
программе они по факту та же страница в проге но в другом окне».

A page of the program asks for a new window or tab — the middle button, Ctrl+click, ``target=_blank``, «Открыть ссылку в
новой вкладке» (``web_gallery_page._GalleryPage.createWindow`` → ``_NewWindowPage``): the program's own address opens
here (:func:`open_tab`) — the first such link opens this window, the next ones are new tabs in it, from the main window
and from this one; another site's — in the usual browser, as before.  A tab opened by the middle button or Ctrl+click
(a tab «in the background») does not take the place of the one shown, nor this window to the front once it is open:
several titles are asked at once.  The same web profile and cookies as the main window's
(``web_gallery_page.profile``); each tab its page's name and ×, the middle button on a tab closes it; closing a tab or
the window touches nothing else — the server, the background work, the main window's video and its full screen.  The
main window closing closes this one too (:func:`close_all`: the program ends with the main window).

What a page asks of the window (``/api/app/*`` → ``ui/web_bridge.py``): every request of a page here carries its tab's
mark — the header ``X-LB-Tab`` (:data:`MARK_HEADER`; the page's own request interceptor, :class:`_Mark`: the program's
addresses only) — so a dialog opens over this window and what follows it goes to this tab (:func:`tab_of`); ⛶ of a
page's own player and F11 — this window full screen.  The video the program plays itself (Qt Multimedia,
``webui/video_relay.RELAY``) is one for the whole program: from a page here it is refused in words, the main window's
goes on (``webui/pages/api.py``); a video the window's browser plays in the page plays here as anywhere.

(v5.44.159; the owner: «скм пиздатая хуйня но выглядит по уебански»; the mockup «Окно вкладок» approved 11.11.2026) The
strip of tabs is the open page's: its ground is the page's header, the tab shown — the page's own ground with the
page's accent over it, the words — the header's (:func:`strip_colours`; a page says its colours when it has loaded and
when its look is changed — :data:`LOOK_JS`, ``/api/app/look``).  A tab has its section's icon (:func:`section_icon`)
and, while its page loads, a running line under its name (:class:`_TabBar`); on the right of the strip — back, forward,
reload and «В главное окно».  On Windows 11 the window's own caption takes the strip's colour (:func:`_tint_frame`).
"""
from __future__ import annotations

import itertools
import json
import logging
import re
import sys
import time

from PySide6.QtCore import QByteArray, QRect, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QBrush, QColor, QIcon, QKeySequence, QLinearGradient, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QMainWindow, QMenu, QPushButton, QTabBar, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

_LOG = logging.getLogger("local_booru.window")

try:
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
except ImportError:                  # PySide6-Addons is not installed: no window of tabs (the pages are in the browser)
    QWebEnginePage = QWebEngineUrlRequestInterceptor = QWebEngineView = None
try:
    from PySide6.QtSvg import QSvgRenderer
except ImportError:                  # no QtSvg: the tabs and the buttons of the strip have their words only
    QSvgRenderer = None

MARK_HEADER = "X-LB-Tab"            # the header of every request of a page in a tab: its tab's mark
TITLE = "Local Booru"
SUFFIX = " — Local Booru"           # the pages' titles end so: a tab shows the name before it
SCREEN_CLAIM_MS = 4000              # a page loaded while the window is full screen for a player: it says so by then
_MARKS = itertools.count(1)
_WINDOW = None                      # the window of tabs (one for the program), None until a page asks for one
_DYING: list = []                   # a window closed, kept until it is destroyed (in the window's thread)
_TABS: dict = {}                    # a tab's mark → the tab (the bridge finds the page that asked by it)
_PLACE: dict = {}                   # the window's place and size as it was left (for this run of the program)


def tab_of(mark: str) -> "Tab | None":
    """The tab whose page carries ``mark`` (None — no such tab now: it was closed)."""
    return _TABS.get(str(mark or ""))


def window_of(page) -> "TabsWindow | None":
    """The window of tabs when ``page`` (a ``QWebEnginePage``) is one of its tabs' — a dialog it asks for opens there."""
    for tab in list(_TABS.values()):
        try:
            if tab.view.page() is page:
                return _WINDOW
        except RuntimeError:          # a tab being destroyed
            continue
    return None


def opened() -> "TabsWindow | None":
    return _WINDOW


def open_tab(url: QUrl, *, background: bool = False, settings_provider=None) -> bool:
    """The program's address ``url`` in a new tab of the window of tabs — the window made and shown when there is none;
    ``background`` (the middle button, Ctrl+click) — the tab shown is not changed and the window not brought to the front
    when it was open already.  False — no window of tabs here (no Qt WebEngine)."""
    global _WINDOW
    if QWebEngineView is None:
        return False
    from ui.web_gallery_page import profile
    if profile() is None:            # no page of the program yet: nothing to share the profile with
        return False
    window, first = _WINDOW, _WINDOW is None
    if first:
        window = TabsWindow(settings_provider)
        _WINDOW = window
    tab = window.add(url, front=first or not background)
    if first:
        window.show_first()
    elif not background:
        window.bring_up()
    elif not window.isActiveWindow():
        QApplication.alert(window)
    return tab is not None


def close_all() -> None:
    """The main window closes: the window of tabs with it (the program ends with the main window — and Qt quits only
    when no window is left)."""
    window = _WINDOW
    if window is not None:
        try:
            window.close()
        except RuntimeError as exc:
            _LOG.warning("the window of tabs not closed: %s", exc)
    _dispose()


def _dispose() -> None:
    """The windows of tabs closed destroyed here, in the window's thread, now — never left to the garbage collector (it
    may run in another thread, and a web view destroyed there ends the program: Chromium's check)."""
    try:
        import shiboken6
    except ImportError:              # a stand-in Qt (the checks without PySide6)
        _DYING.clear()
        return
    while _DYING:
        window = _DYING.pop()
        try:
            if shiboken6.isValid(window):
                shiboken6.delete(window)
        except RuntimeError as exc:
            _LOG.warning("the window of tabs not destroyed: %s", exc)


# ── the strip's look: the open page's colours (v5.44.159) ───────────────────────────────────────────────────────────
# What a page says of its look, asked when it has loaded and when its look is changed: six colours as #rrggbb.  However
# its stylesheet writes a colour (a variable, color-mix, a see-through colour over the ground), it is painted on one
# point of a canvas over what lies under it and read back.  Only a page of the program with its header says anything:
# the empty page a tab passes on its way in (the key of the session → its cookie) has no look — taken for one, it made
# the strip white for a moment before every first page.
LOOK_JS = """(() => {
  try {
    if (!document.querySelector("header.top") || !document.body) return "";
    const cv = document.createElement("canvas");
    cv.width = cv.height = 1;
    const cx = cv.getContext("2d", { willReadFrequently: true });
    const flat = (color, under) => {
      cx.clearRect(0, 0, 1, 1);
      if (under) { cx.fillStyle = under; cx.fillRect(0, 0, 1, 1); }
      if (color && CSS.supports("color", color)) { cx.fillStyle = color; cx.fillRect(0, 0, 1, 1); }
      const d = cx.getImageData(0, 0, 1, 1).data;
      return d[3] < 250 ? (under || "") : "#" + [d[0], d[1], d[2]].map((n) => n.toString(16).padStart(2, "0")).join("");
    };
    const root = getComputedStyle(document.documentElement), body = getComputedStyle(document.body);
    const head = document.querySelector("header.top");
    const top = head ? getComputedStyle(head) : null;
    const v = (name) => root.getPropertyValue(name).trim();
    const bg = flat(body.backgroundColor, flat(root.backgroundColor, "#ffffff"));
    const bar = top ? flat(top.backgroundColor, bg) : bg;
    return JSON.stringify({ bar, bg, fg: flat(v("--text") || body.color, bg), ink: flat(top ? top.color : body.color, bar),
      accent: flat(v("--accent"), bg), line: flat(v("--border"), bg),
      still: document.documentElement.dataset.motion === "off" });
  } catch (_) { return ""; }
})()"""
LOOK_KEYS = ("bar", "bg", "fg", "ink", "accent", "line")
_HEX = re.compile(r"#[0-9a-f]{6}")
# until a page has said its own (the first tab of a run): as the system is, dark or light
DARK_LOOK = {"bar": "#17191c", "bg": "#1f2226", "fg": "#e8eaed", "ink": "#d6d9de", "accent": "#8ab4f8", "line": "#34383e",
             "still": False}
LIGHT_LOOK = {"bar": "#e9ebee", "bg": "#ffffff", "fg": "#1b1c1e", "ink": "#2a2c30", "accent": "#1a5fd0", "line": "#c9cdd3",
              "still": False}
_LOOK: dict = {}                    # the look the strip had last (a new window of tabs starts with it)


def parse_look(text) -> dict | None:
    """What :data:`LOOK_JS` said of a page — its six colours, each #rrggbb, and whether it stands still — or None."""
    try:
        said = json.loads(text) if isinstance(text, str) and text else None
    except ValueError:
        return None
    if not isinstance(said, dict):
        return None
    look = {key: str(said.get(key) or "").lower() for key in LOOK_KEYS}
    if not all(_HEX.fullmatch(value) for value in look.values()):
        return None
    look["still"] = said.get("still") is True
    return look


def _rgb(colour: str) -> tuple:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def mix(a: str, b: str, share: float) -> str:
    """``a`` with ``share`` of ``b`` in it."""
    x, y = _rgb(a), _rgb(b)
    return "#" + "".join(f"{round(x[n] + (y[n] - x[n]) * share):02x}" for n in range(3))


def _light(colour: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in _rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """How well ``a`` is read on ``b`` (1 — not at all, 21 — black on white)."""
    hi, lo = sorted((_light(a), _light(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def strip_colours(look: dict) -> dict:
    """The colours of the strip from a page's look: the strip is the page's header (``bar``, its words ``ink``), the
    tab shown is the page's own ground (``bg``, ``fg``) under a line of its accent.  Words that would not be read on
    their ground are made white or black; a line that would not be seen is made of the words' colour."""
    bar, bg = look["bar"], look["bg"]
    ink = look["ink"] if contrast(look["ink"], bar) >= 4 else ("#ffffff" if _light(bar) < 0.3 else "#111111")
    fg = look["fg"] if contrast(look["fg"], bg) >= 4 else ("#ffffff" if _light(bg) < 0.3 else "#111111")
    accent = look["accent"] if contrast(look["accent"], bg) >= 1.5 else fg
    line = look["line"] if contrast(look["line"], bar) >= 1.15 else mix(bar, ink, 0.22)
    return {"bar": bar, "bg": bg, "fg": fg, "ink": ink, "accent": accent, "line": line,
            "dim": mix(bar, ink, 0.68), "faint": mix(bar, ink, 0.3), "hover": mix(bar, ink, 0.12),
            "press": mix(bar, ink, 0.2), "edge": mix(bar, ink, 0.34)}


def _system_look() -> dict:
    try:
        from PySide6.QtGui import QGuiApplication
        dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except (AttributeError, RuntimeError):       # a Qt before 6.5: no word of the system's side
        dark = True
    return dict(DARK_LOOK if dark else LIGHT_LOOK)


# The icons: 24 × 24, drawn by a line 1.75 thick with round ends (as the pages' own)
ICONS = {
    "book": '<path d="M12 6.5C10 5 7 4.5 4 5v13c3-.5 6 0 8 1.5 2-1.5 5-2 8-1.5V5c-3-.5-6 0-8 1.5z"/><path d="M12 6.5v13"/>',
    "strip": '<rect x="6.5" y="3.5" width="11" height="17" rx="1"/><path d="M6.5 9.5h11"/><path d="M6.5 14.5h11"/>',
    "video": '<rect x="3.5" y="5.5" width="17" height="13" rx="1"/><path d="m10.5 9.5 4 2.5-4 2.5z"/>',
    "down": '<path d="M12 4v11"/><path d="m7.5 11 4.5 4.5 4.5-4.5"/><path d="M5 19.5h14"/>',
    "picture": '<rect x="3.5" y="5" width="17" height="14" rx="1"/><path d="m4 16.5 4.5-4.5 3.5 3.5 2.5-2.5 5.5 5.5"/>'
               '<circle cx="15.5" cy="9.2" r="1.1"/>',
    "parser": '<path d="M4 5.5h16l-6 7.2v5.3l-4 1.5v-6.8z"/>',
    "bell": '<path d="M6.5 16.5v-5a5.5 5.5 0 0 1 11 0v5l1.5 2h-14z"/><path d="M10.5 20.5h3"/>',
    "gear": '<circle cx="12" cy="12" r="2.6"/><circle cx="12" cy="12" r="6.2"/><path d="M12 3.2v2.6M12 18.2v2.6M3.2 12h2.6'
            'M18.2 12h2.6M5.8 5.8l1.8 1.8M16.4 16.4l1.8 1.8M5.8 18.2l1.8-1.8M16.4 7.6l1.8-1.8"/>',
    "globe": '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17"/><path d="M12 3.5c3 3 3 14 0 17c-3-3-3-14 0-17z"/>',
    "copies": '<rect x="8.5" y="8.5" width="11" height="11" rx="1"/><path d="M15.5 8.5v-3a1 1 0 0 0-1-1h-9a1 1 0 0 0-1 1v9'
              'a1 1 0 0 0 1 1h3"/>',
    "pulse": '<path d="M3.5 12h4l2.5-6 4 12 2.5-6h4"/>',
    "help": '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.5a2.5 2.5 0 1 1 3.6 2.3c-.8.5-1.2 1-1.2 2"/>'
            '<path d="M12 16.8h.01"/>',
    "page": '<path d="M7 3.5h7l4 4v13H7z"/><path d="M14 3.5v4h4"/>',
    "back": '<path d="M19 12H5"/><path d="m11 6-6 6 6 6"/>',
    "fwd": '<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>',
    "reload": '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4h-4"/>',
    "close": '<path d="M7.5 7.5l9 9"/><path d="M16.5 7.5l-9 9"/>',
}
# a page's address → the icon of its section (the first that fits; any other page — «page»)
SECTIONS = (("/manhwa", "strip"), ("/manga", "book"), ("/videos", "video"), ("/get", "down"), ("/grab", "down"),
            ("/posts", "picture"), ("/post", "picture"), ("/tags", "picture"), ("/parser", "parser"),
            ("/subs", "bell"), ("/settings", "gear"), ("/sites", "globe"), ("/dupes", "copies"), ("/diag", "pulse"),
            ("/help", "help"))


def section_icon(path: str) -> str:
    """The name of the icon (:data:`ICONS`) of the section a page of this address belongs to."""
    path = str(path or "")
    for prefix, name in SECTIONS:
        if path == prefix or path.startswith(prefix + "/"):
            return name
    return "page"


_DRAWN: dict = {}                   # (an icon, its colour, its size, the screen's ratio) → its picture


def _pixmap(name: str, colour: str, size: int, ratio: float) -> "QPixmap | None":
    """The icon ``name`` drawn in ``colour``, ``size`` points wide, for a screen of ``ratio`` pixels a point (drawn
    once: a tab is dressed again at every page it opens)."""
    key = (name, colour, size, round(ratio, 2))
    if key not in _DRAWN:
        if len(_DRAWN) > 400:                    # a run through many looks: the pictures of the looks left
            _DRAWN.clear()
        _DRAWN[key] = _draw(name, colour, size, ratio)
    return _DRAWN[key]


def _draw(name: str, colour: str, size: int, ratio: float) -> "QPixmap | None":
    if QSvgRenderer is None or name not in ICONS:
        return None
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{colour}" '
           f'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode("ascii")))
    if not renderer.isValid():
        return None
    pixels = max(1, round(size * ratio))
    pixmap = QPixmap(pixels, pixels)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def make_icon(name: str, colour: str, *, on: str = "", off: str = "", size: int = 16, ratio: float = 2.0) -> "QIcon":
    """The icon ``name``: ``colour`` as it is, ``on`` — on the tab shown (a tab bar draws its tab shown «on»), ``off`` —
    on a button that cannot be pressed.  An empty icon where there is no QtSvg."""
    icon = QIcon()
    plain = _pixmap(name, colour, size, ratio)
    if plain is None:
        return icon
    icon.addPixmap(plain, QIcon.Mode.Normal, QIcon.State.Off)
    for tone, mode, state in ((on, QIcon.Mode.Normal, QIcon.State.On), (on, QIcon.Mode.Selected, QIcon.State.On),
                              (off, QIcon.Mode.Disabled, QIcon.State.Off)):
        pixmap = _pixmap(name, tone, size, ratio) if tone else None
        if pixmap is not None:
            icon.addPixmap(pixmap, mode, state)
    return icon


def strip_sheet(c: dict) -> str:
    """The stylesheet of the window of tabs for the colours ``c`` (:func:`strip_colours`)."""
    return f"""
QMainWindow {{ background: {c["bar"]}; }}
QTabWidget::pane {{ border: 0; border-top: 1px solid {c["line"]}; background: {c["bg"]}; }}
QTabWidget::tab-bar {{ left: 6px; }}
QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {c["dim"]}; margin: 5px 1px 0 1px; padding: 6px 6px 7px 10px;
    max-width: 210px; border: 0; border-top: 2px solid transparent;
    border-top-left-radius: 6px; border-top-right-radius: 6px; }}
QTabBar::tab:hover {{ background: {c["hover"]}; color: {c["ink"]}; }}
QTabBar::tab:selected {{ background: {c["bg"]}; color: {c["fg"]}; border-top: 2px solid {c["accent"]}; }}
QToolButton#lbTabClose {{ background: transparent; border: 0; border-radius: 4px; padding: 1px; margin: 1px 0 0 4px; }}
QToolButton#lbTabClose:hover {{ background: {c["press"]}; }}
QWidget#lbTabTools {{ background: transparent; }}
QWidget#lbTabTools QToolButton {{ background: transparent; border: 0; border-radius: 4px; padding: 4px; }}
QWidget#lbTabTools QToolButton:hover:enabled {{ background: {c["hover"]}; }}
QWidget#lbTabTools QToolButton:pressed {{ background: {c["press"]}; }}
QWidget#lbTabTools QPushButton {{ background: transparent; color: {c["ink"]}; border: 1px solid {c["edge"]};
    border-radius: 4px; padding: 4px 10px; margin-left: 6px; }}
QWidget#lbTabTools QPushButton:hover {{ background: {c["hover"]}; border-color: {c["dim"]}; }}
QWidget#lbTabTools QPushButton:pressed {{ background: {c["press"]}; }}
QToolTip {{ background: {c["bg"]}; color: {c["fg"]}; border: 1px solid {c["line"]}; padding: 3px 6px; }}
"""


def _tint_frame(window, colours: dict) -> list | None:
    """The window's own caption in the strip's colour — Windows 11 (an older Windows knows dark and light only: the side
    is said, the colour is refused and that is all).  Never raises: a window with the system's caption is a window.
    What Windows answered to each of the four words (0 — taken), None where there is nothing to ask."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        set_attribute.restype = ctypes.c_long
        handle = wintypes.HWND(int(window.winId()))

        def say(attribute: int, value) -> int:
            return int(set_attribute(handle, attribute, ctypes.byref(value), ctypes.sizeof(value)))

        def ref(colour: str):                    # COLORREF: 0x00bbggrr
            r, g, b = _rgb(colour)
            return wintypes.DWORD(r | (g << 8) | (b << 16))
        dark = ctypes.c_int(1 if _light(colours["bar"]) < 0.3 else 0)
        side = say(20, dark)                     # DWMWA_USE_IMMERSIVE_DARK_MODE (19 before Windows 10 20H1)
        if side:
            side = say(19, dark)
        answers = [side,
                   say(35, ref(colours["bar"])),     # DWMWA_CAPTION_COLOR (Windows 11)
                   say(36, ref(colours["dim"])),     # DWMWA_TEXT_COLOR
                   say(34, ref(colours["line"]))]    # DWMWA_BORDER_COLOR
        _LOG.debug("the caption of the window of tabs: Windows answered %s", answers)
        return answers
    except Exception as exc:                     # noqa: BLE001 - the look of a caption is never worth a window
        _LOG.debug("the caption of the window of tabs not coloured: %s", exc)
        return None


def _clean_title(title: str) -> str:
    """A page's title as a tab shows it: never its address (it carries the session key); without « — Local Booru»."""
    text = str(title or "").strip()
    if not text or "127.0.0.1" in text or "localhost" in text or "://" in text:
        return ""
    return text[:-len(SUFFIX)] if text.endswith(SUFFIX) and len(text) > len(SUFFIX) else text


if QWebEngineUrlRequestInterceptor is not None:
    class _Mark(QWebEngineUrlRequestInterceptor):
        """Every request of the page of a tab — to the program's own server only — carries its tab's mark."""

        def __init__(self, mark: str, parent=None):
            super().__init__(parent)
            self.mark = mark.encode("ascii")

        def interceptRequest(self, info) -> None:  # noqa: N802 - Qt's name
            if info.requestUrl().host() in ("127.0.0.1", "localhost"):
                info.setHttpHeader(MARK_HEADER.encode("ascii"), self.mark)


class _TabBar(QTabBar):
    """The middle button on a tab closes it, as in a browser.  (v5.44.159) A tab whose page loads has a line of the
    page's accent running under its name (a tab's data is True meanwhile: :meth:`set_loading`); where the pages stand
    still («Плавные переходы» — «Нет», the system's «less motion») the line stands."""

    RUN_MS = 1200                    # the line crosses a tab in so long
    TICK_MS = 33

    def __init__(self, parent=None):
        super().__init__(parent)
        self.accent = QColor(DARK_LOOK["accent"])
        self.still = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)

    def set_loading(self, index: int, on: bool) -> None:
        if index < 0 or bool(self.tabData(index)) == bool(on):
            return
        self.setTabData(index, bool(on))
        self._run()
        self.update()

    def loading(self, index: int) -> bool:
        return bool(self.tabData(index))

    def _any(self) -> bool:
        return any(self.tabData(n) for n in range(self.count()))

    def _run(self) -> None:
        """The clock of the running line goes only while a tab loads, the strip is seen and the pages move."""
        wanted = self._any() and not self.still and self.isVisible()
        if wanted and not self._timer.isActive():
            self._timer.start()
        elif not wanted and self._timer.isActive():
            self._timer.stop()

    def _tick(self) -> None:
        self._phase = (self._phase + self.TICK_MS / self.RUN_MS) % 1.0
        if not self._any() or not self.isVisible():
            self._timer.stop()
        self.update()

    def set_still(self, still: bool) -> None:
        self.still = bool(still)
        self._run()
        self.update()

    NARROW = 62                      # a tab among many: its icon and its × (its name is cut with «…» before that)

    def minimumTabSizeHint(self, index):  # noqa: N802 - Qt's name
        """Many tabs in a narrow window: they narrow together, as a browser's — down to an icon and a ×."""
        size = super().minimumTabSizeHint(index)
        return QSize(min(size.width(), self.NARROW), size.height())

    def tabRemoved(self, index):  # noqa: N802 - Qt's name
        super().tabRemoved(index)
        self._run()

    def showEvent(self, event):  # noqa: N802 - Qt's name
        super().showEvent(event)
        self._run()

    def paintEvent(self, event):  # noqa: N802 - Qt's name
        super().paintEvent(event)
        if not self._any():
            return
        painter = QPainter(self)
        clear = QColor(self.accent)
        clear.setAlpha(0)
        for index in range(self.count()):
            if not self.tabData(index):
                continue
            tab = self.tabRect(index)
            track = QRect(tab.left() + 9, tab.bottom() - 3, max(8, tab.width() - 18), 2)
            if self.still:
                faint = QColor(self.accent)
                faint.setAlpha(150)
                painter.fillRect(track, faint)
                continue
            width = max(14, track.width() // 2)
            left = track.left() + round((track.width() + width) * self._phase) - width
            run = QRect(left, track.top(), width, track.height())
            shade = QLinearGradient(float(run.left()), 0.0, float(run.right() + 1), 0.0)
            shade.setColorAt(0.0, clear)
            shade.setColorAt(0.5, self.accent)
            shade.setColorAt(1.0, clear)
            painter.fillRect(run.intersected(track), QBrush(shade))
        painter.end()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt's name
        if event.button() == Qt.MouseButton.MiddleButton:
            index = self.tabAt(event.position().toPoint())
            if index >= 0:
                self.tabCloseRequested.emit(index)
                return
        super().mouseReleaseEvent(event)


class Tab(QWidget):
    """A tab: its page — the program's, in the same web profile as the main window's — and what the window does for a
    page (``ui/web_bridge.py`` asks it as it asks the main window's page: ``open_path``, ``current_path``,
    ``toggle_fullscreen``, ``video_screen``)."""

    def __init__(self, window: "TabsWindow", mark: str):
        super().__init__(window)     # a parent from the first line: never left to the garbage collector
        from ui.qt_language import hide_dead_actions
        from ui.web_gallery_page import new_page, profile
        self.mark = mark
        self.owner = window
        self._profile = profile()
        self.view = QWebEngineView(self)
        page = new_page(self._profile, self.view, window.settings_provider)      # as the main window's pages
        self._mark = _Mark(mark, page)           # the page's own (it goes with the page; the page does not own it)
        page.setUrlRequestInterceptor(self._mark)
        self.view.setPage(page)
        hide_dead_actions(page)
        page.renderProcessTerminated.connect(self._render_died)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self._screen = False          # ⛶ of the page's player: this window full screen for it
        self._screen_was = None
        self._screen_since = 0.0
        self._screen_claimed = False
        self._died_at = 0.0
        self.look = None              # (v5.44.159) the colours its page said last (None — none yet)
        self.ground((_LOOK or _system_look())["bg"])
        self.view.loadStarted.connect(self._load_started)
        self.view.loadFinished.connect(self._load_finished)

    # ── what the bridge asks of a page ──────────────────────────────────────────────────────────────────────────────
    def open_path(self, path: str) -> None:
        from webui import service
        server = service.running()
        if server is not None:
            self.view.load(QUrl(server.login_url(path)))

    def current_path(self) -> str:
        return self.view.url().path()

    def toggle_fullscreen(self) -> None:
        self.owner.toggle_fullscreen()

    def video_screen(self, on: bool) -> None:
        """⛶ of the page's player: this window full screen for it, and back to the state it had."""
        if not on:
            self.leave_screen()
            return
        self._screen_claimed = True
        if self._screen:
            return
        window = self.owner
        self._screen = True
        self._screen_since = time.monotonic()
        window.screen_keys(True)
        if window.isFullScreen():
            self._screen_was = None
        else:
            self._screen_was = window.windowState()
            window.showFullScreen()

    def leave_screen(self) -> None:
        if not self._screen:
            return
        self._screen = False
        window = self.owner
        window.screen_keys(False)
        was, self._screen_was = self._screen_was, None
        if was is not None and window.isFullScreen():
            if was & Qt.WindowState.WindowMaximized:
                window.showMaximized()
            else:
                window.showNormal()

    def screen_on(self) -> bool:
        return self._screen

    def screen_off_by_window(self) -> None:
        """Esc on the whole screen, the window left full screen by itself, another tab shown: the player goes back."""
        if not self._screen:
            return
        self.leave_screen()
        self.view.page().runJavaScript(
            "window.LocalBooru && LocalBooru.nativeVideo && LocalBooru.nativeVideo.screen(false)")

    def read_look(self) -> None:
        """(v5.44.159) The page is asked its colours (:data:`LOOK_JS`); the strip takes them when this tab is shown.
        Asked when the page has loaded and when its look is changed (``/api/app/look``)."""
        try:
            self.view.page().runJavaScript(LOOK_JS, 0, self._look_said)
        except (RuntimeError, TypeError) as exc:      # the page going away
            _LOG.debug("a tab's page not asked its look: %s", exc)

    def _look_said(self, text) -> None:
        look = parse_look(text)
        if look is None:
            return
        self.look = look
        try:
            self.ground(look["bg"])
            self.owner.look_of(self)
        except RuntimeError:          # the window going away
            return

    def ground(self, colour: str) -> None:
        """What is seen where a page has not drawn itself yet — the ground of the page before it, not white (a dark
        look blinked white at every page)."""
        self.view.page().setBackgroundColor(QColor(colour))

    def _load_started(self) -> None:
        self._screen_claimed = False
        self.owner.loading_of(self, True)

    def _load_finished(self, _ok: bool) -> None:
        self.owner.loading_of(self, False)
        self.read_look()
        if self._screen:
            QTimer.singleShot(SCREEN_CLAIM_MS, self._screen_check)

    def _screen_check(self) -> None:
        if self._screen and not self._screen_claimed:
            self.leave_screen()

    def _render_died(self, status, code) -> None:
        """The page's renderer ended: the page loads again (once in a while — not round and round)."""
        if status == QWebEnginePage.RenderProcessTerminationStatus.NormalTerminationStatus:
            return
        _LOG.warning("a tab's page renderer ended (%s, exit code %s)", getattr(status, "name", status), code)
        now = time.monotonic()
        if now - self._died_at > 60:
            self._died_at = now
            QTimer.singleShot(500, self.view.reload)


class TabsWindow(QMainWindow):
    """The window of tabs (one for the program — :func:`open_tab`)."""

    def __init__(self, settings_provider=None):
        super().__init__()
        self.settings_provider = settings_provider or (lambda: {})
        self.setWindowTitle(TITLE)
        self.setMinimumSize(640, 420)
        from core.paths import APP_ICON_FILE
        if APP_ICON_FILE.exists():
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(APP_ICON_FILE)))
        self.tabs = QTabWidget(self)
        self.bar = _TabBar(self.tabs)
        self.tabs.setTabBar(self.bar)
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(False)         # (v5.44.159) each tab its own × in the strip's colours (add)
        self.tabs.setMovable(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.setUsesScrollButtons(False)    # many tabs: they narrow, as a browser's (their names cut with «…»)
        self.tabs.setIconSize(QSize(16, 16))
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._current_changed)
        self.bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bar.customContextMenuRequested.connect(self._tab_menu)
        # (v5.44.159) on the right of the strip: back, forward, reload of the tab shown, «В главное окно»
        self.tools = QWidget(self.tabs)
        self.tools.setObjectName("lbTabTools")
        row = QHBoxLayout(self.tools)
        row.setContentsMargins(6, 4, 8, 3)
        row.setSpacing(2)
        self.back_button = self._tool("Назад (Alt+←)", lambda: self._each(lambda t: t.view.back()))
        self.forward_button = self._tool("Вперёд (Alt+→)", lambda: self._each(lambda t: t.view.forward()))
        self.reload_button = self._tool("Обновить страницу (F5)", lambda: self._each(lambda t: t.view.reload()))
        self.main_button = QPushButton("В главное окно", self.tools)
        self.main_button.setToolTip("Эта страница — в главном окне программы (там играет видео программы); вкладка "
                                    "закроется")
        self.main_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.main_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.main_button.clicked.connect(lambda: self._each(self.to_main))
        for widget in (self.back_button, self.forward_button, self.reload_button, self.main_button):
            row.addWidget(widget)
        self.tabs.setCornerWidget(self.tools, Qt.Corner.TopRightCorner)
        self.setCentralWidget(self.tabs)
        self._colours: dict = {}
        self._was_maximized = False
        self.show_look(_LOOK or _system_look())
        for keys, slot in (("Ctrl+W", lambda: self.close_tab(self.tabs.currentIndex())),
                           ("Ctrl+F4", lambda: self.close_tab(self.tabs.currentIndex())),
                           ("Ctrl+Tab", lambda: self._step(1)), ("Ctrl+PgDown", lambda: self._step(1)),
                           ("Ctrl+Shift+Tab", lambda: self._step(-1)), ("Ctrl+PgUp", lambda: self._step(-1)),
                           ("F5", lambda: self._each(lambda t: t.view.reload())),
                           ("Ctrl+R", lambda: self._each(lambda t: t.view.reload())),
                           ("Alt+Left", lambda: self._each(lambda t: t.view.back())),
                           ("Alt+Right", lambda: self._each(lambda t: t.view.forward())),
                           ("F11", self.toggle_fullscreen)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(slot)
        # Esc of a player on the whole screen: the window's own key, only meanwhile (the pages have their own Esc)
        self._esc = QShortcut(QKeySequence("Esc"), self)
        self._esc.activated.connect(lambda: self._each(lambda t: t.screen_off_by_window()))
        self._esc.setEnabled(False)
        self._place()

    # ── the strip (v5.44.159) ────────────────────────────────────────────────────────────────────────────────────────
    def _tool(self, words: str, slot) -> QToolButton:
        button = QToolButton(self.tools)
        button.setToolTip(words)
        button.setAccessibleName(words)
        button.setAutoRaise(True)
        button.setIconSize(QSize(16, 16))
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)        # the keys stay the page's
        button.clicked.connect(slot)
        return button

    def _ratio(self) -> float:
        try:
            return max(1.0, float(self.devicePixelRatioF()))
        except (RuntimeError, ValueError):
            return 1.0

    def colours(self) -> dict:
        """The colours the strip has now (:func:`strip_colours`)."""
        return dict(self._colours)

    def show_look(self, look: dict) -> None:
        """The strip in the colours of ``look`` (a page's — :func:`parse_look`): its ground, its tabs with their
        icons and ×, the buttons on its right, the window's own caption."""
        colours = strip_colours(look)
        still = bool(look.get("still"))
        if colours == self._colours and still == self.bar.still:
            return
        self._colours = colours
        _LOOK.clear()
        _LOOK.update(look)
        self.setStyleSheet(strip_sheet(colours))
        self.bar.accent = QColor(colours["accent"])
        self.bar.set_still(still)
        ratio = self._ratio()
        for button, name in ((self.back_button, "back"), (self.forward_button, "fwd"), (self.reload_button, "reload")):
            button.setIcon(make_icon(name, colours["dim"], off=colours["faint"], ratio=ratio))
        for index in range(self.tabs.count()):
            self._dress(index)
        _tint_frame(self, colours)

    def _dress(self, index: int) -> None:
        """A tab's icon (its section's) and × in the strip's colours."""
        tab = self.tabs.widget(index)
        if not isinstance(tab, Tab) or not self._colours:
            return
        c, ratio = self._colours, self._ratio()
        name = section_icon(tab.current_path())
        self.tabs.setTabIcon(index, make_icon(name, c["dim"], on=c["accent"], ratio=ratio))
        close = self.bar.tabButton(index, QTabBar.ButtonPosition.RightSide)
        if isinstance(close, QToolButton):
            close.setIcon(make_icon("close", c["dim"], size=14, ratio=ratio))

    def look_of(self, tab: Tab) -> None:
        """``tab``'s page has said its colours: the strip takes them when it is the tab shown."""
        if self.tabs.currentWidget() is tab and tab.look:
            self.show_look(tab.look)

    def loading_of(self, tab: Tab, on: bool) -> None:
        index = self.tabs.indexOf(tab)
        if index < 0:
            return
        self.bar.set_loading(index, on)
        self._dress(index)                       # the page may be of another section now
        if self.tabs.currentWidget() is tab:
            self._ways(tab)

    def _ways(self, tab) -> None:
        """«Назад» and «Вперёд» of the strip: pressed only where the tab shown has a page to go to."""
        history = tab.view.history() if isinstance(tab, Tab) else None
        self.back_button.setEnabled(bool(history is not None and history.canGoBack()))
        self.forward_button.setEnabled(bool(history is not None and history.canGoForward()))
        self.reload_button.setEnabled(isinstance(tab, Tab))
        self.main_button.setEnabled(isinstance(tab, Tab))

    # ── the tabs ────────────────────────────────────────────────────────────────────────────────────────────────────
    def add(self, url: QUrl, *, front: bool = True) -> Tab:
        mark = f"t{next(_MARKS)}"
        tab = Tab(self, mark)
        _TABS[mark] = tab
        index = self.tabs.addTab(tab, "Загрузка…")
        self.tabs.setTabToolTip(index, "Загрузка…")
        close = QToolButton(self.bar)            # (v5.44.159) its own ×: in the strip's colours
        close.setObjectName("lbTabClose")
        close.setToolTip("Закрыть вкладку (Ctrl+W)")
        close.setAccessibleName("Закрыть вкладку")
        close.setAutoRaise(True)
        close.setIconSize(QSize(14, 14))
        close.setFixedSize(20, 20)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.clicked.connect(lambda _checked=False, tab=tab: self.close_tab(self.tabs.indexOf(tab)))
        self.bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, close)
        self._dress(index)
        tab.view.titleChanged.connect(lambda title, tab=tab: self._titled(tab, title))
        tab.view.urlChanged.connect(lambda _url, tab=tab: self._moved(tab))
        tab.view.load(url)
        if front:
            self.tabs.setCurrentWidget(tab)
            tab.view.setFocus()
        return tab

    def _moved(self, tab: Tab) -> None:
        """A tab's page is another address now: its section's icon, «Назад» and «Вперёд»."""
        index = self.tabs.indexOf(tab)
        if index < 0:
            return
        self._dress(index)
        if self.tabs.currentWidget() is tab:
            self._ways(tab)

    def close_tab(self, index: int) -> None:
        """A tab closed: its page goes (its own player with it); the last one — the window closes."""
        tab = self.tabs.widget(index)
        if tab is None:
            return
        if isinstance(tab, Tab):
            tab.leave_screen()
            _TABS.pop(tab.mark, None)
        self.tabs.removeTab(index)
        tab.deleteLater()            # (its parent keeps it until then: never the garbage collector's)
        if self.tabs.count() == 0:
            self.close()

    def _titled(self, tab: Tab, title: str) -> None:
        index = self.tabs.indexOf(tab)
        if index < 0:
            return
        name = _clean_title(title)
        self.tabs.setTabText(index, (name or TITLE).replace("&", "&&"))
        self.tabs.setTabToolTip(index, name or TITLE)
        if self.tabs.currentWidget() is tab:
            self._show_title(tab)

    def _show_title(self, tab) -> None:
        name = _clean_title(tab.view.title()) if isinstance(tab, Tab) else ""
        self.setWindowTitle(f"{name}{SUFFIX}" if name else TITLE)

    def _current_changed(self, _index: int) -> None:
        current = self.tabs.currentWidget()
        for tab in list(_TABS.values()):
            if tab is not current and tab.screen_on():
                tab.screen_off_by_window()           # another tab shown: the player of the hidden one goes back
        if current is not None:
            self._show_title(current)
        self._ways(current)
        if isinstance(current, Tab):                 # (v5.44.159) the strip — in the colours of the page shown now
            if current.look:
                self.show_look(current.look)
            else:
                current.read_look()

    def _step(self, step: int) -> None:
        count = self.tabs.count()
        if count:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % count)

    def _each(self, fn) -> None:
        """``fn`` of the tab shown."""
        current = self.tabs.currentWidget()
        if isinstance(current, Tab):
            fn(current)

    def _tab_menu(self, point) -> None:
        index = self.tabs.tabBar().tabAt(point)
        tab = self.tabs.widget(index) if index >= 0 else None
        if not isinstance(tab, Tab):
            return
        menu = QMenu(self)
        main = menu.addAction("Открыть в главном окне")
        main.setToolTip("Эта страница — в главном окне программы (там играет видео программы)")
        close = menu.addAction("Закрыть вкладку")
        others = menu.addAction("Закрыть другие вкладки")
        others.setEnabled(self.tabs.count() > 1)
        chosen = menu.exec(self.tabs.tabBar().mapToGlobal(point))
        menu.deleteLater()
        if chosen is main:
            self.to_main(tab)
        elif chosen is close:
            self.close_tab(self.tabs.indexOf(tab))
        elif chosen is others:
            for other in [self.tabs.widget(n) for n in range(self.tabs.count())]:
                if other is not tab:
                    self.close_tab(self.tabs.indexOf(other))

    def to_main(self, tab: Tab) -> None:
        """«Открыть в главном окне»: the tab's page in the main window (the video of the program plays there), the main
        window in front; the tab closes."""
        from webui import service
        bridge = service.bridge()
        page = getattr(bridge, "page", None)
        if page is None or not hasattr(page, "open_path"):
            return
        url = tab.view.url()
        path = url.path() + (f"?{url.query()}" if url.query() else "") + (f"#{url.fragment()}" if url.fragment() else "")
        page.open_path(path or "/posts")
        main = page.window()
        if main.isMinimized():
            main.setWindowState(main.windowState() & ~Qt.WindowState.WindowMinimized)
        main.show()
        main.raise_()
        main.activateWindow()
        self.close_tab(self.tabs.indexOf(tab))

    # ── the window ──────────────────────────────────────────────────────────────────────────────────────────────────
    def _place(self) -> None:
        """Where it was left this run; the first time — most of the screen, in the middle of it."""
        geometry = _PLACE.get("geometry")
        if geometry is not None and self.restoreGeometry(geometry):
            return
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1200, 800)
            return
        avail = screen.availableGeometry()
        width, height = max(800, int(avail.width() * 0.8)), max(500, int(avail.height() * 0.85))
        width, height = min(width, avail.width()), min(height, avail.height())
        self.resize(width, height)
        self.move(avail.left() + (avail.width() - width) // 2, avail.top() + (avail.height() - height) // 2)

    def show_first(self) -> None:
        if _PLACE.get("maximized"):
            self.showMaximized()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        if self._colours:
            _tint_frame(self, self._colours)     # (v5.44.159) the caption: the window has its handle only now

    def bring_up(self) -> None:
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def screen_keys(self, on: bool) -> None:
        """A player of a tab on the whole screen: Esc is the window's meanwhile (the pages have their own Esc)."""
        self._esc.setEnabled(on or any(t.screen_on() for t in _TABS.values()))

    def toggle_fullscreen(self) -> None:
        """F11: the whole window on the full screen and back (maximized when it was)."""
        if self.isFullScreen():
            if self._was_maximized:
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()

    def changeEvent(self, event):  # noqa: N802 - Qt's name
        super().changeEvent(event)
        from PySide6.QtCore import QEvent
        if event.type() != QEvent.Type.WindowStateChange:
            return
        full = self.isFullScreen()
        self.tabs.tabBar().setVisible(not full)
        self.tools.setVisible(not full)
        if not full:
            for tab in list(_TABS.values()):
                if tab.screen_on() and time.monotonic() - tab._screen_since > 1.0:
                    tab._screen_was = None           # nothing to give back: it is not full screen any more
                    tab.screen_off_by_window()

    def closeEvent(self, event):  # noqa: N802 - Qt's name
        """The window closes: its tabs' pages go — nothing else (the program, its server and the main window stay)."""
        global _WINDOW
        if not self.isFullScreen():
            _PLACE["geometry"] = self.saveGeometry()
            _PLACE["maximized"] = self.isMaximized()
        for index in reversed(range(self.tabs.count())):
            tab = self.tabs.widget(index)
            if isinstance(tab, Tab):
                _TABS.pop(tab.mark, None)
            self.tabs.removeTab(index)
            if tab is not None:
                tab.deleteLater()
        event.accept()
        if _WINDOW is self:
            _WINDOW = None                       # the next link opens a new one
        if self not in _DYING:
            _DYING.append(self)
            QTimer.singleShot(0, _dispose)       # destroyed once this event is over, in the window's thread
