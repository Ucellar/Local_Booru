from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox, QGridLayout, QPlainTextEdit, QProgressBar

from core.library_diagnostics import audit_library


class OverviewPage(QWidget):
    """Fast read-only home screen for a long-running archive workflow."""
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._active_task = None
        layout = QVBoxLayout(self)
        title = QLabel("Состояние рабочей библиотеки")
        font = title.font(); font.setPointSize(max(15, font.pointSize() + 5)); font.setBold(True); title.setFont(font)
        layout.addWidget(title)
        self.note = QLabel("Исходный архив защищён: приложение читает и копирует оригиналы, а изменяет только рабочую библиотеку.")
        self.note.setWordWrap(True); self.note.setObjectName("DiagnosticsNotice")
        layout.addWidget(self.note)
        controls = QHBoxLayout()
        self.refresh_btn = QPushButton("Обновить состояние")
        self.refresh_btn.clicked.connect(self.refresh_force)
        for caption, page in (("Открыть галерею", "Gallery"), ("Открыть парсер", "Tagger"), ("Диагностика", "Diagnostics"), ("Удалено", "Trash")):
            btn = QPushButton(caption); btn.clicked.connect(lambda _=False, key=page: self.main.go(key)); controls.addWidget(btn)
        controls.addWidget(self.refresh_btn); controls.addStretch(1); layout.addLayout(controls)
        self.progress = QProgressBar(); self.progress.setRange(0, 0); self.progress.setVisible(False); layout.addWidget(self.progress)
        cards = QGroupBox("Ключевые показатели")
        grid = QGridLayout(cards); self.values = {}
        for index, (key, title_text) in enumerate((
            ("live", "Файлов в галерее"), ("trash", "В удалённых"), ("duplicates", "MD5-дубли"),
            ("sources", "Несколько источников"), ("sauce", "Ждут SauceNAO"), ("categories", "Ждут категории"),
            ("errors", "Проблемы source"), ("blocked", "Заблокировано удалений оригинала"),
        )):
            box = QGroupBox(title_text); row = QVBoxLayout(box)
            value = QLabel("—"); value.setAlignment(Qt.AlignCenter)
            vf = value.font(); vf.setPointSize(max(13, vf.pointSize()+5)); vf.setBold(True); value.setFont(vf)
            row.addWidget(value); grid.addWidget(box, index // 4, index % 4); self.values[key] = value
        layout.addWidget(cards)
        self.status = QPlainTextEdit(); self.status.setReadOnly(True); layout.addWidget(self.status, 1)

    def retranslate(self):
        pass

    def refresh(self):
        self.refresh_force()

    def refresh_force(self):
        if self._active_task is not None:
            try:
                if not self._active_task.future.done():
                    return
            except Exception:
                pass
        self.refresh_btn.setEnabled(False); self.progress.setVisible(True); self.status.setPlainText("Сбор состояния…")
        self._active_task = self.main.task_manager.submit(
            audit_library, dict(self.main.settings or {}), name="overview-health",
            on_result=self._done, on_error=lambda err: self.status.setPlainText(str(err)), on_finished=self._finished,
        )

    def _finished(self):
        self.refresh_btn.setEnabled(True); self.progress.setVisible(False)

    def _done(self, report):
        lib = report.get("library", {}); md5 = report.get("md5", {}); queues = report.get("queues", {})
        reverse = sum(int(x.get("files", 0) or 0) for x in queues.get("reverse_retry", []) if str(x.get("service", "")).lower() == "saucenao")
        categories = sum(int(x.get("files", 0) or 0) for x in queues.get("tag_enrichment", []) if str(x.get("status", "")) != "done")
        self.values["live"].setText(str(lib.get("live_files", 0)))
        self.values["trash"].setText(str(lib.get("trash_files", 0)))
        self.values["duplicates"].setText(str(md5.get("duplicate_groups", 0)))
        self.values["sources"].setText(str(lib.get("multi_source_files", 0)))
        self.values["sauce"].setText(str(reverse))
        self.values["categories"].setText(str(categories))
        self.values["errors"].setText(str(lib.get("without_source", 0)))
        self.values["blocked"].setText(str(report.get("source_protection", {}).get("blocked_count_shown", 0)))
        lines = [
            "Рабочая библиотека готова к пересборке; оригинальный архив защищён от перемещения и удаления.",
            f"SQLite: {report.get('database', {}).get('path', '')}",
            f"Проверка базы: {report.get('database', {}).get('quick_check', '—')}",
            f"Без тегов: {lib.get('without_tags', 0)}    Без source: {lib.get('without_source', 0)}",
        ]
        events = queues.get("saucenao_retry_events", [])
        if events:
            last = events[0]; lines.append(f"Последнее доказательство retry SauceNAO: {last.get('status', '')} — {last.get('message', '')}")
        else:
            lines.append("Повтор SauceNAO после окончания кулдауна ещё не зафиксирован.")
        if md5.get("duplicate_groups", 0):
            lines.append("ВНИМАНИЕ: найдены точные MD5-дубли. Открой «Диагностика» перед ремонтом.")
        self.status.setPlainText("\n".join(lines))
