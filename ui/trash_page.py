from __future__ import annotations

from pathlib import Path
from collections import Counter
from datetime import datetime
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QColor, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QMessageBox, QSplitter, QSizePolicy, QProgressDialog, QApplication
)

from core.library_lifecycle import trash_rows, restore_from_trash, purge_trash, folder_size, cleanup_live_exact_duplicates
from core.services.library_service import enrich_items
from core.image_safe import safe_thumbnail_path
from core.tag_utils import tag_display_color

_REASON_LABELS = {
    "gallery_context_delete": "удалено вручную из галереи",
    "post_context_delete": "удалено вручную из просмотра",
    "duplicate_delete": "удалено в окне дубликатов",
    "subscription_visual_duplicate": "авто: похожий дубликат подписки",
    "subscription_session_cleanup": "очистка сессии подписки",
    "downloader_exact_duplicate": "авто: точный дубликат загрузчика",
    "reimport_deleted_rejected": "авто: повторно скачанный удалённый файл",
    "restore_exact_duplicate_cleanup": "авто: убрана точная копия после восстановления",
    "exact_md5_auto_normalized": "авто: склеена точная MD5-копия",
    "delete_by_tag": "удалено по тегу",
    "delete_by_source": "удалено по источнику",
    "delete_by_buckets": "очистка результатов",
    "unknown": "неизвестно / старая сборка",
}

def _reason_text(value):
    key = str(value or "unknown")
    return _REASON_LABELS.get(key, key)

_GROUP_COLORS = {
    "artist": "#ff3838", "contributor": "#e67e22", "character": "#00a000", "copyright": "#ff54a7",
    "species": "#22a6b3", "general": "#004cff", "meta": "#ff9900", "lore": "#9b59b6", "invalid": "#7f8c8d",
    "parody": "#ff54a7", "language": "#cc8800", "category": "#00aaaa", "pages": "#888888",
}

