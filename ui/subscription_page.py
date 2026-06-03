"""Subscription page UI — per-author auto-download."""
from __future__ import annotations

import time
import threading
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QDialog, QFormLayout, QLineEdit, QSpinBox, QCheckBox,
    QMessageBox, QPlainTextEdit, QSplitter, QComboBox,
)
from core.subscriptions import (
    load_subscriptions, add_subscription, update_subscription,
    delete_subscription, run_subscription,
)
from core.settings import SITES_BY_ENGINE


def _all_sites() -> list[str]:
    sites = []
    for eng_sites in SITES_BY_ENGINE.values():
        sites.extend(eng_sites.keys())
    return sorted(sites)


# ── Worker thread ─────────────────────────────────────────────────────────────

class SubWorker(QThread):
    log_line   = Signal(str)
    progress   = Signal(int)
    finished   = Signal(int)
    file_ready = Signal(str)   # emitted after each file is downloaded + tagged
    plan_required = Signal(dict)  # asks UI before a very large download

    def __init__(self, sub: dict, settings: dict, run_mode: str = "all"):
        super().__init__()
        self.sub = sub
        self.settings = settings
        self.run_mode = run_mode
        self._stop_requested = False
        self._plan_event = threading.Event()
        self._plan_allowed = False

    def request_stop(self):
        self._stop_requested = True
        self._plan_allowed = False
        self._plan_event.set()

    def set_plan_decision(self, allowed: bool):
        self._plan_allowed = bool(allowed)
        self._plan_event.set()

    def _confirm_plan(self, plan: dict) -> bool:
        self._plan_event.clear()
        self.plan_required.emit(plan)
        while not self._stop_requested and not self._plan_event.wait(0.2):
            pass
        return bool(self._plan_allowed) and not self._stop_requested

    def run(self):
        total = run_subscription(
            self.sub, self.settings,
            log=self.log_line.emit,
            progress=self.progress.emit,
            stop_flag=self,
            on_file_ready=self.file_ready.emit,
            run_mode=self.run_mode,
            confirm_plan=self._confirm_plan,
        )
        self.finished.emit(total)


# ── Add/Edit dialog ───────────────────────────────────────────────────────────

