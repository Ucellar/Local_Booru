"""Subscription page UI — per-author auto-download."""
from __future__ import annotations

import time
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
    log_line = Signal(str)
    progress  = Signal(int)
    finished  = Signal(int)  # downloaded count

    def __init__(self, sub: dict, settings: dict):
        super().__init__()
        self.sub = sub
        self.settings = settings

    def run(self):
        total = run_subscription(
            self.sub, self.settings,
            log=self.log_line.emit,
            progress=self.progress.emit,
        )
        self.finished.emit(total)


# ── Add/Edit dialog ───────────────────────────────────────────────────────────

class SubEditDialog(QDialog):
    def __init__(self, parent=None, sub: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Подписка" if not sub else f"Редактировать: {sub['name']}")
        self.setMinimumWidth(480)
        lay = QFormLayout(self)

        self.name    = QLineEdit(sub.get("name", "") if sub else "")
        self.site    = QComboBox()
        for s in _all_sites():
            self.site.addItem(s)
        if sub:
            idx = self.site.findText(sub.get("site", ""))
            if idx >= 0:
                self.site.setCurrentIndex(idx)

        self.query   = QLineEdit(sub.get("query", "") if sub else "")
        self.query.setPlaceholderText("artist:seraziel  или просто  seraziel_(artist)")
        self.hours   = QSpinBox(); self.hours.setRange(1, 720); self.hours.setSuffix(" ч")
        self.hours.setValue(sub.get("check_interval_hours", 24) if sub else 24)
        self.pages   = QSpinBox(); self.pages.setRange(1, 50); self.pages.setSuffix(" стр")
        self.pages.setValue(sub.get("max_pages", 3) if sub else 3)
        self.enabled = QCheckBox("Активна")
        self.enabled.setChecked(sub.get("enabled", True) if sub else True)

        lay.addRow("Название:", self.name)
        lay.addRow("Сайт:", self.site)
        lay.addRow("Запрос:", self.query)
        lay.addRow("Проверять каждые:", self.hours)
        lay.addRow("Страниц за раз:", self.pages)
        lay.addRow("", self.enabled)

        hint = QLabel(
            "Запрос — обычный тег как в поиске на сайте.\n"
            "Пример: artist:seraziel  или  dagasi_(artist)")
        hint.setStyleSheet("color:#888;font-size:11px;")
        lay.addRow("", hint)

        btns = QHBoxLayout()
        ok_btn  = QPushButton("Сохранить"); ok_btn.clicked.connect(self.accept)
        can_btn = QPushButton("Отмена");    can_btn.clicked.connect(self.reject)
        btns.addWidget(ok_btn); btns.addWidget(can_btn)
        lay.addRow(btns)

    def result_data(self) -> dict:
        return {
            "name":                 self.name.text().strip(),
            "site":                 self.site.currentText(),
            "query":                self.query.text().strip(),
            "check_interval_hours": self.hours.value(),
            "max_pages":            self.pages.value(),
            "enabled":              self.enabled.isChecked(),
        }


# ── Main page ─────────────────────────────────────────────────────────────────

class SubscriptionPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._worker: SubWorker | None = None
        self._build_ui()
        self._load()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        # Title + info
        title = QLabel("Подписки — автоматическое скачивание по автору")
        title.setStyleSheet("font-size:15px;font-weight:700;margin-bottom:4px;")
        lay.addWidget(title)

        info = QLabel(
            "Добавь автора/тег → программа будет периодически скачивать новые работы.\n"
            "Скачанные файлы → output_папка/subscriptions/сайт/запрос/")
        info.setStyleSheet("color:#888;font-size:11px;margin-bottom:8px;")
        info.setWordWrap(True)
        lay.addWidget(info)

        splitter = QSplitter(Qt.Vertical)

        # Table
        top = QWidget()
        top_lay = QVBoxLayout(top)
        top_lay.setContentsMargins(0,0,0,0)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Название", "Сайт", "Запрос", "Интервал", "Последняя проверка",
            "Скачано", "Активна"
        ])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        for i in (3,4,5,6):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self._edit)
        top_lay.addWidget(self.table)

        # Buttons
        btn_row = QHBoxLayout()
        self.add_btn    = QPushButton("＋ Добавить")
        self.edit_btn   = QPushButton("✎ Изменить")
        self.del_btn    = QPushButton("✕ Удалить")
        self.run_btn    = QPushButton("▶ Проверить сейчас")
        self.run_all_btn= QPushButton("▶▶ Все")
        self.stop_btn   = QPushButton("⏹ Стоп")
        self.stop_btn.setEnabled(False)
        for b in [self.add_btn,self.edit_btn,self.del_btn,
                  self.run_btn,self.run_all_btn,self.stop_btn]:
            btn_row.addWidget(b)
        top_lay.addLayout(btn_row)

        self.add_btn.clicked.connect(self._add)
        self.edit_btn.clicked.connect(self._edit)
        self.del_btn.clicked.connect(self._delete)
        self.run_btn.clicked.connect(self._run_selected)
        self.run_all_btn.clicked.connect(self._run_all)
        self.stop_btn.clicked.connect(self._stop)

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
            self.table.setItem(r, 0, QTableWidgetItem(sub.get("name", "")))
            self.table.setItem(r, 1, QTableWidgetItem(sub.get("site", "")))
            self.table.setItem(r, 2, QTableWidgetItem(sub.get("query", "")))
            self.table.setItem(r, 3, QTableWidgetItem(f"{sub.get('check_interval_hours',24)} ч"))
            last = sub.get("last_check", 0)
            last_str = (time.strftime("%d.%m %H:%M", time.localtime(last))
                        if last > 0 else "никогда")
            self.table.setItem(r, 4, QTableWidgetItem(last_str))
            self.table.setItem(r, 5, QTableWidgetItem(str(sub.get("downloaded_count", 0))))
            en_it = QTableWidgetItem("✓" if sub.get("enabled", True) else "—")
            en_it.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 6, en_it)
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
            if not d["name"] or not d["query"]:
                QMessageBox.warning(self, "Ошибка", "Название и запрос обязательны.")
                return
            add_subscription(**d)
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
        self.log_box.appendPlainText(msg)
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
        self._log(f"▶ Запуск: {sub['name']} @ {sub['site']}")
        self._worker = SubWorker(sub, self.main.settings)
        self._worker.log_line.connect(self._log)
        self._worker.finished.connect(self._on_done)
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
        self._log(f"▶ Проверяю: {sub['name']} @ {sub['site']}")
        self._worker = SubWorker(sub, self.main.settings)
        self._worker.log_line.connect(self._log)
        self._worker.finished.connect(self._on_queue_step)
        self._worker.start()

    def _on_queue_step(self, count: int):
        self._log(f"  → {count} новых файлов")
        self._run_next()

    def _on_done(self, count: int):
        self._log(f"✓ Готово: {count} новых файлов.")
        self._set_running(False)
        self._load()

    def _stop(self):
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._log("⏹ Остановлено.")
        self._sub_queue = []
        self._set_running(False)

    def refresh(self):
        self._load()

    def retranslate(self):
        pass
