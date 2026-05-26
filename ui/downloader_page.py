"""Совместимая точка входа для старых импортов.

Реальная реализация теперь лежит в пакете ui.downloader:
- helpers.py — сетевые и файловые утилиты загрузчика;
- worker.py — фоновый worker;
- page.py — Qt-страница.
"""

from ui.downloader import DownloaderPage, DownloaderWorker

__all__ = ["DownloaderPage", "DownloaderWorker"]
