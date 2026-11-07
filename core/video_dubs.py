"""The public copy of Local Booru: no voice-overs from the video players of the sites.

The owner's own copy reads every voice-over of a title from the players a site shows (``core/video_dubs`` there).  The
public program does not: it downloads only what a page gives openly (files, open streams, open APIs).  This module keeps
the names the program asks for, so «Скачать с сайта» goes on to the open sources and «Настройки» do not offer the
voice-overs (``AVAILABLE``).  ``tools/publish_public.py`` puts this file in place of the real one and refuses to publish
without it.
"""
from __future__ import annotations

from pathlib import Path

from core import video_download

AVAILABLE = False
NAME = "YummyAnime"
QUALITIES = ("best", "1080", "720", "480", "360")          # «Качество» on the page (the same values as the owner's copy)
# (v5.44.114) the other names the program asks for (``core/video_watch.py``, ``core/catalog.py``…): a page of that site
# is never one of its titles here (``is_yummy``), so nothing below is asked of a player
SITE = ""
ORDER: tuple = ()
PLAYERS: dict = {}
SORTS: tuple = ()


class DubError(video_download.DownloadError):
    """Not raised here: nothing is asked of the players."""


def enabled(settings: dict) -> bool:
    return False


def find_address(address: str, root: Path, *, settings: dict, say, stop_check=None):
    """None: the page is read as any other («Скачать с сайта» looks for open files, then for the open sources)."""
    return None


def find(page, root: Path, *, settings: dict, say, stop_check=None):
    return None


# ── (v5.44.114) the names other modules ask for — nothing of the players: a title of that site is never found here ──
def is_yummy(address: str) -> bool:
    return False


def alias_of(address: str) -> str:
    return ""


def api_of(settings: dict) -> str:
    return ""


def absolute(address: str) -> str:
    return str(address or "")


def ratings_of(anime, address: str = "") -> list:
    return []


def order_of(*_args, **_kwargs) -> list:
    return []


def cap_of(quality: str) -> int:
    return 0


def order(links, cap: int = 0, **_kwargs) -> list:
    return []


def within(every, cap: int = 0) -> list:
    return []


def direct(session):
    return session


def title_page(anime, page=None):
    return None


def _unavailable(*_args, **_kwargs):
    raise DubError("Озвучки из плееров сайтов в этой версии программы недоступны")


Api = Resolver = Merger = _found = stream_way = _unavailable


def __getattr__(name: str):
    """Any other name of the owner's module (the code of the players) — not here: asked for, it says so."""
    if name.startswith("__"):
        raise AttributeError(name)
    return _unavailable
