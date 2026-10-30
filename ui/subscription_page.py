"""Subscription page UI — per-author auto-download.

Checks run as core jobs (``core/subscription_jobs.py``), one at a time for the whole program; this
page starts them, shows the journal of whatever check is going (also one started on the web page
«Подписки») and runs the automatic check once a minute.
"""
from __future__ import annotations

import time
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QDialog, QFormLayout, QSpinBox, QCheckBox,
    QMessageBox, QPlainTextEdit, QSplitter, QComboBox,
)
from core import subscription_jobs
from core.subscriptions import (
    load_subscriptions, add_subscription, update_subscription,
    delete_subscription,
)
from core.settings import SITES_BY_ENGINE


def _all_sites() -> list[str]:
    sites = []
    for eng_sites in SITES_BY_ENGINE.values():
        sites.extend(eng_sites.keys())
    return sorted(sites)


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

        # ── Shared Grabber/Subscriptions blocklist ───────────────────────────
        self.blacklist = QPlainTextEdit()
        self.blacklist.setPlaceholderText("Общий блоклист задаётся в Настройки → Граббер / Подписки")
        self.blacklist.setFixedHeight(80)
        self.blacklist.setReadOnly(True)
        _settings_bl = ""
        try:
            _settings_bl = str((getattr(self, "_main", None).settings or {}).get("grabber_subscriptions_blocklist") or (getattr(self, "_main", None).settings or {}).get("downloader_blocklist") or "")
        except Exception:
            _settings_bl = ""
        self.blacklist.setPlainText(_settings_bl)

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
        lay.addRow("Общий блоклист:", self.blacklist)

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

        bl_tags = []
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
        self._mine: set = set()          # checks started here (this page asks their large-download question)
        self._shown_job = 0
        self._log_after = 0
        self._was_running = False
        self._plan_box: QMessageBox | None = None
        self._plan_key: tuple = ()       # (check, question number) already asked here
        self._gallery_refreshed_at = 0.0
        self._downloaded_seen = 0
        self._build_ui()
        self._load()

        from PySide6.QtCore import QTimer
        # Auto-check timer: fires every minute, runs due subscriptions
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(60_000)
        self._auto_timer.timeout.connect(self._auto_check)
        self._auto_timer.start()
        # The check in progress (from here, the web page or the automatic check): journal and buttons
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

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

    def _busy_with(self, sub_id: str) -> bool:
        if sub_id in subscription_jobs.running_ids():
            QMessageBox.information(self, "Подписки", "Эта подписка сейчас проверяется — остановите проверку или дождитесь конца.")
            return True
        return False

    def _delete(self):
        sub_id = self._selected_sub_id()
        if not sub_id or self._busy_with(sub_id):
            return
        row = self.table.currentRow()
        name = self.table.item(row, 0).text() if row >= 0 else "?"
        reply = QMessageBox.question(self, "Удалить",
                    f"Удалить подписку «{name}»?\nСкачанные файлы остаются.",
                    QMessageBox.Yes | QMessageBox.No)
        # the automatic check may have taken it while the question was open
        if reply == QMessageBox.Yes and not self._busy_with(sub_id):
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

    def _start(self, subs: list, label: str, kind: str = "check") -> None:
        try:
            job = subscription_jobs.start(self.main.settings, subs, label=label, kind=kind)
        except subscription_jobs.SubscriptionsBusy as exc:
            QMessageBox.information(self, "Подписки", str(exc))
            return
        self._mine.add(job.id)
        self._poll()

    def _run_sub(self, sub: dict):
        from core.subscriptions import normalize_sites
        _sites_str = ', '.join(s['site'] for s in normalize_sites(sub))
        self._start([sub], f"Подписка: {sub.get('name') or sub.get('query')} @ {_sites_str}")

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
        self._start(subs, "Все подписки")

    def _poll(self):
        """Show the check in progress: new journal lines, the buttons, its large-download question."""
        job = subscription_jobs.current()
        if job is None:
            self._set_running(subscription_jobs.is_running())
            return
        if job.id != self._shown_job:
            self._shown_job, self._log_after, self._downloaded_seen = job.id, 0, 0
            self._log(f"▶ {job.label}")
        snap = job.snapshot(after=self._log_after)
        for line in snap["log"]:
            self._log(line["text"])
        self._log_after = max(self._log_after, snap["last_seq"])
        running = snap["status"] in {"starting", "running", "waiting", "stopping"}
        self._set_running(running)
        if snap["downloaded"] > self._downloaded_seen and time.time() - self._gallery_refreshed_at > 10:
            self._downloaded_seen = snap["downloaded"]
            self._on_file_ready("")
        key = (job.id, snap["plan"].get("seq"))
        if snap["status"] == "waiting" and snap["plan"] and job.id in self._mine and key != self._plan_key:
            self._ask_plan(job, snap["plan"], key)
        elif snap["status"] != "waiting" and self._plan_box is not None:
            box, self._plan_box = self._plan_box, None
            box.setProperty("lb_closed_by_page", True)
            box.close()                       # answered on the web page, or nobody answered in time
        if self._was_running and not running:
            self._load()
            self._on_file_ready("")
        self._was_running = running

    def _ask_plan(self, job, plan: dict, key: tuple):
        from core.preflight import format_bytes
        self._plan_key = key
        text = (
            f"Найдено файлов для загрузки: {int(plan.get('files', 0) or 0)}\n"
            f"Известный размер: {format_bytes(plan.get('known_bytes', 0))} "
            f"для {int(plan.get('known_files', 0) or 0)} файл(ов)\n"
            f"Свободно на диске: {format_bytes(plan.get('disk_free', 0))}\n\n"
            "Загрузка может занять много времени из-за ограничений сайтов. "
            "При временных ошибках программа будет ждать и продолжать позже."
        )
        if plan.get("not_enough_space"):
            text += "\n\nВНИМАНИЕ: известный размер уже превышает свободное место с резервом."
        box = QMessageBox(QMessageBox.Question, "Большая загрузка", text + "\n\nПродолжить?",
                          QMessageBox.Yes | QMessageBox.No, self)
        box.setAttribute(Qt.WA_DeleteOnClose)

        def _closed(_result):
            if self._plan_box is box:
                self._plan_box = None
            if box.property("lb_closed_by_page"):
                return                        # answered elsewhere: nothing to answer from here
            clicked = box.clickedButton()     # None: closed with × or Alt+F4 — that is «no», as before
            job.decide(clicked is not None and box.standardButton(clicked) == QMessageBox.Yes,
                       seq=plan.get("seq"))
        box.finished.connect(_closed)
        self._plan_box = box
        box.open()                            # not exec(): the journal keeps moving while it is open

    def _on_file_ready(self, path: str):
        """Refresh the old gallery when new files arrived."""
        self._gallery_refreshed_at = time.time()
        try:
            gallery = getattr(self.main, "gallery_page", None)
            if gallery and hasattr(gallery, "refresh"):
                gallery.refresh()
        except Exception as exc:          # the gallery shows the files at its next refresh anyway
            self._log(f"  галерея не обновилась: {type(exc).__name__}: {exc}")

    def _stop(self):
        job = subscription_jobs.current()
        if job is not None and job.status in {"running", "waiting", "starting"}:
            job.stop()
            self._log("⏹ Остановка после текущего файла...")

    def _clear_downloads(self):
        sub_id = self._selected_sub_id()
        if not sub_id:
            QMessageBox.information(self, "Подписки", "Выбери подписку в таблице.")
            return
        if self._busy_with(sub_id):
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
        if reply != QMessageBox.Yes or self._busy_with(sub_id):     # taken by the automatic check meanwhile
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
        try:
            job = subscription_jobs.start_due(self.main.settings)
        except Exception as exc:          # a broken subscriptions file must not stop the timer
            self._log(f"Автопроверка не запустилась: {type(exc).__name__}: {exc}")
            return
        if job is not None:
            self._mine.add(job.id)
            self._log(f"⏰ Автопроверка: {len(job.subs)} подписок(и) пора проверить")

    def _reset_checkpoint(self):
        sub_id = self._selected_sub_id()
        if not sub_id:
            QMessageBox.information(self, "Подписки", "Выбери подписку.")
            return
        if self._busy_with(sub_id):
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
        if reply != QMessageBox.Yes or self._busy_with(sub_id):     # taken by the automatic check meanwhile
            return
        update_subscription(sub_id, last_post_ids={}, oldest_post_ids={}, last_check=0)
        self._log("↺ Checkpoint сброшен.")
        self._load()

    def refresh(self):
        self._load()

    def retranslate(self):
        pass
