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


class DubError(video_download.DownloadError):
    """Not raised here: nothing is asked of the players."""


def enabled(settings: dict) -> bool:
    return False


def find_address(address: str, root: Path, *, settings: dict, say, stop_check=None):
    """None: the page is read as any other («Скачать с сайта» looks for open files, then for the open sources)."""
    return None


def find(page, root: Path, *, settings: dict, say, stop_check=None):
    return None
