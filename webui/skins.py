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
    dark: bool = False             # (v5.44.45) the look's own palette is dark — the starting side of its light/dark switch
    toggle: bool = True            # offers a light/dark switch; the r34 trio does not (it IS the light/dark/system set)


SKINS = (
    Skin("auto", "r34 (как в системе)", "Светлая или тёмная — как выбрано в Windows",
         ("#b4dca9", "#fbfcfb", "#17191e", "#0a58ca"), toggle=False),
    Skin("classic", "r34", "Как на привычных booru: меню сверху, теги слева, ровная сетка",
         ("#b4dca9", "#fbfcfb", "#0a58ca", "#aa0000"), toggle=False),
    Skin("classic-dark", "r34, ночь", "То же самое в тёмных тонах",
         ("#23351f", "#17191e", "#82b6ff", "#ff8a8b"), dark=True, toggle=False),
    Skin("deep", "Глубина", "Тёмно-синяя, превью карточками: размер, оценка и избранное под каждой",
         ("#0d2241", "#173763", "#f2b233", "#b4c7d9"), dark=True),
    Skin("flow", "Лента", "Светлая и просторная: картинки рядами без обрезки, фильтры в выдвижной панели",
         ("#f4f5f7", "#ffffff", "#15171c", "#2251d1")),
    Skin("cinema", "Кинозал", "Чёрная: крупные квадраты без рамок, теги при наведении, картинка на весь экран",
         ("#050506", "#141416", "#e8c27a", "#ece8e1"), dark=True),
    Skin("hub", "Хаб", "Чёрная с оранжевым, как тот самый сайт: превью как у видео, подпись под каждым",
         ("#000000", "#1b1b1b", "#ff9000", "#ffffff"), dark=True),
    Skin("win98", "Win98", "Серые окна на бирюзовом столе, панель задач с «Пуском» и часами",
         ("#008080", "#c0c0c0", "#000080", "#ffffff")),
    Skin("nhentai", "nhentai", "Тёмно-серая с розово-красным, как сайт манги; для манги — по умолчанию",
         ("#1f1f1f", "#0d0d0d", "#ed2553", "#d9d9d9"), dark=True),
    Skin("pixiv", "Pixiv", "Светлая бело-голубая, скруглённые превью и много воздуха — как на pixiv",
         ("#eef1f6", "#ffffff", "#0096fa", "#16223c")),
    Skin("danbooru", "Danbooru", "Светлая, как сам Danbooru: тёмная шапка, теги слева по цветам категорий, ровная сетка",
         ("#2d3a4a", "#f4f6f8", "#0b73c7", "#b4252b")),
    Skin("sepia", "Сепия", "Тёплая кремовая «читальня»: засечки, превью в паспарту как фотографии",
         ("#efe4ca", "#f7efdc", "#9c5a28", "#40331f")),
    Skin("youtube", "YouTube", "Белая с красным, скруглённые превью и тёмные чипсы — для вкладки «Видео»",
         ("#ffffff", "#f9f9f9", "#ff0000", "#0f0f0f")),
    Skin("legacy", "Legacy", "Как старое окно программы: меню слева, тёмно-синяя с фиолетовыми кнопками",
         ("#090b12", "#0d0f16", "#8060e0", "#ff9000"), dark=True),
)

BY_ID = {skin.id: skin for skin in SKINS}
DEFAULT = "auto"
VARIANTS = ("light", "dark")


def valid(skin_id: str) -> str:
    return skin_id if skin_id in BY_ID else DEFAULT


def variant_of(skin_id: str, chosen: str = "") -> str:
    """The light/dark side a page shows: the viewer's choice when it is one of :data:`VARIANTS` and the look offers the
    switch, otherwise the look's own side (``dark`` → «dark», else «light»).  (v5.44.45)"""
    skin = BY_ID.get(valid(skin_id))
    if skin and skin.toggle and chosen in VARIANTS:
        return chosen
    return "dark" if skin and skin.dark else "light"
