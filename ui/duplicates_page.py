from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QFrame, QCheckBox, QMessageBox, QDialog, QSpinBox
)

from core.settings import save_settings
from core.services.library_service import (
    trash_duplicate_paths,
    trash_duplicate_groups,
    validate_duplicate_group_plans,
    remember_duplicate_relation,
)
from core.duplicate_scanner import run_duplicate_scan


class ImagePreviewDialog(QDialog):
    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(Path(path).name)
        self.resize(1200, 850)
        lay = QVBoxLayout(self)
        lab = QLabel()
        lab.setAlignment(Qt.AlignCenter)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        pix = QPixmap(path)
        if pix.isNull():
            lab.setText(path)
        else:
            lab.setPixmap(pix.scaled(1050, 720, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay.addWidget(lab, 1)
        row = QHBoxLayout()
        full = QPushButton("Во весь экран")
        close = QPushButton("Закрыть")
        row.addStretch(1)
        row.addWidget(full)
        row.addWidget(close)
        lay.addLayout(row)
        full.clicked.connect(self.showFullScreen)
        close.clicked.connect(self.accept)
        QShortcut(QKeySequence("Esc"), self).activated.connect(self.accept)


class ClickableImage(QLabel):
    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        dlg = ImagePreviewDialog(self.path, self)
        dlg.exec()


class DuplicateItemCard(QFrame):
    def __init__(self, info: dict, checked: bool = False, protected: bool = False):
        super().__init__()
        self.info = info
        self.setFixedWidth(235)
        self.setMaximumHeight(430)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("QFrame{background:#171a21;border:1px solid #2f3541;border-radius:10px;}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        self.checkbox = QCheckBox("Удалить")
        self.checkbox.setChecked(bool(checked))
        self.checkbox.setEnabled(not protected)
        if protected:
            self.checkbox.setText("Оставить")
        lay.addWidget(self.checkbox)

        img = ClickableImage(info.get("path", ""))
        img.setAlignment(Qt.AlignCenter)
        img.setFixedSize(210, 160)
        pix = QPixmap(info.get("path", ""))
        if not pix.isNull():
            img.setPixmap(pix.scaled(img.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            img.setText("NO PREVIEW")
        lay.addWidget(img)

        pixels = info.get("pixels") or [0, 0]
        size_mb = int(info.get("bytes") or 0) / 1024 / 1024
        text = (
            f"название: {info.get('name','')}\n"
            f"размер: {int(info.get('bytes') or 0)} bytes ({size_mb:.2f} MB)\n"
            f"размер в пикселях: {pixels[0]}x{pixels[1]}\n"
            f"md5: {info.get('md5','')}\n"
            f"путь: {info.get('path','')}"
        )
        meta = QLabel(text)
        meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        meta.setWordWrap(True)
        meta.setStyleSheet("font-family:Consolas,monospace;font-size:10px;color:#d8dbe2;")
        lay.addWidget(meta, 1)


class DuplicateGroupWidget(QFrame):
    def __init__(self, group: dict, settings: dict, on_deleted):
        super().__init__()
        self.group = group
        self.settings = dict(settings or {})
        self.on_deleted = on_deleted
        self.cards = []
        self.setStyleSheet("QFrame{background:#11141a;border:1px solid #343b49;border-radius:12px;}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)

        title = QLabel(group.get("reason", "Дубликаты"))
        title.setWordWrap(True)
        title.setStyleSheet("font-size:16px;font-weight:900;")
        lay.addWidget(title)

        if group.get("safe"):
            note = QLabel("Безопасные копии уже отмечены. Оставляется оригинал/самая ранняя версия, файлы с (1)/(2)/(3) можно удалить без ручного сравнения.")
            note.setWordWrap(True)
            note.setStyleSheet("color:#9fb3c8;")
            lay.addWidget(note)
        elif len(group.get("items", [])) >= 2:
            _items = list(group.get("items", []))
            def _quality(item):
                px = item.get("pixels") or [0, 0]
                return (int(px[0] or 0) * int(px[1] or 0), int(item.get("bytes") or 0))
            _best = max(_items, key=_quality)
            _px = _best.get("pixels") or [0, 0]
            if int(_px[0] or 0) * int(_px[1] or 0) > 0:
                suggest = QLabel(f"Подсказка: версия «{_best.get('name','')}» выглядит качественнее по разрешению ({_px[0]}x{_px[1]}). Решение всё равно принимаешь ты.")
                suggest.setWordWrap(True); suggest.setStyleSheet("color:#9fb3c8;")
                lay.addWidget(suggest)

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        sc.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.setMaximumHeight(455)
        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(0, 0, 0, 0)
        keep_path = group.get("keep_path")
        for info in group.get("items", []):
            checked = bool(group.get("safe")) and info.get("path") != keep_path
            protected = bool(group.get("safe")) and info.get("path") == keep_path
            card = DuplicateItemCard(info, checked=checked, protected=protected)
            self.cards.append(card)
            row.addWidget(card)
        row.addStretch(1)
        sc.setWidget(body)
        lay.addWidget(sc)

        buttons = QHBoxLayout()
        delete_btn = QPushButton("В Удалено отмеченные")
        relation_allowed = not bool(group.get("safe")) and not str(group.get("reason", "")).startswith("Точный дубль")
        alt_btn = QPushButton("Альтернативная версия")
        not_dup_btn = QPushButton("Не дубль / ложное совпадение")
        alt_btn.setEnabled(relation_allowed)
        not_dup_btn.setEnabled(relation_allowed)
        if not relation_allowed:
            alt_btn.setToolTip("Для точных MD5-дублей связь не заменяет удаление/сохранение.")
            not_dup_btn.setToolTip("Точные MD5-дубли всегда считаются дублями.")
        skip_btn = QPushButton("Пропустить")
        buttons.addWidget(delete_btn)
        buttons.addWidget(alt_btn)
        buttons.addWidget(not_dup_btn)
        buttons.addWidget(skip_btn)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        delete_btn.clicked.connect(self.delete_checked)
        alt_btn.clicked.connect(lambda: self.remember_relation("alternative"))
        not_dup_btn.clicked.connect(lambda: self.remember_relation("not_duplicate"))
        skip_btn.clicked.connect(self.hide)

    def checked_cards(self):
        return [c for c in self.cards if c.checkbox.isEnabled() and c.checkbox.isChecked()]

    def remember_relation(self, relation: str):
        try:
            count = remember_duplicate_relation(self.settings, self.group.get("items", []), relation=relation)
        except Exception as exc:
            QMessageBox.warning(self, "Дубликаты", f"Не удалось сохранить решение:\n{exc}")
            return
        label = "Альтернативная версия" if relation == "alternative" else "Не дубль"
        QMessageBox.information(
            self, "Дубликаты",
            f"Сохранено: {label}.\nЗапомнено пар: {count}.\n\n"
            "Новые неизвестные файлы всё равно будут проверяться, а точные MD5-дубли не скрываются."
        )
        self.hide()

    def delete_checked(self):
        checked = self.checked_cards()
        if not checked:
            QMessageBox.information(self, "Дубликаты", "Ничего не отмечено.")
            return

        checked_ids = {id(card) for card in checked}
        keep_cards = [
            card for card in self.cards
            if id(card) not in checked_ids
            and Path(str(card.info.get("path", ""))).is_file()
        ]
        if not keep_cards:
            QMessageBox.warning(
                self,
                "Дубликаты",
                "Отмечены все файлы группы. Оставьте хотя бы один файл, "
                "в который будут перенесены теги и источники.",
            )
            return

        deleted_paths = [str(card.info.get("path", "")) for card in checked]
        keep_path = str(keep_cards[0].info.get("path", ""))
        ok = QMessageBox.question(
            self,
            "Дубликаты",
            f"Переместить отмеченные файлы в «Удалено»?\nФайлов: {len(deleted_paths)}\n\n"
            "Теги, источники, рейтинг и favorite будут объединены "
            "с оставленным файлом. Перед операцией будет создана резервная копия базы.",
        )
        if ok != QMessageBox.Yes:
            return

        moved = error_count = 0
        try:
            result = trash_duplicate_paths(self.settings, deleted_paths, keep_path=keep_path)
            moved = int(result.get("deleted_files", 0) or 0)
            error_count = int(result.get("errors", 0) or 0)
        except Exception as exc:
            error_count = 1
            QMessageBox.warning(self, "Дубликаты", f"Удаление отменено или завершилось ошибкой:\n{exc}")

        if moved and not error_count:
            for card in checked:
                card.setVisible(False)
        self.on_deleted(moved, error_count)
        if not error_count and moved:
            self.hide()


class DuplicatesPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.scan_task = None
        self.scan_result = {}
        self.group_widgets = []
        self.pending_groups = []
        self.render_index = 0
        self.render_timer = QTimer(self)
        self.render_timer.timeout.connect(self.render_next_batch)

        root = QVBoxLayout(self)
        title = QLabel("Дубликаты / Duplicates")
        title.setStyleSheet("font-size:26px;font-weight:900")
        root.addWidget(title)

        row = QHBoxLayout()
        self.scan_btn = QPushButton("Сканировать")
        self.threshold = QSpinBox()
        self.threshold.setRange(50, 100)
        self.threshold.setValue(int(self.main.settings.get("duplicate_visual_threshold", 85) if hasattr(self.main, "settings") else 85))
        self.threshold.setSuffix("%")
        self.threshold.setToolTip("Порог визуального сравнения. 85% — меньше мусора, 65% — больше возможных совпадений.")
        self.delete_all_btn = QPushButton("Удалить все отмеченные")
        self.clear_btn = QPushButton("Очистить список")
        self.status = QLabel("Готово.")
        row.addWidget(self.scan_btn)
        row.addWidget(QLabel("Порог похожести:"))
        row.addWidget(self.threshold)
        row.addWidget(self.delete_all_btn)
        row.addWidget(self.clear_btn)
        row.addWidget(self.status, 1)
        root.addLayout(row)

        info = QLabel(
            "DB-first: используются сохранённые SQLite MD5/pHash/размеры. "
            "VP-tree выдаёт только пары-кандидаты; файлы библиотеки повторно не хэшируются. "
            "Управляемые папки дополнительно проверяются только на неиндексированные физические файлы."
        )
        info.setWordWrap(True)
        root.addWidget(info)
        self.scan_notice = QLabel("")
        self.scan_notice.setWordWrap(True)
        self.scan_notice.setVisible(False)
        self.scan_notice.setStyleSheet("color:#d9a441;")
        root.addWidget(self.scan_notice)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.addStretch(1)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)

        self.scan_btn.clicked.connect(self.scan)
        self.clear_btn.clicked.connect(self.clear_results)
        self.delete_all_btn.clicked.connect(self.delete_all_checked)

    def retranslate(self):
        pass

    def clear_results(self):
        self.render_timer.stop()
        self.pending_groups = []
        self.render_index = 0
        self.group_widgets.clear()
        while self.body_lay.count() > 1:
            item = self.body_lay.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self.scan_notice.clear()
        self.scan_notice.setVisible(False)
        self.status.setText("Список очищен.")

    def scan(self):
        current = self.scan_task
        if current is not None and (current.future is None or not current.future.done()):
            current.cancel()
            self.scan_btn.setEnabled(False)
            self.scan_btn.setText("Остановка...")
            self.status.setText("Дубликаты: запрошена остановка; старые candidate relations останутся рабочими")
            return
        self.clear_results()
        self.scan_notice.setVisible(False)
        self.scan_result = {}
        try:
            self.main.settings["duplicate_visual_threshold"] = int(self.threshold.value())
            save_settings(self.main.settings)
        except Exception:
            pass
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("Отменить сканирование")
        self.status.setText("DB-first сканирование: подготовка...")
        try:
            self.scan_task = self.main.task_manager.submit(
                run_duplicate_scan,
                dict(self.main.settings or {}),
                int(self.threshold.value()),
                mode="forced",
                name="maintenance.duplicate-candidate-scan",
                on_progress=self.status.setText,
                on_result=self.render_scan_result,
                on_error=self.scan_error,
                on_finished=self.scan_finished,
            )
        except Exception as exc:
            self.scan_task = None
            self.scan_btn.setEnabled(True)
            self.scan_btn.setText("Сканировать")
            self.status.setText(f"SCAN ERROR: {exc}")

    def scan_error(self, error):
        text = str(error or "")
        self.status.setText("SCAN ERROR: " + text[-1200:])
        QMessageBox.warning(self, "Дубликаты", "DB-first сканирование завершилось ошибкой:\n" + text[-3000:])

    def scan_finished(self):
        task = self.scan_task
        self.scan_task = None
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("Сканировать")
        if task is not None and task.cancelled():
            self.status.setText("Сканирование остановлено; предыдущий полный набор candidate relations сохранён")

    def render_scan_result(self, result: dict):
        self.scan_result = dict(result or {})
        unindexed = int(self.scan_result.get("unindexed_files", 0) or 0)
        root_issues = list(self.scan_result.get("filesystem_root_issues") or [])
        notices = []
        if unindexed:
            notices.append(
                f"В управляемых media-папках найдено неиндексированных файлов: {unindexed}. "
                "Они не хэшировались и не включались в удаляемые группы; сначала требуется переиндексация."
            )
        if root_issues:
            notices.append(f"Filesystem fallback не смог проверить корней: {len(root_issues)}.")
        self.scan_notice.setText("\n".join(notices))
        self.scan_notice.setVisible(bool(notices))
        self.render_groups(
            list(self.scan_result.get("groups") or []),
            total_groups=int(self.scan_result.get("total_groups", 0) or 0),
        )

    def render_groups(self, groups: list, *, total_groups: int | None = None):
        total = int(total_groups if total_groups is not None else len(groups))
        if not groups:
            suffix = f"; неиндексированных файлов: {int(self.scan_result.get('unindexed_files',0) or 0)}" if self.scan_result else ""
            self.status.setText("Визуальные пары не найдены" + suffix + ".")
            return
        self.pending_groups = list(groups[:250])
        self.render_index = 0
        self.status.setText(f"Найдено пар: {total}. Отрисовка списка...")
        self.render_timer.start(1)

    def render_next_batch(self):
        # Heavy QPixmap/card creation is split into small UI batches so the window
        # does not freeze for 30-60 seconds after the scan itself is finished.
        batch = 4
        end = min(len(self.pending_groups), self.render_index + batch)
        for g in self.pending_groups[self.render_index:end]:
            w = DuplicateGroupWidget(g, self.main.settings, self.on_deleted)
            self.group_widgets.append(w)
            self.body_lay.insertWidget(self.body_lay.count() - 1, w)
        self.render_index = end
        self.status.setText(f"Отрисовка групп: {self.render_index}/{len(self.pending_groups)}")
        if self.render_index >= len(self.pending_groups):
            self.render_timer.stop()
            total = int(self.scan_result.get("total_groups", len(self.pending_groups)) or 0)
            extra = ""
            if total > len(self.pending_groups):
                extra = f" (показаны первые {len(self.pending_groups)})"
            unindexed = int(self.scan_result.get("unindexed_files", 0) or 0)
            tail = f"; неиндексированных: {unindexed}" if unindexed else ""
            self.status.setText(f"Готово. Пар: {total}{extra}{tail}")

    def delete_all_checked(self):
        raw_plans = []
        for group_index, widget in enumerate(list(self.group_widgets), start=1):
            if not widget.isVisible():
                continue
            checked = widget.checked_cards()
            if not checked:
                continue
            checked_ids = {id(card) for card in checked}
            keep_cards = [
                card for card in widget.cards
                if id(card) not in checked_ids
                and Path(str(card.info.get("path", ""))).is_file()
            ]
            raw_plans.append({
                "group_index": group_index,
                "group_label": f"Группа {group_index}",
                "keep_path": str(keep_cards[0].info.get("path", "")) if keep_cards else "",
                "delete_paths": [str(card.info.get("path", "")) for card in checked],
            })

        if not raw_plans:
            QMessageBox.information(self, "Дубликаты", "Ничего не отмечено.")
            return

        validation = validate_duplicate_group_plans(raw_plans)
        if not validation.get("ok"):
            QMessageBox.warning(
                self,
                "Дубликаты",
                "Операция не выполнена:\n\n" + "\n".join(validation.get("errors") or []),
            )
            return

        plans = list(validation.get("plans") or [])
        if not plans:
            QMessageBox.information(self, "Дубликаты", "Ничего не отмечено.")
            return

        ok = QMessageBox.question(
            self,
            "Дубликаты",
            f"Переместить отмеченные файлы в «Удалено»?\n"
            f"Групп: {int(validation.get('groups', 0) or 0)}\n"
            f"Файлов: {int(validation.get('files', 0) or 0)}\n\n"
            "В каждой группе теги, источники, рейтинг и favorite будут объединены "
            "с оставленным файлом. Будет создана одна резервная копия базы.",
        )
        if ok != QMessageBox.Yes:
            return

        total = error_count = 0
        try:
            result = trash_duplicate_groups(self.main.settings, plans)
            total = int(result.get("deleted_files", 0) or 0)
            error_count = int(result.get("errors", 0) or 0)
        except Exception as exc:
            error_count = 1
            QMessageBox.warning(self, "Дубликаты", f"Удаление отменено или завершилось ошибкой:\n{exc}")

        if total:
            self.clear_results()
        self.on_deleted(total, error_count)

    def on_deleted(self, count: int, error_count: int = 0):
        msg = f"Перемещено в «Удалено»: {int(count or 0)}"
        if error_count:
            msg += f"; ошибок: {int(error_count or 0)}"
        self.status.setText(msg)
