from pathlib import Path
import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QSplitter, QMessageBox, QPlainTextEdit
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt

from core.nomatch_db import list_nomatches, remove_nomatch, set_manual_url, upsert_nomatch
from core.tagger import Tagger, unique_keep_order, filter_numeric_tags, merge_tag_groups, groups_to_tags, promote_manual_match, result_output_base
from ui.memory_tools import bounded_append, set_bounded_log


class NoMatchPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.items = []
        self.current_path = None

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.open_google_btn = QPushButton("Google fallback / Lens")
        self.google_info_btn = QPushButton("Инструкция Google")
        self.remove_btn = QPushButton("Remove from list")
        self.retry_btn = QPushButton("Retry NO_MATCH")
        top.addWidget(self.refresh_btn)
        top.addWidget(self.open_google_btn)
        top.addWidget(self.google_info_btn)
        top.addWidget(self.remove_btn)
        top.addWidget(self.retry_btn)
        top.addStretch(1)
        lay.addLayout(top)

        split = QSplitter()
        lay.addWidget(split, 1)

        left = QWidget()
        l = QVBoxLayout(left)
        self.list = QListWidget()
        l.addWidget(self.list, 1)
        split.addWidget(left)

        right = QWidget()
        r = QVBoxLayout(right)
        self.title = QLabel("NO_MATCH")
        self.title.setWordWrap(True)
        self.preview = QLabel("Preview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(360)
        self.preview.setStyleSheet("border:1px solid #2f3541;border-radius:8px")
        self.url = QLineEdit(); self.url.setClearButtonEnabled(True)
        self.url.setPlaceholderText("Paste any supported post URL here: rule34.xxx, rule34.us, gelbooru, e621, danbooru, custom site...")
        row = QHBoxLayout()
        self.apply_url_btn = QPushButton("Fetch tags from URL")
        self.open_file_btn = QPushButton("Open full image")
        row.addWidget(self.apply_url_btn)
        row.addWidget(self.open_file_btn)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        set_bounded_log(self.log, int(self.main.settings.get("max_console_lines", 2500)))
        self.log.setMaximumHeight(160)
        r.addWidget(self.title)
        r.addWidget(self.preview, 1)
        r.addWidget(self.url)
        r.addLayout(row)
        r.addWidget(self.log)
        split.addWidget(right)
        split.setSizes([420, 900])

        self.refresh_btn.clicked.connect(self.refresh)
        self.list.currentRowChanged.connect(self.select_row)
        self.list.itemClicked.connect(self.copy_selected_nomatch_name)
        self.apply_url_btn.clicked.connect(self.apply_manual_url)
        self.open_file_btn.clicked.connect(self.open_full)
        self.open_google_btn.clicked.connect(self.open_google)
        self.google_info_btn.clicked.connect(self.google_instruction)
        self.remove_btn.clicked.connect(self.remove_current)
        self.retry_btn.clicked.connect(self.retry_nomatch)

    def retranslate(self):
        pass

    def append_log(self, msg):
        bounded_append(self.log, msg, int(self.main.settings.get("max_console_lines", 2500)))

    def refresh(self):
        root = self.main.settings.get("root", "")
        items = list_nomatches(root, settings=self.main.settings)
        seen = {str(Path(x.get("path", "")).resolve()).lower() for x in items if x.get("path")}
        try:
            nm_media = result_output_base(self.main.settings) / "no_match" / "media"
            if nm_media.exists():
                for p in sorted(nm_media.iterdir()):
                    if p.is_file():
                        key = str(p.resolve()).lower()
                        if key not in seen:
                            items.append({"path": str(p), "name": p.name, "reason": "output_no_match", "ts": p.stat().st_mtime, "manual_url": ""})
                            seen.add(key)
        except Exception:
            pass
        self.items = items
        self.list.clear()
        for item in self.items:
            p = Path(item.get("path", ""))
            label = f"{p.name}"
            if item.get("manual_url"):
                label += "  [url]"
            li = QListWidgetItem(label)
            li.setToolTip(str(p))
            self.list.addItem(li)
        if self.items:
            self.list.setCurrentRow(0)
        else:
            self.current_path = None
            self.title.setText("NO_MATCH: empty")
            self.preview.setText("No items")
            self.preview.setPixmap(QPixmap())
            self.url.clear()

    def select_row(self, row):
        if row < 0 or row >= len(self.items):
            return
        item = self.items[row]
        p = Path(item.get("path", ""))
        self.current_path = p
        self.title.setText(str(p))
        self.url.setText(item.get("manual_url", ""))
        self.preview.setPixmap(QPixmap())
        if p.exists() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            pix = QPixmap(str(p))
            if not pix.isNull():
                self.preview.setPixmap(pix.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return
        self.preview.setText("Preview unavailable. Use Open full image.")

    def copy_selected_nomatch_name(self, item):
        try:
            from PySide6.QtWidgets import QApplication
            text = item.text().split("  [url]")[0].strip()
            QApplication.clipboard().setText(text)
            self.append_log(f"Скопировано имя NO_MATCH: {text}")
        except Exception:
            pass

    def open_full(self):
        if self.current_path and self.current_path.exists():
            try:
                import os
                os.startfile(str(self.current_path))
            except Exception as e:
                QMessageBox.warning(self, "Open error", str(e))

    def open_google(self):
        """
        Google fallback via br34. Full automatic parsing is intentionally kept as a
        browser-assisted workflow because Google Lens is JS-heavy and changes often.
        """
        try:
            from ui.login_browser import open_br34
            self.google_instruction()
            open_br34("https://lens.google.com/", self, log_func=self.append_log)
        except Exception as e:
            self.append_log(f"GOOGLE/LENS OPEN ERROR: {e}")

    def google_instruction(self):
        p = self.current_path
        self.append_log(
            "GOOGLE FALLBACK ИНСТРУКЦИЯ:\n"
            "1) Нажми Google fallback / Lens.\n"
            "2) В br34 откроется Google Lens.\n"
            "3) Нажми загрузку изображения и выбери текущий файл вручную.\n"
            "4) Найди страницу-источник: rule34/e621/gelbooru/danbooru/etc.\n"
            "5) Скопируй URL поста в поле URL search на этой вкладке.\n"
            "6) Нажми Fetch tags from URL.\n"
            f"Текущий файл: {p if p else 'не выбран'}\n"
            "Почему так: Google не даёт стабильный официальный reverse-image API для локального bulk-поиска; "
            "автоматический парсинг часто упирается в антибот и капчи. br34 оставляет рабочий ручной fallback."
        )

    def apply_manual_url(self):
        if not self.current_path:
            return
        url = self.url.text().strip()
        if not url:
            return
        try:
            self.main.settings["skip_existing"] = False
            tagger = Tagger(self.main.settings, self.append_log)
            self.append_log(f"URL SEARCH: {url}")
            tags = tagger.tags_from_url(url)
            groups = tagger.grouped_tags_from_url(url)
            if not tags and groups:
                tags = groups_to_tags(groups)
            tags = unique_keep_order(filter_numeric_tags(tags, self.main.settings.get("ignore_numeric_tags")))
            if not tags:
                self.append_log("NO TAGS: URL returned no supported/parseable tags. Check site enabled, URL is a post page, and login/cookies if needed.")
                return
            p = self.current_path
            if not groups_to_tags(groups):
                groups = {"artist": [], "character": [], "copyright": [], "general": tags, "meta": []}
            set_manual_url(p, url, settings=self.main.settings)
            promote_manual_match(self.main.settings, p, tags, url, groups)
            self.append_log(f"MANUAL TAGS → FOUND: {len(tags)}")
            self.refresh()
        except Exception as e:
            self.append_log(f"URL SEARCH ERROR: {type(e).__name__}: {e}")
            try:
                from core.tagger import append_error_log
                append_error_log(f"NO_MATCH URL SEARCH {self.url.text().strip()}: {type(e).__name__}: {e}")
            except Exception:
                pass

    def retry_nomatch(self):
        try:
            original_root = self.main.settings.get("root", "")
            nm_media = result_output_base(self.main.settings) / "no_match" / "media"

            if not nm_media.exists():
                self.append_log("NO_MATCH folder is empty")
                return

            self.append_log(f"RETRY NO_MATCH FOLDER: {nm_media}")

            # IMPORTANT:
            # Do NOT overwrite the user's main source folder permanently.
            # Retry uses a temporary worker settings copy only.
            retry_settings = self.main.settings.copy()
            retry_settings["root"] = str(nm_media)
            retry_settings["skip_existing"] = False
            retry_settings["tag_only_untagged"] = False
            retry_settings["retry_nomatch"] = True

            self.main.go("Tagger")

            # Keep visible source folder unchanged in UI.
            self.main.tagger_page.root.setText(str(original_root))

            self.main.tagger_page.log.clear()
            self.main.tagger_page.start.setEnabled(False)
            self.main.tagger_page.pause_btn.setEnabled(True)
            self.main.tagger_page.pause_btn.setChecked(False)
            self.main.tagger_page.stop_btn.setEnabled(True)

            from ui.tagger_page import TaggerWorker

            self.main.tagger_page.worker = TaggerWorker(retry_settings)

            self.main.tagger_page.worker.log.connect(
                self.main.tagger_page.append_log
            )
            self.main.tagger_page.worker.progress.connect(
                self.main.tagger_page.on_worker_progress
            )
            self.main.tagger_page.worker.current_file.connect(
                self.main.tagger_page.show_current_preview
            )
            self.main.tagger_page.worker.done.connect(
                self.main.tagger_page.on_worker_done
            )

            self.main.tagger_page.worker.start()

        except Exception as e:
            self.append_log(
                f"RETRY NO_MATCH ERROR: {type(e).__name__}: {e}"
            )

    def remove_current(self):
        if self.current_path:
            remove_nomatch(self.current_path, settings=self.main.settings)
            self.refresh()
