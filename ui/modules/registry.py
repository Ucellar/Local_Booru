"""Page registry — all pages, workspaces and nav config in one place."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional

from ui.gallery_page import GalleryPage
from ui.tags_page import TagsPage
from ui.post_page import PostPage
from ui.settings_page import SettingsPage
from ui.tagger_page import TaggerPage
from ui.nomatch_page import NoMatchPage
from ui.manga import MangaPage
from ui.downloader import DownloaderPage
from ui.games_page import GamesPage
from ui.duplicates_page import DuplicatesPage
from ui.subscription_page import SubscriptionPage
from ui.trash_page import TrashPage
from ui.diagnostics_page import DiagnosticsPage
from ui.overview_page import OverviewPage


@dataclass(frozen=True)
class PageSpec:
    key: str
    attr: str
    title_key: str
    button_attr: Optional[str]
    button_text: str
    factory: Callable
    workspace: str = "system"
    refresh_on_open: bool = False
    random_enabled: bool = False


PAGE_SPECS: tuple[PageSpec, ...] = (
    # Парсер workspace (был АПТ)
    PageSpec("Tagger",     "tagger_page",     "TaggerTitle", "btn_tagger",     "Парсер",   TaggerPage,     "apt"),
    PageSpec("NO_MATCH",   "nomatch_page",    "NO_MATCH",    "btn_nomatch",    "Брак",     NoMatchPage,    "apt", True),
    # Галерея workspace — overview + content pages
    PageSpec("Overview",   "overview_page",   "Overview",    "btn_overview",   "Обзор",     OverviewPage,   "gallery", True),
    PageSpec("Gallery",    "gallery_page",    "Gallery",     "btn_gallery",    "Галерея",  GalleryPage,    "gallery", True, True),
    PageSpec("Trash",      "trash_page",      "Trash",       "btn_trash",      "Удалено",   TrashPage,      "gallery", True),
    PageSpec("Diagnostics", "diagnostics_page", "Diagnostics", "btn_diagnostics", "Диагностика", DiagnosticsPage, "gallery", True),
    PageSpec("Tags",       "tags_page",       "Tags",        "btn_tags",       "Теги",     TagsPage,       "gallery", True),
    PageSpec("Manga",      "manga_page",      "Manga",       "btn_manga",      "Манга",    MangaPage,      "gallery", True, True),
    PageSpec("Games",      "games_page",      "Games",       "btn_games",      "Игры",     GamesPage,      "gallery", True),
    # Граббер workspace (был АСП)
    PageSpec("DLER",       "downloader_page", "DLER",        "btn_dler",       "Граббер",  DownloaderPage, "adp"),
    PageSpec("Subs",       "subs_page",       "Subs",        "btn_subs",       "Подписки", SubscriptionPage, "adp"),
    PageSpec("Duplicates", "duplicates_page", "Duplicates",  "btn_duplicates", "Дубли",    DuplicatesPage, "duplicates", True),
    # System (always available via code)
    PageSpec("Post",       "post_page",       "Post",        None,             "Post",     PostPage,       "system", False, True),
    PageSpec("Settings",   "settings_page",   "Settings",    "btn_settings",   "Настройки",SettingsPage,  "system"),
)

PAGE_BY_KEY = {spec.key: spec for spec in PAGE_SPECS}

WORKSPACE_DEFAULT_PAGE = {
    "apt":        "Tagger",
    "gallery":    "Gallery",
    "adp":        "DLER",
    "duplicates": "Duplicates",
}

WORKSPACE_TITLES = {
    "apt":        {"ru": "Парсер",  "en": "Parser"},
    "gallery":    {"ru": "Галерея", "en": "Gallery"},
    "adp":        {"ru": "Граббер", "en": "Grabber"},
    "duplicates": {"ru": "Дубли",   "en": "Dupes"},
}
