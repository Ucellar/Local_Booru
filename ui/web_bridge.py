"""Requests from the web pages that need the program window: dialogs, the clipboard, the video player,
dragging a file out, settings that the program owns.

The web server answers in its own threads; Qt windows may be touched only from the main thread.
``AppBridge.request`` may be called from any thread: it emits a signal, and Qt runs the action
later in the main thread.  The server looks up everything from the library first (paths, tags),
so the actions here only deal with the window.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from PySide6.QtCore import QMimeData, QObject, Qt, QUrl, Signal

_LOG = logging.getLogger("local_booru.webui")

ACTIONS = ("choose_manga_folder", "choose_parser_folder", "choose_path", "open_settings", "play_video", "copy_file",
           "drag_file", "tag_color", "export_results", "fullscreen", "save_settings", "import_profile",
           "choose_archive", "video", "choose_video_folder", "fetch_page", "video_screen", "open_card",
           "choose_move_target", "restart_app")
# Settings the pages may change (webui/settings_store.py; /api/app/save-settings from a page is refused).
from webui.settings_store import PAGE_KEYS as SETTINGS_FROM_PAGES, PATH_KEYS
from webui.service import begin_call


class AppBridge(QObject):
    _requested = Signal(str, object)
    _drag_copy_ready = Signal(str, str)          # key of the file, the copy with tags ("" when it failed)

    def __init__(self, main_window, gallery_page):
        super().__init__(gallery_page)
        self.mw = main_window
        self.page = gallery_page
        self._requested.connect(self._run)
        self._drag_copy_ready.connect(self._drag_copy_made)
        self._drag_copies: dict[str, str] = {}   # file key -> its copy with tags (made once per run)
        self._drag_making: set[str] = set()

    def request(self, name: str, payload=None) -> bool:
        """Ask the program to do ``name``; returns False for an unknown request (nothing is done)."""
        if name not in ACTIONS:
            return False
        self._requested.emit(name, payload or {})
        return True

    # ── main thread ──────────────────────────────────────────────────────────
    def _run(self, name: str, payload) -> None:
        try:
            getattr(self, "_do_" + name)(payload)
        except Exception as exc:  # a failed dialog must not take the window down; tell the log
            _LOG.error("web request %s failed: %s", name, exc, exc_info=True)

    def _do_choose_manga_folder(self, _payload) -> None:
        from PySide6.QtWidgets import QFileDialog
        current = str(self.mw.settings.get("manga_root") or "")
        chosen = QFileDialog.getExistingDirectory(self.mw, "Папка с мангой", current)
        if not chosen:
            return
        self.mw.settings["manga_root"] = chosen
        self.mw.save_settings()
        from core import manga_library
        manga_library.LIBRARY.invalidate()        # the server scans the new folder on the next page
        self.page.open_path("/manga")

    def _do_choose_video_folder(self, _payload) -> None:
        from PySide6.QtWidgets import QFileDialog

        from core import video_library
        current = video_library.video_root(self.mw.settings)
        chosen = QFileDialog.getExistingDirectory(self.mw, "Папка видео: одна папка — один сериал", current)
        if not chosen:
            return
        self.mw.settings["video_root"] = chosen
        self.mw.save_settings()
        video_library.LIBRARY.invalidate()
        self.page.open_path("/videos")

    def _do_fetch_page(self, payload) -> None:
        """«Описание с сайта» of a site that checks for robots: the window's browser opens the page out of sight
        (``ui/page_fetch.py``) and gives its HTML to the waiting task (``payload["box"]``)."""
        if not begin_call(payload):               # the task stopped waiting
            return
        from ui.page_fetch import PageFetch
        profile = getattr(self.page, "_profile", None)
        if profile is None:
            payload["box"]["error"] = "Окно программы без встроенного браузера"
            payload["done"].set()
            return
        fetch = PageFetch(profile, str(payload.get("url") or ""), payload["box"], payload["done"], parent=self)
        fetch.start()

    def _do_choose_parser_folder(self, _payload) -> None:
        from PySide6.QtWidgets import QFileDialog
        current = str(self.mw.settings.get("root") or "")
        chosen = QFileDialog.getExistingDirectory(self.mw, "Папка с картинками для парсера", current)
        if not chosen:
            return
        self.mw.settings["root"] = chosen
        self.mw.save_settings()
        self.page.open_path("/parser")

    def _do_choose_path(self, payload) -> None:
        """A folder or a file for a setting of «Настройки» (only the keys of ``PATH_KEYS``)."""
        from PySide6.QtWidgets import QFileDialog
        key = str(payload.get("key") or "")
        if key not in PATH_KEYS:
            return
        kind, title, flt = PATH_KEYS[key]
        current = str(self.mw.settings.get(key) or "")
        if kind == "folder":
            chosen = QFileDialog.getExistingDirectory(self.mw, title, current)
        else:
            chosen, _ = QFileDialog.getOpenFileName(self.mw, title, current, flt)
        if not chosen:
            return
        if key == "light_backup_dir":
            # the rule of the page's own form (a folder typed there is checked by the server)
            from core.light_backup import backup_folder_problem
            problem = backup_folder_problem(self.mw.settings, chosen)
            if problem:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self.mw, title, f"{chosen}\n\n{problem}. Папка не изменена.")
                return
        self.mw.settings[key] = chosen
        self.mw.save_settings()
        if key == "manga_root":
            from core import manga_library
            manga_library.LIBRARY.invalidate()
        if key == "video_root":
            from core import video_library
            video_library.LIBRARY.invalidate()
        self.page.open_path("/settings")

    def _do_choose_archive(self, _payload) -> None:
        """«Настройки → Папки → Подключить другой архив»: it opens at the next start (``core.archive_switch``); this
        run keeps its archive, settings and folders."""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from core import archive_switch
        title = "Папка архива Local_Booru_Archive"
        chosen = QFileDialog.getExistingDirectory(self.mw, title, str(archive_switch.open_archive().parent))
        if not chosen:
            return
        result = archive_switch.connect(chosen)
        if not result.get("ok"):
            QMessageBox.warning(self.mw, title, str(result.get("error") or "Не получилось"))
        elif result.get("stayed"):
            QMessageBox.information(self.mw, title, "Это архив, который сейчас открыт: при следующем запуске "
                                    "откроется он же.")
        else:
            QMessageBox.information(
                self.mw, title, f"При следующем запуске откроется архив:\n{result['archive']}\n\n"
                "Закройте программу и откройте снова. До этого программа работает с открытым сейчас архивом и "
                "сохраняет всё в него; подключённый архив не меняется.")
        self.page.open_path("/settings#set-folders")

    def _do_choose_move_target(self, _payload) -> None:
        """«Настройки → Папки → Перенести архив на другой диск…»: the folder it goes to; the copy starts at once (a
        page task of ``core/archive_move.py``), the rest is done at the next start."""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from core import archive_move, page_jobs
        title = "Куда перенести архив"
        root = archive_move.source()
        chosen = QFileDialog.getExistingDirectory(self.mw, title, str(root.parent) if root is not None else "")
        if not chosen:
            return
        try:
            target = archive_move.target_for(chosen)
        except archive_move.MoveError as exc:
            QMessageBox.warning(self.mw, title, str(exc))
            return
        sure = QMessageBox.question(
            self.mw, title, f"Весь архив будет скопирован сюда:\n{target}\n\nКопирование идёт, пока вы работаете с "
            "программой; большой архив копируется часами. Потом программа попросит перезапуск: при запуске она "
            "докопирует изменённое, перенесёт базу и откроет архив на новом месте. Старый архив останется как есть, "
            "пока вы сами не удалите его в «Настройках».\n\nНачать копирование?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if sure != QMessageBox.Yes:
            return
        try:
            archive_move.request(chosen)
            page_jobs.start(archive_move.KIND, "Перенос архива", archive_move.copy_job)
        except archive_move.MoveError as exc:
            QMessageBox.warning(self.mw, title, str(exc))
        except page_jobs.TaskBusy as exc:
            QMessageBox.information(self.mw, title, f"Уже идёт: {exc.label}")
        except OSError as exc:
            QMessageBox.warning(self.mw, title, f"Не получилось начать: {exc}")
        self.page.open_path("/settings#set-folders")

    def _do_restart_app(self, _payload) -> None:
        """«Перезапустить» of a page: the window closes, app.py starts the program again at the end of its exit."""
        from core import app_restart
        app_restart.request()
        self.mw.close()

    def _do_save_settings(self, payload) -> None:
        try:
            if not begin_call(payload):            # the page stopped waiting: it said «not saved»
                return
            changes = {k: v for k, v in dict(payload.get("changes") or {}).items() if k in SETTINGS_FROM_PAGES}
            if changes:
                self.mw.settings.update(changes)
                self.mw.save_settings()
        finally:
            done = payload.get("done")
            if done is not None:
                done.set()

    def _do_import_profile(self, payload) -> None:
        """A settings profile over the window's settings (the page sent the file; the server checked it).

        The window's settings change only when the profile is saved; the merged settings keep every
        current key, so they are updated in place (the web server reads the same dictionary meanwhile)."""
        from core.redaction import sanitize_text
        answer = payload.get("answer") if isinstance(payload.get("answer"), dict) else {}
        try:
            if not begin_call(payload):            # the page stopped waiting: it told the user «not loaded»
                return
            from core.settings_bundle import import_profile
            try:
                result = import_profile(str(payload["path"]), self.mw.settings, apply=True)
            except Exception as exc:               # whatever it was, nothing is applied and the page hears why
                _LOG.warning("settings profile import failed: %s", sanitize_text(f"{type(exc).__name__}: {exc}"))
                answer.update(ok=False, error=sanitize_text(str(exc)) or type(exc).__name__)
                return
            self.mw.settings.update(result["settings"])
            answer.update(ok=True, backup=str(result.get("backup") or ""), skipped=list(result.get("skipped") or []))
        finally:
            done = payload.get("done")
            if done is not None:
                done.set()

    def _do_open_settings(self, _payload) -> None:
        self.page.open_path("/settings")

    def _do_open_card(self, payload) -> None:
        """«Уже есть» clicked in the browser extension (v5.31): the card's page, the window in front (out of the
        taskbar when minimized, as it was otherwise: maximized stays maximized)."""
        from PySide6.QtCore import Qt
        try:
            if not begin_call(payload):            # the extension stopped waiting: it said «не открылось»
                return
            self.page.open_path(f"/post/{int(payload['id'])}")
            window = self.mw
            if window.isMinimized():
                window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
            window.show()
            window.raise_()
            window.activateWindow()
        finally:
            done = payload.get("done")
            if done is not None:
                done.set()

    def _do_play_video(self, payload) -> None:
        image_id = payload.get("id")
        self.page.play_video(str(payload["path"]), str(payload.get("title") or ""),
                             int(image_id) if image_id is not None else None,
                             prefer=payload.get("prefer") or None, series=str(payload.get("series") or ""),
                             top=str(payload.get("top") or ""), episode=str(payload.get("episode") or ""),
                             start_at=float(payload.get("start_at") or 0.0), paused=bool(payload.get("paused")),
                             resume=float(payload.get("resume") or 0.0))

    def _do_video(self, payload) -> None:
        """A video played inside the page: start (the server made the token) or a command of its buttons."""
        self.page.inline_video(dict(payload))

    def _do_copy_file(self, payload) -> None:
        """The file itself to the clipboard: Explorer, Telegram and the like paste it as a file."""
        from PySide6.QtWidgets import QApplication
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(payload["path"]))])
        QApplication.clipboard().setMimeData(mime)

    def _do_drag_file(self, payload) -> None:
        """Drag a copy of the file out of the window, with its tags and sources written into it
        (ExifTool).

        The copy is made in a background thread — a big video takes seconds, and the window must
        not freeze meanwhile — and the drag starts when it is ready, if the mouse button is still
        held.  The copy is kept, so the next drag of the same card starts at once."""
        item = dict(payload["item"])
        try:
            st = Path(item["path"]).stat()
        except OSError as exc:
            _LOG.warning("drag: %s", exc)
            return
        key = f'{item["path"]}|{st.st_mtime_ns}|{st.st_size}'
        ready = self._drag_copies.get(key)
        if ready and Path(ready).is_file():
            self._start_drag(ready)
            return
        if key in self._drag_making:
            return                                  # being made: the drag starts when it is ready
        self._drag_making.add(key)
        threading.Thread(target=self._make_drag_copy, args=(dict(self.mw.settings), item, key),
                         name="drag-copy", daemon=True).start()

    def _make_drag_copy(self, settings: dict, item: dict, key: str) -> None:
        """(background thread) the copy with tags; the result goes back to the window by a signal."""
        try:
            from core.metadata_export import cleanup_old_drag_exports, drag_export_root, export_gallery_item_copy
            cleanup_old_drag_exports(settings)
            export_dir = drag_export_root(settings) / ("drag_" + str(int(time.time() * 1000)))
            made = str(export_gallery_item_copy(settings, item, export_dir))
        except Exception as exc:  # ExifTool missing or failing, disk full: no drag, a line in the log
            _LOG.error("drag copy of %s failed: %s", item.get("path"), exc, exc_info=True)
            made = ""
        self._drag_copy_ready.emit(key, made)

    def _drag_copy_made(self, key: str, made: str) -> None:
        self._drag_making.discard(key)
        if made:
            self._drag_copies[key] = made
            self._start_drag(made)

    def _start_drag(self, path: str) -> None:
        from PySide6.QtGui import QDrag
        from PySide6.QtWidgets import QApplication
        if not QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            return            # the button was let go before the copy was ready; the next drag starts at once
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path)])
        drag = QDrag(self.page)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def _do_tag_color(self, payload) -> None:
        colors = dict(self.mw.settings.get("tag_colors") or {})
        tag, color = str(payload["tag"]), str(payload.get("color") or "")
        if color:
            colors[tag] = color
        else:
            colors.pop(tag, None)
        self.mw.settings["tag_colors"] = colors
        self.mw.save_settings()

    def _do_export_results(self, payload) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        ids = [int(x) for x in payload.get("ids") or []]
        if not ids:
            QMessageBox.information(self.mw, "Экспорт", "В этой выдаче нет картинок — экспортировать нечего.")
            return
        path, selected = QFileDialog.getSaveFileName(self.mw, f"Экспорт тегов и источников ({len(ids)} шт.)",
                                                     "local_booru_metadata.json", "JSON (*.json);;CSV (*.csv)")
        if not path:
            return
        fmt = "csv" if str(path).lower().endswith(".csv") or "CSV" in selected else "json"
        from core.library_lifecycle import export_metadata
        try:
            count = export_metadata(self.mw.settings, path, fmt, image_ids=ids)
        except Exception as exc:  # disk full, no access, a locked base: say so instead of a silent log line
            _LOG.error("export of %d cards failed: %s", len(ids), exc, exc_info=True)
            QMessageBox.warning(self.mw, "Экспорт", f"Экспорт не удался: {exc}")
            return
        QMessageBox.information(self.mw, "Экспорт", f"Экспортировано файлов: {count}\n{path}")

    def _do_fullscreen(self, _payload) -> None:
        self.page.toggle_fullscreen()

    def _do_video_screen(self, payload) -> None:
        """⛶ of the page's player: the window full screen for it, or back."""
        self.page.video_screen(bool(payload.get("on")))
