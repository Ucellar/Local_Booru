"""Looks of the web gallery.  A look is one stylesheet in ``static/skins/`` that changes the whole page:
colours, fonts, the arrangement (where the tags live, how thumbnails are laid out), not only a palette.
All looks share the same HTML; the page script switches them without reloading."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Skin:
    id: str
    name: str
    description: str
    swatches: tuple[str, ...]      # a few colours for the menu


SKINS = (
    Skin("auto", "r34 (как в системе)", "Светлая или тёмная — как выбрано в Windows",
         ("#b4dca9", "#fbfcfb", "#17191e", "#0a58ca")),
    Skin("classic", "r34", "Как на привычных booru: меню сверху, теги слева, ровная сетка",
         ("#b4dca9", "#fbfcfb", "#0a58ca", "#aa0000")),
    Skin("classic-dark", "r34, ночь", "То же самое в тёмных тонах",
         ("#23351f", "#17191e", "#82b6ff", "#ff8a8b")),
    Skin("deep", "Глубина", "Тёмно-синяя, превью карточками: размер, оценка и избранное под каждой",
         ("#0d2241", "#173763", "#f2b233", "#b4c7d9")),
    Skin("flow", "Лента", "Светлая и просторная: картинки рядами без обрезки, фильтры в выдвижной панели",
         ("#f4f5f7", "#ffffff", "#15171c", "#2251d1")),
    Skin("cinema", "Кинозал", "Чёрная: крупные квадраты без рамок, теги при наведении, картинка на весь экран",
         ("#050506", "#141416", "#e8c27a", "#ece8e1")),
    Skin("hub", "Хаб", "Чёрная с оранжевым, как тот самый сайт: превью как у видео, подпись под каждым",
         ("#000000", "#1b1b1b", "#ff9000", "#ffffff")),
    Skin("win98", "Win98", "Серые окна на бирюзовом столе, панель задач с «Пуском» и часами",
         ("#008080", "#c0c0c0", "#000080", "#ffffff")),
    Skin("nhentai", "nhentai", "Тёмно-серая с розово-красным, как сайт манги; для манги — по умолчанию",
         ("#1f1f1f", "#0d0d0d", "#ed2553", "#d9d9d9")),
    Skin("legacy", "Legacy", "Как старое окно программы: меню слева, тёмно-синяя с фиолетовыми кнопками",
         ("#090b12", "#0d0f16", "#8060e0", "#ff9000")),
    Skin("legacy-sakura", "Legacy: Сакура", "Старое окно в теме «Сакура»: тёмная сливовая с розовыми кнопками",
         ("#0d0810", "#150d12", "#c05088", "#ff9000")),
)

BY_ID = {skin.id: skin for skin in SKINS}
DEFAULT = "auto"


def valid(skin_id: str) -> str:
    return skin_id if skin_id in BY_ID else DEFAULT