class SubEditDialog(QDialog):
    """Add / edit subscription dialog with multi-site priority support."""

    def __init__(self, parent=None, sub: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Подписка" if not sub else f"Редактировать: {sub['name']}")
        self.setMinimumWidth(540)
        from core.subscriptions import normalize_sites
        lay = QFormLayout(self)

        self._main = getattr(parent, "main", None)
        def _known_tags():
            try:
                from core.services.library_service import candidate_tags
                return candidate_tags(self._main.settings) if self._main else []
            except Exception:
                return []
        from ui.gallery_page import _TagCompleteEdit
        self.query = _TagCompleteEdit(_known_tags)
        self.query.setText(sub.get("query", sub.get("name", "")) if sub else "")
        self.query.setPlaceholderText("тег или автор для подписки")
        self.hours = QSpinBox(); self.hours.setRange(1, 720); self.hours.setSuffix(" ч")
        self.hours.setValue(sub.get("check_interval_hours", 24) if sub else 24)
        self.pages = QSpinBox(); self.pages.setRange(1, 50); self.pages.setSuffix(" стр")
        self.pages.setValue(sub.get("max_pages", 3) if sub else 3)
        self.enabled = QCheckBox("Активна")
        self.enabled.setChecked(sub.get("enabled", True) if sub else True)

        # ── Multi-site table ──────────────────────────────────────────────────
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView
        self._sites_table = QTableWidget(0, 3)
        self._sites_table.setHorizontalHeaderLabels(["Сайт", "Приор.", "Запрос (если пусто — общий)"])
        self._sites_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._sites_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._sites_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._sites_table.verticalHeader().setVisible(False)
        self._sites_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._sites_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._sites_table.setFixedHeight(130)

        # Populate from existing sub or defaults
        existing_sites = normalize_sites(sub) if sub else []
        if not existing_sites:
            existing_sites = [{"site": _all_sites()[0], "priority": 3}]
        for cfg in existing_sites:
            self._add_site_row(cfg["site"], cfg.get("priority", 3), cfg.get("query_override", ""))

        site_btn_row = QHBoxLayout()
        add_site_btn = QPushButton("Добавить сайт")
        del_site_btn = QPushButton("Убрать сайт")
        add_site_btn.clicked.connect(self._add_site_row_default)
        del_site_btn.clicked.connect(self._remove_site_row)
        site_btn_row.addWidget(add_site_btn)
        site_btn_row.addWidget(del_site_btn)
        site_btn_row.addStretch()

        site_hint = QLabel("Приоритет 5 = источник скачивания выбирается первым. Теги собираются со всех найденных сайтов.")
        site_hint.setStyleSheet("color:#888;font-size:10px;")

        # ── Blacklist tags ────────────────────────────────────────────────────
        self.blacklist = QPlainTextEdit()
        self.blacklist.setPlaceholderText(
            "Теги-исключения — через запятую или по одному на строку\n"
            "Например: censored, ai-generated, gore")
        self.blacklist.setFixedHeight(80)
        existing_bl = sub.get("blacklist_tags", []) if sub else []
        self.blacklist.setPlainText("\n".join(existing_bl))

        # ── Download mode ─────────────────────────────────────────────────
        self.run_mode = QComboBox()
        self.run_mode.addItem("Все", "all")
        self.run_mode.addItem("Только новые (сначала baseline)", "new")
        self.run_mode.addItem("Только старые", "old")
        saved_mode = sub.get("run_mode", "all") if sub else "all"
        for i in range(self.run_mode.count()):
            if self.run_mode.itemData(i) == saved_mode:
                self.run_mode.setCurrentIndex(i)
                break

        self.run_direction = QComboBox()
        self.run_direction.addItem("От последнего к первому", "newest_to_oldest")
        self.run_direction.addItem("От первого к последнему", "oldest_to_newest")
        saved_dir = sub.get("run_direction", "newest_to_oldest") if sub else "newest_to_oldest"
        for i in range(self.run_direction.count()):
            if self.run_direction.itemData(i) == saved_dir:
                self.run_direction.setCurrentIndex(i)
                break

        # ── Layout ───────────────────────────────────────────────────────────
        lay.addRow("Тег / запрос:", self.query)
        lay.addRow("Сайты:", self._sites_table)
        lay.addRow("", site_btn_row)
        lay.addRow("", site_hint)
        lay.addRow("Проверять каждые:", self.hours)
        lay.addRow("Страниц за раз:", self.pages)
        lay.addRow("Режим:", self.run_mode)
        lay.addRow("Порядок:", self.run_direction)
        lay.addRow("", self.enabled)
        lay.addRow("Исключить теги:", self.blacklist)

        btns = QHBoxLayout()
        ok_btn  = QPushButton("Сохранить"); ok_btn.clicked.connect(self.accept)
        can_btn = QPushButton("Отмена");    can_btn.clicked.connect(self.reject)
        btns.addWidget(ok_btn); btns.addWidget(can_btn)
        lay.addRow(btns)

    # ── Site table helpers ────────────────────────────────────────────────────

    def _add_site_row(self, site: str = "", priority: int = 3, query_override: str = ""):
        r = self._sites_table.rowCount()
        self._sites_table.insertRow(r)

        cb = QComboBox()
        for s in _all_sites():
            cb.addItem(s)
        idx = cb.findText(site)
        if idx >= 0:
            cb.setCurrentIndex(idx)
        self._sites_table.setCellWidget(r, 0, cb)

        sb = QSpinBox()
        sb.setRange(1, 5)
        sb.setValue(priority)
        self._sites_table.setCellWidget(r, 1, sb)

        from PySide6.QtWidgets import QLineEdit
        qe = QLineEdit()
        qe.setPlaceholderText("другой запрос для этого сайта")
        qe.setText(query_override)
        self._sites_table.setCellWidget(r, 2, qe)

    def _add_site_row_default(self):
        self._add_site_row(_all_sites()[0], 3)

    def _remove_site_row(self):
        row = self._sites_table.currentRow()
        if row >= 0 and self._sites_table.rowCount() > 1:
            self._sites_table.removeRow(row)

    # ── Result ────────────────────────────────────────────────────────────────

    def result_data(self) -> dict:
        sites = []
        seen = set()
        for r in range(self._sites_table.rowCount()):
            cb = self._sites_table.cellWidget(r, 0)
            sb = self._sites_table.cellWidget(r, 1)
            qe = self._sites_table.cellWidget(r, 2)
            if cb and sb:
                site = cb.currentText()
                if site and site not in seen:
                    seen.add(site)
                    entry = {"site": site, "priority": sb.value()}
                    if qe and qe.text().strip():
                        entry["query_override"] = qe.text().strip()
                    sites.append(entry)

        import re
        bl_tags = [t.strip() for t in re.split(r"[,;\s]+", self.blacklist.toPlainText()) if t.strip()]
        return {
            "name":                 self.query.text().strip(),
            "sites":                sites,
            "query":                self.query.text().strip(),
            "check_interval_hours": self.hours.value(),
            "max_pages":            self.pages.value(),
            "enabled":              self.enabled.isChecked(),
            "run_mode":             self.run_mode.currentData(),
            "run_direction":        self.run_direction.currentData(),
            "blacklist_tags":       bl_tags,
        }


# ── Main page ─────────────────────────────────────────────────────────────────

class SubscriptionPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._worker: SubWorker | None = None
        self._sub_queue: list = []
        self._build_ui()
        self._load()

        # Auto-check timer: fires every minute, runs due subscriptions
        from PySide6.QtCore import QTimer
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(60_000)
        self._auto_timer.timeout.connect(self._auto_check)
        self._auto_timer.start()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        # Title + info
        title = QLabel("Подписки")
        title.setStyleSheet("font-size:15px;font-weight:700;margin-bottom:4px;")
        lay.addWidget(title)

        info = QLabel(
            "Добавь тег или автора → программа будет периодически искать и скачивать найденные работы.\n"
            "Скачанные файлы → output_папка/subscriptions/сайт/запрос/")
        info.setStyleSheet("color:#888;font-size:11px;margin-bottom:8px;")
        info.setWordWrap(True)
        lay.addWidget(info)

        splitter = QSplitter(Qt.Vertical)

        # Table
        top = QWidget()
        top_lay = QVBoxLayout(top)
        top_lay.setContentsMargins(0,0,0,0)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Тег / запрос", "Сайты", "Интервал", "Последняя проверка",
            "Скачано", "Активна"
        ])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in (2,3,4,5):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self._edit)
        top_lay.addWidget(self.table)

        # Buttons
        btn_row = QHBoxLayout()
        self.add_btn    = QPushButton("Добавить")
        self.edit_btn   = QPushButton("Изменить")
        self.del_btn    = QPushButton("Удалить")
        self.run_btn    = QPushButton("Проверить сейчас")
        self.run_all_btn= QPushButton("Проверить все")
        self.stop_btn   = QPushButton("Стоп")
        self.clear_btn  = QPushButton("Удалить скачанные")
        self.reset_btn  = QPushButton("Сбросить прогресс")
        self.stop_btn.setEnabled(False)
        for b in [self.add_btn,self.edit_btn,self.del_btn,
                  self.run_btn,self.run_all_btn,self.stop_btn,self.clear_btn,self.reset_btn]:
            btn_row.addWidget(b)
        top_lay.addLayout(btn_row)

        self.add_btn.clicked.connect(self._add)
        self.edit_btn.clicked.connect(self._edit)
        self.del_btn.clicked.connect(self._delete)
        self.run_btn.clicked.connect(self._run_selected)
        self.run_all_btn.clicked.connect(self._run_all)
        self.stop_btn.clicked.connect(self._stop)
        self.clear_btn.clicked.connect(self._clear_downloads)
        self.reset_btn.clicked.connect(self._reset_checkpoint)

        splitter.addWidget(top)

        # Log
        bot = QWidget()
        bot_lay = QVBoxLayout(bot)
        bot_lay.setContentsMargins(0,0,0,0)
        log_lbl = QLabel("Лог:")
        log_lbl.setStyleSheet("font-size:11px;color:#888;")
        bot_lay.addWidget(log_lbl)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(500)
        self.log_box.setStyleSheet("font-size:11px;font-family:monospace;")
        bot_lay.addWidget(self.log_box)
        splitter.addWidget(bot)
        splitter.setSizes([400, 200])

        lay.addWidget(splitter, 1)

    def _load(self):
        self.table.setRowCount(0)
        subs = load_subscriptions()
        for sub in subs:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(sub.get("query", sub.get("name", ""))))
            from core.subscriptions import normalize_sites
            sites_cfg = normalize_sites(sub)
            sites_str = ", ".join(f"{s['site']}({s['priority']})" for s in sites_cfg) or "—"
            self.table.setItem(r, 1, QTableWidgetItem(sites_str))
            self.table.setItem(r, 2, QTableWidgetItem(f"{sub.get('check_interval_hours',24)} ч"))
            last = sub.get("last_check", 0)
            last_str = (time.strftime("%d.%m %H:%M", time.localtime(last))
                        if last > 0 else "никогда")
            self.table.setItem(r, 3, QTableWidgetItem(last_str))
            self.table.setItem(r, 4, QTableWidgetItem(str(sub.get("downloaded_count", 0))))
            en_it = QTableWidgetItem("✓" if sub.get("enabled", True) else "—")
            en_it.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 5, en_it)
            # Store sub id in first column
            self.table.item(r, 0).setData(Qt.UserRole, sub.get("id"))

    def _selected_sub_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        it = self.table.item(row, 0)
        return it.data(Qt.UserRole) if it else None

    def _add(self):
        dlg = SubEditDialog(self)
        if dlg.exec():
            d = dlg.result_data()
            if not d["query"] or not d["sites"]:
                QMessageBox.warning(self, "Ошибка", "Тег/запрос и хотя бы один сайт обязательны.")
                return
            add_subscription(name=d["name"], sites=d["sites"], query=d["query"],
                             check_interval_hours=d["check_interval_hours"],
                             max_pages=d["max_pages"], blacklist_tags=d["blacklist_tags"],
                             run_mode=d["run_mode"], run_direction=d["run_direction"])
            self._load()

    def _edit(self):
        sub_id = self._selected_sub_id()
        if not sub_id:
            return
        from core.subscriptions import get_subscription
        sub = get_subscription(sub_id)
        if not sub:
            return
        dlg = SubEditDialog(self, sub=sub)
        if dlg.exec():
            d = dlg.result_data()
            update_subscription(sub_id, **d)
            self._load()

    def _delete(self):
        sub_id = self._selected_sub_id()
        if not sub_id:
            return
        row = self.table.currentRow()
        name = self.table.item(row, 0).text() if row >= 0 else "?"
        reply = QMessageBox.question(self, "Удалить",
                    f"Удалить подписку «{name}»?\nСкачанные файлы остаются.",
                    QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            delete_subscription(sub_id)
            self._load()

    def _log(self, msg: str):
        from core.redaction import sanitize_text
        self.log_box.appendPlainText(sanitize_text(msg))
        sb = self.log_box.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _set_running(self, running: bool):
        self.add_btn.setEnabled(not running)
        self.run_btn.setEnabled(not running)
        self.run_all_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    def _run_sub(self, sub: dict):
        if self._worker and self._worker.isRunning():
            return
        self._set_running(True)
        from core.subscriptions import normalize_sites
        _sites_str = ', '.join(s['site'] for s in normalize_sites(sub))
        self._log(f"▶ Запуск: {sub['name']} @ {_sites_str}")
        self._worker = SubWorker(sub, self.main.settings,
                                  run_mode=sub.get("run_mode", "all"))
        self._worker.log_line.connect(self._log)
        self._worker.plan_required.connect(self._confirm_large_plan)
        self._worker.finished.connect(self._on_done)
        self._worker.file_ready.connect(self._on_file_ready)
        self._worker.start()

    def _run_selected(self):
        sub_id = self._selected_sub_id()
        if not sub_id:
            QMessageBox.information(self, "Подписки", "Выбери подписку в таблице.")
            return
        from core.subscriptions import get_subscription
        sub = get_subscription(sub_id)
        if sub:
            self._run_sub(sub)

    def _run_all(self):
        subs = [s for s in load_subscriptions() if s.get("enabled", True)]
        if not subs:
            QMessageBox.information(self, "Подписки", "Нет активных подписок.")
            return
        # Run sequentially via queue
        self._sub_queue = subs[:]
        self._run_next()

    def _run_next(self):
        if not hasattr(self, "_sub_queue") or not self._sub_queue:
            self._set_running(False)
            self._log("✓ Все подписки проверены.")
            self._load()
            return
        sub = self._sub_queue.pop(0)
        self._set_running(True)
        from core.subscriptions import normalize_sites
        _sites_str = ', '.join(s['site'] for s in normalize_sites(sub))
        self._log(f"▶ Проверяю: {sub['name']} @ {_sites_str}")
        self._worker = SubWorker(sub, self.main.settings,
                                  run_mode=sub.get("run_mode", "all"))
        self._worker.log_line.connect(self._log)
        self._worker.plan_required.connect(self._confirm_large_plan)
        self._worker.finished.connect(self._on_queue_step)
        self._worker.file_ready.connect(self._on_file_ready)
        self._worker.start()

    def _on_queue_step(self, count: int):
        self._log(f"  → {count} новых файлов")
        self._run_next()

    def _confirm_large_plan(self, plan: dict):
        from core.preflight import format_bytes
        worker = self._worker
        if not worker:
            return
        disk = plan.get("disk", {})
        known = int(plan.get("known_files", 0) or 0)
        text = (
            f"Найдено файлов для загрузки: {int(plan.get('groups', 0) or 0)}\n"
            f"Известный размер: {format_bytes(plan.get('known_bytes', 0))} "
            f"для {known} файл(ов)\n"
            f"Свободно на диске: {format_bytes(disk.get('free', 0))}\n\n"
            "Загрузка может занять много времени из-за ограничений сайтов. "
            "При временных ошибках программа будет ждать и продолжать позже."
        )
        if plan.get("not_enough_space"):
            text += "\n\nВНИМАНИЕ: известный размер уже превышает свободное место с резервом."
        answer = QMessageBox.question(self, "Большая загрузка", text + "\n\nПродолжить?", QMessageBox.Yes | QMessageBox.No)
        worker.set_plan_decision(answer == QMessageBox.Yes)

    def _on_file_ready(self, path: str):
        """Refresh gallery when a new file is ready."""
        try:
            gallery = getattr(self.main, "gallery_page", None)
            if gallery and hasattr(gallery, "refresh"):
                gallery.refresh()
        except Exception:
            pass

    def _on_done(self, count: int):
        self._log(f"✓ Готово: {count} новых файлов.")
        self._set_running(False)
        self._load()

    def _stop(self):
        if self._worker and self._worker.isRunning():
            self._worker.request_stop()
            self._log("⏹ Остановка после текущего файла...")
        self._sub_queue = []
        self._set_running(False)

    def _clear_downloads(self):
        sub_id = self._selected_sub_id()
        if not sub_id:
            QMessageBox.information(self, "Подписки", "Выбери подписку в таблице.")
            return
        from core.subscriptions import get_subscription, normalize_sites, update_subscription
        from core.downloader_utils import _safe
        from pathlib import Path
        import shutil

        sub = get_subscription(sub_id)
        if not sub:
            return

        sites_cfg = normalize_sites(sub)
        query     = sub.get("query", "")
        out_base  = Path(self.main.settings.get("output_dir", "")) / "subscriptions"

        # Collect all timestamp session folders
        all_sessions = []
        for sc in sites_cfg:
            base = out_base / _safe(sc["site"]) / _safe(query)
            if base.exists():
                for child in sorted(base.iterdir()):
                    if child.is_dir():
                        all_sessions.append(child)
                # Also catch flat files at base level (old format)
                flat_files = [f for f in base.iterdir() if f.is_file()]
                if flat_files:
                    all_sessions.insert(0, base)

        if not all_sessions:
            QMessageBox.information(self, "Удалить скачанные", "Файлов не найдено.")
            return

        # Show selection dialog
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QListWidget, QDialogButtonBox, QLabel, QComboBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Что удалить?")
        dlg.setMinimumWidth(420)
        vlay = QVBoxLayout(dlg)

        mode_cb = QComboBox()
        mode_cb.addItem("Все сессии", "all")
        mode_cb.addItem("Только старые (оставить последнюю)", "keep_last")
        mode_cb.addItem("Выбрать конкретные сессии", "pick")
        vlay.addWidget(QLabel(f"Подписка: {sub.get('name','?')}"))
        vlay.addWidget(mode_cb)

        lst = QListWidget()
        lst.setSelectionMode(QListWidget.MultiSelection)
        for s in all_sessions:
            file_count = sum(1 for _ in s.rglob("*") if _.is_file())
            lst.addItem(f"{s.name}  ({file_count} файлов)")
        lst.setVisible(False)
        lst.setMaximumHeight(180)
        vlay.addWidget(lst)

        def _on_mode():
            lst.setVisible(mode_cb.currentData() == "pick")
            dlg.adjustSize()
        mode_cb.currentIndexChanged.connect(_on_mode)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        vlay.addWidget(btns)

        if dlg.exec() != QDialog.Accepted:
            return

        mode = mode_cb.currentData()
        if mode == "all":
            to_delete = all_sessions
        elif mode == "keep_last":
            to_delete = all_sessions[:-1]
        else:  # pick
            to_delete = [all_sessions[i] for i in range(lst.count())
                         if lst.item(i).isSelected()]

        if not to_delete:
            return

        total_files = sum(sum(1 for _ in s.rglob("*") if _.is_file()) for s in to_delete)
        reply = QMessageBox.question(
            self, "Подтверждение",
            f"Удалить {total_files} файлов из {len(to_delete)} сессии(й)?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        # Never destroy downloaded session media directly: move every media file
        # to the recoverable Trash lifecycle, including files not indexed yet.
        from core.media_utils import is_media
        all_media = []
        for folder in to_delete:
            try:
                all_media.extend(p for p in folder.rglob("*") if p.is_file() and is_media(p))
            except Exception:
                pass
        try:
            from core.library_lifecycle import trash_media_paths
            result = trash_media_paths(self.main.settings, all_media, reason="subscription_session_cleanup", make_backup=True)
            moved = int(result.get("trashed_files", 0) or 0)
            self._log(f"  В «Удалено» перемещено: {moved} файлов")
            if result.get("error"):
                self._log(f"  ОШИБКА: {result.get('error')}")
                return
        except Exception as e:
            self._log(f"  Корзина: {e}")
            return

        # Remove only empty directory shells; any unknown non-media artifact is
        # intentionally kept rather than permanently deleted without recovery.
        for folder in to_delete:
            try:
                for child in sorted(folder.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                    if child.is_dir():
                        try: child.rmdir()
                        except OSError: pass
                try: folder.rmdir()
                except OSError: pass
            except Exception as e:
                self._log(f"  Папка сессии оставлена: {folder}: {e}")

        if mode == "all":
            update_subscription(sub_id, downloaded_count=0, last_post_ids={})
        self._log("  Seed cache сохранён: удалённые файлы не будут скачаны обратно автоматически.")
        self._log(f"✓ Перемещено в «Удалено» {len(all_media)} файлов.")
        self._load()

    def _auto_check(self):
        """Called every minute. Runs subscriptions that are past their interval."""
        if self._worker and self._worker.isRunning():
            return
        if getattr(self, "_sub_queue", []):
            return
        try:
            from core.subscriptions import due_subscriptions
            due = due_subscriptions()
        except Exception:
            return
        if not due:
            return
        self._log(f"\u23f0 \u0410\u0432\u0442\u043e-\u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430: {len(due)} \u043f\u043e\u0434\u043f\u0438\u0441\u043e\u043a(\u0438) \u043f\u0440\u043e\u0441\u0440\u043e\u0447\u0435\u043d\u043e")
        self._sub_queue = list(due)
        self._run_next()

    def _reset_checkpoint(self):
        sub_id = self._selected_sub_id()
        if not sub_id:
            QMessageBox.information(self, "Подписки", "Выбери подписку.")
            return
        from core.subscriptions import get_subscription, update_subscription
        sub = get_subscription(sub_id)
        if not sub:
            return
        name = sub.get("name", "?")
        reply = QMessageBox.question(
            self, "Сброс checkpoint",
            "Сбросить checkpoint для " + name + "?\n"
            "Следующий запуск пересканирует все посты с нуля.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        update_subscription(sub_id, last_post_ids={}, oldest_post_ids={}, last_check=0)
        self._log("↺ Checkpoint сброшен.")
        self._load()

    def refresh(self):
        self._load()

    def retranslate(self):
        pass
