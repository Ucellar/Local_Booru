"""Qt wrappers around the parser (core/parser). No parsing logic lives here."""
from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from core.parser.runner import ParserRunner


class TaggerWorker(QThread):
    log = Signal(str)
    progress = Signal(int, int)
    current_file = Signal(str)
    site_current = Signal(str, str, str)
    done = Signal()

    def __init__(self, settings):
        super().__init__()
        try:
            from core.shutdown import register_qthread
            register_qthread(self, "TaggerWorker")
        except ImportError:
            pass
        self.settings = dict(settings or {})
        self.summary: dict = {}
        self._runner = ParserRunner(
            self.settings,
            log=self._emit_log,
            progress=lambda d, t: self.progress.emit(int(d), int(t)),
            current_file=lambda p: self.current_file.emit(str(p)),
            activity=lambda s, st, p: self.site_current.emit(str(s), str(st), str(p)),
        )

    def _emit_log(self, message: str) -> None:
        self.log.emit(str(message))

    def run(self):
        from core.parser import jobs
        owner = f"старая страница «Парсер», запуск в {time.strftime('%H:%M:%S')}"
        if not jobs.claim(owner):          # one parser run at a time for the whole program
            self._emit_log(f"ЗАПУСК НЕ НАЧАТ: {jobs.busy_text(jobs.owner())}")
            self.done.emit()
            return
        try:
            self.summary = self._runner.run() or {}
        except Exception as exc:  # last line of defence: show it, never die silently
            import traceback
            self._emit_log(f"ПАРСЕР ОСТАНОВЛЕН ОШИБКОЙ: {type(exc).__name__}: {exc}")
            self._emit_log(traceback.format_exc()[-3000:])
        finally:
            jobs.release(owner)
            self.done.emit()

    # ── controls used by the parser page ────────────────────────────────────
    def set_paused(self, paused):
        self._runner.set_paused(bool(paused))

    def requestInterruption(self):
        self._runner.stop()
        super().requestInterruption()


class BrowserLoginWorker(QThread):
    """Open the site login page in the user's browser and wait a little."""

    log = Signal(str)

    def __init__(self, auth_url, wait_seconds, settings=None):
        super().__init__()
        self.auth_url = str(auth_url or "")
        self.wait_seconds = int(wait_seconds or 0)
        self.settings = settings if isinstance(settings, dict) else {}

    def run(self):
        from core.external_browser import open_external_browser_url
        url = self.auth_url.strip()
        if not url:
            self.log.emit("ВХОД: пустой адрес")
            return
        self.log.emit("ВХОД: открываю страницу сайта в браузере")
        if not open_external_browser_url(url, self.settings, log_fn=self.log.emit):
            self.log.emit("ВХОД: браузер не открылся")
            return
        self.log.emit("Войди на сайт / пройди Cloudflare; расширение Companion передаст cookies")
        for elapsed in range(max(0, self.wait_seconds)):
            if self.isInterruptionRequested():
                self.log.emit("ВХОД: остановлено")
                return
            left = self.wait_seconds - elapsed
            if left % 10 == 0 or left <= 5:
                self.log.emit(f"ВХОД: жду {left} с")
            time.sleep(1)
        self.log.emit("ВХОД: ожидание закончено")
