from pathlib import Path
from PySide6.QtWidgets import QWidget,QVBoxLayout,QFormLayout,QLineEdit,QPushButton,QFileDialog,QLabel,QSpinBox,QMessageBox,QHBoxLayout,QComboBox,QCheckBox,QDialog,QSlider,QTextEdit,QListWidget,QSplitter,QListWidgetItem,QCompleter,QApplication,QListView,QAbstractSpinBox,QGroupBox,QSizePolicy
from PySide6.QtCore import Qt, QStringListModel, QSortFilterProxyModel, QEvent
from PySide6.QtGui import QPixmap
from PIL import Image
from core.settings import save_settings
from core.tagger_engine import result_output_base
from core.paths import CACHE_FILE
import shutil
import json
import re
from core.tag_utils import normalize_tag


class ContainsFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pattern = ""

    def setFilterFixedString(self, pattern):
        self._pattern = (pattern or "").lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not self._pattern:
            return True
        idx = self.sourceModel().index(source_row, 0, source_parent)
        text = str(self.sourceModel().data(idx) or "").lower()
        return self._pattern in text


class LogoCropDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent); self.path=Path(path); self.setWindowTitle("Logo crop editor"); self.resize(720,520)
        lay=QVBoxLayout(self); self.preview=QLabel(); self.preview.setAlignment(Qt.AlignCenter); self.preview.setMinimumHeight(260); lay.addWidget(self.preview,1)
        self.left=QSlider(Qt.Horizontal); self.left.setRange(0,100); self.top=QSlider(Qt.Horizontal); self.top.setRange(0,100); self.zoom=QSlider(Qt.Horizontal); self.zoom.setRange(100,300); self.zoom.setValue(100)
        form=QFormLayout(); form.addRow("Left",self.left); form.addRow("Top",self.top); form.addRow("Zoom",self.zoom); lay.addLayout(form)
        row=QHBoxLayout(); self.apply=QPushButton("Apply crop"); self.cancel=QPushButton("Cancel"); row.addStretch(1); row.addWidget(self.apply); row.addWidget(self.cancel); lay.addLayout(row)
        self.left.valueChanged.connect(self.update_preview); self.top.valueChanged.connect(self.update_preview); self.zoom.valueChanged.connect(self.update_preview); self.apply.clicked.connect(self.accept); self.cancel.clicked.connect(self.reject); self.update_preview()
    def crop_box(self):
        img=Image.open(self.path); w,h=img.size; target=3/1; zoom=self.zoom.value()/100
        cw=int(w/zoom); ch=int(cw/target)
        if ch>h:
            ch=int(h/zoom); cw=int(ch*target)
        cw=max(1,min(w,cw)); ch=max(1,min(h,ch)); x=int((w-cw)*self.left.value()/100); y=int((h-ch)*self.top.value()/100); return x,y,x+cw,y+ch
    def update_preview(self):
        try:
            img=Image.open(self.path).convert("RGB"); crop=img.crop(self.crop_box()).resize((480,160)); tmp=Path("logo_crop_preview_tmp.png"); crop.save(tmp); pix=QPixmap(str(tmp)); self.preview.setPixmap(pix.scaled(self.preview.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation))
        except Exception: pass
    def save_crop(self):
        img=Image.open(self.path).convert("RGB"); crop=img.crop(self.crop_box()).resize((480,160)); out=Path("logo.png"); crop.save(out); return str(out)

