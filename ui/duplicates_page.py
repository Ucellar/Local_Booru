
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from collections import defaultdict

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QFrame, QCheckBox, QSizePolicy, QMessageBox, QDialog, QSpinBox
)

from core.tagger_engine import result_output_base
from core.settings import save_settings
from core.deleted_registry import record_deleted_file

MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
COPY_SUFFIX_RE = re.compile(r"\s*\((\d+)\)$")

try:
    from PIL import Image
except Exception:
    Image = None

try:
    import imagehash
except Exception:
    imagehash = None


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def image_size(path: Path) -> tuple[int, int]:
    if Image is None:
        return (0, 0)
    try:
        with Image.open(path) as img:
            return (int(img.width), int(img.height))
    except Exception:
        return (0, 0)


def perceptual_hash(path: Path) -> str:
    if Image is None:
        return ""
    try:
        with Image.open(path) as img:
            rgb = img.convert("RGB")
            if imagehash is not None:
                return str(imagehash.phash(rgb))
            # Fallback without imagehash: simple 8x8 average hash.
            small = rgb.resize((8, 8))
            vals = []
            for r, g, b in small.getdata():
                vals.append((r + g + b) // 3)
            avg = sum(vals) / max(1, len(vals))
            bits = "".join("1" if v >= avg else "0" for v in vals)
            return f"{int(bits, 2):016x}"
    except Exception:
        return ""


def phash_similarity(a: str, b: str) -> float | None:
    if not a or not b:
        return None
    try:
        d = bin(int(a, 16) ^ int(b, 16)).count("1")
        return max(0.0, 100.0 * (1.0 - (d / 64.0)))
    except Exception:
        return None


def base_without_copy_suffix(path_or_stem) -> str:
    stem = Path(str(path_or_stem)).stem if "." in str(path_or_stem) else str(path_or_stem)
    return COPY_SUFFIX_RE.sub("", stem).strip().lower()


def copy_suffix_number(path: Path) -> int | None:
    m = COPY_SUFFIX_RE.search(path.stem)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def sidecar_paths(media: Path) -> list[Path]:
    out = [
        media.with_suffix(".tags.json"),
        media.with_suffix(".tags.txt"),
        media.with_suffix(".txt"),
        media.with_suffix(".sources.txt"),
        Path(str(media) + ".tags.txt"),
        Path(str(media) + ".sources.txt"),
    ]
    try:
        if media.parent.name == "media":
            bucket = media.parent.parent
            out += [
                bucket / "tags" / f"{media.stem}.tags.json",
                bucket / "tags" / f"{media.stem}.tags.txt",
                bucket / "source" / f"{media.stem}.sources.txt",
                bucket / "searched" / f"{media.stem}.searched.json",
                bucket / "cache" / f"{media.stem}.raw.json",
            ]
    except Exception:
        pass
    return list(dict.fromkeys(out))


def delete_media_with_sidecars(path: Path) -> tuple[bool, str]:
    try:
        md5 = ""
        size = 0
        pixels = []
        try:
            size = path.stat().st_size if path.exists() else 0
        except Exception:
            size = 0
        try:
            md5 = file_md5(path) if path.exists() else ""
        except Exception:
            md5 = ""
        try:
            pixels = list(image_size(path)) if path.exists() else []
        except Exception:
            pixels = []

        # Remember exact deleted duplicates so APT does not later rescan the
        # same recreated "name (1).png" copy. Exact name+md5 matching means the
        # original "name.png" remains allowed.
        record_deleted_file(path, reason="duplicates_page_delete", md5=md5, size=size, pixels=pixels)

        for s in sidecar_paths(path):
            try:
                s.unlink(missing_ok=True)
            except Exception:
                pass
        path.unlink(missing_ok=True)
        try:
            from core.database.storage import delete_image_records
            delete_image_records({}, [path])
        except Exception:
            pass
        return True, str(path)
    except Exception as e:
        return False, f"{path}: {type(e).__name__}: {e}"


def collect_media_roots(settings: dict) -> list[Path]:
    base = result_output_base(settings)
    roots = [
        base / "found" / "media",
        base / "no_match" / "media",
        base / "downloads" / "found" / "media",
        base / "downloads" / "no_match" / "media",
    ]
    return [r for r in roots if r.exists()]


def scan_media(settings: dict) -> list[Path]:
    files = []
    for root in collect_media_roots(settings):
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                files.append(p)
    return sorted(set(files), key=lambda x: str(x).lower())


def item_info(path: Path) -> dict:
    try:
        bytes_size = path.stat().st_size
    except Exception:
        bytes_size = 0
    w, h = image_size(path)
    md5 = ""
    try:
        md5 = file_md5(path)
    except Exception:
        pass
    phash = perceptual_hash(path)
    return {
        "path": str(path),
        "name": path.name,
        "bytes": bytes_size,
        "pixels": [w, h],
        "md5": md5,
        "phash": phash,
        "base": base_without_copy_suffix(path),
        "copy_no": copy_suffix_number(path),
    }


def choose_keep_for_safe_copy(items: list[dict]) -> str:
    def score(it):
        p = Path(it["path"])
        copy_no = it.get("copy_no")
        # First keep unsuffixed, otherwise smallest suffix, then shortest path.
        return (copy_no is not None, copy_no if copy_no is not None else -1, len(str(p)), str(p).lower())
    return sorted(items, key=score)[0]["path"]


class DuplicateScanWorker(QThread):
    progress = Signal(str)
    finished_groups = Signal(list)

    def __init__(self, settings: dict, visual_threshold: int = 85):
        super().__init__()
        self.settings = dict(settings or {})
        self.visual_threshold = max(50, min(100, int(visual_threshold or 85)))

    def run(self):
        groups = []
        try:
            files = scan_media(self.settings)
            self.progress.emit(f"SCAN: файлов={len(files)}")

            infos = []
            for i, p in enumerate(files, 1):
                if i % 25 == 0:
                    self.progress.emit(f"SCAN: {i}/{len(files)}")
                infos.append(item_info(p))

            by_safe = defaultdict(list)
            for it in infos:
                px = tuple(it.get("pixels") or [0, 0])
                if it.get("md5") and it.get("bytes") and px != (0, 0):
                    by_safe[(it["md5"], it["base"], it["bytes"], px)].append(it)

            for key, xs in by_safe.items():
                if len(xs) > 1 and any(x.get("copy_no") is not None for x in xs):
                    keep = choose_keep_for_safe_copy(xs)
                    groups.append({
                        "reason": "Безопасный дубль: MD5 + имя без (1)/(2) + размер + пиксели совпадают",
                        "items": xs,
                        "safe": True,
                        "keep_path": keep,
                    })

            # Exact MD5 duplicates that are not just copy suffixes.
            by_md5 = defaultdict(list)
            for it in infos:
                if it["md5"]:
                    by_md5[it["md5"]].append(it)
            for md5, xs in by_md5.items():
                if len(xs) > 1:
                    groups.append({"reason": "Точный дубль: одинаковый MD5", "items": xs})

            # Same base name with Windows copy suffix.
            by_base = defaultdict(list)
            for it in infos:
                by_base[it["base"]].append(it)
            for b, xs in by_base.items():
                if len(xs) > 1 and any(x.get("copy_no") is not None for x in xs):
                    groups.append({"reason": "Похожее имя: отличается только (1)/(2)/(3)", "items": xs})

            # Same pixel dimensions but different binary file: possible censored/uncensored/edit.
            by_pixels = defaultdict(list)
            for it in infos:
                px = tuple(it.get("pixels") or [0, 0])
                if px != (0, 0):
                    by_pixels[px].append(it)
            for px, xs in by_pixels.items():
                md5s = {x.get("md5") for x in xs}
                if len(xs) > 1 and len(md5s) > 1:
                    groups.append({"reason": "Одинаковые пиксели, но разный файл: возможна цензура/другая версия", "items": xs})

            # Visual similarity. Default is strict enough to avoid garbage groups.
            threshold = float(self.visual_threshold)
            if True:
                for i, a in enumerate(infos):
                    if not a.get("phash"):
                        continue
                    local = [a]
                    best = 0.0
                    for b in infos[i + 1:]:
                        sim = phash_similarity(a.get("phash"), b.get("phash"))
                        if sim is not None and sim >= threshold:
                            local.append(b)
                            best = max(best, sim)
                    if len(local) > 1:
                        groups.append({
                            "reason": f"Похожее фото: визуальное совпадение примерно {best:.0f}%+ / порог {threshold:.0f}%",
                            "items": local,
                        })

            uniq = []
            seen = set()
            for g in groups:
                paths = tuple(sorted(x["path"] for x in g["items"]))
                # Keep safe group before generic duplicates.
                key = (paths, bool(g.get("safe")))
                if len(paths) < 2 or key in seen:
                    continue
                seen.add(key)
                uniq.append(g)

            uniq.sort(key=lambda g: (not g.get("safe", False), g.get("reason", "")))
            self.progress.emit(f"DONE: групп={len(uniq)}")
            self.finished_groups.emit(uniq)
        except Exception as e:
            self.progress.emit(f"SCAN ERROR: {type(e).__name__}: {e}")
            self.finished_groups.emit([])


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
    def __init__(self, group: dict, on_deleted):
        super().__init__()
        self.group = group
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
        delete_btn = QPushButton("Удалить отмеченные")
        skip_btn = QPushButton("Пропустить")
        buttons.addWidget(delete_btn)
        buttons.addWidget(skip_btn)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        delete_btn.clicked.connect(self.delete_checked)
        skip_btn.clicked.connect(self.hide)

    def checked_cards(self):
        return [c for c in self.cards if c.checkbox.isEnabled() and c.checkbox.isChecked()]

    def delete_checked(self):
        checked = self.checked_cards()
        if not checked:
            QMessageBox.information(self, "Дубликаты", "Ничего не отмечено.")
            return
        if len(checked) >= len([c for c in self.cards if c.checkbox.isEnabled()]):
            ok = QMessageBox.question(self, "Дубликаты", "Отмечены все удаляемые файлы в группе. Точно удалить?")
            if ok != QMessageBox.Yes:
                return
        deleted = []
        errors = []
        for card in checked:
            ok, msg = delete_media_with_sidecars(Path(card.info["path"]))
            if ok:
                deleted.append(msg)
                card.setVisible(False)
            else:
                errors.append(msg)
        self.on_deleted(len(deleted), errors)
        if not errors:
            self.hide()


class DuplicatesPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.worker = None
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

        info = QLabel("Сканируются: found, no_match, downloads/found, downloads/no_match. Клик по фото открывает крупный просмотр.")
        info.setWordWrap(True)
        root.addWidget(info)

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
        self.group_widgets.clear()
        while self.body_lay.count() > 1:
            item = self.body_lay.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self.status.setText("Список очищен.")

    def scan(self):
        if self.worker and self.worker.isRunning():
            self.status.setText("Сканирование уже идёт.")
            return
        self.clear_results()
        try:
            self.main.settings["duplicate_visual_threshold"] = int(self.threshold.value())
            save_settings(self.main.settings)
        except Exception:
            pass
        self.scan_btn.setEnabled(False)
        self.status.setText("Сканирование...")
        self.worker = DuplicateScanWorker(self.main.settings, self.threshold.value())
        self.worker.progress.connect(self.status.setText)
        self.worker.finished_groups.connect(self.render_groups)
        self.worker.start()

    def render_groups(self, groups: list):
        self.scan_btn.setEnabled(True)
        if not groups:
            self.status.setText("Дубликаты не найдены.")
            return
        self.pending_groups = list(groups[:250])
        self.render_index = 0
        self.status.setText(f"Найдено групп: {len(groups)}. Отрисовка списка...")
        self.render_timer.start(1)

    def render_next_batch(self):
        # Heavy QPixmap/card creation is split into small UI batches so the window
        # does not freeze for 30-60 seconds after the scan itself is finished.
        batch = 4
        end = min(len(self.pending_groups), self.render_index + batch)
        for g in self.pending_groups[self.render_index:end]:
            w = DuplicateGroupWidget(g, self.on_deleted)
            self.group_widgets.append(w)
            self.body_lay.insertWidget(self.body_lay.count() - 1, w)
        self.render_index = end
        self.status.setText(f"Отрисовка групп: {self.render_index}/{len(self.pending_groups)}")
        if self.render_index >= len(self.pending_groups):
            self.render_timer.stop()
            extra = ""
            if len(self.pending_groups) >= 250:
                extra = " (показаны первые 250 групп)"
            self.status.setText(f"Готово. Групп: {len(self.pending_groups)}{extra}")

    def delete_all_checked(self):
        total = 0
        errors = []
        for w in list(self.group_widgets):
            if not w.isVisible():
                continue
            checked = w.checked_cards()
            for card in checked:
                ok, msg = delete_media_with_sidecars(Path(card.info["path"]))
                if ok:
                    total += 1
                    card.setVisible(False)
                else:
                    errors.append(msg)
            if checked and not errors:
                w.hide()
        self.on_deleted(total, errors)

    def on_deleted(self, count: int, errors: list[str]):
        msg = f"Удалено: {count}"
        if errors:
            msg += f"; ошибок: {len(errors)}"
        self.status.setText(msg)
