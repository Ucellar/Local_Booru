from pathlib import Path
from urllib.parse import urlparse
import json
import re
import time
import webbrowser
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,QPlainTextEdit,QProgressBar,QCheckBox,QDoubleSpinBox,QSpinBox,QLineEdit,QFileDialog,QGroupBox,QFormLayout,QSplitter,QTableWidget,QTableWidgetItem,QComboBox,QHeaderView,QMessageBox,QAbstractItemView,QSizePolicy,QStackedWidget,QScrollArea,QGridLayout
from PySide6.QtCore import QThread, Signal, Qt, QTimer
from core.settings import save_settings, DEFAULT_SITES
from core.tagger import Tagger, MEDIA_EXTS, video_frame_image, output_processed_status, result_output_base, result_paths_for, has_copy_suffix, is_md5, file_md5, file_phash
from ui.login_browser import LoginBrowserDialog, open_br34, open_br34_multi
from ui.sites_widget import SitesWidget
from ui.memory_tools import bounded_append, set_bounded_log, soft_gc
from core.memory_guard import process_memory_snapshot, format_snapshot, soft_trim_memory
from core.deleted_registry import should_skip_deleted_file, has_deleted_record_for_name
from core.paths import BROWSER_PROFILE_DIR, BROWSER_COOKIES_DIR
from ui.tagger.workers import TaggerWorker, BrowserLoginWorker




# Parser changes that must rescan only one source without replaying every lane.
# The suffix is part of the internal journal identity, not a visible domain.




def _ui_normalize_url(url: str):
    url = (url or "").strip().strip('\"\'')
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("//"):
        return "https:" + url
    if "." in url and not any(ch.isspace() for ch in url):
        return "https://" + url
    return None