class SettingsPage(QWidget):
    def __init__(self, main):
        super().__init__(); self.main=main
        lay=QVBoxLayout(self); lay.setSpacing(0); lay.setContentsMargins(0,0,0,0)
        # Scroll area — all content inside, works on any screen size
        from PySide6.QtWidgets import QScrollArea
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._inner = QWidget()
        self._inner.setAutoFillBackground(True)
        self._inner.setObjectName("SettingsInner")

        self.root=QLineEdit(); self.choose=QPushButton(); self.choose.clicked.connect(self.choose_root); row=QHBoxLayout(); row.addWidget(self.root,1); row.addWidget(self.choose)
        self.flaresolverr_url=QLineEdit(); self.flaresolverr_url.setPlaceholderText("http://localhost:8191  (оставь пустым если не используешь)")
        self.fs_test_inline=QPushButton("Проверить"); self.fs_test_inline.clicked.connect(self._test_flaresolverr); self.fs_test_inline.setMaximumWidth(100)
        _fs_row=QHBoxLayout(); _fs_row.addWidget(self.flaresolverr_url,1); _fs_row.addWidget(self.fs_test_inline)
        self.lang=QComboBox(); self.lang.setObjectName("SettingsCombo"); self.lang.setView(QListView()); self.lang.addItem("Русский","ru"); self.lang.addItem("English","en")
        self.appearance=QComboBox(); self.appearance.setObjectName("SettingsCombo"); self.appearance.setView(QListView()); self.appearance.addItem("Abyss (тёмный синий)","abyss"); self.appearance.addItem("Ember (тёмный янтарный)","ember"); self.appearance.addItem("Slate (нейтральный серый)","slate"); self.appearance.addItem("Sakura (тёмно-розовый)","sakura"); self.appearance.addItem("PornHub (чёрный+оранжевый)","pornhub"); self.appearance.addItem("R34 / Old web green","r34"); self.appearance.addItem("R34 Dark / Old web night","r34dark"); self.appearance.addItem("Windows 95","win95"); self.appearance.addItem("Light (светлый)","light")
        self.title=QLineEdit(); self.logo=QLineEdit(); self.logo_fit=QComboBox(); self.logo_fit.addItem("Crop","crop"); self.logo_fit.addItem("Contain","contain"); self.logo_choose=QPushButton(); self.logo_choose.clicked.connect(self.choose_logo); self.logo_crop=QPushButton(); self.logo_crop.clicked.connect(self.crop_logo)
        lrow=QHBoxLayout(); lrow.addWidget(self.logo,1); lrow.addWidget(self.logo_choose); lrow.addWidget(self.logo_crop)
        self.output_dir=QLineEdit(); self.choose_output=QPushButton("..."); self.choose_output.clicked.connect(self.choose_output_dir); outrow=QHBoxLayout(); outrow.addWidget(self.output_dir,1); outrow.addWidget(self.choose_output)
        self.copy_results=QCheckBox()
        self.debug_logging=QCheckBox()
        self.debug_logging.setChecked(False)

        self.cols=QSpinBox(); self.cols.setRange(1,12); self.rows=QSpinBox(); self.rows.setRange(1,20); self.card=QSpinBox(); self.card.setRange(100,700); self.ignore_numeric=QCheckBox(); self.show_preview=QCheckBox(); self.error_console=QCheckBox(); self.max_console_lines=QSpinBox(); self.max_console_lines.setRange(200,20000); self.max_console_lines.setSingleStep(100); self.manga_root=QLineEdit(); self.choose_manga=QPushButton("..."); self.choose_manga.clicked.connect(self.choose_manga_root); mrow=QHBoxLayout(); mrow.addWidget(self.manga_root,1); mrow.addWidget(self.choose_manga)
        self.games_root=QLineEdit(); self.choose_games=QPushButton("..."); self.choose_games.clicked.connect(self.choose_games_root); grow=QHBoxLayout(); grow.addWidget(self.games_root,1); grow.addWidget(self.choose_games)
        # Bare checkbox fields: show only the indicator, never a painted tail.
        for _cb in (self.copy_results, self.debug_logging, self.ignore_numeric, self.show_preview, self.error_console):
            _cb.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            _cb.setFixedSize(23, 23)
        for _spin in (self.cols, self.rows, self.card, self.max_console_lines):
            _spin.setMaximumWidth(180)
        self.lang.setMaximumWidth(360)
        self.appearance.setMaximumWidth(420)
        self.logo_fit.setMaximumWidth(240)
        self.title.setMaximumWidth(620)
        self.form = QFormLayout()
        self.form.setContentsMargins(6, 5, 6, 5)
        self.form.setHorizontalSpacing(8)
        self.form.setVerticalSpacing(5)
        _ilay = QVBoxLayout(self._inner)
        _ilay.setContentsMargins(6, 6, 6, 6)
        _ilay.setSpacing(5)
        self.form_rows=[]
        for key, w, tip in [
            ("Images folder", row, "tip_root"),
            ("Language", self.lang, "tip_language"),
            ("Appearance", self.appearance, "tip_appearance"),
            ("Title", self.title, "tip_title"),
            ("Logo path", lrow, "tip_logo"),
            ("Logo fit", self.logo_fit, "tip_logo_fit"),
            ("Columns", self.cols, "tip_columns"),
            ("Rows/page", self.rows, "tip_rows"),
            ("Card height", self.card, "tip_card"),
            ("Copy results", self.copy_results, "tip_copy_results"),
            ("Debug logging", self.debug_logging, "tip_debug_logging"),
            ("Output folder", outrow, "tip_output_folder"),
            ("Ignore numeric tags", self.ignore_numeric, "tip_numeric"),
            ("Search preview", self.show_preview, "tip_preview"),
            ("Console line limit", self.max_console_lines, "tip_console_limit"),
            ("Error console", self.error_console, "tip_error_console"),
            ("Manga folder", mrow, "tip_manga_root"),
            ("Папка игр", grow, "tip_games_root")
        ]:
            self.add_tip_row(key, w, tip)
        # Wrap form and buttons in scroll area for any screen size
        from PySide6.QtWidgets import QScrollArea as _SA
        _scroll = _SA(); _scroll.setWidgetResizable(True)
        _scroll.setFrameShape(_SA.Shape.NoFrame)
        _inner = __import__("PySide6.QtWidgets",fromlist=["QWidget"]).QWidget()
        _ilay.addLayout(self.form)

        # FlareSolverr is not useful for Danbooru, but the current Ascii2D
        # fallback can still use it. Keep it available without wasting space on
        # the main settings screen.
        self.advanced_btn = QPushButton("Дополнительные настройки")
        self.advanced_btn.setCheckable(True)
        self.advanced_box = QGroupBox()
        _advanced_form = QFormLayout(self.advanced_box)
        _advanced_form.setContentsMargins(6, 6, 6, 6)
        _advanced_form.setSpacing(5)
        _advanced_form.addRow("FlareSolverr (Ascii2D / совместимость)", _fs_row)
        self.advanced_box.setVisible(False)
        self.advanced_btn.toggled.connect(self.advanced_box.setVisible)
        _ilay.addWidget(self.advanced_btn)
        _ilay.addWidget(self.advanced_box)

        _primary_row = QHBoxLayout()
        self.save_btn=QPushButton(); self.save_btn.clicked.connect(self.save); _primary_row.addWidget(self.save_btn, 1)
        self.instruction_btn=QPushButton("Инструкция"); self.instruction_btn.clicked.connect(self.show_instruction); _primary_row.addWidget(self.instruction_btn, 1)
        _ilay.addLayout(_primary_row)

        self.maintenance_box = QGroupBox("Обслуживание библиотеки")
        _maintenance = QVBoxLayout(self.maintenance_box)
        _maintenance.setContentsMargins(6, 8, 6, 6)
        _maintenance.setSpacing(4)
        _mrow1 = QHBoxLayout()
        self.rebuild_sql_btn=QPushButton("Пересобрать SQLite-индекс")
        self.rebuild_sql_btn.clicked.connect(self.rebuild_sql_index)
        self.rebuild_vptree_btn = QPushButton("Пересобрать VP-tree")
        self.rebuild_vptree_btn.clicked.connect(self._rebuild_vptree)
        _mrow1.addWidget(self.rebuild_sql_btn, 1); _mrow1.addWidget(self.rebuild_vptree_btn, 1)
        _maintenance.addLayout(_mrow1)
        _mrow2 = QHBoxLayout()
        self.sql_optimize_btn=QPushButton("Оптимизировать SQLite")
        self.sql_optimize_btn.clicked.connect(self.optimize_sqlite)
        self.sql_stats_btn=QPushButton("Статистика SQLite")
        self.sql_stats_btn.clicked.connect(self.show_sqlite_stats)
        _mrow2.addWidget(self.sql_optimize_btn, 1); _mrow2.addWidget(self.sql_stats_btn, 1)
        _maintenance.addLayout(_mrow2)
        _mrow3 = QHBoxLayout()
        self.integrity_check_btn=QPushButton("Проверить целостность")
        self.integrity_check_btn.clicked.connect(self.check_library_integrity)
        self.integrity_repair_btn=QPushButton("Починить безопасные ошибки")
        self.integrity_repair_btn.clicked.connect(self.repair_library_integrity)
        _mrow3.addWidget(self.integrity_check_btn, 1); _mrow3.addWidget(self.integrity_repair_btn, 1)
        _maintenance.addLayout(_mrow3)
        self.sql_status=QLabel("")
        self.sql_status.setWordWrap(True)
        self.sql_status.setVisible(False)
        _maintenance.addWidget(self.sql_status)
        _ilay.addWidget(self.maintenance_box)

        self.danger=QLabel("Удаление данных")
        self.danger.setStyleSheet("font-size:16px;font-weight:900;color:#ff3838;margin-top:12px")
        _ilay.addWidget(self.danger)

        self.tag_cleanup_info = QLabel(
            "Удаление по тегу или источнику. Сначала выбери вариант и нажми «Показать связанные файлы». "
            "Удаляются только показанные результаты и их служебные данные. Исходная папка не затрагивается."
        )
        self.tag_cleanup_info.setWordWrap(True)
        _ilay.addWidget(self.tag_cleanup_info)

        self.tag_cleanup_scope = QComboBox()
        self.tag_cleanup_scope.addItem("Все результаты", "all")
        self.tag_cleanup_scope.addItem("Только результаты парсера", "tagger")
        self.tag_cleanup_scope.addItem("Только результаты загрузчика", "downloader")
        self.tag_cleanup_scope.addItem("Только найденные", "found")
        self.tag_cleanup_scope.addItem("Только не найденные", "no_match")
        self.tag_cleanup_kind = QComboBox()
        self.tag_cleanup_kind.addItem("Тег", "tag")
        self.tag_cleanup_kind.addItem("Source", "source")
        self.tag_cleanup_kind.currentIndexChanged.connect(self.refresh_cleanup_candidates)
        self.tag_cleanup_scope.currentIndexChanged.connect(self.refresh_cleanup_candidates)

        self.tag_cleanup_query = QLineEdit()
        self.tag_cleanup_query.setPlaceholderText("начни писать и выбери тег/source из списка")
        self.cleanup_candidate_model = QStringListModel(self)
        self.cleanup_candidate_proxy = ContainsFilterProxyModel(self)
        self.cleanup_candidate_proxy.setSourceModel(self.cleanup_candidate_model)
        self.cleanup_completer = QCompleter(self.cleanup_candidate_proxy, self)
        self.cleanup_completer.setCaseSensitivity(Qt.CaseInsensitive)
        try:
            self.cleanup_completer.setFilterMode(Qt.MatchContains)
        except Exception:
            pass
        self.cleanup_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.tag_cleanup_query.setCompleter(self.cleanup_completer)
        self.tag_cleanup_query.textEdited.connect(self.update_cleanup_completion)

        self.tag_cleanup_find_btn = QPushButton("Показать связанные файлы")
        self.tag_cleanup_find_btn.clicked.connect(self.find_tag_cleanup_matches)
        _target_row = QHBoxLayout()
        _target_row.addWidget(self.tag_cleanup_scope, 2)
        _target_row.addWidget(self.tag_cleanup_kind, 1)
        _target_row.addWidget(self.tag_cleanup_query, 4)
        _target_row.addWidget(self.tag_cleanup_find_btn, 2)
        _ilay.addLayout(_target_row)

        self.tag_cleanup_list = QListWidget()
        self.tag_cleanup_list.setMaximumHeight(120)
        self.tag_cleanup_list.setVisible(False)
        _ilay.addWidget(self.tag_cleanup_list)

        self.tag_cleanup_delete_btn = QPushButton("Удалить показанные связанные файлы")
        self.tag_cleanup_delete_btn.setEnabled(False)
        self.tag_cleanup_delete_btn.setStyleSheet("QPushButton{background:#7f1d1d;border:1px solid #ff3838;color:white;font-weight:900}QPushButton:disabled{background:#2a2020;color:#777}")
        self.tag_cleanup_delete_btn.clicked.connect(self.delete_tag_cleanup_matches)
        self.tag_cleanup_delete_btn.setVisible(False)
        _ilay.addWidget(self.tag_cleanup_delete_btn)

        self.output_cleanup_info = QLabel(
            "Или очистить раздел целиком. Удаляются результаты и их записи в базе, исходная папка не затрагивается."
        )
        self.output_cleanup_info.setWordWrap(True)
        _ilay.addWidget(self.output_cleanup_info)

        _parser_delete_row = QHBoxLayout()
        self.delete_mode = QComboBox()
        self.delete_mode.addItem("Все результаты парсера", "all")
        self.delete_mode.addItem("Только найденные/частичные", "found")
        self.delete_mode.addItem("Только не найденные", "no_match")
        self.delete_results_btn = QPushButton("Удалить результаты парсера")
        self.delete_results_btn.clicked.connect(self.delete_tags)
        _parser_delete_row.addWidget(self.delete_mode, 1)
        _parser_delete_row.addWidget(self.delete_results_btn)
        _ilay.addLayout(_parser_delete_row)

        _downloader_delete_row = QHBoxLayout()
        self.downloader_delete_mode = QComboBox()
        self.downloader_delete_mode.addItem("Все результаты загрузчика", "all")
        self.downloader_delete_mode.addItem("Только найденные", "found")
        self.downloader_delete_mode.addItem("Только частичные", "partial_match")
        self.downloader_delete_mode.addItem("Только не найденные", "no_match")
        self.delete_downloader_btn = QPushButton("Удалить результаты загрузчика")
        self.delete_downloader_btn.clicked.connect(self.delete_downloader_results)
        _downloader_delete_row.addWidget(self.downloader_delete_mode, 1)
        _downloader_delete_row.addWidget(self.delete_downloader_btn)
        _ilay.addLayout(_downloader_delete_row)

        self.video_note=QLabel()  # Текст перенесён в инструкцию, здесь больше не занимает место.

        # ── NUKE: delete everything ───────────────────────────────────────────
        _cleanup_btn = QPushButton("Удалить записи без файлов")
        _cleanup_btn.setToolTip("Убирает из БД записи на файлы которые уже удалены с диска")
        def _do_cleanup():
            try:
                from core.database.storage import cleanup_missing
                n = cleanup_missing(self.main.settings)
                QMessageBox.information(self, "Готово", f"Удалено {n} записей без файлов.")
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", str(e))
        _cleanup_btn.clicked.connect(_do_cleanup)
        _ilay.addWidget(_cleanup_btn)

        _nuke_label = QLabel("")
        _nuke_label.setVisible(False)

        _nuke_info = QLabel(
            "Полный сброс: удаляет все файлы, теги, источники, кэш и базу данных. "
            "Для подтверждения введи слово DELETE."
        )
        _nuke_info.setWordWrap(True)
        _ilay.addWidget(_nuke_info)

        _nuke_row = QHBoxLayout()
        self.nuke_confirm_input = QLineEdit()
        self.nuke_confirm_input.setPlaceholderText("введи DELETE для разблокировки")
        self.nuke_confirm_input.setMaximumWidth(260)

        self.nuke_subs_check = QCheckBox("включая подписки")
        self.nuke_subs_check.setChecked(True)
        self.nuke_subs_check.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.nuke_btn = QPushButton("Удалить всё")
        self.nuke_btn.setEnabled(False)
        self.nuke_btn.setStyleSheet(
            "QPushButton{background:#7f0000;border:2px solid #ff3838;color:white;"
            "font-weight:900;font-size:13px;padding:4px 12px}"
            "QPushButton:enabled{background:#b91c1c}"
            "QPushButton:disabled{background:#2a1010;color:#555}"
        )

        def _nuke_check(txt):
            self.nuke_btn.setEnabled(txt.strip() == "DELETE")
        self.nuke_confirm_input.textChanged.connect(_nuke_check)
        self.nuke_btn.clicked.connect(self._nuke_everything)

        _nuke_row.addWidget(self.nuke_confirm_input)
        _nuke_row.addWidget(self.nuke_subs_check)
        _nuke_row.addWidget(self.nuke_btn)
        _nuke_row.addStretch()
        _ilay.addLayout(_nuke_row)
        # ─────────────────────────────────────────────────────────────────────

        _ilay.addStretch(1)
        # Use Qt-painted popup views, not platform-native combo popups.
        # Native popups can keep old theme/shadow colors and create black bars
        # on R34/Win95 after restart or theme switching.
        try:
            for _cb in self.findChildren(QComboBox):
                _cb.setView(QListView())
                _cb.setMaxVisibleItems(12)
        except Exception:
            pass
        self._scroll.setWidget(self._inner)
        self._scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        lay.addWidget(self._scroll, 1)
        self.load_values(); self.retranslate(); self.apply_tips(); self.refresh_cleanup_candidates()
        self._install_wheel_guards()

    def _install_wheel_guards(self):
        """Mouse wheel scrolls the settings page, not values under the cursor."""
        for widget_type in (QComboBox, QAbstractSpinBox, QSlider):
            for widget in self.findChildren(widget_type):
                widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel and isinstance(watched, (QComboBox, QAbstractSpinBox, QSlider)):
            try:
                bar = self._scroll.verticalScrollBar()
                delta = event.angleDelta().y()
                bar.setValue(bar.value() - delta)
                event.accept()
                return True
            except Exception:
                return True
        return super().eventFilter(watched, event)

    def add_tip_row(self, label_key, widget, tip_key):
        lab=QLabel(self.main.t(label_key)+"  ?")
        lab.setToolTip(self.main.t(tip_key))
        _tc = self.main.settings.get("appearance","abyss")
        _label_colors = {"light": ("#1a1c2a","#5060d0"), "dark": ("#c0c8e0","#6c85e0"), "abyss": ("#c0c8e0","#6c85e0"),
                         "r34": ("#111111","#3a7a35"), "r34dark": ("#d6e4d3","#6aa5ff"), "win95": ("#000000","#000080"), "windows95": ("#000000","#000080"),
                         "ph": ("#f5f5f5","#ff9000"), "pornhub": ("#f5f5f5","#ff9000"),
                         "ember": ("#c8b090","#c87040"), "slate": ("#b0c8d0","#5a8a9f"),
                         "sakura": ("#e0b0d0","#d060a0")}
        _lc, _hc = _label_colors.get(_tc, ("#c0c8e0","#6c85e0"))
        lab.setStyleSheet(f"QLabel{{font-weight:700;min-width:165px;color:{_lc};}} QLabel:hover{{color:{_hc};}}")
        lab.setMinimumWidth(165)
        if hasattr(widget, "setToolTip"):
            widget.setToolTip(self.main.t(tip_key))
        self.form.addRow(lab, widget)
        self.form_rows.append((lab,label_key,widget,tip_key))
    def set_tip(self, widget, key): widget.setToolTip(self.main.t(key))
    def apply_tips(self):
        self.set_tip(self.root,"tip_root"); self.set_tip(self.ignore_numeric,"tip_numeric"); self.set_tip(self.cols,"tip_root"); self.set_tip(self.rows,"tip_root")
    def retranslate(self):
        self.apply_theme_style(self.main.settings.get("appearance", "abyss"))
        t=self.main.t; self.choose.setText(t("Choose")); self.logo_choose.setText(t("Choose")); self.logo_crop.setText(t("Crop logo")); self.save_btn.setText(t("Save settings")); self.danger.setText("Удаление данных" if self.main.settings.get("language","ru") == "ru" else "Data deletion")


        self.apply_tips()
        for lab, label_key, w, tip_key in getattr(self, "form_rows", []):
            lab.setText(t(label_key) + "  ?")
            lab.setToolTip(t(tip_key))

            if hasattr(w, "setToolTip"):
                w.setToolTip(t(tip_key))
    def apply_theme_style(self, theme_name=None):
        """Refresh only settings-page inline labels/palette after theme switch.

        Important: do NOT style QScrollArea children with broad selectors here.
        Broad local selectors such as ``QScrollArea > QWidget > QWidget`` override the
        global theme QSS for QLineEdit/QComboBox/QSpinBox and create the "masked
        lines" bug after restart. The real widget styling must stay in
        ui/styles/themes.py.
        """
        theme_name = theme_name or self.main.settings.get("appearance", "abyss")
        colors = {
            "light": ("#f4f5f8", "#1a1c2a", "#5060d0"),
            "dark": ("#0d0f16", "#c0c8e0", "#6c85e0"),
            "abyss": ("#0d0f16", "#c0c8e0", "#6c85e0"),
            "r34": ("#a8d99f", "#111111", "#3a7a35"),
            "r34dark": ("#10150f", "#d6e4d3", "#6aa5ff"),
            "win95": ("#c0c0c0", "#000000", "#000080"),
            "windows95": ("#c0c0c0", "#000000", "#000080"),
            "ph": ("#0f0f0f", "#f5f5f5", "#ff9000"),
            "pornhub": ("#0f0f0f", "#f5f5f5", "#ff9000"),
            "ember": ("#14141e", "#c8b090", "#c87040"),
            "slate": ("#16181e", "#b0c8d0", "#5a8a9f"),
            "sakura": ("#140820", "#e0b0d0", "#d060a0"),
        }
        bg, fg, hover = colors.get(theme_name, colors["abyss"])
        try:
            from PySide6.QtGui import QPalette, QColor
            for w in (self, self._inner, self._scroll, self._scroll.viewport()):
                if w is None:
                    continue
                pal = w.palette()
                pal.setColor(QPalette.ColorRole.Window, QColor(bg))
                pal.setColor(QPalette.ColorRole.Base, QColor(bg))
                pal.setColor(QPalette.ColorRole.Text, QColor(fg))
                pal.setColor(QPalette.ColorRole.WindowText, QColor(fg))
                pal.setColor(QPalette.ColorRole.ButtonText, QColor(fg))
                w.setPalette(pal)
                w.setAutoFillBackground(True)
            self._inner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            self._inner.setStyleSheet(f"QWidget#SettingsInner{{background:{bg}; color:{fg}; border:none;}}")
            self._scroll.setStyleSheet("QScrollArea{border:none; background:transparent;}")
            self._scroll.viewport().setStyleSheet("")
        except Exception:
            pass
        try:
            for lab, label_key, w, tip_key in getattr(self, "form_rows", []):
                lab.setStyleSheet(f"QLabel{{font-weight:700;min-width:165px;color:{fg};background:transparent;}} QLabel:hover{{color:{hover};}}")
        except Exception:
            pass
        try:
            # Force combo popups to recreate/repolish the non-native list views.
            for cb in self.findChildren(QComboBox):
                cb.setMaxVisibleItems(12)
                view = cb.view()
                if view is not None:
                    view.setPalette(cb.palette())
                    view.setStyleSheet("")
                    view.update()
        except Exception:
            pass

    def _nuke_everything(self):
        include_subs = self.nuke_subs_check.isChecked()
        extra = " и папку подписок" if include_subs else ""
        msg = ("Это удалит ВСЕ файлы, теги, источники, кэш и базу данных" + extra + "."
               + chr(10) + chr(10) + "Это действие НЕОБРАТИМО. Продолжить?")
        reply = QMessageBox.question(
            self, "ВЫ ТОЧНО УВЕРЕНЫ?", msg,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        import shutil
        from pathlib import Path
        from core.paths import result_output_base
        from core.database.connection import db_path

        settings = self.main.settings
        from core.paths import result_output_base as _rob
        out = _rob(settings)
        # Also check output_dir directly in case paths differ
        out_direct = Path(settings.get("output_dir", ""))
        deleted = []
        errors = []

        # Collect unique candidate paths for each bucket
        for bucket in ("found", "partial_match", "no_match", "downloads"):
            candidates = [out / bucket]
            if out_direct and out_direct != out:
                candidates.append(out_direct / bucket)
            for p in candidates:
                if p.exists():
                    try:
                        shutil.rmtree(p)
                        deleted.append(str(p))
                        break  # deleted, no need to try other candidate
                    except Exception as e:
                        errors.append(str(p) + ": " + str(e))

        if include_subs:
            subs_dir = Path(settings.get("output_dir", str(out))) / "subscriptions"
            if subs_dir.exists():
                try:
                    shutil.rmtree(subs_dir)
                    deleted.append(str(subs_dir))
                except Exception as e:
                    errors.append(str(e))
            try:
                from core.subscriptions import SUBS_FILE
                from core.subscription_engine.seed_cache import SEED_CACHE_DB, OLD_JSON_FILE
                for state_file in (SUBS_FILE, SEED_CACHE_DB, Path(str(SEED_CACHE_DB) + "-wal"), Path(str(SEED_CACHE_DB) + "-shm"), OLD_JSON_FILE):
                    if Path(state_file).exists():
                        Path(state_file).unlink()
                        deleted.append(str(state_file))
            except Exception as e:
                errors.append("Подписки: " + str(e))

        # SQLite in WAL mode can keep the database locked on Windows. Close
        # pooled handles first and remove WAL/SHM sidecars with the main DB.
        try:
            from core.database import connection as _dbc
            _dbc.close_pooled_connections()
            db = Path(db_path(settings))
            for db_file in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
                if db_file.exists():
                    db_file.unlink()
                    deleted.append(str(db_file))
            _dbc._INIT_DONE.clear()
        except Exception as e:
            errors.append("БД: " + str(e))

        # The button says that cache is deleted, so also remove the persistent
        # thumbnail/parser cache rather than only per-bucket sidecars.
        try:
            from core.paths import CACHE_DIR
            if Path(CACHE_DIR).exists():
                shutil.rmtree(CACHE_DIR)
                Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
                deleted.append(str(CACHE_DIR))
        except Exception as e:
            errors.append("Кэш: " + str(e))
        self.nuke_confirm_input.clear()
        summary = "Удалено " + str(len(deleted)) + " объектов."
        if errors:
            summary += chr(10) + "Ошибки:" + chr(10) + chr(10).join(errors)
        QMessageBox.information(self, "Готово", summary)

        try:
            gallery = getattr(self.main, "gallery_page", None)
            if gallery and hasattr(gallery, "refresh"):
                gallery.refresh()
        except Exception:
            pass


    def load_values(self):
        s=self.main.settings; self.root.setText(s.get("root","C:/Local_Booru_Input")); self.title.setText(s.get("theme_title","Local Booru")); self.logo.setText(s.get("logo_path","")); self.cols.setValue(int(s.get("columns",4))); self.rows.setValue(int(s.get("rows_per_page",4))); self.card.setValue(int(s.get("card_height",220))); self.ignore_numeric.setChecked(bool(s.get("ignore_numeric_tags",False))); self.show_preview.setChecked(bool(s.get("show_search_preview", True))); self.error_console.setChecked(bool(s.get("enable_error_console", True))); self.max_console_lines.setValue(int(s.get("max_console_lines",2500))); self.manga_root.setText(s.get("manga_root",""))
        self.flaresolverr_url.setText(s.get("flaresolverr_url","")); self.games_root.setText(s.get("games_root","")); self.output_dir.setText(s.get("output_dir","")); self.copy_results.setChecked(bool(s.get("copy_results_enabled", True)))
        self.lang.setCurrentIndex(max(0,self.lang.findData(s.get("language","ru")))); self.appearance.setCurrentIndex(max(0,self.appearance.findData(s.get("appearance","dark")))); self.logo_fit.setCurrentIndex(max(0,self.logo_fit.findData(s.get("logo_fit","crop"))))
    def choose_root(self):
        f=QFileDialog.getExistingDirectory(self,self.main.t("Choose"),self.root.text())
        if f: self.root.setText(f)
    def choose_manga_root(self):
        f=QFileDialog.getExistingDirectory(self,self.main.t("Choose"),self.manga_root.text() or self.root.text())
        if f: self.manga_root.setText(f)

    def choose_output_dir(self):
        f=QFileDialog.getExistingDirectory(self,self.main.t("Choose"),self.output_dir.text() or self.root.text())
        if f:
            from core.paths import ensure_output_base
            safe = ensure_output_base(f, self.root.text())
            self.output_dir.setText(str(safe))
            QMessageBox.information(self, "Output", f"Файлы будут складываться в:\n{safe}")

    def choose_games_root(self):
        f=QFileDialog.getExistingDirectory(self,self.main.t("Choose"),self.games_root.text() or self.root.text())
        if f: self.games_root.setText(f)

    def choose_logo(self):
        f,_=QFileDialog.getOpenFileName(self,self.main.t("Choose"),self.logo.text(),"Images (*.png *.jpg *.jpeg *.webp)")
        if f: self.logo.setText(f)
    def crop_logo(self):
        if not Path(self.logo.text()).exists(): return
        dlg=LogoCropDialog(self.logo.text(), self)
        dlg.setWindowTitle(self.main.t("Logo crop editor")); dlg.apply.setText(self.main.t("Apply crop"));
        if dlg.exec():
            self.logo.setText(dlg.save_crop()); self.logo_fit.setCurrentIndex(max(0,self.logo_fit.findData("crop")))
    def save(self):
        s=self.main.settings; s["root"]=self.root.text(); s["language"]=self.lang.currentData(); s["appearance"]=self.appearance.currentData(); s["theme_title"]=self.title.text(); s["logo_path"]=self.logo.text(); s["logo_fit"]=self.logo_fit.currentData(); s["columns"]=self.cols.value(); s["rows_per_page"]=self.rows.value(); s["items_per_page"]=self.cols.value()*self.rows.value(); s["card_height"]=self.card.value(); s["ignore_numeric_tags"]=self.ignore_numeric.isChecked(); s["show_search_preview"]=self.show_preview.isChecked(); s["enable_error_console"]=self.error_console.isChecked(); s["max_console_lines"]=self.max_console_lines.value(); s["manga_root"]=self.manga_root.text(); s["games_root"]=self.games_root.text(); s["output_dir"]=self.output_dir.text(); s["flaresolverr_url"]=self.flaresolverr_url.text().strip(); s["copy_results_enabled"]=self.copy_results.isChecked(); save_settings(s); self.main.gallery_page.items=[]; self.main.tags_page.items=[]; self.main.apply_theme(); self.main.retranslate(); QMessageBox.information(self,self.main.t("Saved"),self.main.t("Settings saved"))

    def _run_maintenance(self):
        from core.stability import run_file_maintenance
        log_lines = []
        def _log(m): log_lines.append(m)
        stats = run_file_maintenance(self.main.settings, log=_log, max_check=99999)
        msg = (f"Проверено: {stats['checked']} файлов\n"
               f"Отсутствует: {stats['missing']} (помечены удалёнными)\n"
               f"Ошибок: {stats['errors']}")
        QMessageBox.information(self, "File Maintenance", msg)

    def _test_flaresolverr(self):
        from core.flaresolverr import FlareSolverrClient
        url = self.flaresolverr_url.text().strip() or "http://localhost:8191"
        client = FlareSolverrClient(url)
        if client.is_running():
            QMessageBox.information(self, "FlareSolverr",
                f"✅ FlareSolverr доступен на {url}\n\n"
                "Теперь ascii2d и другие CF-сайты будут работать через реальный Chrome.")
        else:
            QMessageBox.warning(self, "FlareSolverr",
                f"❌ FlareSolverr недоступен на {url}\n\n"
                "Установка:\n"
                "1. Скачай с github.com/FlareSolverr/FlareSolverr/releases\n"
                "2. Распакуй и запусти flaresolverr.exe\n"
                "3. Или: docker run -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest\n"
                "4. Укажи URL выше и нажми Сохранить настройки")

    def _backup_db(self):
        from core.stability import maybe_backup_db
        backup = maybe_backup_db(self.main.settings, max_backups=10)
        if backup:
            from pathlib import Path
            QMessageBox.information(self, "Резервная копия",
                f"Бэкап создан:\n{Path(backup).name}\n{Path(backup).stat().st_size//1024} KB")
        else:
            QMessageBox.information(self, "Резервная копия",
                "Бэкап уже создавался в последние 24 часа.\nДля принудительного — удали файлы в data/db_backups/")

    def _rebuild_vptree(self):
        from core.database.connection import get_connection
        from core.vptree import VPTree
        from PySide6.QtWidgets import QProgressDialog, QApplication
        conn = get_connection(self.main.settings)
        dlg = QProgressDialog("Пересборка VP-tree...", "Отмена", 0, 100, self)
        dlg.setWindowTitle("VP-tree")
        dlg.setMinimumDuration(0)
        dlg.show()
        def progress(done, total):
            if total > 0:
                dlg.setValue(int(100 * done / total))
            QApplication.processEvents()
        count = VPTree(conn).rebuild(progress_cb=progress)
        dlg.setValue(100)
        QMessageBox.information(self, "VP-tree", f"Готово! Проиндексировано: {count} файлов.")

    def rebuild_sql_index(self):
        self.rebuild_sql_btn.setEnabled(False)
        self.sql_status.setVisible(True); self.sql_status.setText("SQLite: индексация запущена...")
        try:
            from core.services.index_service import rebuild_index
            settings = dict(self.main.settings or {})
            task = self.main.task_manager.submit(
                rebuild_index,
                settings,
                force=True,
                with_md5=False,
                on_progress=lambda msg: self.sql_status.setText("SQLite: " + str(msg)),
                on_result=self._sql_index_done,
                on_error=self._sql_index_error,
            )
            self._sql_index_task = task
        except Exception as e:
            self.rebuild_sql_btn.setEnabled(True)
            self.sql_status.setText(f"SQLite ERROR: {e}")

    def _sql_index_done(self, result):
        self.rebuild_sql_btn.setEnabled(True)
        self.sql_status.setText(
            "SQLite готов: "
            f"scanned={result.get('scanned', 0)} "
            f"indexed={result.get('indexed', 0)} "
            f"skipped={result.get('skipped', 0)} "
            f"removed={result.get('removed', 0)}"
        )
        try:
            self.main.gallery_page.items = []
            self.main.gallery_page.refresh_force()
        except Exception:
            pass

    def _sql_index_error(self, text):
        self.rebuild_sql_btn.setEnabled(True)
        self.sql_status.setText("SQLite ERROR:\n" + str(text)[-3000:])

    def show_instruction(self):
        dlg = InstructionDialog(self.main, self)
        dlg.exec()


    def _cleanup_roots_for_scope(self, scope):
        out = result_output_base(self.main.settings)
        roots = []
        tagger_buckets = ["found", "partial_match", "no_match"]
        downloader_buckets = ["found", "partial_match", "no_match"]
        if scope in ("all", "tagger", "found", "no_match"):
            for b in tagger_buckets:
                if scope == "found" and b == "no_match":
                    continue
                if scope == "no_match" and b != "no_match":
                    continue
                roots.append(out / b)
        if scope in ("all", "downloader", "found", "no_match"):
            for b in downloader_buckets:
                if scope == "found" and b == "no_match":
                    continue
                if scope == "no_match" and b != "no_match":
                    continue
                roots.append(out / "downloads" / b)
        return roots

    def _tags_from_file(self, f):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []
        if f.suffix.lower() == ".json":
            try:
                data = json.loads(text)
                found = []
                def walk(x):
                    if isinstance(x, dict):
                        for v in x.values():
                            walk(v)
                    elif isinstance(x, (list, tuple)):
                        for v in x:
                            walk(v)
                    elif isinstance(x, str):
                        found.append(x)
                walk(data)
                return found
            except Exception:
                pass
        return re.split(r"[\s,;]+", text)

    def _sources_from_file(self, f):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []
        found = []
        if f.suffix.lower() == ".json":
            try:
                data = json.loads(text)
                def walk(x):
                    if isinstance(x, dict):
                        for k, v in x.items():
                            if "source" in str(k).lower() or "url" in str(k).lower():
                                if isinstance(v, str):
                                    found.append(v)
                            walk(v)
                    elif isinstance(x, (list, tuple)):
                        for v in x:
                            walk(v)
                    elif isinstance(x, str):
                        if "://" in x or "." in x:
                            found.append(x)
                walk(data)
            except Exception:
                pass
        for m in re.findall(r"https?://[^\s\"'<>]+", text):
            found.append(m)
        for line in text.splitlines():
            line = line.strip()
            if line and ("." in line or "source" in line.lower()):
                found.append(line)
        clean = []
        seen = set()
        for s in found:
            s = str(s).strip().strip(",;")
            if not s:
                continue
            if s not in seen:
                seen.add(s)
                clean.append(s)
        return clean

    def _media_for_stem(self, bucket_dir, stem):
        media = bucket_dir / "media"
        if not media.exists():
            return []
        return [f for f in media.iterdir() if f.is_file() and f.stem == stem]

    def _sidecars_for_stem(self, bucket_dir, stem):
        files = []
        for sub in ("tags", "source", "searched", "cache"):
            d = bucket_dir / sub
            if not d.exists():
                continue
            for f in d.iterdir():
                if f.is_file() and (f.stem == stem or f.stem.startswith(stem + ".")):
                    files.append(f)
        return files

    def _cleanup_process_events(self):
        try:
            QApplication.processEvents()
        except Exception:
            pass

    def _candidate_values(self, kind):
        scope = self.tag_cleanup_scope.currentData() or "all"
        try:
            if kind == "source":
                from core.database.repository import candidate_sources
                return candidate_sources(self.main.settings, scope)
            from core.database.repository import candidate_tags
            return candidate_tags(self.main.settings, scope)
        except Exception as e:
            QMessageBox.warning(self, "SQLite", f"Не удалось прочитать варианты из SQLite:\n{e}")
            return []


    def refresh_cleanup_candidates(self):
        kind = self.tag_cleanup_kind.currentData() or "tag"
        vals = self._candidate_values(kind)
        self._cleanup_candidates = vals
        self.cleanup_candidate_model.setStringList(vals)
        self.update_cleanup_completion()

    def update_cleanup_completion(self):
        try:
            text = self.tag_cleanup_query.text().strip()
            self.cleanup_candidate_proxy.setFilterFixedString(text)
            self.cleanup_completer.setCompletionPrefix(text)
            if text:
                self.cleanup_completer.complete()
        except Exception:
            pass

    def _selected_cleanup_value(self):
        q = self.tag_cleanup_query.text().strip()
        if not q:
            return ""
        # For tags we compare normalized values, for source exact text is kept.
        kind = self.tag_cleanup_kind.currentData() or "tag"
        if kind == "tag":
            qn = normalize_tag(q)
            vals = getattr(self, "_cleanup_candidates", None) or self._candidate_values(kind)
            for v in vals:
                if normalize_tag(v).lower() == qn.lower():
                    return normalize_tag(v)
            return qn
        return q

    def _stem_from_sidecar(self, f):
        stem = f.stem
        for suffix in (".tags", ".raw", ".searched", ".sources", ".source"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
        return stem

    def _find_tag_cleanup_matches(self):
        value = self._selected_cleanup_value()
        if not value:
            return []
        kind = self.tag_cleanup_kind.currentData() or "tag"
        scope = self.tag_cleanup_scope.currentData() or "all"
        try:
            if kind == "source":
                from core.database.repository import find_images_by_source
                rows = find_images_by_source(self.main.settings, value, scope)
            else:
                from core.database.repository import find_images_by_tag
                rows = find_images_by_tag(self.main.settings, value, scope)
            return [{"id": r["id"], "path": r["path"], "file_name": r["file_name"], "bucket": r["bucket"], "hits": [value]} for r in rows]
        except Exception as e:
            QMessageBox.warning(self, "SQLite", f"Ошибка поиска связанных файлов:\n{e}")
            return []


    def find_tag_cleanup_matches(self):
        if not hasattr(self, "_cleanup_candidates"):
            self.refresh_cleanup_candidates()
        self.tag_cleanup_find_btn.setEnabled(False)
        self.tag_cleanup_find_btn.setText("Сканирование...")
        self.tag_cleanup_list.clear()
        self.tag_cleanup_list.setVisible(False)
        self.tag_cleanup_delete_btn.setEnabled(False)
        self.tag_cleanup_delete_btn.setVisible(False)
        try:
            self._cleanup_process_events()
            self._tag_cleanup_matches = self._find_tag_cleanup_matches()
        finally:
            self.tag_cleanup_find_btn.setEnabled(True)
            self.tag_cleanup_find_btn.setText("Показать связанные файлы")

        value = self._selected_cleanup_value()
        kind_label = "source" if (self.tag_cleanup_kind.currentData() == "source") else "tag"
        total = len(self._tag_cleanup_matches)
        shown_limit = 500
        self.tag_cleanup_list.setUpdatesEnabled(False)
        try:
            if total > shown_limit:
                self.tag_cleanup_list.addItem(f"Найдено: {total}. Показаны первые {shown_limit}, удаление применится ко всем найденным.")
            for m in self._tag_cleanup_matches[:shown_limit]:
                media_name = m.get("file_name") or Path(m.get("path", "")).name
                hit = ", ".join(str(x) for x in m.get("hits", [])[:3])
                self.tag_cleanup_list.addItem(f'{media_name}  |  {m.get("bucket", "")}  |  {kind_label}: {hit}')
        finally:
            self.tag_cleanup_list.setUpdatesEnabled(True)
        has_matches = bool(self._tag_cleanup_matches)
        self.tag_cleanup_list.setVisible(has_matches)
        self.tag_cleanup_delete_btn.setEnabled(has_matches)
        self.tag_cleanup_delete_btn.setVisible(has_matches)
        if not self._tag_cleanup_matches and value:
            QMessageBox.information(self, self.main.t("Done"), f"Связанных файлов не найдено для: {value}")

    def delete_tag_cleanup_matches(self):
        matches = getattr(self, "_tag_cleanup_matches", None)
        if matches is None:
            matches = self._find_tag_cleanup_matches()
        value = self._selected_cleanup_value()
        kind_label = "source" if (self.tag_cleanup_kind.currentData() == "source") else "tag"
        if not value or not matches:
            QMessageBox.information(self, self.main.t("Done"), "Ничего не найдено.")
            return
        if QMessageBox.warning(
            self,
            self.main.t("Confirm"),
            f"Удалить {len(matches)} файлов/результатов, связанных с {kind_label}: {value!r}?\n"
            "Будут удалены медиа и связанные tags/source/searched/cache.",
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return
        try:
            if self.tag_cleanup_kind.currentData() == "source":
                from core.services.library_service import delete_by_source
                result = delete_by_source(self.main.settings, value, scope=self.tag_cleanup_scope.currentData() or "all", delete_files=True)
            else:
                from core.services.library_service import delete_by_tag
                result = delete_by_tag(self.main.settings, value, scope=self.tag_cleanup_scope.currentData() or "all", delete_files=True)
            deleted = result.get("deleted_files", result.get("files", 0))
            errors = result.get("errors", 0)
            records = result.get("deleted_records", result.get("records", 0))
        except Exception as e:
            QMessageBox.warning(self, "SQLite", f"Ошибка удаления из SQLite:\n{e}")
            return
        self.tag_cleanup_list.clear()
        self.tag_cleanup_list.setVisible(False)
        self.tag_cleanup_delete_btn.setEnabled(False)
        self.tag_cleanup_delete_btn.setVisible(False)
        self._tag_cleanup_matches = []
        try:
            self.main.gallery_page.items = []
            self.main.tags_page.items = []
            self.main.gallery_page.refresh_force()
        except Exception:
            pass
        self.refresh_cleanup_candidates()
        QMessageBox.information(self, self.main.t("Done"), f"Удалено записей: {records}\nУдалено файлов: {deleted}\nОшибки: {errors}")

    def _refresh_after_delete(self):
        try:
            self.main.gallery_page.refresh_force()
        except Exception:
            pass
        try:
            if hasattr(self.main.tags_page, "refresh"):
                self.main.tags_page.refresh()
        except Exception:
            pass
        try:
            self.refresh_cleanup_candidates()
        except Exception:
            pass

    def _delete_leftovers_in_buckets(self, base: Path, buckets: list[str]) -> tuple[int, int]:
        """Delete files not indexed in SQLite after DB-backed removal."""
        deleted = errors = 0
        for bucket in buckets:
            folder = Path(base) / bucket
            if not folder.exists():
                continue
            for f in [p for p in folder.rglob("*") if p.is_file()]:
                try:
                    f.unlink()
                    deleted += 1
                except Exception:
                    errors += 1
            for d in sorted([p for p in folder.rglob("*") if p.is_dir()], key=lambda p: len(p.parts), reverse=True):
                try:
                    d.rmdir()
                except Exception:
                    pass
            try:
                folder.rmdir()
            except Exception:
                pass
        return deleted, errors

    def delete_downloader_results(self):
        """Delete downloader results from disk and SQLite in one operation."""
        mode = self.downloader_delete_mode.currentData() or "all"
        disk_buckets = ["found", "partial_match", "no_match"] if mode == "all" else [mode]
        db_map = {
            "found": "downloaded_found",
            "partial_match": "downloaded_partial_match",
            "no_match": "downloaded_no_match",
        }
        db_buckets = [db_map[b] for b in disk_buckets if b in db_map] + (["downloaded"] if mode == "all" else [])
        msg = (
            "Удалить результаты загрузчика?\n\n"
            f"Разделы: {', '.join(disk_buckets)}\n"
            "Файлы будут удалены с диска и из базы. Исходная папка не затрагивается."
        )
        if QMessageBox.warning(self, self.main.t("Confirm"), msg, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            from core.services.library_service import delete_by_buckets
            result = delete_by_buckets(self.main.settings, db_buckets, delete_files=True)
            extra_deleted, extra_errors = self._delete_leftovers_in_buckets(result_output_base(self.main.settings) / "downloads", disk_buckets)
            deleted = int(result.get("deleted_files", 0)) + extra_deleted
            records = int(result.get("deleted_records", 0))
            errors = int(result.get("errors", 0)) + extra_errors
            self._refresh_after_delete()
            QMessageBox.information(self, self.main.t("Done"), f"Удалено записей из базы: {records}\nУдалено файлов: {deleted}\nОшибки: {errors}")
        except Exception as e:
            QMessageBox.warning(self, "Удаление", f"Ошибка удаления результатов загрузчика:\n{e}")

    def delete_tags(self):
        """Delete parser output safely and keep SQLite consistent."""
        mode = self.delete_mode.currentData() or "all"
        buckets = {
            "found": ["found", "partial_match"],
            "no_match": ["no_match"],
            "all": ["found", "partial_match", "no_match"],
        }.get(mode, ["found", "partial_match", "no_match"])
        msg = (
            "Удалить выбранные результаты парсера?\n\n"
            f"Разделы: {', '.join(buckets)}\n"
            "Файлы будут удалены с диска и из базы. Исходная папка не затрагивается."
        )
        if QMessageBox.warning(self, self.main.t("Confirm"), msg, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            from core.services.library_service import delete_by_buckets
            result = delete_by_buckets(self.main.settings, buckets, delete_files=True)
            extra_deleted, extra_errors = self._delete_leftovers_in_buckets(result_output_base(self.main.settings), buckets)
            deleted = int(result.get("deleted_files", 0)) + extra_deleted
            records = int(result.get("deleted_records", 0))
            errors = int(result.get("errors", 0)) + extra_errors
            try:
                if CACHE_FILE.exists():
                    CACHE_FILE.unlink()
            except Exception:
                pass
            self._refresh_after_delete()
            QMessageBox.information(self, self.main.t("Done"), f"Удалено записей из базы: {records}\nУдалено файлов: {deleted}\nОшибки: {errors}")
        except Exception as e:
            QMessageBox.warning(self, "Удаление", f"Ошибка удаления результатов парсера:\n{e}")


    def check_library_integrity(self):
        try:
            from core.services.library_service import check_integrity, unfinished_operations
            res = check_integrity(self.main.settings)
            unfinished = unfinished_operations(self.main.settings, limit=20)
            counts = res.get("counts", {})
            text = [f"Проблем найдено: {res.get('total', 0)}"]
            if counts:
                text += [f"{k}: {v}" for k, v in sorted(counts.items())]
            if unfinished:
                text.append(f"Незавершённых операций в journal: {len(unfinished)}")
            self.sql_status.setText("Integrity: " + "; ".join(text))
            QMessageBox.information(self, "Integrity", "\n".join(text))
        except Exception as e:
            self.sql_status.setText("Integrity check error: " + str(e))

    def repair_library_integrity(self):
        try:
            from core.services.library_service import repair_integrity
            res = repair_integrity(self.main.settings)
            fixed = res.get("fixed", {})
            after = res.get("after", {})
            text = (
                f"Помечено отсутствующих файлов как deleted: {fixed.get('missing_marked_deleted', 0)}\n"
                f"Удалено orphan-строк: {fixed.get('orphan_rows_removed', 0)}\n"
                f"Осталось проблем: {after.get('total', 0)}"
            )
            self.sql_status.setText("Repair: " + text.replace("\n", "; "))
            QMessageBox.information(self, "Repair", text)
        except Exception as e:
            self.sql_status.setText("Integrity repair error: " + str(e))

    def optimize_sqlite(self):
        try:
            from core.database.maintenance import optimize
            res = optimize(self.main.settings)
            self.sql_status.setText("SQLite optimized: " + str(res.get("db", "")))
        except Exception as e:
            self.sql_status.setText("SQLite optimize error: " + str(e))

    def show_sqlite_stats(self):
        try:
            from core.database.maintenance import stats
            data = stats(self.main.settings)
            self.sql_status.setText("SQLite stats: " + ", ".join(f"{k}={v}" for k,v in data.items()))
        except Exception as e:
            self.sql_status.setText("SQLite stats error: " + str(e))



class InstructionDialog(QDialog):
    SECTIONS = {
        "Быстрый старт": """1. Укажи папку с картинками в «Настройки → Папка с картинками»
2. Открой «Парсер» → войди на нужные сайты (кнопка «Войти (все)»)
3. Сохрани куки и нажми СТАРТ
4. Готово — теги появятся в «Галерея»

Danbooru может не работать из-за Cloudflare. Сейчас это необязательный источник:
  • если сайт отвечает — он используется
  • если возвращает 403 — парсер продолжает работу через другие сайты""",

        "Поиск в Галерее": """Строка поиска поддерживает:

── Теги (как обычно) ──────────────────
  fox_girl    — найти все с этим тегом
  -loli       — исключить тег
  tag1 tag2   — оба тега одновременно

── Числовой поиск (человеческий язык) ─
  размер файла больше 50мб
  ширина больше 2000
  высота меньше 720
  рейтинг не меньше 4
  длительность больше 30 секунд
  длительность больше 1 минуты
  размер файла меньше 2гб

По-английски тоже работает:
  filesize > 100mb
  width > 1920
  rating >= 4
  duration < 60

Можно совмещать теги и числовой поиск:
  fox_girl размер файла больше 2мб

Прямо под строкой поиска появится превью:
  [размер файла] [больше] [50 MB]

Фильтр «★+» в галерее — только файлы с рейтингом.""",

        "Рейтинг ★★★★★": """В посте внизу — 5 звёздочек.
Кликни чтобы поставить оценку (1-5).
Повторный клик на ту же звезду — снять оценку.
Рейтинг сохраняется в базе данных.

Фильтрация по рейтингу:
  В Галерее выбери «★+» / «★★+» / «★★★+» и т.д.""",

        "Перед поиском тегов": """1. Проверь cookies для сайтов через br34
2. Укажи папку с картинками
3. Укажи output-папку (программа создаст Local_Booru_Output)
4. Не перемещай файлы во время поиска
5. Для долгого прогона: лимит строк консоли + пауза между запросами

MD5-поиск работает только если файл уже был на booru с таким именем.
IQDB и SauceNAO находят по содержимому — работают почти всегда.""",

        "Парсер / Сайты": """Система поиска тегов:
  1. MD5 по filename (мгновенно)
  2. MD5 реального файла (быстро)
  3. IQDB (поиск по изображению)
  4. SauceNAO (нужен API ключ)
  5. ascii2d (японский поиск по контенту)

Авторизация сайтов:
  rule34.xxx использует login / API key / User ID из таблицы сайтов
  Для сайтов с cookies можно использовать br34 или импорт cookies.txt

Danbooru:
  Cloudflare может вернуть 403 даже после входа. Это известное ограничение сайта,
  поэтому Danbooru не должен останавливать поиск по другим источникам.""",

        "Синонимы тегов": """В разделе «Теги» внизу — редактор синонимов.
Пример: добавь  catgirl → cat_girl
Тогда при поиске «catgirl» найдутся файлы с тегом «cat_girl».

Синонимы хранятся в БД, работают в обе стороны.""",

        "VP-tree (похожие файлы)": """В Настройках → «Пересобрать VP-tree»
Строит индекс для быстрого поиска похожих изображений.
Используется в разделе «Дубли».

Нужно пересобрать после добавления новых файлов.
Время: ~1-2 мин на 30 000 файлов.""",

        "Управление": """Галерея:
  A / D        — предыдущий / следующий пост
  F            — добавить в избранное
  W            — переключить подгонку (ширина / высота)
  E            — вкл/выкл звук видео
  F11          — полный экран
  +  /  -      — увеличить / уменьшить
  0            — сбросить масштаб
  Колесо мыши  — на изображении: пред/след пост
  Клик по видео — пауза / продолжить
  При переходе на другой пост воспроизведение видео останавливается.

Поиск:
  Enter / Обновить — применить фильтр
  Рандом           — случайный пост""",

        "Папки и данные": """Настройки, cookies, БД:  Documents/Local_Booru/
Output-папка:            выбираешь сам (Local_Booru_Output внутри)
Куки для сайтов:         Local_Booru/runtime/browser_cookies/

Программу можно обновить заменой файлов — данные не потеряются.""",

        "Ошибки": """JS/CSP/iframe ошибки в консоли — норма, от самих сайтов, не влияют на работу.

Важные ошибки:
  MD5 BLOCK:  Cloudflare блокирует — нужны куки
  MD5 ERROR:  проблема с API сайта
  IQDB ERROR: временно недоступен
  Python traceback — реальный баг, можно скопировать и сообщить

Лог ошибок: Documents/Local_Booru/logs/errors.log""",
    }
    def __init__(self, main, parent=None):
        super().__init__(parent); self.main=main; self.setWindowTitle('Инструкция'); self.resize(900,650)
        lay=QVBoxLayout(self); split=QSplitter(Qt.Horizontal); lay.addWidget(split,1)
        self.list=QListWidget(); self.text=QTextEdit(); self.text.setReadOnly(True)
        split.addWidget(self.list); split.addWidget(self.text); split.setSizes([260,640])
        for name in self.SECTIONS:
            self.list.addItem(QListWidgetItem(name))
        self.list.currentRowChanged.connect(self.show_section)
        self.list.setCurrentRow(0)
    def show_section(self, row):
        keys=list(self.SECTIONS.keys())
        if 0 <= row < len(keys):
            k=keys[row]
            self.text.setPlainText(k+'\n\n'+self.SECTIONS[k])
