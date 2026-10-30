"""Qt's own words in the program's language (``language`` of the settings, «ru» or «en»).

Qt and its web engine speak English unless a translation is installed: the menu of the right mouse button over a page
(«Back», «Reload», «Copy link address»…), the buttons of the window's dialogs («Yes», «Cancel»).  PySide6 ships Qt's
translations (``qtbase_ru``, ``qtwebengine_ru``, ``qtmultimedia_ru``); they are installed for Russian before the
window is built (``program_window.make``).  The default locale goes with them: the web engine takes its own language
from it when it starts (its date fields, «Выберите файл», the messages of forms), so it is set before the first page.

Items of that menu that do nothing in the program's window are hidden (``hide_dead_actions``): the page's source
(another window, which this one does not open), saving the program's own page, a link «in a new window» (the same
as «in a new tab» here).
"""
from __future__ import annotations

import logging

_LOG = logging.getLogger("local_booru.window")

QT_PARTS = ("qtbase", "qtwebengine", "qtmultimedia")
DEAD_ACTIONS = ("ViewSource", "SavePage", "OpenLinkInNewWindow")
_STATE: dict = {"language": "", "translators": []}   # kept for the run: a translator gone stops translating


def apply(app, settings: dict | None) -> str:
    """Install Qt's translation for the settings' language (once; again only when it changed); the language set."""
    from core.language_bootstrap import normalize_language
    language = normalize_language((settings or {}).get("language"))
    if app is None or _STATE["language"] == language:
        return language
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    for translator in _STATE["translators"]:
        app.removeTranslator(translator)
    _STATE["translators"] = []
    QLocale.setDefault(QLocale("ru_RU" if language == "ru" else "en_US"))
    if language != "en":
        folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        for part in QT_PARTS:
            translator = QTranslator(app)
            if translator.load(QLocale(), part, "_", folder):
                app.installTranslator(translator)
                _STATE["translators"].append(translator)
            else:
                _LOG.info("Qt translation %s_%s not found in %s: those words stay in English", part, language, folder)
    _STATE["language"] = language
    return language


def hide_dead_actions(page) -> None:
    """Hide the menu items of a web page that do nothing in the program's window (see the module's text)."""
    from PySide6.QtWebEngineCore import QWebEnginePage
    for name in DEAD_ACTIONS:
        item = getattr(QWebEnginePage.WebAction, name, None)
        if item is not None:
            page.action(item).setVisible(False)