class ActivityPreviewLabel(QLabel):
    """Small parser preview cell: only this widget opens the file on double-click."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._open_callback = None
        try:
            self.setCursor(Qt.PointingHandCursor)
        except Exception:
            pass

    def set_open_callback(self, callback):
        self._open_callback = callback

    def mouseDoubleClickEvent(self, event):
        try:
            if event.button() == Qt.LeftButton and self._open_callback is not None:
                event.accept()
                self._open_callback()
                return
        except Exception:
            pass
        try:
            super().mouseDoubleClickEvent(event)
        except Exception:
            pass





class TaggerPage(QWidget):
    def __init__(self, main):
        super().__init__(); self.main=main; self.worker=None; self.browser_worker=None
        self._parser_done_signal_seen=False
        self._parser_worker_finished_seen=False
        self._parser_done_finalized=False
        self._drop_explicit_paths=[]
        self.setAcceptDrops(True)
        lay=QVBoxLayout(self); lay.setContentsMargins(8, 8, 8, 8); lay.setSpacing(6); split=QSplitter(); lay.addWidget(split,3)
        left=QWidget(); left_lay=QHBoxLayout(left); left_lay.setContentsMargins(0, 0, 6, 0); left_lay.setSpacing(8); self.form_left=QFormLayout(); self.form_right=QFormLayout(); left_lay.addLayout(self.form_left,1); left_lay.addLayout(self.form_right,1); self._form_col=0
        row=QHBoxLayout(); self.root=QLineEdit(); self.choose_btn=QPushButton(); self.choose_btn.clicked.connect(self.choose); row.addWidget(self.root,1); row.addWidget(self.choose_btn)
        self.api=QLineEdit(); self.api.setEchoMode(QLineEdit.Password)
        self.sauce_state=QLabel("Нет данных")
        self.sauce_state.setWordWrap(True)
        self.min_sim=QDoubleSpinBox(); self.min_sim.setRange(50,99); self.min_sim.setSingleStep(0.5)
        self.skip=QCheckBox(); self.only_untagged=QCheckBox(); self.skip_copy_suffix=QCheckBox(); self.md5=QCheckBox(); self.sauce=QCheckBox(); self.ascii2d=QCheckBox()
        self.iqdb=QCheckBox(); self.danbooru_iqdb=QCheckBox(); self.e621_iqdb=QCheckBox(); self.tineye=QCheckBox(); self.low_power=QCheckBox(); self.bg_rule34_categories=QCheckBox()
        # v204: FuzzySearch/Fluffle removed from active parser UI/queue.
        # Hidden legacy widgets remain only so old layouts/settings code cannot crash.
        self.fuzzysearch=QCheckBox(); self.fluffle=QCheckBox()
        self.fuzzy_key=QLineEdit(); self.fuzzy_key.setEchoMode(QLineEdit.Password)
        self.fluffle_key=QLineEdit(); self.fluffle_key.setEchoMode(QLineEdit.Password)
        self.tineye_key=QLineEdit(); self.tineye_key.setEchoMode(QLineEdit.Password)
        # API endpoints are intentionally not exposed in the main parser form.
        # They are stable service defaults; the clickable service name opens the
        # key/API page instead of wasting UI rows on raw URLs.
        self.fuzzy_endpoint=QLineEdit(); self.fluffle_endpoint=QLineEdit()
        self.api.setPlaceholderText("API key")
        self.fuzzy_key.setPlaceholderText("API key не нужен")
        self.fuzzy_key.setVisible(False)
        self.fluffle_key.setPlaceholderText("API key не нужен")
        self.fluffle_key.setVisible(False)
        self.tineye_key.setPlaceholderText("TinEye API key")
        self.tineye_key.setVisible(False)
        self.site_interval=QDoubleSpinBox(); self.site_interval.setRange(1.10, 30.0); self.site_interval.setDecimals(2); self.site_interval.setSingleStep(0.10); self.site_interval.setSuffix(" с")
        self.conveyor_window=QSpinBox(); self.conveyor_window.setRange(2,128)
        # Keep bare indicators compact, but leave enough room for QSS borders.
        # Old fixedWidth(20) clipped 17px indicators with 2px borders in dark themes.
        for _cb in [self.skip,self.only_untagged,self.skip_copy_suffix,self.md5,
                    self.sauce,self.ascii2d,self.iqdb,self.danbooru_iqdb,self.e621_iqdb,self.tineye,self.low_power,self.bg_rule34_categories]:
            _cb.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            _cb.setFixedSize(23, 23)
        self.iqdb_min=QDoubleSpinBox(); self.iqdb_min.setRange(50,99); self.iqdb_min.setSingleStep(0.5)
        self.delay=QDoubleSpinBox(); self.delay.setRange(0,120); self.limit=QSpinBox(); self.limit.setRange(0,1000000); self.req_timeout=QSpinBox(); self.req_timeout.setRange(5,300); self.sauce_cooldown=QSpinBox(); self.sauce_cooldown.setRange(1,1440)
        self.form_rows=[]
        self.add_tip_row("Folder", row, "tip_root")
        self.add_api_service_row("SauceNAO", self.sauce, self.api, "tip_sauce_service", "saucenao")
        self.add_api_service_row("Danbooru IQDB", self.danbooru_iqdb, None, "tip_danbooru_iqdb", "danbooru_iqdb")
        self.add_api_service_row("TinEye", self.tineye, None, "tip_tineye", "tineye")
        # User-facing parser settings are intentionally compact.  Defaults/blueprints own
        # local throttling, fallback timing and background maintenance.  Hidden legacy
        # widgets remain only so older settings files and code paths keep working.
        for label,w,tip in [("SauceNAO состояние",self.sauce_state,"tip_saucenao_state"),("SauceNAO min similarity",self.min_sim,"tip_min_similarity"),("MD5 lookup",self.md5,"tip_md5"),("IQDB fuzzy fallback",self.iqdb,"tip_iqdb"),("e621 IQDB fallback",self.e621_iqdb,"tip_e621_iqdb"),("IQDB min similarity",self.iqdb_min,"tip_iqdb"),("Ascii2D fallback",self.ascii2d,"tip_ascii2d"),("Skip existing",self.skip,"tip_skip"),("Skip files ending (1)/(2)",self.skip_copy_suffix,"tip_skip_copy_suffix")]: self.add_tip_row(label,w,tip)
        # Hidden blueprint-owned settings.  Do not confuse these with the runtime
        # parser PAUSE button: Start/Stop + Pause/Resume remain user-facing controls.
        for _hidden in (self.only_untagged, self.bg_rule34_categories, self.low_power, self.site_interval, self.conveyor_window, self.delay, self.req_timeout, self.sauce_cooldown, self.limit):
            _hidden.setVisible(False)
        split.addWidget(left)
        right=QWidget(); rlay=QVBoxLayout(right); rlay.setContentsMargins(6, 0, 0, 0); rlay.setSpacing(4)
        self.sites_widget = SitesWidget()
        # Действия выбранного сайта открываются через контекстное меню таблицы.
        # Настройки всей страницы сохраняются единой нижней кнопкой страницы.
        self.sites_widget.login_selected_requested.connect(self.open_selected_login)
        self.sites_widget.all_login_btn.clicked.connect(self.open_all_logins)
        rlay.addWidget(self.sites_widget)
        split.addWidget(right); split.setSizes([520,820])
        row2=QHBoxLayout(); row2.setContentsMargins(0, 0, 0, 0); row2.setSpacing(6); self.save_btn=QPushButton(); self.save_btn.clicked.connect(self.sync); self.start=QPushButton(); self.start.setObjectName("ParserStartStopButton"); self.start.clicked.connect(self.run_or_stop); self.pause_btn=QPushButton("PAUSE"); self.pause_btn.setObjectName("ParserPauseButton"); self.pause_btn.setCheckable(True); self.pause_btn.clicked.connect(self.pause_resume); self.pause_btn.setEnabled(False); self.pause_btn.setVisible(True); self.stop_btn = QPushButton(); self.stop_btn.setObjectName("ParserStopButton"); self.stop_btn.clicked.connect(self.stop); self.stop_btn.setEnabled(False); self.stop_btn.setVisible(False);
        for _btn in (self.save_btn, self.start, self.pause_btn):
            _btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            row2.addWidget(_btn, 1)
        lay.addLayout(row2)
        self.progress=QProgressBar(); lay.addWidget(self.progress)
        self.console_preview_split = QSplitter(Qt.Horizontal)
        self._log_channel_buffers = {}
        self._log_channel_widgets = {}
        self._log_channel_meta = {}
        self._active_log_channels = []
        self._active_log_channel_set = set()
        self.console_panel = self._build_console_panel()
        # Большое одиночное превью в парсере больше не показываем: оно жрало место
        # и дублировало маленькие превью в таблице активных очередей.  Объект
        # оставлен скрытым только для совместимости со старым кодом show_current_preview.
        self.preview_box=QLabel(""); self.preview_box.setAlignment(Qt.AlignCenter); self.preview_box.setVisible(False); self.preview_box.setMaximumWidth(0)
        self.console_preview_split.addWidget(self.console_panel)
        self.console_preview_split.setSizes([1180])
        lay.addWidget(self.console_preview_split,2)
        self._last_site_table = None
        self._last_site_row = -1
        # v339: queued Qt log signals are cheap only if the slot is cheap.
        # Batch real QTextDocument appends on a timer so the UI stays responsive
        # during high-throughput parser bursts.
        self._pending_log_lines = []
        self._pending_log_dropped = 0
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setInterval(int(self.main.settings.get("tagger_ui_log_flush_interval_ms", 120) or 120))
        self._log_flush_timer.timeout.connect(self._flush_pending_logs)
        self._log_flush_timer.start()
        self.low_power.toggled.connect(self.update_preview_visibility)
        self.load_values(); self.retranslate(); self.update_preview_visibility()
        self._build_drag_overlay()

    def _build_drag_overlay(self):
        self._drag_overlay = QLabel("Перетащите сюда, чтобы сразу пропарсить", self)
        self._drag_overlay.setAlignment(Qt.AlignCenter)
        self._drag_overlay.setWordWrap(True)
        self._drag_overlay.setStyleSheet(
            "background:rgba(0,0,0,190); color:#ffffff; font-size:30px; "
            "font-weight:700; border:3px dashed #ff9900; border-radius:16px;"
        )
        self._drag_overlay.hide()
        self._drag_overlay.raise_()

    def resizeEvent(self, event):
        try:
            super().resizeEvent(event)
        except Exception:
            pass
        try:
            self._drag_overlay.setGeometry(self.rect().adjusted(12, 12, -12, -12))
        except Exception:
            pass

    def _drag_urls_to_paths(self, mime):
        paths=[]
        try:
            for url in mime.urls() if mime and mime.hasUrls() else []:
                if url.isLocalFile():
                    p=Path(url.toLocalFile())
                    if p.exists():
                        paths.append(p)
        except Exception:
            pass
        # Preserve order, remove duplicates.
        out=[]; seen=set()
        for p in paths:
            try:
                key=str(p.resolve()).casefold()
            except Exception:
                key=str(p).casefold()
            if key in seen:
                continue
            seen.add(key); out.append(p)
        return out

    def dragEnterEvent(self, event):
        paths=self._drag_urls_to_paths(event.mimeData())
        if paths:
            event.acceptProposedAction()
            try:
                self._drag_overlay.setGeometry(self.rect().adjusted(12, 12, -12, -12))
                self._drag_overlay.show(); self._drag_overlay.raise_()
            except Exception:
                pass
            return
        event.ignore()

    def dragMoveEvent(self, event):
        if self._drag_urls_to_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        try:
            self._drag_overlay.hide()
        except Exception:
            pass
        try:
            super().dragLeaveEvent(event)
        except Exception:
            pass

    def dropEvent(self, event):
        paths=self._drag_urls_to_paths(event.mimeData())
        try:
            self._drag_overlay.hide()
        except Exception:
            pass
        if not paths:
            event.ignore(); return
        media_roots=[]
        for p in paths:
            if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                media_roots.append(p)
            elif p.is_dir():
                media_roots.append(p)
        if not media_roots:
            self.append_log("DROP INPUT: нет поддерживаемых медиа-файлов/папок")
            event.acceptProposedAction(); return
        self._drop_explicit_paths=[str(p) for p in media_roots]
        # Drop input is a temporary explicit run.  It must never replace the
        # normal parser root/archive folder in the UI or in app_settings.json.
        self.append_log(f"DROP INPUT: принято {len(media_roots)} файл(ов)/папок; запуск парсинга")
        event.acceptProposedAction()
        try:
            running = bool(self.worker and self.worker.isRunning())
        except Exception:
            running = False
        if running:
            self.append_log("DROP INPUT WARNING: парсер уже работает; дождись завершения или останови текущий прогон")
            return
        try:
            QTimer.singleShot(0, self.run)
        except Exception:
            self.run()

    def _effective_log_line_limit(self):
        try:
            base = int(self.main.settings.get("max_console_lines", 1000) or 1000)
        except Exception:
            base = 1000
        if bool(self.main.settings.get("tagger_ram_safe_mode", True)):
            try:
                safe = int(self.main.settings.get("tagger_ram_safe_console_lines", 250) or 250)
            except Exception:
                safe = 250
            return max(50, min(base, safe))
        return max(100, min(base, 5000))

    def _effective_log_queue_cap(self):
        try:
            base = int(self.main.settings.get("tagger_ui_log_queue_cap", 5000) or 5000)
        except Exception:
            base = 5000
        if bool(self.main.settings.get("tagger_ram_safe_mode", True)):
            try:
                safe = int(self.main.settings.get("tagger_ram_safe_log_queue_cap", 750) or 750)
            except Exception:
                safe = 750
            return max(100, min(base, safe))
        return max(500, min(base, 50000))

    def _effective_log_flush_batch(self):
        try:
            base = int(self.main.settings.get("tagger_ui_log_flush_batch", 250) or 250)
        except Exception:
            base = 250
        if bool(self.main.settings.get("tagger_ram_safe_mode", True)):
            try:
                safe = int(self.main.settings.get("tagger_ram_safe_log_flush_batch", 80) or 80)
            except Exception:
                safe = 80
            return max(20, min(base, safe))
        return max(20, min(base, 2000))

    def _build_console_panel(self):
        panel = QWidget()
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        self.console_mode_buttons = []
        for idx, text in enumerate(("Общий", "Сетка", "Один сайт")):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, i=idx: self.set_console_mode(i))
            self.console_mode_buttons.append(btn)
            bar.addWidget(btn)
        self.single_log_combo = QComboBox()
        self.single_log_combo.currentTextChanged.connect(lambda _text: self._refresh_single_log_view())
        bar.addWidget(QLabel("Канал:"))
        bar.addWidget(self.single_log_combo, 1)
        outer.addLayout(bar)

        self.console_stack = QStackedWidget()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        set_bounded_log(self.log, self._effective_log_line_limit())

        self.site_activity_table=QTableWidget(0,4)
        self.site_activity_table.setHorizontalHeaderLabels(["Сайт", "Состояние", "MD5", "Текущий файл"])
        self.site_activity_table.verticalHeader().setVisible(False)
        self.site_activity_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.site_activity_table.setSelectionMode(QAbstractItemView.NoSelection)
        # v371: status table must not "walk" when status text or filenames change.
        # Columns are user-resizable, but never ResizeToContents/Stretch-driven.
        # Their widths are saved/restored from settings.
        header = self.site_activity_table.horizontalHeader()
        header.setSectionsMovable(False)
        header.setStretchLastSection(False)
        for col in range(4):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        self.site_activity_table.verticalHeader().setDefaultSectionSize(70)
        self.site_activity_table.setWordWrap(False)
        self.site_activity_table.setTextElideMode(Qt.ElideMiddle)
        self.site_activity_table.setMinimumWidth(520)
        self.site_activity_table.setMinimumHeight(28)
        self._restore_site_activity_table_layout()
        try:
            header.sectionResized.connect(lambda *_args: self._save_site_activity_table_layout())
        except Exception:
            pass
        self._site_activity_rows={}; self._site_activity_paths={}; self._site_activity_preview_labels={}
        self._site_activity_md5_by_name={}; self._site_activity_md5_by_path={}; self._site_activity_name_to_sites={}; self._site_activity_current_name_by_site={}

        # Общий режим: консоль и состояние должны быть рядом, а не
        # "состояние сверху / консоль снизу". Таблица состояния остаётся
        # сжимаемой обычным splitter'ом, поэтому отдельная кнопка скрытия
        # не нужна: потянул разделитель вправо — статус почти пропал.
        self.general_console_split = QSplitter(Qt.Horizontal)
        self.general_console_split.setChildrenCollapsible(True)
        self.log.setMinimumWidth(360)
        self.general_console_split.addWidget(self.log)
        self.general_console_split.addWidget(self.site_activity_table)
        self.general_console_split.setSizes([900, 360])
        self.console_stack.addWidget(self.general_console_split)

        self.log_grid_scroll = QScrollArea()
        self.log_grid_scroll.setWidgetResizable(True)
        self.log_grid_widget = QWidget()
        self.log_grid = QGridLayout(self.log_grid_widget)
        self.log_grid.setContentsMargins(0, 0, 0, 0)
        self.log_grid.setSpacing(6)
        self.log_grid_scroll.setWidget(self.log_grid_widget)
        self.console_stack.addWidget(self.log_grid_scroll)

        self.single_log = QPlainTextEdit()
        self.single_log.setReadOnly(True)
        set_bounded_log(self.single_log, self._effective_log_line_limit())
        self.console_stack.addWidget(self.single_log)

        outer.addWidget(self.console_stack, 1)
        self.set_console_mode(int(self.main.settings.get("tagger_console_mode", 0) or 0), save=False)
        self._prepare_log_channels(reset=True)
        return panel

    def _restore_site_activity_table_layout(self):
        try:
            raw = self.main.settings.get("tagger_site_activity_column_widths", [])
            if not isinstance(raw, (list, tuple)) or len(raw) < 4:
                raw = [150, 165, 250, 275]
            widths = [max(60, min(1200, int(x))) for x in list(raw)[:4]]
        except Exception:
            widths = [150, 165, 250, 275]
        for col, width in enumerate(widths):
            try:
                self.site_activity_table.setColumnWidth(col, width)
            except Exception:
                pass

    def _save_site_activity_table_layout(self):
        try:
            widths = [int(self.site_activity_table.columnWidth(i)) for i in range(4)]
            if self.main.settings.get("tagger_site_activity_column_widths") == widths:
                return
            self.main.settings["tagger_site_activity_column_widths"] = widths
            save_settings(self.main.settings)
        except Exception:
            pass

    def set_console_mode(self, index: int, save: bool = True):
        try:
            index = int(index)
        except Exception:
            index = 0
        # v259: отдельного режима «Статус» больше нет — таблица статусов
        # встроена в общий режим и сжимается обычным splitter'ом. Старое
        # сохранённое значение 3 мягко возвращаем в общий режим.
        if index >= 3:
            index = 0
        index = max(0, min(2, index))
        try:
            self.console_stack.setCurrentIndex(index)
            for i, btn in enumerate(getattr(self, "console_mode_buttons", [])):
                btn.setChecked(i == index)
            self.single_log_combo.setVisible(index == 2)
            if save:
                self.main.settings["tagger_console_mode"] = index
                save_settings(self.main.settings)
            if index == 2:
                self._refresh_single_log_view()
        except Exception:
            pass

    def _enabled_log_channels(self):
        channels = []
        seen = {}
        try:
            tagger = Tagger(self.main.settings, lambda _m: None)
            for site in tagger._all_enabled_site_configs():
                label = tagger._site_label(site)
                seen[label] = seen.get(label, 0) + 1
                shown = label if seen[label] == 1 else f"{label} ({seen[label]})"
                channels.append(shown)
        except Exception:
            try:
                sites = self.main.settings.get("sites", {}) if isinstance(self.main.settings, dict) else {}
                if isinstance(sites, dict):
                    for domain, cfg in sites.items():
                        if isinstance(cfg, dict) and bool(cfg.get("enabled", True)):
                            channels.append(str(cfg.get("name") or cfg.get("domain") or domain))
            except Exception:
                pass
        if bool(self.main.settings.get("enable_iqdb", True)):
            channels.append("IQDB")
        if bool(self.main.settings.get("enable_danbooru_iqdb", False)):
            channels.append("Danbooru IQDB")
        if bool(self.main.settings.get("enable_e621_iqdb", True)):
            channels.append("e621 IQDB")
        if bool(self.main.settings.get("enable_ascii2d", False)):
            channels.append("Ascii2D")
        if bool(self.main.settings.get("enable_saucenao", True)) and str(self.main.settings.get("saucenao_api_key") or "").strip():
            channels.append("SauceNAO")
        if bool(self.main.settings.get("enable_tineye", False)):
            channels.append("TinEye")
        if any(ch in channels for ch in ("IQDB", "Danbooru IQDB", "e621 IQDB", "Ascii2D", "TinEye", "SauceNAO")):
            channels.append("Reverse side queue")
        channels.append("source→MD5 relay")
        channels.append("rule34 40hex/SHA1")
        channels.append("Финальная сборка")
        if bool(self.main.settings.get("tagger_background_tag_groups", self.main.settings.get("tagger_background_rule34_categories", True))):
            channels.append("Категории тегов")
        out = []
        used = set()
        for ch in channels:
            ch = str(ch or "").strip()
            if ch and ch not in used:
                out.append(ch); used.add(ch)
        return out

    def _prepare_log_channels(self, reset: bool = False):
        if reset:
            self._log_channel_buffers.clear()
            self._log_channel_widgets.clear()
            self._log_channel_meta.clear()
            try:
                self.log.clear()
                self.single_log.clear()
            except Exception:
                pass
        self._active_log_channels = self._enabled_log_channels()
        self._active_log_channel_set = set(self._active_log_channels)
        for channel in self._active_log_channels:
            self._ensure_log_channel(channel)
        self._rebuild_log_grid()
        self._refresh_channel_combo()
        self._reset_activity_rows()
        for channel in self._active_log_channels:
            self.update_site_activity(channel, "Ожидает", "")

    def _ensure_log_channel(self, channel: str):
        channel = str(channel)
        self._log_channel_buffers.setdefault(channel, [])
        if channel in self._log_channel_widgets:
            return self._log_channel_widgets[channel]
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        header = QLabel(channel)
        header.setStyleSheet("font-weight:700; padding:3px 6px; border:1px solid #2f3541; border-radius:5px;")
        status = QLabel("Ожидает")
        status.setStyleSheet("padding:1px 6px;")
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setMinimumHeight(140)
        set_bounded_log(edit, self._effective_log_line_limit())
        lay.addWidget(header)
        lay.addWidget(status)
        lay.addWidget(edit, 1)
        self._log_channel_widgets[channel] = box
        self._log_channel_meta[channel] = {"header": header, "status": status, "log": edit}
        return box

    def _rebuild_log_grid(self):
        try:
            while self.log_grid.count():
                item = self.log_grid.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
            n = max(1, len(self._active_log_channels))
            if n <= 1:
                cols = 1
            elif n <= 4:
                cols = 2
            elif n <= 6:
                cols = 3
            else:
                cols = 3
            for i, channel in enumerate(self._active_log_channels):
                self.log_grid.addWidget(self._ensure_log_channel(channel), i // cols, i % cols)
            self.log_grid.setColumnStretch(cols, 0)
        except Exception:
            pass

    def _refresh_channel_combo(self):
        try:
            current = self.single_log_combo.currentText()
            self.single_log_combo.blockSignals(True)
            self.single_log_combo.clear()
            self.single_log_combo.addItems(self._active_log_channels)
            if current in self._active_log_channel_set:
                self.single_log_combo.setCurrentText(current)
            elif self._active_log_channels:
                self.single_log_combo.setCurrentIndex(0)
            self.single_log_combo.blockSignals(False)
            self._refresh_single_log_view()
        except Exception:
            pass

    def _reset_activity_rows(self):
        try:
            self.site_activity_table.setRowCount(0)
        except Exception:
            pass
        self._site_activity_rows = {}
        self._site_activity_paths = {}
        self._site_activity_preview_labels = {}
        self._site_activity_md5_by_name = {}
        self._site_activity_md5_by_path = {}
        self._site_activity_name_to_sites = {}
        self._site_activity_current_name_by_site = {}

    def _classify_log_channel(self, text: str):
        text = str(text or "")
        if text.startswith("[MD5:"):
            rest = text[5:]
            parts = rest.split(":", 2)
            if parts:
                return parts[0]
        upper = text.upper()
        if text.startswith("[R34-VARIANT:") or "R34-VARIANT" in upper or "RULE34 IMAGE-KEY" in upper or "RULE34 40HEX" in upper:
            return "rule34 40hex/SHA1"
        if "SOURCE MD5 RELAY" in upper or "SOURCE-MD5" in upper or "MD5 RELAY" in upper:
            return "source→MD5 relay"
        if "FINAL" in upper or "ФИНАЛ" in upper or "SOURCE-ONLY SAVED" in upper or "NO MATCH SAVED" in upper or "TAGS [" in upper:
            return "Финальная сборка"
        if "DANBOORU IQDB" in upper:
            return "Danbooru IQDB"
        if "E621 IQDB" in upper:
            return "e621 IQDB"
        if "TINEYE" in upper:
            return "TinEye"
        if "SAUCENAO" in upper or "SAUCE" in upper:
            return "SauceNAO"
        if "ASCII2D" in upper:
            return "Ascii2D"
        if "IQDB" in upper:
            return "IQDB"
        if text.startswith("[REVERSE:") or text.startswith("[SAUCENAO-RETRY:") or text.startswith("[REVERSE-ASYNC:"):
            return "Reverse side queue"
        if "TAG CATEGORY" in upper or "КАТЕГОР" in upper:
            return "Категории тегов"
        return None

    def _append_channel_log(self, channel: str, text: str):
        channel = str(channel or "").strip()
        if not channel or channel not in self._active_log_channel_set:
            return
        self._ensure_log_channel(channel)
        limit = self._effective_log_line_limit()
        buf = self._log_channel_buffers.setdefault(channel, [])
        buf.append(str(text))
        if len(buf) > limit:
            del buf[:len(buf)-limit]
        meta = self._log_channel_meta.get(channel) or {}
        edit = meta.get("log")
        if edit is not None:
            bounded_append(edit, text, limit)
        if self.console_stack.currentIndex() == 2 and self.single_log_combo.currentText() == channel:
            bounded_append(self.single_log, text, limit)

    def _refresh_single_log_view(self):
        try:
            channel = self.single_log_combo.currentText()
            self.single_log.clear()
            self.single_log.setPlainText("\n".join(self._log_channel_buffers.get(channel, [])))
            self.single_log.moveCursor(self.single_log.textCursor().End)
        except Exception:
            pass

    def _set_log_channel_status(self, channel: str, status: str, path: str = ""):
        channel = str(channel or "").strip()
        if channel not in self._active_log_channel_set:
            return
        self._ensure_log_channel(channel)
        meta = self._log_channel_meta.get(channel) or {}
        label = meta.get("status")
        if label is not None:
            name = Path(str(path)).name if path else "—"
            label.setText(f"{status} · {name}" if name != "—" else str(status))
            label.setToolTip(str(path or ""))


    def add_tip_row(self, label_key, widget, tip_key):
        lab = QLabel(self.main.t(label_key) + "  ?")
        lab.setToolTip(self.main.t(tip_key))
        _tc2 = self.main.settings.get("appearance","abyss") if hasattr(self,"main") else "abyss"
        _lmap = {"light": ("#1a1c2a","#5060d0"), "r34": ("#111111","#3a7a35"), "r34dark": ("#d6e4d3","#7fb06f"),
                 "win95": ("#000000","#000080"), "windows95": ("#000000","#000080"),
                 "ph": ("#f5f5f5","#ff9000"), "pornhub": ("#f5f5f5","#ff9000"),
                 "dark": ("#c0c8e0","#6c85e0"), "abyss": ("#c0c8e0","#6c85e0"),
                 "ember": ("#c8b090","#c87040"), "slate": ("#b0c8d0","#5a8a9f"),
                 "sakura": ("#e0b0d0","#d060a0")}
        _lc2, _hc2 = _lmap.get(_tc2, ("#c0c8e0","#6c85e0"))
        lab.setStyleSheet(f"QLabel{{font-weight:700;color:{_lc2};}} QLabel:hover{{color:{_hc2};}}")

        if hasattr(widget, "setToolTip"):
            widget.setToolTip(self.main.t(tip_key))

        target_form = self.form_left if getattr(self, "_form_col", 0) % 2 == 0 else self.form_right
        target_form.addRow(lab, widget)
        self._form_col = getattr(self, "_form_col", 0) + 1
        self.form_rows.append((lab, label_key, widget, tip_key))


    def add_api_service_row(self, label_key, checkbox, key_widget, tip_key, service):
        """Add compact service row: clickable name + enable checkbox + optional API key field.

        Public services such as Fluffle do not show a useless key field; docs/API
        URLs are baked into the clickable service name instead of wasting rows.
        """
        lab = QLabel(self.main.t(label_key))
        lab.setToolTip(self.main.t(tip_key))
        lab.setCursor(Qt.PointingHandCursor)
        lab.mousePressEvent = lambda _event, svc=service: self.open_external_api_doc(svc)
        _tc2 = self.main.settings.get("appearance","abyss") if hasattr(self,"main") else "abyss"
        _lmap = {"light": ("#1a1c2a","#5060d0"), "r34": ("#111111","#3a7a35"), "r34dark": ("#d6e4d3","#7fb06f"),
                 "win95": ("#000000","#000080"), "windows95": ("#000000","#000080"),
                 "ph": ("#f5f5f5","#ff9000"), "pornhub": ("#f5f5f5","#ff9000"),
                 "dark": ("#c0c8e0","#6c85e0"), "abyss": ("#c0c8e0","#6c85e0"),
                 "ember": ("#c8b090","#c87040"), "slate": ("#b0c8d0","#5a8a9f"),
                 "sakura": ("#e0b0d0","#d060a0")}
        _lc2, _hc2 = _lmap.get(_tc2, ("#c0c8e0","#6c85e0"))
        lab.setStyleSheet(f"QLabel{{font-weight:700;color:{_lc2};text-decoration:underline;}} QLabel:hover{{color:{_hc2};}}")

        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(checkbox, 0)
        if key_widget is not None:
            lay.addWidget(key_widget, 1)
        else:
            lay.addStretch(1)
        row.setToolTip(self.main.t(tip_key))
        if hasattr(checkbox, "setToolTip"):
            checkbox.setToolTip(self.main.t(tip_key))
        if key_widget is not None and hasattr(key_widget, "setToolTip"):
            key_widget.setToolTip(self.main.t(tip_key))

        target_form = self.form_left if getattr(self, "_form_col", 0) % 2 == 0 else self.form_right
        target_form.addRow(lab, row)
        self._form_col = getattr(self, "_form_col", 0) + 1
        self.form_rows.append((lab, label_key, row, tip_key))
        if not hasattr(self, "_api_service_rows"):
            self._api_service_rows = []
        self._api_service_rows.append((lab, label_key, row, tip_key, service, checkbox, key_widget))


    def apply_theme_style(self, theme_name: str | None = None):
        """Refresh parser page inline label styles after runtime theme switch."""
        theme_name = theme_name or self.main.settings.get("appearance", "abyss")
        colors = {
            "light": ("#1a1c2a", "#5060d0"),
            "r34": ("#111111", "#3a7a35"),
            "r34dark": ("#d6e4d3", "#7fb06f"),
            "win95": ("#000000", "#000080"),
            "windows95": ("#000000", "#000080"),
            "ph": ("#f5f5f5", "#ff9000"),
            "pornhub": ("#f5f5f5", "#ff9000"),
            "dark": ("#c0c8e0", "#6c85e0"),
            "abyss": ("#c0c8e0", "#6c85e0"),
            "ember": ("#c8b090", "#c87040"),
            "slate": ("#b0c8d0", "#5a8a9f"),
            "sakura": ("#e0b0d0", "#d060a0"),
        }
        fg, hover = colors.get(theme_name, colors["abyss"])
        try:
            for lab, label_key, widget, tip_key in getattr(self, "form_rows", []):
                lab.setStyleSheet(f"QLabel{{font-weight:700;color:{fg};background:transparent;}} QLabel:hover{{color:{hover};}}")
            for lab, *_rest in getattr(self, "_api_service_rows", []):
                lab.setStyleSheet(f"QLabel{{font-weight:700;color:{fg};background:transparent;text-decoration:underline;}} QLabel:hover{{color:{hover};}}")
        except Exception:
            pass
        try:
            if theme_name in ("win95", "windows95"):
                self.preview_box.setStyleSheet("border-top:2px solid #808080;border-left:2px solid #808080;border-bottom:2px solid #ffffff;border-right:2px solid #ffffff;border-radius:0px;background:#c0c0c0;color:#000000;")
            elif theme_name == "r34":
                self.preview_box.setStyleSheet("border:1px solid #6da36b;border-radius:0px;background:#b7e2af;color:#111111;")
            elif theme_name == "r34dark":
                self.preview_box.setStyleSheet("border:1px solid #345032;border-radius:0px;background:#171e15;color:#d6e4d3;")
            else:
                self.preview_box.setStyleSheet("border:1px solid #2f3541;border-radius:8px;")
        except Exception:
            pass

    def enqueue_log(self, msg):
        """Cheap Qt slot for worker.log.  Heavy QTextEdit appends are batched.

        Without this, a fast parser can enqueue thousands of strings faster than
        Qt can repaint QPlainTextEdit, growing RAM until Windows marks the app as
        not responding.
        """
        try:
            cap = self._effective_log_queue_cap()
        except Exception:
            cap = 5000
        if len(self._pending_log_lines) >= cap:
            drop = max(1, cap // 4)
            del self._pending_log_lines[:drop]
            self._pending_log_dropped += drop
        self._pending_log_lines.append(str(msg))

    def _flush_pending_logs(self):
        if not getattr(self, "_pending_log_lines", None) and not getattr(self, "_pending_log_dropped", 0):
            return
        try:
            max_batch = self._effective_log_flush_batch()
        except Exception:
            max_batch = 250
        lines = self._pending_log_lines[:max_batch]
        del self._pending_log_lines[:max_batch]
        dropped = int(getattr(self, "_pending_log_dropped", 0) or 0)
        if dropped:
            self._pending_log_dropped = 0
            lines.insert(0, f"UI LOG THROTTLE: пропущено {dropped} строк, чтобы интерфейс не съел память")
        for line in lines:
            self.append_log(line)

    def append_log(self, msg):
        _text = str(msg)
        self._observe_md5_log_line(_text)
        bounded_append(self.log, _text, self._effective_log_line_limit())
        channel = self._classify_log_channel(_text)
        if channel:
            self._append_channel_log(channel, _text)
        if "SAUCENAO LIMITS:" in _text or "SAUCENAO COOLDOWN" in _text or "SAUCENAO RETRY QUEUED" in _text:
            self.refresh_saucenao_state()

    def trim_ui_memory(self):
        try:
            from PySide6.QtGui import QPixmapCache
            QPixmapCache.clear()
        except Exception:
            pass
        try:
            from core.thumb_service import ThumbnailService
            ThumbnailService.instance().clear_memory_cache()
        except Exception:
            pass
        try:
            soft_trim_memory(0.0)
        except Exception:
            pass
        soft_gc()

    def bool_item(self, checked):
        it=QTableWidgetItem(); it.setFlags(it.flags()|Qt.ItemIsUserCheckable); it.setCheckState(Qt.Checked if checked else Qt.Unchecked); return it
    def load_values(self):
        s=self.main.settings; self.root.setText(s.get("root","C:/Local_Booru_Input")); self.api.setText(s.get("saucenao_api_key","")); self.min_sim.setValue(float(s.get("min_similarity",85))); self.skip.setChecked(bool(s.get("skip_existing",True))); self.only_untagged.setChecked(bool(s.get("tag_only_untagged",True))); self.skip_copy_suffix.setChecked(bool(s.get("skip_copy_suffix_files",True))); self.md5.setChecked(bool(s.get("enable_md5_lookup",True))); self.sauce.setChecked(bool(s.get("enable_saucenao",True))); self.ascii2d.setChecked(s.get("enable_ascii2d",False))
        self.iqdb.setChecked(bool(s.get("enable_iqdb",True))); self.danbooru_iqdb.setChecked(bool(s.get("enable_danbooru_iqdb",False))); self.e621_iqdb.setChecked(bool(s.get("enable_e621_iqdb",True))); self.fuzzysearch.setChecked(False); self.fluffle.setChecked(False); self.tineye.setChecked(bool(s.get("enable_tineye",False))); self.fuzzy_key.setText(""); self.fluffle_key.setText(""); self.fuzzy_endpoint.setText(""); self.fluffle_endpoint.setText(""); self.iqdb_min.setValue(float(s.get("iqdb_min_similarity",75))); self.delay.setValue(float(s.get("delay_seconds",8))); self.req_timeout.setValue(int(float(s.get("request_timeout_seconds",20)))); self.sauce_cooldown.setValue(int(float(s.get("saucenao_cooldown_seconds",3600))/60)); self.limit.setValue(int(s.get("limit_files",0)))
        # Hidden defaults owned by the parser blueprint/runtime.
        self.only_untagged.setChecked(True); self.bg_rule34_categories.setChecked(True); self.low_power.setChecked(False); self.site_interval.setValue(1.10); self.conveyor_window.setValue(100); self.delay.setValue(0.0); self.req_timeout.setValue(30); self.sauce_cooldown.setValue(60); self.limit.setValue(0); self.update_preview_visibility()
        self.sites_widget.load(s)
        self.refresh_saucenao_state()

    def refresh_saucenao_state(self):
        try:
            from core.services.service_state import get_cooldown
            state = get_cooldown(self.main.settings, "saucenao")
            now = int(time.time())
            left = max(0, int(state.get("cooldown_until", 0) or 0) - now)
            _short = state.get("short_remaining", -1)
            _long = state.get("long_remaining", -1)
            short_rem = int(_short if _short is not None else -1)
            long_rem = int(_long if _long is not None else -1)
            counters = []
            if short_rem >= 0:
                counters.append(f"короткий: {short_rem}")
            if long_rem >= 0:
                counters.append(f"сутки: {long_rem}")
            quota = " · ".join(counters) if counters else "лимит ещё не получен"
            if left:
                status = f"пауза ещё {left//60}м {left%60}с"
            else:
                status = "готов"
            self.sauce_state.setText(f"{quota}; {status}")
        except Exception:
            self.sauce_state.setText("Состояние станет доступно после запроса")

    def retranslate(self):
        t=self.main.t; self.choose_btn.setText(t("Choose")); self.save_btn.setText(t("Save settings")); self.start.setText("СТОП" if (self.worker and self.worker.isRunning()) else t("START")); self.pause_btn.setText(t("RESUME") if self.pause_btn.isChecked() else t("PAUSE")); self.stop_btn.setText(t("STOP")); self.apply_tips()
        api_labs = {id(row[0]) for row in getattr(self, "_api_service_rows", [])}
        for lab, label_key, w, tip_key in getattr(self, "form_rows", []):
            lab.setText(t(label_key) if id(lab) in api_labs else t(label_key) + "  ?")
            lab.setToolTip(t(tip_key))

            if hasattr(w, "setToolTip"):
                w.setToolTip(t(tip_key))
        for lab, label_key, row, tip_key, service, checkbox, key_widget in getattr(self, "_api_service_rows", []):
            lab.mousePressEvent = lambda _event, svc=service: self.open_external_api_doc(svc)
            if hasattr(checkbox, "setToolTip"):
                checkbox.setToolTip(t(tip_key))
            if hasattr(key_widget, "setToolTip"):
                key_widget.setToolTip(t(tip_key))
    def apply_tips(self):
        t=self.main.t
        pairs=[(self.root,"tip_root"),(self.api,"tip_sauce_service"),(self.sauce_state,"tip_saucenao_state"),(self.min_sim,"tip_min_similarity"),(self.md5,"tip_md5"),(self.sauce,"tip_sauce_service"),(self.iqdb,"tip_iqdb"),(self.danbooru_iqdb,"tip_danbooru_iqdb"),(self.e621_iqdb,"tip_e621_iqdb"),(self.tineye,"tip_tineye"),(self.tineye_key,"tip_tineye"),(self.delay,"tip_delay"),(self.req_timeout,"tip_delay"),(self.sauce_cooldown,"tip_sauce"),(self.limit,"tip_limit"),(self.skip,"tip_skip"),(self.only_untagged,"tip_only_untagged"),(self.skip_copy_suffix,"tip_skip_copy_suffix"),(self.bg_rule34_categories,"tip_background_groups"),(self.low_power,"tip_low_power"),(self.site_interval,"tip_site_interval"),(self.conveyor_window,"tip_conveyor_window")]
        for w,k in pairs: w.setToolTip(t(k))

    def _table_clicked(self, *args, **kwargs):
        pass  # replaced by SitesWidget

    def clear_site_selection(self, *args, **kwargs):
        pass  # replaced by SitesWidget

    def _selected_rows_for_table(self, table):
        rows = sorted({i.row() for i in table.selectedIndexes()})
        if rows:
            return rows
        row = table.currentRow()
        return [row] if row >= 0 else []

    def selected_login_urls(self):
        return self.sites_widget.selected_login_urls()

    def open_login_browser(self, url, sync_first=True):
        if sync_first:
            self.sync(show_message=False)
        norm = _ui_normalize_url(url)
        if not norm:
            self.append_log(f"SKIP INVALID LOGIN URL: {url!r}")
            return
        self.append_log(f"OPEN APP LOGIN BROWSER: {norm}")
        open_br34(norm, self, log_func=self.append_log)
        self.append_log("br34 OPENED / TAB ADDED")

    def open_selected_login(self):
        urls = self.selected_login_urls()
        if not urls:
            self.append_log("NO LOGIN URL SELECTED")
            return
        self.sync(show_message=False)
        normed = [_ui_normalize_url(u) for u in urls]
        normed = [u for u in normed if u]
        if not normed:
            self.append_log("NO VALID LOGIN URLs")
            return
        for u in normed:
            self.append_log(f"OPEN APP LOGIN BROWSER: {u}")
        open_br34_multi(normed, parent=self, log_func=self.append_log)
        self.append_log("br34 OPENED / ALL TABS ADDED")

    def open_all_logins(self):
        self.sync(show_message=False)
        urls = self.sites_widget.all_enabled_login_urls()
        if not urls:
            self.append_log("NO LOGIN URLS")
            return
        self.append_log(f"OPENING {len(urls)} LOGIN URLs IN br34 (all as tabs)")
        for i, u in enumerate(urls, 1):
            self.append_log(f"LOGIN URL {i}: {u}")
        open_br34_multi(urls, parent=self, log_func=self.append_log)
        self.append_log("br34 OPENED / ALL TABS ADDED")

    def browser_login(self):
        self.open_selected_login()


    def open_external_api_doc(self, service):
        service = str(service or "").lower().strip()
        if service == "saucenao":
            url = str(self.main.settings.get("saucenao_api_docs_url", "https://saucenao.com/user.php?page=search-api") or "https://saucenao.com/user.php?page=search-api")
        elif service == "danbooru_iqdb":
            url = str(self.main.settings.get("danbooru_iqdb_docs_url", "https://danbooru.iqdb.org/") or "https://danbooru.iqdb.org/")
        elif service == "tineye":
            url = "https://tineye.com/"
        else:
            url = ""
        if url:
            webbrowser.open(url)
            self.append_log(f"OPEN API DOCS: {url}")

    def choose(self):
        f=QFileDialog.getExistingDirectory(self,self.main.t("Choose"),self.root.text())
        if f:
            self.root.setText(f)
            self._drop_explicit_paths=[]
            try:
                self.main.settings.pop("_parser_explicit_paths", None)
            except Exception:
                pass
    def add_custom(self, *args, **kwargs):
        pass  # replaced by SitesWidget

    def delete_custom(self, *args, **kwargs):
        pass  # replaced by SitesWidget

    def sync(self, show_message=True):
        s = self.main.settings
        s["root"] = self.root.text()
        s["saucenao_api_key"] = self.api.text()
        s["min_similarity"] = self.min_sim.value()
        s["skip_existing"] = self.skip.isChecked()
        s["tag_only_untagged"] = self.only_untagged.isChecked()
        s["skip_copy_suffix_files"] = self.skip_copy_suffix.isChecked()
        
        s.pop("mark_no_match", None)
        s["enable_md5_lookup"] = self.md5.isChecked()
        s["enable_saucenao"] = self.sauce.isChecked()
        s["enable_iqdb"] = self.iqdb.isChecked()
        s["enable_danbooru_iqdb"] = self.danbooru_iqdb.isChecked()
        s["enable_e621_iqdb"] = self.e621_iqdb.isChecked()
        # v204: Fluffle/FuzzySearch are removed. Purge legacy settings so old
        # configs cannot silently re-enable them.
        for _removed_key in (
            "enable_fuzzysearch", "fuzzysearch_api_key", "fuzzysearch_endpoint",
            "fuzzysearch_api_docs_url", "fuzzysearch_max_results",
            "enable_fluffle", "fluffle_api_key", "fluffle_endpoint",
            "fluffle_api_docs_url", "fluffle_max_results",
        ):
            s.pop(_removed_key, None)
        self.fuzzysearch.setChecked(False)
        self.fluffle.setChecked(False)
        s["enable_tineye"] = self.tineye.isChecked()
        # TinEye uses web scraping - no API key needed
        s["enable_ascii2d"] = self.ascii2d.isChecked()
        # ascii2d has no public API
        s.pop("tagger_site_conveyor_enabled", None)  # conveyor is fixed architecture
        # Fixed sane defaults: parser blueprint/runtime owns throttling and resource policy.
        # Runtime PAUSE is intentionally separate and remains visible as a parser control.
        s["tag_only_untagged"] = True
        s["tagger_background_tag_groups"] = True
        s["tagger_background_rule34_categories"] = True  # backward compatibility
        s["tagger_low_power_mode"] = False
        self.update_preview_visibility()
        s["tagger_site_interval_seconds"] = 1.10
        s["tagger_conveyor_window"] = 100
        s["iqdb_min_similarity"] = self.iqdb_min.value()
        s["delay_seconds"] = 0.0
        s["request_timeout_seconds"] = 30
        s["saucenao_cooldown_seconds"] = 60 * 60
        s["limit_files"] = 0
        s["tagger_ram_safe_disable_parser_preview"] = False
        s["tagger_ram_safe_disable_activity_thumbs"] = False
        # Retired live sidecar/cookie-mode controls are not part of the parser UI.
        for _retired in ("output_suffix", "sources_suffix", "tags_suffix", "use_browser_auth", "use_system_browser_cookies", "browser_auth_wait_seconds"):
            s.pop(_retired, None)

        collected_sites, collected_custom = self.sites_widget.collect()

        # Preserve hidden/advanced keys that the UI table does not expose
        # (api format, endpoints, parser mode, custom md5/tag settings, etc.).
        old_sites = s.get("sites") if isinstance(s.get("sites"), dict) else {}
        merged_sites = {}
        for domain, cfg in collected_sites.items():
            old = old_sites.get(domain, {}) if isinstance(old_sites.get(domain, {}), dict) else {}
            merged_sites[domain] = {**old, **cfg}

        old_custom_list = s.get("custom_sites") if isinstance(s.get("custom_sites"), list) else []
        old_custom = {}
        for item in old_custom_list:
            if isinstance(item, dict):
                key = (item.get("domain") or item.get("base_url") or item.get("name") or "").strip()
                if key:
                    old_custom[key] = item
        merged_custom = []
        for cfg in collected_custom:
            key = (cfg.get("domain") or cfg.get("base_url") or cfg.get("name") or "").strip()
            old = old_custom.get(key, {}) if isinstance(old_custom.get(key, {}), dict) else {}
            merged_custom.append({**old, **cfg})

        s["sites"] = merged_sites
        s["custom_sites"] = merged_custom
        # v131 site-manager metadata: presets may be removed and neutral view
        # preserves the manual drag-and-drop order used by the parser lanes.
        s["deleted_builtin_sites"] = self.sites_widget.deleted_builtin_sites()
        s["site_manual_order"] = self.sites_widget.manual_order()
        save_settings(s)
        if show_message:
            QMessageBox.information(self, self.main.t("Saved"), self.main.t("Settings saved"))
    def run_or_stop(self):
        if self.worker and self.worker.isRunning():
            self.stop()
            return
        self.run()

    def run(self):
        self.sync(show_message=False)
        self._prepare_log_channels(reset=True)
        self.start.setEnabled(True)
        self.start.setText("СТОП")
        self.pause_btn.setEnabled(True)
        self.pause_btn.setChecked(False)
        self.stop_btn.setEnabled(True)
        try:
            self.main.settings["_parser_running"] = True
        except Exception:
            pass
        # Parser owns SQLite and RAM. Kill already queued gallery aggregations and
        # prefetch jobs before they build huge sqlite temp tables beside the parser.
        try:
            tm = getattr(self.main, "task_manager", None)
            if tm is not None:
                n = tm.cancel_by_name("gallery-facets", "gallery-sidebar-tag-counts", "gallery-thumb-prefetch")
                if n:
                    self.append_log(f"RAM SAFE: остановлены фоновые задачи галереи перед парсером: {n}")
        except Exception:
            pass

        self.update_preview_visibility()
        try:
            if getattr(self, "_drop_explicit_paths", None):
                self.main.settings["_parser_explicit_paths"] = list(self._drop_explicit_paths)
                self.append_log(f"DROP INPUT ACTIVE: {len(self._drop_explicit_paths)} путь(ей) из drag-and-drop")
            else:
                self.main.settings.pop("_parser_explicit_paths", None)
        except Exception:
            pass
        # Warn if DB is still in safe mode after a crash
        try:
            from core.database.connection import writes_blocked, writes_blocked_reason
            if writes_blocked():
                self.append_log(f"  WARN: SQLite в безопасном режиме ({writes_blocked_reason()})")
                self.append_log("  Checkpoints будут пропущены пока фоновая проверка не завершится.")
                self.append_log("  Рекомендуется подождать 15-20 сек после запуска программы.")
        except Exception:
            pass
        try:
            from core.database.connection import writes_blocked
            if writes_blocked():
                self.append_log("  WARN: SQLite в безопасном режиме — checkpoints пропущены до завершения проверки")
        except Exception:
            pass
        self._parser_done_signal_seen=False
        self._parser_worker_finished_seen=False
        self._parser_done_finalized=False
        self.worker = TaggerWorker(self.main.settings)
        self.worker.log.connect(self.enqueue_log)
        self.worker.progress.connect(self.on_worker_progress)
        self.worker.current_file.connect(self.show_current_preview)
        self.worker.site_current.connect(self.update_site_activity)
        self.worker.done.connect(self.on_worker_done)
        self.worker.finished.connect(self._on_worker_thread_finished)
        self.worker.start()

    def _md5_from_path_or_cache(self, path: str) -> str:
        path = str(path or "")
        if not path:
            return "—"
        cached = self._site_activity_md5_by_path.get(path)
        if cached:
            return cached
        name = Path(path).name
        cached = self._site_activity_md5_by_name.get(name)
        if cached:
            return cached
        # Exact 32hex filename/stem is usually a booru/file MD5.  Do not treat
        # 40hex names as MD5 here: those are often source keys/SHA1-like names.
        stem = Path(name).stem.lower()
        if re.fullmatch(r"[0-9a-f]{32}", stem):
            self._remember_activity_md5(name, stem, path=path)
            return stem
        m = re.search(r"(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])", name.lower())
        if m:
            value = m.group(1)
            self._remember_activity_md5(name, value, path=path)
            return value
        return "ожидает"

    def _remember_activity_md5(self, name: str, md5: str, *, path: str = ""):
        md5 = str(md5 or "").lower().strip()
        if not re.fullmatch(r"[0-9a-f]{32}", md5):
            return
        name = Path(str(name or "")).name
        if name:
            self._site_activity_md5_by_name[name] = md5
        if path:
            self._site_activity_md5_by_path[str(path)] = md5
        # Refresh visible rows currently showing this file.
        for site in list((self._site_activity_name_to_sites.get(name) or set())):
            row = self._site_activity_rows.get(site)
            if row is not None and self.site_activity_table.item(row, 2) is not None:
                self.site_activity_table.item(row, 2).setText(md5)
                self.site_activity_table.item(row, 2).setToolTip(md5)

    def _prefix_name_from_log_line(self, text: str) -> str:
        text = str(text or "")
        m = re.match(r"^\[(MD5|REVERSE|REVERSE-ASYNC|SAUCENAO-RETRY|R34-VARIANT):([^\]]+)\]", text)
        if not m:
            return ""
        payload = m.group(2)
        # [MD5:site:file] uses the last component as visible file name;
        # [MD5:file] and [REVERSE:file] are already just a file name.
        if m.group(1) == "MD5" and ":" in payload:
            payload = payload.rsplit(":", 1)[-1]
        return Path(payload).name

    def _observe_md5_log_line(self, text: str):
        """Update the live status table when parser logs discover real/site MD5.

        This is deliberately log-driven instead of hashing in the UI thread, so
        large videos do not freeze the interface just to fill the status table.
        """
        try:
            name = self._prefix_name_from_log_line(text)
            if not name:
                return
            md5 = ""
            patterns = (
                r"TRY REAL FILE MD5:\s*([0-9a-fA-F]{32})",
                r"REAL FILE MD5:\s*([0-9a-fA-F]{32})",
                r"TRY MD5 FROM FILENAME:\s*([0-9a-fA-F]{32})",
                r"TRY VARIANT SITE MD5 RELAY:\s*([0-9a-fA-F]{32})",
                r"ATF PIXEL HASH ASSET:.*?\bmd5=([0-9a-fA-F]{32})",
                r"\b(?:extracted_md5|md5)=([0-9a-fA-F]{32})\b",
            )
            for pat in patterns:
                m = re.search(pat, text)
                if m:
                    md5 = m.group(1).lower()
                    break
            if md5:
                self._remember_activity_md5(name, md5)
        except Exception:
            pass

    def update_site_activity(self, site, status, path):
        site = str(site); path = str(path or "")
        if site not in getattr(self, "_active_log_channel_set", set()):
            return
        self._set_log_channel_status(site, str(status), path)
        display_name = Path(path).name if path else "—"
        md5_text = self._md5_from_path_or_cache(path)
        old_name = self._site_activity_current_name_by_site.get(site, "")
        if old_name and old_name != display_name:
            try:
                self._site_activity_name_to_sites.get(old_name, set()).discard(site)
            except Exception:
                pass
        if display_name != "—":
            self._site_activity_name_to_sites.setdefault(display_name, set()).add(site)
            self._site_activity_current_name_by_site[site] = display_name
        else:
            self._site_activity_current_name_by_site.pop(site, None)
        row = self._site_activity_rows.get(site)
        if row is None:
            row = self.site_activity_table.rowCount(); self.site_activity_table.insertRow(row); self._site_activity_rows[site] = row
            self.site_activity_table.setItem(row, 0, QTableWidgetItem(site))
            self.site_activity_table.setItem(row, 1, QTableWidgetItem(str(status)))
            md5_item = QTableWidgetItem(str(md5_text))
            md5_item.setToolTip(str(md5_text))
            try:
                md5_item.setFont(self.site_activity_table.font())
            except Exception:
                pass
            self.site_activity_table.setItem(row, 2, md5_item)
            wrap = QWidget(); h = QHBoxLayout(wrap); h.setContentsMargins(2,2,2,2); h.setSpacing(6)
            thumb = ActivityPreviewLabel(); thumb.setFixedSize(64,64); thumb.setAlignment(Qt.AlignCenter); thumb.setStyleSheet("border:1px solid #2f3541;border-radius:4px;")
            thumb.setToolTip("Двойной клик по превью — открыть файл через приложение Windows по умолчанию")
            thumb.set_open_callback(lambda key=site: self.open_site_activity_external_by_site(key))
            name = QLabel(display_name); name.setToolTip(path); name.setWordWrap(False); name.setTextInteractionFlags(Qt.NoTextInteraction)
            h.addWidget(thumb); h.addWidget(name, 1); self.site_activity_table.setCellWidget(row, 3, wrap); self.site_activity_table.setRowHeight(row, 70)
            self._site_activity_preview_labels[site] = (thumb, name)
        else:
            self.site_activity_table.item(row, 1).setText(str(status))
            if self.site_activity_table.item(row, 2) is not None:
                self.site_activity_table.item(row, 2).setText(str(md5_text))
                self.site_activity_table.item(row, 2).setToolTip(str(md5_text))
            thumb, name = self._site_activity_preview_labels[site]; name.setText(display_name); name.setToolTip(path)
            if not path:
                thumb.clear()
        self._site_activity_paths[site] = path
        if not path:
            return
        if bool(self.main.settings.get("tagger_ram_safe_disable_activity_thumbs", False)):
            return
        from core.thumb_service import ThumbnailService
        svc = ThumbnailService.instance()
        cached = svc.request(path, 64, 64, lambda received, pix, key=site: self._on_site_activity_preview(key, received, pix))
        if cached is not None and not cached.isNull():
            self._on_site_activity_preview(site, path, cached)

    def _on_site_activity_preview(self, site, path, pix):
        if self._site_activity_paths.get(site) != str(path):
            return
        labels = self._site_activity_preview_labels.get(site)
        if not labels or pix is None or pix.isNull():
            return
        thumb, _name = labels
        thumb.setPixmap(pix.scaled(thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def on_worker_progress(self, v, t):
        self.progress.setMaximum(max(1, t))
        self.progress.setValue(v)
        if v % 100 == 0:
            self.trim_ui_memory()

    def _open_external_file(self, path):
        """Open parser preview original file with the OS default app.

        For local files on Windows do not use QDesktopServices/QUrl(file://...).
        Qt's URL path goes through ShellExecute with an encoded file URL and is
        fragile with Cyrillic/non-ASCII source paths, producing `file:///F:/????`
        failures.  The parser preview must open the original source file, so use
        native Windows paths directly.
        """
        raw = str(path or "").strip()
        if not raw:
            return False
        p = Path(raw)
        if not p.exists():
            try:
                self.append_log(f"OPEN ORIGINAL ERROR: file not found: {raw}")
            except Exception:
                pass
            return False

        native_path = str(p)
        last_error = None

        # Windows: native Unicode ShellExecute path only.  No QUrl/file:/// fallback.
        try:
            import os
            if hasattr(os, "startfile"):
                os.startfile(native_path)
                return True
        except Exception as e:
            last_error = e

        # Secondary Windows fallback: call ShellExecuteW explicitly with a UTF-16 path.
        # This is still a native path, not a file URL.
        try:
            import sys
            if sys.platform.startswith("win"):
                import ctypes
                rc = ctypes.windll.shell32.ShellExecuteW(None, "open", native_path, None, None, 1)
                if int(rc) > 32:
                    return True
                last_error = RuntimeError(f"ShellExecuteW rc={int(rc)}")
        except Exception as e:
            last_error = e

        # Non-Windows fallback only.
        try:
            import sys, subprocess
            if sys.platform == "darwin":
                subprocess.Popen(["open", native_path])
                return True
            if sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", native_path])
                return True
        except Exception as e:
            last_error = e

        try:
            self.append_log(f"OPEN ORIGINAL ERROR: {native_path} :: {last_error}")
        except Exception:
            pass
        return False

    def open_current_preview_external(self):
        return self._open_external_file(getattr(self, "_current_preview_path", ""))

    def open_site_activity_external(self, row=None, _col=None):
        try:
            row = int(row)
        except Exception:
            return False
        for site, r in list(getattr(self, "_site_activity_rows", {}).items()):
            if int(r) == row:
                return self._open_external_file(getattr(self, "_site_activity_paths", {}).get(site, ""))
        return False

    def open_site_activity_external_by_site(self, site):
        return self._open_external_file(getattr(self, "_site_activity_paths", {}).get(str(site), ""))

    def update_preview_visibility(self, *_args):
        """Large single parser preview is disabled; small queue previews stay enabled."""
        try:
            self.preview_box.setVisible(False)
            self.preview_box.setMaximumWidth(0)
            self.site_activity_table.setToolTip("Статус включённых сайтов. Открытие файла — только двойным кликом по мини-превью.")
        except Exception:
            pass

    def show_current_preview(self, path):
        # Store current path for compatibility, but do not render the old large
        # single preview. Parser previews now live only in the compact status table.
        self._current_preview_path = str(path)
        self.update_preview_visibility()
        return

    def _on_preview_ready(self, path: str, pix) -> None:
        # Called from UI thread by ThumbnailService
        if getattr(self, "_current_preview_path", None) != str(path):
            return  # stale — a newer file was requested
        if not self.preview_box.isVisible():
            return
        if pix.isNull():
            self.preview_box.setText(Path(path).name)
            return
        size = self.preview_box.contentsRect().size()
        if size.width() < 20 or size.height() < 20:
            size = self.preview_box.size()
        from PySide6.QtCore import Qt
        self.preview_box.clear()
        self.preview_box.setPixmap(pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.preview_box.setToolTip(str(path))

    # _preview_source_path and _render_preview kept for compatibility but no longer
    # called from show_current_preview.
    def _preview_source_path(self, path):
        return Path(path)

    def _render_preview(self, path):
        pass  # replaced by ThumbnailService async flow

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            pass
        except Exception:
            pass

    def _clear_parser_runtime_ui_memory(self, *, clear_status: bool = False, clear_logs: bool = False):
        """Release parser-only UI objects that keep QPixmaps/QTextDocuments alive."""
        try:
            for labels in list(getattr(self, "_site_activity_preview_labels", {}).values()):
                try:
                    thumb, name = labels
                    thumb.clear()
                    thumb.setPixmap(None)
                    name.setText("—")
                    name.setToolTip("")
                except Exception:
                    pass
            if clear_status:
                self._reset_activity_rows()
        except Exception:
            pass
        try:
            self.preview_box.clear()
            self.preview_box.setPixmap(None)
            self.preview_box.setToolTip("")
        except Exception:
            pass
        try:
            self._site_activity_paths.clear()
            self._site_activity_md5_by_name.clear()
            self._site_activity_md5_by_path.clear()
            self._site_activity_name_to_sites.clear()
            self._site_activity_current_name_by_site.clear()
        except Exception:
            pass
        try:
            self._pending_log_lines.clear()
            self._pending_log_dropped = 0
        except Exception:
            pass
        if clear_logs:
            try:
                self._log_channel_buffers.clear()
                for meta in list(getattr(self, "_log_channel_meta", {}).values()):
                    edit = meta.get("log") if isinstance(meta, dict) else None
                    if edit is not None:
                        edit.clear()
                self.single_log.clear()
                # Keep the main log short with the final summary rather than
                # retaining thousands of rich QTextDocument blocks after STOP.
                set_bounded_log(self.log, 80)
            except Exception:
                pass
        self.trim_ui_memory()

    def _schedule_post_parser_memory_trims(self):
        try:
            self._clear_parser_runtime_ui_memory(clear_status=False, clear_logs=False)
        except Exception:
            pass
        for delay in (500, 2000, 5000):
            try:
                QTimer.singleShot(delay, lambda: self.trim_ui_memory())
            except Exception:
                pass

    def _on_worker_thread_finished(self):
        # v406: do not checkpoint/print DONE from the worker.done signal itself.
        # Qt queued log signals emitted by helper threads can otherwise be
        # delivered after DONE.  Wait for QThread.finished, then give the event
        # loop one short tick to enqueue remaining logs, flush them, and only then
        # perform the shutdown checkpoint and final UI transition.
        self._parser_worker_finished_seen = True
        try:
            self.main.settings["_parser_running"] = False
        except Exception:
            pass
        try:
            QTimer.singleShot(250, self._finalize_worker_done_after_finished)
        except Exception:
            self._finalize_worker_done_after_finished()

    def _finalize_worker_done_after_finished(self):
        if getattr(self, "_parser_done_finalized", False):
            return
        self._parser_done_finalized = True
        # Drain the batched log queue before the final checkpoint/DONE marker.
        try:
            for _ in range(20):
                if not getattr(self, "_pending_log_lines", None) and not getattr(self, "_pending_log_dropped", 0):
                    break
                self._flush_pending_logs()
        except Exception:
            pass
        if not getattr(self, "_parser_done_signal_seen", False):
            self.append_log("INTERNAL WARNING: worker thread finished without parser done signal; finalizing UI safely")
        try:
            self.main.settings["_parser_running"] = False
        except Exception:
            pass
        self.refresh_saucenao_state()
        try:
            from core.light_backup import checkpoint_sqlite
            if bool(self.main.settings.get("sqlite_checkpoint_on_exit", True)):
                res = checkpoint_sqlite(self.main.settings, truncate=True, optimize=True)
                if res.get("ok"):
                    self.append_log("SQLite WAL checkpoint TRUNCATE: parser stopped/done")
        except Exception as e:
            self.append_log(f"SQLite checkpoint warning: {e}")
        self._schedule_post_parser_memory_trims()
        self.append_log("DONE")
        self.start.setEnabled(True)
        self.start.setText(self.main.t("START"))
        self.pause_btn.setEnabled(False)
        self.pause_btn.setChecked(False)
        self.pause_btn.setText(self.main.t("PAUSE"))
        self.stop_btn.setEnabled(False)
        # Drag-and-drop runs are one-shot.  After completion, forget explicit
        # paths so the next manual START uses the normal root again.
        try:
            self._drop_explicit_paths=[]
            self.main.settings.pop("_parser_explicit_paths", None)
        except Exception:
            pass
        worker = getattr(self, "worker", None)
        try:
            if worker is not None:
                try:
                    worker.log.disconnect(self.enqueue_log)
                except Exception:
                    pass
                try:
                    worker.progress.disconnect(self.on_worker_progress)
                except Exception:
                    pass
                try:
                    worker.current_file.disconnect(self.show_current_preview)
                except Exception:
                    pass
                try:
                    worker.site_current.disconnect(self.update_site_activity)
                except Exception:
                    pass
                try:
                    worker.done.disconnect(self.on_worker_done)
                except Exception:
                    pass
                try:
                    worker.finished.disconnect(self._on_worker_thread_finished)
                except Exception:
                    pass
                try:
                    worker.deleteLater()
                except Exception:
                    pass
            self.worker = None
        except Exception:
            self.worker = None

    def on_worker_done(self):
        self._parser_done_signal_seen = True
        try:
            self.main.settings["_parser_running"] = False
        except Exception:
            pass
        # Actual checkpoint/DONE is intentionally delayed until QThread.finished.
        # See _on_worker_thread_finished / _finalize_worker_done_after_finished.
        try:
            self._flush_pending_logs()
        except Exception:
            pass

    def pause_resume(self):
        if not self.worker or not self.worker.isRunning():
            return
        paused = self.pause_btn.isChecked()
        self.worker.set_paused(paused)
        self.pause_btn.setText(self.main.t("RESUME") if paused else self.main.t("PAUSE"))
        self.append_log("PAUSED" if paused else "RESUMED")

    def stop(self):
        if self.worker and self.worker.isRunning():
            # STOP must also break a paused worker immediately.
            self.pause_btn.setChecked(False)
            self.pause_btn.setText(self.main.t("PAUSE"))
            self.worker.set_paused(False)
            self.worker.requestInterruption()
            try:
                self._clear_parser_runtime_ui_memory(clear_status=False, clear_logs=False)
            except Exception:
                pass
            try:
                self.main.settings["_parser_running"] = False
            except Exception:
                pass
            self.stop_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)
            self.start.setText(self.main.t("START"))
            self.append_log("STOPPING: cancelling queued checks and reverse-search starts...")