class TrashPage(QWidget):
    """Recoverable files with a real inspection panel before permanent deletion."""
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._rows: list[dict] = []
        self._preview_pix = QPixmap()
        lay = QVBoxLayout(self)
        note = QLabel("Файлы здесь ещё можно восстановить. Если ты ничего не удалял, сначала посмотри строку «Причины» ниже: автоматические чистки теперь показаны явно.")
        note.setWordWrap(True); lay.addWidget(note)
        row = QHBoxLayout()
        self.refresh_btn = QPushButton("Обновить")
        self.restore_btn = QPushButton("Восстановить выбранные")
        self.restore_all_btn = QPushButton("Восстановить всё")
        self.cleanup_exact_btn = QPushButton("Склеить точные MD5-записи")
        self.purge_btn = QPushButton("Удалить выбранные окончательно")
        self.empty_btn = QPushButton("Очистить корзину полностью")
        for btn in (self.refresh_btn, self.restore_btn, self.restore_all_btn, self.cleanup_exact_btn, self.purge_btn, self.empty_btn): row.addWidget(btn)
        row.addStretch(1); lay.addLayout(row)
        self.info = QLabel(""); lay.addWidget(self.info)

        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget(); self.list.setSelectionMode(QListWidget.ExtendedSelection); self.list.setMinimumWidth(300)
        split.addWidget(self.list)
        detail = QWidget(); dl = QVBoxLayout(detail); dl.setContentsMargins(8, 0, 0, 0)
        self.preview = QLabel("Выбери удалённый файл")
        self.preview.setAlignment(Qt.AlignCenter); self.preview.setMinimumHeight(360); self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview.setStyleSheet("border:1px solid palette(mid); background:transparent;")
        dl.addWidget(self.preview, 3)
        self.metadata = QLabel(""); self.metadata.setWordWrap(True); dl.addWidget(self.metadata)
        low = QSplitter(Qt.Horizontal)
        tags_box = QWidget(); tl=QVBoxLayout(tags_box); tl.setContentsMargins(0,0,4,0); tl.addWidget(QLabel("Теги выбранного файла")); self.tags=QListWidget(); tl.addWidget(self.tags)
        src_box = QWidget(); sl=QVBoxLayout(src_box); sl.setContentsMargins(4,0,0,0); sl.addWidget(QLabel("Источники")); self.sources=QListWidget(); sl.addWidget(self.sources)
        low.addWidget(tags_box); low.addWidget(src_box); low.setSizes([520, 360]); dl.addWidget(low, 2)
        split.addWidget(detail); split.setSizes([330, 1000]); lay.addWidget(split, 1)

        self.refresh_btn.clicked.connect(self.refresh)
        self.restore_btn.clicked.connect(self.restore_selected)
        self.restore_all_btn.clicked.connect(self.restore_all)
        self.cleanup_exact_btn.clicked.connect(self.cleanup_exact_duplicates)
        self.purge_btn.clicked.connect(self.purge_selected)
        self.empty_btn.clicked.connect(self.empty_trash)
        self.list.currentItemChanged.connect(lambda *_: self._show_current())

    def retranslate(self):
        pass

    def refresh(self):
        selected = self.list.currentItem().data(Qt.UserRole) if self.list.currentItem() else None
        self._rows = trash_rows(self.main.settings)
        self.list.clear(); total = 0; select_row = -1
        reasons = Counter(_reason_text(r.get("delete_reason")) for r in self._rows)
        for pos, row in enumerate(self._rows):
            total += int(row.get("size_bytes") or 0)
            original = row.get("original_media_path") or row.get("path") or row.get("file_name") or ""
            stamp = int(row.get("trashed_at") or 0)
            item = QListWidgetItem(f"{Path(str(original)).name}    {int(row.get('size_bytes') or 0) / 1024 / 1024:.2f} MB")
            item.setData(Qt.UserRole, int(row.get("id") or 0)); self.list.addItem(item)
            if selected is not None and int(selected) == int(row.get("id") or 0): select_row = pos
        reason_summary = "; ".join(f"{reason}: {count}" for reason, count in reasons.most_common())
        self.info.setWordWrap(True)
        self.info.setText(f"В корзине: {len(self._rows)} файлов / {total / 1024 / 1024:.2f} MB" + (f"\nПричины: {reason_summary}" if reason_summary else ""))
        if self._rows:
            self.list.setCurrentRow(select_row if select_row >= 0 else 0)
        else:
            self.preview.clear(); self.preview.setText("Корзина пуста"); self.metadata.clear(); self.tags.clear(); self.sources.clear()

    def _selected_ids(self):
        return [int(it.data(Qt.UserRole)) for it in self.list.selectedItems() if it.data(Qt.UserRole)]

    def _show_current(self):
        current = self.list.currentItem()
        if current is None:
            return
        image_id = int(current.data(Qt.UserRole) or 0)
        row = next((dict(r) for r in self._rows if int(r.get("id") or 0) == image_id), None)
        if not row:
            return
        enrich_items(self.main.settings, [row])
        path = Path(str(row.get("path") or ""))
        thumb = safe_thumbnail_path(path, 1000, 760) if path.exists() else ""
        self._preview_pix = QPixmap(thumb) if thumb else QPixmap()
        self._paint_preview(path.name)
        original = row.get("original_media_path") or ""
        moved_at = ""
        try:
            if int(row.get("trashed_at") or 0):
                moved_at = datetime.fromtimestamp(int(row.get("trashed_at") or 0)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            moved_at = ""
        reason = _reason_text(row.get("delete_reason"))
        target = str(row.get("delete_target") or "")
        self.metadata.setText(
            f"Файл: {path.name}\n"
            f"Было: {original}\n"
            f"Причина: {reason}" + (f" ({target})" if target else "") + (f"    Когда: {moved_at}" if moved_at else "") + "\n"
            f"Размер: {int(row.get('size_bytes') or 0) / 1024 / 1024:.2f} MB    "
            f"Разрешение: {int(row.get('width') or 0)}×{int(row.get('height') or 0)}"
        )
        self.tags.clear()
        order = list(self.main.settings.get("tag_group_order") or _GROUP_COLORS.keys())
        for group in _GROUP_COLORS:
            if group not in order:
                order.append(group)
        for group in order:
            vals = (row.get("tag_groups") or {}).get(group, [])
            if not vals: continue
            head = QListWidgetItem(group); head.setFlags(Qt.NoItemFlags); head.setForeground(QBrush(QColor(_GROUP_COLORS.get(group, "#888"))))
            font=head.font(); font.setBold(True); head.setFont(font); self.tags.addItem(head)
            for tag in vals:
                it=QListWidgetItem(str(tag)); it.setForeground(QBrush(QColor(tag_display_color(tag, group, self.main.settings, _GROUP_COLORS)))); self.tags.addItem(it)
        self.sources.clear()
        for src in row.get("sources", []):
            self.sources.addItem(str(src.get("url") or src.get("host") or ""))

    def _paint_preview(self, fallback_text=""):
        if self._preview_pix.isNull():
            self.preview.setText(fallback_text or "Нет предпросмотра"); self.preview.setPixmap(QPixmap()); return
        size = self.preview.size()
        self.preview.setPixmap(self._preview_pix.scaled(max(100, size.width()-16), max(100, size.height()-16), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._preview_pix.isNull(): self._paint_preview()

    def restore_selected(self):
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "Корзина", "Выбери файлы для восстановления."); return
        res = restore_from_trash(self.main.settings, ids); self.refresh()
        try: self.main.gallery_page.refresh_force()
        except Exception: pass
        QMessageBox.information(self, "Корзина", f"Восстановлено: {res.get('restored', 0)}\nНе возвращено, уже есть точная копия: {res.get('skipped_existing', 0)}\nСнято устаревших запретов MD5: {res.get('unblocked_md5', 0)}\nОшибки: {res.get('errors', 0)}")


    def restore_all(self):
        if not self._rows:
            return
        reasons = Counter(_reason_text(r.get("delete_reason")) for r in self._rows)
        summary = "\n".join(f"- {reason}: {count}" for reason, count in reasons.most_common())
        if QMessageBox.question(
            self, "Восстановить всё",
            f"Восстановить все файлы из «Удалено»?\nФайлов: {len(self._rows)}\n\nЕсли точная MD5-копия уже есть в архиве, вторая копия восстановлена не будет; её теги и источники будут добавлены к живому файлу.\n\nПричины:\n{summary}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return
        res = restore_from_trash(self.main.settings, [int(r.get("id") or 0) for r in self._rows if r.get("id")])
        self.refresh()
        try: self.main.gallery_page.refresh_force()
        except Exception: pass
        QMessageBox.information(self, "Корзина", f"Восстановлено: {res.get('restored', 0)}\nНе возвращено, уже есть точная копия: {res.get('skipped_existing', 0)}\nСнято устаревших запретов MD5: {res.get('unblocked_md5', 0)}\nОшибки: {res.get('errors', 0)}")

    def cleanup_exact_duplicates(self):
        if QMessageBox.warning(
            self, "Склеить точные MD5-записи",
            "Нормализовать всю живую галерею по точному MD5?\n\n"
            "Для каждого байт-в-байт одинакового файла останется одна физическая копия и одна карточка. "
            "Все источники, теги, рейтинг и избранное будут объединены в неё; лишние копии переместятся в «Удалено».\n\n"
            "Старым записям без MD5 потребуется один раз вычислить хеши, это может занять время.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return
        dlg = QProgressDialog("Проверка точных MD5-дублей…", "Отмена", 0, 0, self)
        dlg.setWindowTitle("Склейка точных MD5")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.show()
        def progress(stage, done, total):
            dlg.setLabelText(f"Вычисление MD5 старых записей: {done}/{total}")
            dlg.setMaximum(max(1, int(total or 1)))
            dlg.setValue(int(done or 0))
            QApplication.processEvents()
        res = cleanup_live_exact_duplicates(self.main.settings, make_backup=True, progress=progress, cancel_check=dlg.wasCanceled)
        dlg.close()
        self.refresh()
        try: self.main.gallery_page.refresh_force()
        except Exception: pass
        if res.get("cancelled"):
            QMessageBox.information(self, "Склейка точных MD5", "Операция отменена. Уже вычисленные MD5 сохранены и не повредили библиотеку.")
            return
        msg = res.get("error") or (
            f"Досчитано MD5 старых записей: {res.get('hashed_missing', 0)}\n"
            f"Групп одинаковых файлов: {res.get('groups', 0)}\n"
            f"Лишних физических копий перемещено в «Удалено»: {res.get('trashed_records', 0)}\n"
            f"Записей с объединёнными тегами/источниками: {res.get('merged_existing', 0)}\n"
            f"Снято устаревших запретов MD5: {res.get('unblocked_md5', 0)}\n"
            f"Ошибки: {res.get('errors', 0)}"
        )
        QMessageBox.information(self, "Склейка точных MD5", msg)

    def purge_selected(self):
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "Корзина", "Выбери файлы для окончательного удаления."); return
        if QMessageBox.warning(self, "Удалить окончательно", "Файл, теги, источники и записи базы будут удалены окончательно. Продолжить?", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes: return
        res = purge_trash(self.main.settings, ids); self.refresh()
        QMessageBox.information(self, "Корзина", f"Удалено окончательно: {res.get('removed_records', 0)}\nОшибки: {res.get('errors', 0)}")

    def empty_trash(self):
        if not self._rows: return
        if QMessageBox.warning(self, "Очистить корзину", "Окончательно удалить ВСЕ файлы в корзине и связанные данные?", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes: return
        res = purge_trash(self.main.settings, None); self.refresh()
        QMessageBox.information(self, "Корзина", f"Удалено окончательно: {res.get('removed_records', 0)}\nОшибки: {res.get('errors', 0)}")
