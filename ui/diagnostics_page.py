from __future__ import annotations

from pathlib import Path
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QPlainTextEdit, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QFileDialog, QGroupBox, QGridLayout, QProgressBar, QAbstractItemView
)

from core.library_diagnostics import (
    audit_library, save_audit_json, create_forced_backup,
    clear_obsolete_live_md5_blocks, restore_critical_indices, requeue_stale_tag_enrichments,
)


class DiagnosticsPage(QWidget):
    """Read-only library control panel plus deliberately separate repair actions."""
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._report = None
        self._active_task = None

        layout = QVBoxLayout(self)
        note = QLabel(
            "Диагностика ничего не удаляет и не перемещает. Ремонт запускается только отдельными кнопками ниже "
            "и для операций над базой сначала создаёт резервную копию."
        )
        note.setWordWrap(True)
        note.setObjectName("DiagnosticsNotice")
        layout.addWidget(note)

        toolbar = QHBoxLayout()
        self.refresh_btn = QPushButton("Проверить библиотеку")
        self.verify_files = QCheckBox("Проверить наличие физических файлов (медленно)")
        self.export_btn = QPushButton("Экспорт отчёта JSON")
        self.export_btn.setEnabled(False)
        self.lost_sources_btn = QPushButton("Проверить потерянные source")
        self.benchmark_btn = QPushButton("Замерить скорость SQLite")
        self.cancel_tasks_btn = QPushButton("Остановить фоновые задачи")
        toolbar.addWidget(self.refresh_btn)
        toolbar.addWidget(self.verify_files)
        toolbar.addWidget(self.lost_sources_btn)
        toolbar.addWidget(self.benchmark_btn)
        toolbar.addWidget(self.cancel_tasks_btn)
        toolbar.addWidget(self.export_btn)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.progress_label = QLabel("Отчёт ещё не построен")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress)

        cards = QGroupBox("Ключевые показатели")
        grid = QGridLayout(cards)
        self.cards = {}
        for pos, (key, title) in enumerate((
            ("live", "Живых файлов"), ("trash", "В корзине"),
            ("duplicates", "Групп MD5-дублей"), ("extra", "Лишних копий"),
            ("no_source", "Без source"), ("no_tags", "Без тегов"),
            ("sauce", "Очередь SauceNAO"), ("obsolete", "Старых MD5-запретов"),
            ("slow", "Медленных операций"), ("thumb_cache", "Превью в кэше"),
            ("source_blocked", "Защита исходников"),
        )):
            box = QGroupBox(title)
            bl = QVBoxLayout(box)
            label = QLabel("—")
            label.setAlignment(Qt.AlignCenter)
            font = label.font(); font.setPointSize(max(12, font.pointSize() + 5)); font.setBold(True); label.setFont(font)
            bl.addWidget(label)
            grid.addWidget(box, pos // 4, pos % 4)
            self.cards[key] = label
        layout.addWidget(cards)

        self.tabs = QTabWidget()
        self.summary = QPlainTextEdit(); self.summary.setReadOnly(True)
        self.site_table = self._make_table(["Сайт", "Проверено", "Совпадений", "Осталось среди начатых", "Ошибок"])
        self.queue_table = self._make_table(["Тип", "Очередь / задача", "Статус", "Файлов", "Детали"])
        self.trash_table = self._make_table(["Причина", "Файлов"])
        self.dup_table = self._make_table(["MD5", "Живых копий", "Размер группы"])
        self.samples = QPlainTextEdit(); self.samples.setReadOnly(True)
        self.errors = QPlainTextEdit(); self.errors.setReadOnly(True)
        self.source_protection = QPlainTextEdit(); self.source_protection.setReadOnly(True)
        self.performance_table = self._make_table(["Время", "Операция", "мс", "Детали"])
        self.benchmark_table = self._make_table(["SQL-проверка", "мс", "Строк", "Ошибка / пример"])
        self.tabs.addTab(self.summary, "Сводка")
        self.tabs.addTab(self.site_table, "Сайты")
        self.tabs.addTab(self.queue_table, "Очереди")
        self.tabs.addTab(self.trash_table, "Корзина")
        self.tabs.addTab(self.dup_table, "MD5-дубли")
        self.tabs.addTab(self.samples, "Потерянные метаданные")
        self.tabs.addTab(self.performance_table, "Медленные операции")
        self.tabs.addTab(self.benchmark_table, "Замер SQLite")
        self.tabs.addTab(self.source_protection, "Защита исходников")
        self.tabs.addTab(self.errors, "Ошибки / падения")
        layout.addWidget(self.tabs, 1)

        repair = QGroupBox("Явные действия ремонта — меняют данные только после подтверждения")
        rlay = QHBoxLayout(repair)
        self.backup_btn = QPushButton("Создать backup базы")
        self.normalize_btn = QPushButton("Склеить точные MD5-дубли")
        self.unblock_btn = QPushButton("Снять запреты для живых MD5")
        self.indices_btn = QPushButton("Восстановить индексы SQLite")
        self.requeue_categories_btn = QPushButton("Вернуть stale-категории в очередь")
        self.redact_logs_btn = QPushButton("Обезличить старые логи")
        for btn in (self.backup_btn, self.normalize_btn, self.unblock_btn, self.indices_btn, self.requeue_categories_btn, self.redact_logs_btn):
            rlay.addWidget(btn)
        rlay.addStretch(1)
        layout.addWidget(repair)

        self.refresh_btn.clicked.connect(self.refresh_force)
        self.export_btn.clicked.connect(self.export_report)
        self.lost_sources_btn.clicked.connect(self.check_lost_sources)
        self.benchmark_btn.clicked.connect(self.run_performance_audit)
        self.cancel_tasks_btn.clicked.connect(self.cancel_background_tasks)
        self.backup_btn.clicked.connect(self.create_backup)
        self.normalize_btn.clicked.connect(self.normalize_exact_md5)
        self.unblock_btn.clicked.connect(self.unblock_live_md5)
        self.indices_btn.clicked.connect(self.restore_indices)
        self.requeue_categories_btn.clicked.connect(self.requeue_stale_categories)
        self.redact_logs_btn.clicked.connect(self.redact_existing_logs)

    @staticmethod
    def _make_table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def retranslate(self):
        pass

    def refresh(self):
        # Page refresh-on-open must remain safe and not repeatedly start a long
        # task if the user clicks navigation while an audit is still running.
        if self._report is None and self._active_task is None:
            self.refresh_force()

    def _set_busy(self, text):
        self.progress_label.setText(text)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.refresh_btn.setEnabled(False)

    def _clear_busy(self):
        self.progress.setVisible(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.refresh_btn.setEnabled(True)
        self._active_task = None

    def refresh_force(self):
        if self._active_task is not None:
            return
        self._set_busy("Диагностика: чтение состояния библиотеки…")
        settings = dict(self.main.settings or {})
        self._active_task = self.main.task_manager.submit(
            audit_library, settings, verify_files=self.verify_files.isChecked(), name="library-audit",
            on_progress=lambda message: self.progress_label.setText(str(message)),
            on_result=self._audit_done,
            on_error=self._audit_error,
            on_finished=self._clear_busy,
        )

    def _audit_error(self, error):
        self.progress_label.setText("Ошибка диагностики")
        QMessageBox.warning(self, "Диагностика библиотеки", str(error))

    def _audit_done(self, report):
        self._report = dict(report or {})
        self.export_btn.setEnabled(True)
        self.progress_label.setText(f"Последняя проверка: {self._report.get('created_at', '')}. Данные не изменялись.")
        self._render_report()

    def _render_report(self):
        report = self._report or {}
        lib = report.get("library", {})
        md5 = report.get("md5", {})
        reverse = report.get("queues", {}).get("reverse_retry", [])
        sauce = sum(int(r.get("files", 0) or 0) for r in reverse if str(r.get("service", "")).lower() == "saucenao")
        self.cards["live"].setText(str(lib.get("live_files", "—")))
        self.cards["trash"].setText(str(lib.get("trash_files", "—")))
        self.cards["duplicates"].setText(str(md5.get("duplicate_groups", "—")))
        self.cards["extra"].setText(str(md5.get("redundant_rows", "—")))
        self.cards["no_source"].setText(str(lib.get("without_source", "—")))
        self.cards["no_tags"].setText(str(lib.get("without_tags", "—")))
        self.cards["sauce"].setText(str(sauce))
        self.cards["obsolete"].setText(str(md5.get("obsolete_live_blocks", "—")))
        perf_rows = report.get("performance", {}).get("slow_operations", [])
        thumb = report.get("storage", {}).get("thumbnail_cache", {})
        self.cards["slow"].setText(str(len(perf_rows)))
        self.cards["thumb_cache"].setText(str(thumb.get("files", "—")))
        protection = report.get("source_protection", {})
        self.cards["source_blocked"].setText(str(protection.get("blocked_count_shown", "—")))
        self.summary.setPlainText(str(report.get("summary_text", "")))
        events = list(protection.get("blocked_events", []) or [])
        plines = [
            "ИСХОДНЫЙ АРХИВ НЕПРИКОСНОВЕНЕН",
            f"Только чтение / копирование из: {protection.get('source_root','')}",
            f"Файловые изменения разрешены только в: {protection.get('output_root','')}",
            "",
            f"Заблокированные попытки изменения оригинала (показано): {len(events)}",
        ]
        for item in reversed(events):
            plines.append(f"  {item.get('operation','?')}: {item.get('path','')}")
        self.source_protection.setPlainText("\n".join(plines))

        sites = report.get("site_scan", {}).get("sites", [])
        self.site_table.setRowCount(len(sites))
        for row, data in enumerate(sites):
            values = [data.get("site"), data.get("checked"), data.get("matches"), data.get("pending_among_started"), data.get("errors")]
            for col, value in enumerate(values): self.site_table.setItem(row, col, QTableWidgetItem(str(value)))

        queue_rows = []
        now = int(time.time())
        for data in report.get("queues", {}).get("reverse_retry", []):
            eta = max(0, int(data.get("next_retry", 0) or 0) - now)
            queue_rows.append(["Обратный поиск", data.get("service"), "готово к запуску" if data.get("due_now") else "ждёт кулдауна", data.get("files"), f"через {eta//60}м {eta%60}с; max попыток={data.get('max_attempts',0)}"])
        for data in report.get("queues", {}).get("tag_enrichment", []):
            queue_rows.append(["Категории тегов", data.get("job_key"), data.get("status"), data.get("files"), f"max попыток={data.get('max_attempts',0)}"])
        for data in report.get("queues", {}).get("service_state", []):
            eta = max(0, int(data.get("cooldown_until", 0) or 0) - now)
            short_rem = int(data.get("short_remaining", -1) if data.get("short_remaining", -1) is not None else -1)
            long_rem = int(data.get("long_remaining", -1) if data.get("long_remaining", -1) is not None else -1)
            quota = f"короткий={short_rem if short_rem >= 0 else '—'}, сутки={long_rem if long_rem >= 0 else '—'}"
            queue_rows.append(["Состояние сервиса", data.get("service"), "кулдаун" if eta else "готов", "—", f"{quota}; через {eta//60}м {eta%60}с; {data.get('reason','')}"])
        for data in report.get("queues", {}).get("saucenao_retry_events", []):
            queue_rows.append(["SauceNAO proof", data.get("message") or "файл", data.get("status"), "1", f"время={data.get('created_at','')}"])
        for data in report.get("queues", {}).get("unfinished_operations", []):
            queue_rows.append(["Операция", data.get("op_type"), data.get("status"), "1", data.get("error") or data.get("target_id") or ""])
        try:
            for task in self.main.task_manager.active_snapshot():
                if str(task.get("name")) != "library-audit":
                    queue_rows.append(["Сейчас выполняется", task.get("name"), "отмена" if task.get("cancelled") else task.get("state", "работает"), "1", f"{task.get('progress', '')}; {task.get('elapsed_seconds', 0)} сек"])
        except Exception:
            pass
        self.queue_table.setRowCount(len(queue_rows))
        for row, data in enumerate(queue_rows):
            for col, value in enumerate(data): self.queue_table.setItem(row, col, QTableWidgetItem(str(value)))

        trash = report.get("trash", {}).get("by_reason", [])
        self.trash_table.setRowCount(len(trash))
        for row, data in enumerate(trash):
            self.trash_table.setItem(row, 0, QTableWidgetItem(str(data.get("label"))))
            self.trash_table.setItem(row, 1, QTableWidgetItem(str(data.get("files"))))

        dup = report.get("samples", {}).get("duplicate_md5", [])
        self.dup_table.setRowCount(len(dup))
        for row, data in enumerate(dup):
            self.dup_table.setItem(row, 0, QTableWidgetItem(str(data.get("md5", ""))))
            self.dup_table.setItem(row, 1, QTableWidgetItem(str(data.get("files", 0))))
            self.dup_table.setItem(row, 2, QTableWidgetItem(self._fmt_bytes(data.get("bytes", 0))))

        no_source = report.get("samples", {}).get("without_source", [])
        no_tags = report.get("samples", {}).get("without_tags", [])
        text = ["Файлы без source (первые 30):"] + [f"  {x.get('path')}" for x in no_source]
        text += ["", "Файлы без тегов (первые 30):"] + [f"  {x.get('path')}" for x in no_tags]
        if report.get("library", {}).get("missing_on_disk") is not None:
            text += ["", f"Отсутствует на диске: {report.get('library',{}).get('missing_on_disk',0)}"]
            text += [f"  {x.get('path')}" for x in report.get("samples", {}).get("missing_on_disk", [])]
        self.samples.setPlainText("\n".join(text))
        perf_rows = report.get("performance", {}).get("slow_operations", [])
        self.performance_table.setRowCount(len(perf_rows))
        for row, data in enumerate(perf_rows):
            detail = data.get("detail", {})
            values = [data.get("at", ""), data.get("operation", ""), data.get("duration_ms", ""), str(detail)]
            for col, value in enumerate(values):
                self.performance_table.setItem(row, col, QTableWidgetItem(str(value)))
        crash = report.get("errors", {}).get("last_crash", {})
        error_lines = list(report.get("errors", {}).get("last_lines", []))
        if crash:
            error_lines += ["", "ПОСЛЕДНЕЕ ПАДЕНИЕ:", f"Время: {crash.get('created_at','')}", f"База: {crash.get('database','')}", str(crash.get("traceback", ""))]
        self.errors.setPlainText("\n".join(error_lines) or "В errors.log последних записей не найдено.")

    @staticmethod
    def _fmt_bytes(value):
        n = float(value or 0)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB": return f"{n:.2f} {unit}"
            n /= 1024


    def run_performance_audit(self):
        """Run explicit read-only SQL timings; does not modify the database."""
        try:
            from core.performance_audit import audit_query_performance
        except Exception as exc:
            QMessageBox.warning(self, "Производительность", str(exc)); return
        self.benchmark_btn.setEnabled(False)
        self.progress_label.setText("Замер SQLite: подготовка…")
        self.progress.setRange(0, 0); self.progress.setVisible(True)
        self._benchmark_task = self.main.task_manager.submit(
            audit_query_performance, dict(self.main.settings or {}), name="sqlite-performance-audit",
            on_progress=lambda msg: self.progress_label.setText(str(msg)),
            on_result=self._performance_audit_done,
            on_error=lambda err: QMessageBox.warning(self, "Производительность", str(err)),
            on_finished=self._performance_audit_finished,
        )

    def _performance_audit_finished(self):
        self.benchmark_btn.setEnabled(True)
        self.progress.setVisible(False)

    def _performance_audit_done(self, result):
        if isinstance(self._report, dict):
            self._report["sqlite_benchmark"] = dict(result or {})
        rows = list((result or {}).get("queries", []) or [])
        self.benchmark_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            sample = item.get("error") or str(item.get("sample") or "")
            for col, value in enumerate([item.get("name", ""), item.get("duration_ms", ""), item.get("rows", ""), sample]):
                self.benchmark_table.setItem(row, col, QTableWidgetItem(str(value)))
        self.tabs.setCurrentWidget(self.benchmark_table)
        if result.get("error"):
            self.progress_label.setText(str(result.get("error")))
        else:
            slowest = result.get("slowest", {}) or {}
            self.progress_label.setText(f"Замер готов: всего {result.get('total_ms',0)} мс; медленнее всего — {slowest.get('name','—')} ({slowest.get('duration_ms','—')} мс)")

    def cancel_background_tasks(self):
        count = 0
        try:
            count = int(self.main.task_manager.cancel_all())
        except Exception as exc:
            QMessageBox.warning(self, "Фоновые задачи", str(exc)); return
        QMessageBox.information(self, "Фоновые задачи", f"Запрошена остановка фоновых задач: {count}.\n\nЭто останавливает задачи обслуживания/диагностики; для самого парсера используй его кнопку «Стоп».")
        self.refresh_force()

    def check_lost_sources(self):
        """Read-only audit shortcut; it never guesses or creates a source."""
        self.tabs.setCurrentWidget(self.samples)
        self.refresh_force()

    def export_report(self):
        if not self._report:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить отчёт диагностики", "Local_Booru_library_audit.json", "JSON (*.json)")
        if not path:
            return
        try:
            save_audit_json(self._report, path)
            QMessageBox.information(self, "Диагностика", f"Отчёт сохранён:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Диагностика", str(exc))

    def create_backup(self):
        try:
            out = create_forced_backup(self.main.settings)
            if out:
                QMessageBox.information(self, "Резервная копия", f"Backup создан:\n{out}")
            else:
                QMessageBox.warning(self, "Резервная копия", "Не удалось создать backup базы.")
        except Exception as exc:
            QMessageBox.warning(self, "Резервная копия", str(exc))

    def _warn_repair(self, title, text):
        return QMessageBox.warning(
            self, title,
            text + "\n\nПеред изменением будет создана резервная копия. Парсер на время ремонта лучше остановить.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes

    def normalize_exact_md5(self):
        if not self._warn_repair("Склеить точные MD5-дубли", "Склеить все точные живые MD5-дубли?\nИсточники и теги будут объединены; лишние физические копии уйдут в «Удалено»."):
            return
        self._set_busy("Склейка точных MD5-дублей…")
        from core.library_lifecycle import cleanup_live_exact_duplicates
        def work(settings, progress=None, stop_check=None):
            def adapt(_stage, done, total):
                if progress: progress(f"Склейка: вычисление MD5 {done}/{total}")
            return cleanup_live_exact_duplicates(settings, make_backup=True, progress=adapt, cancel_check=stop_check)
        self._active_task = self.main.task_manager.submit(
            work, dict(self.main.settings or {}), name="normalize-exact-md5",
            on_progress=lambda msg: self.progress_label.setText(str(msg)),
            on_result=self._normalize_done, on_error=self._audit_error, on_finished=self._repair_finished,
        )

    def _normalize_done(self, result):
        msg = result.get("error") or (
            f"Backup: {result.get('backup','')}\n"
            f"Групп дублей: {result.get('groups',0)}\n"
            f"Лишних копий отправлено в корзину: {result.get('trashed_records',0)}\n"
            f"Объединено записей/источников: {result.get('merged_existing',0)}\n"
            f"Снято MD5-запретов: {result.get('unblocked_md5',0)}\n"
            f"Ошибок: {result.get('errors',0)}"
        )
        QMessageBox.information(self, "Склейка точных MD5", msg)

    def unblock_live_md5(self):
        if not self._warn_repair("Снять устаревшие MD5-запреты", "Отключить в SQLite только те ручные MD5-запреты, точная живая копия которых уже есть в библиотеке?"):
            return
        try:
            result = clear_obsolete_live_md5_blocks(self.main.settings)
            QMessageBox.information(self, "MD5-запреты", f"Снято запретов: {result.get('removed',0)}\nBackup базы: {result.get('backup','')}\nОсталось активных ручных запретов: {result.get('remaining',0)}")
            self.refresh_force()
        except Exception as exc:
            QMessageBox.warning(self, "MD5-запреты", str(exc))


    def requeue_stale_categories(self):
        if not self._warn_repair("Вернуть stale-категории", "Вернуть устаревшие задания фоновой раскладки тегов в очередь?\nMD5-поиск и файлы не затрагиваются."):
            return
        try:
            result = requeue_stale_tag_enrichments(self.main.settings)
            QMessageBox.information(self, "Фоновая раскладка", result.get("error") or f"Backup базы: {result.get('backup','')}\nВозвращено в очередь: {result.get('requeued',0)}")
            self.refresh_force()
        except Exception as exc:
            QMessageBox.warning(self, "Фоновая раскладка", str(exc))


    def redact_existing_logs(self):
        answer = QMessageBox.warning(
            self, "Обезличить старые логи",
            "Удалить API-ключи, логины, токены и cookie-значения из уже записанных файлов логов и отчётов падения?\n\n"
            "Операция касается только logs/*. Старые сырые копии с секретами сохраняться не будут.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            from core.redaction import sanitize_log_directory
            from core.paths import LOGS_DIR
            result = sanitize_log_directory(LOGS_DIR)
            QMessageBox.information(
                self, "Обезличить старые логи",
                f"Проверено файлов: {result.get('checked', 0)}\n"
                f"Обезличено файлов: {result.get('changed', 0)}\n\n"
                "API-ключи на сайтах всё равно рекомендуется перевыпустить, потому что они уже были записаны в старый лог.",
            )
            self.refresh_force()
        except Exception as exc:
            QMessageBox.warning(self, "Обезличить старые логи", str(exc))

    def restore_indices(self):
        if not self._warn_repair("Восстановить индексы SQLite", "Проверить и создать отсутствующие критические индексы SQLite?"):
            return
        try:
            result = restore_critical_indices(self.main.settings)
            QMessageBox.information(self, "Индексы SQLite", result.get("error") or f"Backup: {result.get('backup','')}\nВосстановлено: {', '.join(result.get('restored',[])) or 'не требовалось'}\nОсталось отсутствующих: {', '.join(result.get('missing_after',[])) or 'нет'}")
            self.refresh_force()
        except Exception as exc:
            QMessageBox.warning(self, "Индексы SQLite", str(exc))

    def _repair_finished(self):
        self._clear_busy()
        self.refresh_force()
        try:
            self.main.gallery_page.refresh_force()
            self.main.trash_page.refresh()
        except Exception:
            pass
