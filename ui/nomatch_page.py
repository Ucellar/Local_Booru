from pathlib import Path
import json
import os
import shutil
import time
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QSplitter, QMessageBox, QPlainTextEdit, QComboBox
)
from PySide6.QtGui import QPixmap, QMovie, QImageReader
from PySide6.QtCore import Qt, QSize

from core.nomatch_db import (
    list_nomatches, remove_nomatch, set_manual_url, upsert_nomatch,
    update_nomatch_media_path, set_visual_status,
)
from core.tagger import Tagger, unique_keep_order, filter_numeric_tags, merge_tag_groups, groups_to_tags, promote_manual_match, result_output_base, copy_result_files
from core.image_safe import safe_thumbnail_path
from core.paths import suggested_settings_storage_dir
from ui.memory_tools import bounded_append, set_bounded_log


class NoMatchPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.items = []
        self.current_path = None
        self.preview_movie = None

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.refresh_btn = QPushButton("Обновить")
        self.open_google_btn = QPushButton("Поиск через Google Lens")
        self.google_info_btn = QPushButton("Инструкция Google")
        self.remove_btn = QPushButton("Убрать из списка")
        self.retry_btn = QPushButton("Повторить поиск")
        self.reason_filter = QComboBox()
        self.reason_filter.addItem("Все", "all")
        self.reason_filter.addItem("Не найдено", "no_match")
        self.reason_filter.addItem("Источник без тегов", "source_only")
        self.reason_filter.addItem("Файл отсутствует", "missing_file")
        self.visual_filter = QComboBox()
        self.visual_filter.addItem("Вид: все", "all")
        self.visual_filter.addItem("real", "real")
        self.visual_filter.addItem("booru", "booru")
        top.addWidget(self.refresh_btn)
        top.addWidget(self.open_google_btn)
        top.addWidget(self.google_info_btn)
        top.addWidget(self.remove_btn)
        top.addWidget(self.retry_btn)
        top.addWidget(self.reason_filter)
        top.addWidget(self.visual_filter)
        top.addStretch(1)
        lay.addLayout(top)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)

        self.ai_status = QLabel("")
        self.ai_status.setWordWrap(True)
        self.ai_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.ai_status.setStyleSheet("padding:6px 8px; border:1px solid #5b3350; border-radius:6px; background:#180812;")
        self.ai_download_btn = QPushButton("Скачать AI-модель")
        self.ai_download_btn.setMaximumWidth(170)
        self.ai_status_help_btn = QPushButton("Что с AI?")
        self.ai_status_help_btn.setMaximumWidth(120)
        ai_row = QHBoxLayout()
        ai_row.addWidget(self.ai_status, 1)
        ai_row.addWidget(self.ai_download_btn)
        ai_row.addWidget(self.ai_status_help_btn)
        lay.addLayout(ai_row)
        self.ai_status.hide()
        self.ai_download_btn.hide()
        self.ai_status_help_btn.hide()

        split = QSplitter()
        lay.addWidget(split, 1)

        left = QWidget()
        l = QVBoxLayout(left)
        self.list = QListWidget()
        l.addWidget(self.list, 1)
        split.addWidget(left)

        right = QWidget()
        r = QVBoxLayout(right)
        self.title = QLabel("Брак / Не найдено")
        self.title.setWordWrap(True)
        self.preview = QLabel("Предпросмотр")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(360)
        self.preview.setStyleSheet("border:1px solid #2f3541;border-radius:8px")
        self.url = QLineEdit(); self.url.setClearButtonEnabled(True)
        self.url.setPlaceholderText("Вставь ссылку на пост: rule34.xxx, rule34.us, gelbooru, e621, danbooru или свой сайт...")
        row = QHBoxLayout()
        self.apply_url_btn = QPushButton("Получить теги по ссылке")
        self.open_file_btn = QPushButton("Открыть файл")
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
        self.reason_filter.currentIndexChanged.connect(self.refresh)
        self.visual_filter.currentIndexChanged.connect(self.refresh)
        self.ai_status_help_btn.clicked.connect(self.show_ai_status_help)
        self.ai_download_btn.clicked.connect(self.start_ai_model_download)

    def retranslate(self):
        pass

    def append_log(self, msg):
        bounded_append(self.log, msg, int(self.main.settings.get("max_console_lines", 2500)))

    def update_ai_status(self):
        self._update_ai_status_banner()

    def _update_ai_status_banner(self):
        settings = self.main.settings or {}
        enabled = bool(settings.get("visual_nomatch_classify_enabled", True))
        backend = str(settings.get("visual_nomatch_backend", "clip_local") or "clip_local").strip().lower()
        if not enabled or backend not in ("clip_local", "clip", "ai"):
            self.ai_status.hide(); self.ai_download_btn.hide(); self.ai_status_help_btn.hide()
            return
        try:
            from core.visual_status import local_clip_model_state
            state = local_clip_model_state(settings)
        except Exception as exc:
            state = {"available": False, "deps_ok": False, "model_dir": "models/clip", "deps_error": f"{type(exc).__name__}: {exc}", "checked": []}
        if state.get("available") and state.get("deps_ok", True):
            self.ai_status.hide(); self.ai_download_btn.hide(); self.ai_status_help_btn.hide()
            return

        parts = []
        if state.get("download_active"):
            parts.append("скачивается " + str(state.get("download_text") or "AI-модель"))
        elif not state.get("available"):
            parts.append("модель ещё не скачана/не встроена")
        if not state.get("deps_ok", True):
            deps = ", ".join(state.get("missing_deps") or ["torch/transformers"])
            parts.append("AI runtime не установлен: " + deps)
        reason = "; ".join(parts) or "локальный AI недоступен"
        extra = ""
        if not state.get("deps_ok", True):
            extra = " Для запуска из исходников установи requirements_visual_ai.txt; для нормальной передачи другу нужен EXE, собранный с AI runtime."
        self.ai_status.setText(
            "NO_MATCH AI-классификация пока отключена: " + reason + ". "
            "Файлы архива никуда не отправляются; без готовой модели/рантайма спорные элементы остаются [вид ?]." + extra
        )
        self.ai_status.show(); self.ai_status_help_btn.show()
        self.ai_download_btn.setVisible(not bool(state.get("available")))
        self.ai_download_btn.setEnabled(not bool(state.get("download_active")))
        self.ai_download_btn.setText("Скачивается…" if state.get("download_active") else "Скачать AI-модель")

    def on_ai_model_download_progress(self, msg):
        try:
            self.ai_status.setText(str(msg))
            self.ai_status.show(); self.ai_status_help_btn.show()
            self.ai_download_btn.show(); self.ai_download_btn.setEnabled(False); self.ai_download_btn.setText("Скачивается…")
        except Exception:
            pass

    def start_ai_model_download(self):
        try:
            self.ai_download_btn.setEnabled(False)
            self.ai_status.setText("Скачиваю локальную AI-модель CLIP (~600 МБ). Это только загрузка весов; файлы архива никуда не отправляются.")
            handle = getattr(self.main, "ensure_local_clip_model_download", lambda force=False: None)(force=True)
            if handle is None:
                self.ai_download_btn.setEnabled(True)
                self.update_ai_status()
        except Exception as exc:
            self.ai_download_btn.setEnabled(True)
            QMessageBox.warning(self, "AI-модель", f"Не удалось начать скачивание:\n{exc}")

    def show_ai_status_help(self):
        settings = self.main.settings or {}
        try:
            from core.visual_status import local_clip_model_state
            state = local_clip_model_state(settings)
        except Exception:
            state = {"model_dir": "models/clip", "checked": [], "deps_error": ""}
        checked = "\n".join(str(x) for x in state.get("checked", [])[:8]) or "models/clip"
        missing = ", ".join(state.get("missing_files") or []) or "нет"
        dltxt = str(state.get("download_text") or "нет активной загрузки")
        deps = str(state.get("deps_error") or "OK")
        QMessageBox.information(
            self,
            "Локальная AI-модель NO_MATCH",
            "Классификация NO_MATCH работает локально: без API, без облака, без отправки картинок. "
            "Интернет нужен только один раз, чтобы скачать веса модели CLIP.\n\n"
            "Текущее состояние:\n"
            f"Модель: {state.get('model_dir') or 'models/clip'}\n"
            f"Загрузка: {dltxt}\n"
            f"Недостающие файлы: {missing}\n"
            f"Python: {state.get('python_version', '?')}\n"
            f"AI runtime: {deps}\n\n"
            "Для обычного человека правильный вариант — EXE/сборка, где torch/transformers уже встроены. "
            "Если запускать исходники через python app.py, надо один раз установить requirements_visual_ai.txt. "
            "На Python 3.14 torch может не установиться; для сборки AI-EXE лучше Python 3.10/3.11.\n\n"
            "Проверенные места модели:\n" + checked
        )

    def refresh(self):
        # Run in background — list_nomatches does Path.exists() for every item
        # which freezes UI with large no_match tables.
        try:
            self.list.setEnabled(False)
            self.main.task_manager.submit(
                self._load_nomatches_bg, self.main.settings,
                name="nomatch-refresh",
                on_result=self._on_nomatches_loaded,
                on_error=lambda e: (self.list.setEnabled(True),),
                on_finished=lambda: None,
            )
        except Exception:
            self._refresh_sync()

    def _load_nomatches_bg(self, settings):
        root = settings.get("root", "")
        items = list_nomatches(root, settings=settings)
        items = self._merge_output_nomatch_media(items, settings)
        items = self._fill_missing_visual_status(items, settings)
        return items

    @staticmethod
    def _safe_path_key(path_like):
        try:
            if not path_like:
                return ""
            return str(Path(path_like).resolve()).lower()
        except Exception:
            return str(path_like or "").lower()

    def _merge_output_nomatch_media(self, items, settings):
        # SQLite is the source of truth, but older/partial runs may leave files
        # in output/no_match/media without a DB row.  Merge them before filters,
        # otherwise real/booru filters can look empty even when files exist.
        merged = list(items or [])
        seen = set()
        for x in merged:
            for key_name in ("path", "media_path", "original_path"):
                key = self._safe_path_key(x.get(key_name) if isinstance(x, dict) else "")
                if key:
                    seen.add(key)
        try:
            nm_media = result_output_base(settings) / "no_match" / "media"
            if nm_media.exists():
                for p in sorted(nm_media.iterdir()):
                    if not p.is_file():
                        continue
                    key = self._safe_path_key(p)
                    if key in seen:
                        continue
                    merged.append({
                        "path": str(p),
                        "original_path": str(p),
                        "media_path": str(p),
                        "name": p.name,
                        "reason": "output_no_match",
                        "ts": p.stat().st_mtime,
                        "manual_url": "",
                        "visual_status": "",
                        "visual_confidence": 0.0,
                        "visual_model": "",
                        "visual_checked_at": 0,
                    })
                    seen.add(key)
        except Exception:
            pass
        return merged

    def _fill_missing_visual_status(self, items, settings):
        if not bool((settings or {}).get("visual_nomatch_classify_enabled", True)):
            return items
        try:
            from core.visual_status import classify_nomatch_if_enabled, current_visual_model_name, local_clip_model_state
        except Exception:
            return items
        try:
            backend = str((settings or {}).get("visual_nomatch_backend", "clip_local") or "clip_local").strip().lower()
            if backend in ("clip_local", "clip", "ai"):
                state = local_clip_model_state(settings)
                # If runtime/model is not ready, do not poison the DB with
                # unknown 0% rows.  Show the banner and retry after it is fixed.
                if not (state.get("available") and state.get("deps_ok", True)):
                    return items
        except Exception:
            return items
        filled = []
        for item in list(items or []):
            if not isinstance(item, dict):
                continue
            p = Path(str(item.get("path") or item.get("media_path") or item.get("original_path") or ""))
            status = str(item.get("visual_status") or "").strip().lower()
            model = str(item.get("visual_model") or "").strip()
            expected_model = current_visual_model_name(settings)
            # Recompute empty/old/wrong-backend rows.  Also recompute broken
            # v293/v294 cache entries: CLIP exceptions were saved as
            # "unknown 0%" with the normal model name, so they looked valid
            # forever after torch/model was later installed.
            valid_statuses = ("real", "booru", "unknown")
            conf = float(item.get("visual_confidence") or 0.0)
            model_is_error = model.endswith(":error") or ":error" in model
            stale_unknown_zero = (status == "unknown" and conf <= 0.001 and model.startswith("local_clip_photo_illustration"))
            needs_visual = (
                status not in valid_statuses
                or bool(model and model != expected_model)
                or model_is_error
                or stale_unknown_zero
            )
            if needs_visual and p.exists():
                try:
                    info = classify_nomatch_if_enabled(p, settings)
                    if info:
                        status2 = str(info.get("visual_status") or "").strip().lower()
                        if status2 in ("real", "booru", "unknown"):
                            item["visual_status"] = status2
                            item["visual_confidence"] = float(info.get("visual_confidence") or 0.0)
                            item["visual_model"] = str(info.get("visual_model") or "")
                            item["visual_checked_at"] = int(info.get("visual_checked_at") or 0)
                            item["visual_score"] = info.get("score")
                            item["photo_score"] = info.get("photo_score")
                            item["booru_score"] = info.get("booru_score")
                            item["visual_margin"] = info.get("margin")
                            item["visual_features"] = info.get("features")
                            item["visual_error"] = info.get("error")
                            # Persist only real classifier results.  Do not save
                            # transient runtime/model errors as durable unknown 0%.
                            if not info.get("error"):
                                # Persist if the row exists.  For pure filesystem fallback
                                # this UPDATE is harmless if no row matches.
                                set_visual_status(
                                    item.get("original_path") or item.get("media_path") or p,
                                    status2,
                                    float(item.get("visual_confidence") or 0.0),
                                    str(item.get("visual_model") or ""),
                                    int(item.get("visual_checked_at") or 0),
                                    settings=settings,
                                )
                except Exception as exc:
                    item["visual_error"] = f"{type(exc).__name__}: {exc}"
            filled.append(item)
        return filled

    def _on_nomatches_loaded(self, items):
        self.list.setEnabled(True)
        self._apply_loaded_items(items)

    def _refresh_sync(self):
        items = self._load_nomatches_bg(self.main.settings)
        self._apply_loaded_items(items)

    def _apply_loaded_items(self, items):
        self._update_ai_status_banner()
        settings = self.main.settings
        root = settings.get("root", "")
        selected_reason = "all"
        try:
            selected_reason = str(self.reason_filter.currentData() or "all")
        except Exception:
            selected_reason = "all"
        if selected_reason == "source_only":
            items = [x for x in items if str(x.get("reason") or "") == "source_only"]
        elif selected_reason == "no_match":
            items = [x for x in items if str(x.get("reason") or "no_match") in ("no_match", "output_no_match", "legacy_cache")]
        elif selected_reason == "missing_file":
            items = [x for x in items if x.get("file_missing")]
        try:
            selected_visual = str(self.visual_filter.currentData() or "all")
        except Exception:
            selected_visual = "all"
        if selected_visual in ("real", "booru"):
            items = [x for x in items if str(x.get("visual_status") or "").lower() == selected_visual]
        self.items = items
        self.list.clear()
        all_count = len(items or [])
        real_count = sum(1 for x in items if str(x.get("visual_status") or "").lower() == "real")
        booru_count = sum(1 for x in items if str(x.get("visual_status") or "").lower() == "booru")
        unknown_count = sum(1 for x in items if str(x.get("visual_status") or "").lower() in ("", "unknown"))
        try:
            self.summary.setText(f"Показано: {all_count}   real: {real_count}   booru: {booru_count}   ?: {unknown_count}")
        except Exception:
            pass
        fallback_count = 0
        missing_count = 0
        for item in self.items:
            p = Path(item.get("path", ""))
            label = f"{p.name}"
            if item.get("fallback_to_original"):
                label += "  [восстановить копию]"
                fallback_count += 1
            elif item.get("file_missing"):
                label += "  [файл отсутствует]"
                missing_count += 1
            if item.get("reason") == "source_only":
                sim = float(item.get("source_similarity") or 0)
                suffix = f"источник {sim:.0f}%" if sim else "источник без тегов"
                label += f"  [{suffix}]"
            visual_status = str(item.get("visual_status") or "").lower()
            if visual_status in ("real", "booru"):
                conf = float(item.get("visual_confidence") or 0.0) * 100.0
                label += f"  [{visual_status} {conf:.0f}%]"
            else:
                label += "  [вид ?]"
            if item.get("manual_url"):
                label += "  [ссылка]"
            li = QListWidgetItem(label)
            stale = str(item.get("media_path", "") or "")
            tooltip = str(p)
            if item.get("fallback_to_original"):
                tooltip += f"\nСтарая копия NO_MATCH отсутствует: {stale}"
            if item.get("source_label") or item.get("source_url"):
                tooltip += f"\nНайден неподдерживаемый источник: {item.get('source_label','')}"
                if item.get("source_url"):
                    tooltip += f"\n{item.get('source_url')}"
            if item.get("visual_status") and str(item.get("visual_status") or "").lower() != "unknown":
                tooltip += f"\nВизуальный статус: {item.get('visual_status')} {float(item.get('visual_confidence') or 0.0) * 100:.0f}%"
                if item.get("visual_model"):
                    tooltip += f"\nМодель: {item.get('visual_model')}"
                if item.get("visual_score") is not None:
                    tooltip += f"\nСчёт real: {item.get('visual_score')}"
                if isinstance(item.get("visual_features"), dict):
                    feat = item.get("visual_features") or {}
                    compact = ", ".join(f"{k}={float(v):.3f}" for k, v in feat.items() if isinstance(v, (int, float)))
                    if compact:
                        tooltip += f"\nПризнаки: {compact}"
            else:
                if str(item.get("visual_status") or "").lower() == "unknown":
                    tooltip += "\nВизуальный статус: не уверен"
                    if item.get("visual_model"):
                        tooltip += f"\nМодель: {item.get('visual_model')}"
                    if item.get("photo_score") is not None or item.get("booru_score") is not None:
                        tooltip += f"\nФото: {float(item.get('photo_score') or 0.0) * 100:.0f}% / Рисунок: {float(item.get('booru_score') or 0.0) * 100:.0f}%"
                else:
                    tooltip += "\nВизуальный статус: не проверено"
            if item.get("visual_error"):
                tooltip += f"\nОшибка визуальной проверки: {item.get('visual_error')}"
            li.setToolTip(tooltip)
            self.list.addItem(li)
        if fallback_count:
            self.append_log(f"Найдено записей брака без архивной копии: {fallback_count}. Для предпросмотра используется исходный файл; при повторном поиске копии будут восстановлены в текущем output.")
        if missing_count:
            self.append_log(f"Записей брака без доступного файла: {missing_count}. Их можно убрать из списка.")
        if self.items:
            self.list.setCurrentRow(0)
        else:
            self.current_path = None
            self.title.setText("Брак: список пуст")
            self._clear_preview("Нет файлов")
            self.url.clear()

    def _clear_preview(self, text=""):
        if self.preview_movie is not None:
            try:
                self.preview_movie.stop()
            except Exception:
                pass
            try:
                self.preview.setMovie(None)
            except Exception:
                pass
            try:
                self.preview_movie.deleteLater()
            except Exception:
                pass
            self.preview_movie = None
        self.preview.setPixmap(QPixmap())
        self.preview.setText(text)

    def _preview_target_size(self, fallback_w=900, fallback_h=620):
        size = self.preview.size()
        w = max(64, size.width() or fallback_w)
        h = max(64, size.height() or fallback_h)
        return QSize(w - 10, h - 10)

    def _show_image_preview(self, p: Path) -> bool:
        pix = QPixmap(str(p))
        if pix.isNull():
            return False
        self._clear_preview("")
        self.preview.setPixmap(pix.scaled(self._preview_target_size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        return True

    def _show_gif_preview(self, p: Path) -> bool:
        movie = QMovie(str(p))
        if not movie.isValid():
            return False
        target = self._preview_target_size()
        try:
            reader = QImageReader(str(p))
            base = reader.size()
            if base.isValid() and base.width() > 0 and base.height() > 0:
                target = base.scaled(target, Qt.KeepAspectRatio)
        except Exception:
            pass
        self._clear_preview("")
        try:
            movie.setCacheMode(QMovie.CacheAll)
            movie.setScaledSize(target)
        except Exception:
            pass
        self.preview.setMovie(movie)
        self.preview_movie = movie
        movie.start()
        return True

    def _show_video_preview(self, p: Path) -> bool:
        # NO_MATCH is a triage screen, not the full media player.  A reliable
        # first-frame preview is enough here and works even when QtMultimedia
        # codecs are missing.  Full playback remains available through
        # «Открыть файл» / the post viewer.
        target = self._preview_target_size()
        thumb = safe_thumbnail_path(p, max(256, target.width()), max(256, target.height()))
        if not thumb:
            return False
        return self._show_image_preview(Path(thumb))

    def select_row(self, row):
        if row < 0 or row >= len(self.items):
            return
        item = self.items[row]
        p = Path(item.get("path", ""))
        self.current_path = p
        visual_status = str(item.get("visual_status") or "").lower()
        if visual_status in ("real", "booru"):
            visual_line = f"\nВид: {item.get('visual_status')} {float(item.get('visual_confidence') or 0.0) * 100:.0f}%"
            if item.get("visual_model"):
                visual_line += f"  [{item.get('visual_model')}]"
            if item.get("visual_score") is not None:
                visual_line += f"  score={item.get('visual_score')}"
        elif visual_status == "unknown":
            visual_line = f"\nВид: не уверен {float(item.get('visual_confidence') or 0.0) * 100:.0f}%"
            if item.get("visual_model"):
                visual_line += f"  [{item.get('visual_model')}]"
            if item.get("photo_score") is not None or item.get("booru_score") is not None:
                visual_line += f"  photo={float(item.get('photo_score') or 0.0) * 100:.0f}% booru={float(item.get('booru_score') or 0.0) * 100:.0f}%"
        else:
            visual_line = "\nВид: не проверено"
        if item.get("fallback_to_original"):
            self.title.setText(f"{p}\nКопия в архиве отсутствует; показан исходный файл{visual_line}")
        elif item.get("reason") == "source_only":
            label = item.get("source_label") or "найден неподдерживаемый источник"
            self.title.setText(f"{p}\nИсточник найден, но теги не получены: {label}{visual_line}")
        else:
            self.title.setText(str(p) + visual_line)
        self.url.setText(item.get("manual_url") or item.get("source_url") or "")
        self._clear_preview("")
        self.open_file_btn.setEnabled(p.exists())
        if not p.exists():
            self._clear_preview("Файл отсутствует")
            return
        suffix = p.suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp"} and self._show_image_preview(p):
            return
        if suffix == ".gif" and self._show_gif_preview(p):
            return
        if suffix in {".mp4", ".webm", ".mkv", ".mov", ".avi"} and self._show_video_preview(p):
            return
        self._clear_preview("Предпросмотр недоступен. Открой файл полностью.")

    def copy_selected_nomatch_name(self, item):
        try:
            from PySide6.QtWidgets import QApplication
            text = item.text().split("  [")[0].strip()
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
                QMessageBox.warning(self, "Ошибка открытия", str(e))

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
            "1) Нажми «Поиск через Google Lens».\n"
            "2) В br34 откроется Google Lens.\n"
            "3) Нажми загрузку изображения и выбери текущий файл вручную.\n"
            "4) Найди страницу-источник: rule34/e621/gelbooru/danbooru/etc.\n"
            "5) Скопируй ссылку поста в поле на этой вкладке.\n"
            "6) Нажми «Получить теги по ссылке».\n"
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
                self.append_log("ТЕГИ НЕ НАЙДЕНЫ: ссылка не дала поддерживаемые теги. Проверь сайт, ссылку на пост, вход и cookies.")
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

    def _restore_missing_nomatch_copies(self, only_items=None):
        """Rebuild missing/old NO_MATCH output copies in the active archive on demand.

        ``only_items`` is important for the NO_MATCH visual filters: if the user
        has selected ``real`` or ``booru``, repeat search must not silently restore
        and enqueue the whole no_match folder again.
        """
        active_media = result_output_base(self.main.settings) / "no_match" / "media"
        restored = 0
        source_items = list(only_items) if only_items is not None else list_nomatches(self.main.settings.get("root", ""), settings=self.main.settings)
        for item in source_items:
            original_raw = str(item.get("original_path", "") or "").strip()
            visible_raw = str(item.get("path", "") or "").strip()
            original = Path(original_raw) if original_raw else None
            visible = Path(visible_raw) if visible_raw else None
            stored_media = Path(item.get("media_path", "") or "") if item.get("media_path") else None
            source = original if original is not None and original.exists() else visible
            if source is None or not source.exists():
                continue
            already_active = False
            if stored_media and stored_media.exists():
                try:
                    already_active = stored_media.resolve().is_relative_to(active_media.resolve())
                except Exception:
                    already_active = str(stored_media.resolve()).lower().startswith(str(active_media.resolve()).lower())
            if already_active:
                continue
            rebuilt = copy_result_files(self.main.settings, source, "nomatch")
            if rebuilt and Path(rebuilt).exists():
                update_nomatch_media_path(original if original is not None else source, rebuilt, settings=self.main.settings)
                # Keep the in-memory row useful for the queue builder below.
                try:
                    item["media_path"] = str(rebuilt)
                    item["path"] = str(rebuilt)
                    item["fallback_to_original"] = False
                    item["file_missing"] = False
                except Exception:
                    pass
                restored += 1
        if restored:
            self.append_log(f"Восстановлено копий брака в текущем архиве: {restored}")
        return restored

    def _selected_filter_label(self):
        try:
            reason = str(self.reason_filter.currentText() or "Все")
        except Exception:
            reason = "Все"
        try:
            visual = str(self.visual_filter.currentData() or "all")
        except Exception:
            visual = "all"
        if visual == "all":
            visual_label = "вид: все"
        else:
            visual_label = f"вид: {visual}"
        return f"{reason}; {visual_label}"

    def _retry_source_path_for_item(self, item):
        """Return a readable file path for one visible NO_MATCH row."""
        candidates = [
            item.get("path"),
            item.get("media_path"),
            item.get("original_path"),
        ]
        for raw in candidates:
            if not raw:
                continue
            try:
                p = Path(str(raw))
                if p.exists() and p.is_file():
                    return p
            except Exception:
                continue
        return None

    def _prepare_retry_nomatch_queue(self, items):
        """Create a temporary root containing exactly the currently displayed rows.

        TaggerWorker accepts a root folder, not an arbitrary list of files, so we
        stage hardlinks/copies under settings/cache/nomatch_retry_queue.  This
        makes the Retry button obey the NO_MATCH filters: ``booru`` retries only
        booru rows, ``real`` retries only real rows, and ``Все`` retries exactly
        the currently displayed list.
        """
        selected = list(items or [])
        if not selected:
            return None, 0, []
        queue_root = suggested_settings_storage_dir(self.main.settings) / "cache" / "nomatch_retry_queue"
        try:
            shutil.rmtree(queue_root, ignore_errors=True)
        except Exception:
            pass
        queue_root.mkdir(parents=True, exist_ok=True)

        used_names = set()
        manifest = []
        queued = 0
        skipped = []
        for item in selected:
            src = self._retry_source_path_for_item(item)
            if src is None:
                skipped.append(str(item.get("name") or item.get("path") or item.get("original_path") or "<unknown>"))
                continue
            name = src.name or "file"
            stem = src.stem or "file"
            suffix = src.suffix
            dest_name = name
            idx = 2
            while dest_name.lower() in used_names or (queue_root / dest_name).exists():
                dest_name = f"{stem}__retry{idx}{suffix}"
                idx += 1
            used_names.add(dest_name.lower())
            dst = queue_root / dest_name
            try:
                try:
                    os.link(src, dst)
                except Exception:
                    shutil.copy2(src, dst)
                queued += 1
                manifest.append({
                    "queue_file": str(dst),
                    "source_file": str(src),
                    "original_path": str(item.get("original_path") or ""),
                    "media_path": str(item.get("media_path") or ""),
                    "visual_status": str(item.get("visual_status") or ""),
                    "visual_confidence": float(item.get("visual_confidence") or 0.0),
                    "reason": str(item.get("reason") or ""),
                })
            except Exception as exc:
                skipped.append(f"{src}: {type(exc).__name__}: {exc}")
        try:
            (queue_root / "_retry_nomatch_manifest.json").write_text(json.dumps({
                "created_at": int(time.time()),
                "filter": self._selected_filter_label(),
                "count": queued,
                "items": manifest,
                "skipped": skipped,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return queue_root, queued, skipped

    def retry_nomatch(self):
        try:
            original_root = self.main.settings.get("root", "")
            selected_items = list(self.items or [])
            if not selected_items:
                self.append_log("Повторный поиск: текущий отфильтрованный список пуст")
                return

            # Restore only currently visible rows.  Previously this restored the
            # entire no_match folder, which made the visual filter useless for
            # retry actions.
            self._restore_missing_nomatch_copies(selected_items)
            queue_root, queued, skipped = self._prepare_retry_nomatch_queue(selected_items)

            if not queue_root or queued <= 0:
                self.append_log("Повторный поиск: нет доступных файлов в текущем фильтре")
                if skipped:
                    self.append_log("Пропущено: " + "; ".join(skipped[:8]))
                return

            filter_label = self._selected_filter_label()
            self.append_log(f"ПОВТОРНЫЙ ПОИСК БРАКА: фильтр [{filter_label}], файлов: {queued}, очередь: {queue_root}")
            if skipped:
                self.append_log(f"Пропущено недоступных файлов: {len(skipped)}")

            # IMPORTANT:
            # Do NOT overwrite the user's main source folder permanently.
            # Retry uses a temporary worker settings copy only.  The temporary
            # root contains exactly the currently filtered NO_MATCH rows.
            retry_settings = self.main.settings.copy()
            retry_settings["root"] = str(queue_root)
            retry_settings["skip_existing"] = False
            retry_settings["tag_only_untagged"] = False
            retry_settings["retry_nomatch"] = True
            retry_settings["retry_nomatch_filter"] = filter_label
            retry_settings["retry_nomatch_count"] = queued
            retry_settings["retry_nomatch_queue_root"] = str(queue_root)

            # Booru-only NO_MATCH retry may contain files whose filename starts
            # with a rule34 40-hex image key while the local byte-MD5 is different.
            # Do NOT brute-force /samples/<bucket>/sample_<key>.* here.  The user
            # explicitly rejected the sample/bucket path.  Use only the direct
            # hotlink form:
            #   https://hl.rule34.xxx/public/hotlink.php?img=<40hex>.png
            try:
                selected_visual = str(self.visual_filter.currentData() or "all").lower()
            except Exception:
                selected_visual = "all"
            if selected_visual == "booru":
                retry_settings["rule34_image_key_locator_mode"] = "hotlink_only"
                retry_settings["rule34_image_key_bucket_probe_enabled"] = False
                retry_settings["rule34_image_key_bucket_probe_sequence"] = ""
                retry_settings["rule34_image_key_bucket_probe_max"] = 0
                retry_settings["rule34_image_key_bucket_probe_step"] = 0
                retry_settings["rule34_image_key_hotlink_extensions"] = "png"
                retry_settings["rule34_image_key_bucket_request_timeout"] = 5.0
                retry_settings["rule34_image_key_bucket_total_timeout"] = 25.0
                self.append_log(
                    "Повторный поиск booru: rule34.xxx image-key locator работает "
                    "только через hotlink.php?img=<40hex>.png; sample/bucket перебор отключён"
                )

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
                f"ОШИБКА ПОВТОРНОГО ПОИСКА БРАКА: {type(e).__name__}: {e}"
            )

    def remove_current(self):
        if self.current_path:
            remove_nomatch(self.current_path, settings=self.main.settings)
            self.refresh()
