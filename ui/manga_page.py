"""Совместимая точка входа для старых импортов.

Реальная реализация теперь лежит в пакете ui.manga:
- helpers.py — чтение папок/архивов/метаданных;
- widgets.py — карточки и выбор глав;
- reader.py — окно чтения;
- page.py — Qt-страница библиотеки манги.
"""

from ui.manga import MangaPage, MangaReader, MangaCard, ChapterSelection

__all__ = ["MangaPage", "MangaReader", "MangaCard", "ChapterSelection"]
