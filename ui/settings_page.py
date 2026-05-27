from pathlib import Path
from PySide6.QtWidgets import QWidget,QVBoxLayout,QFormLayout,QLineEdit,QPushButton,QFileDialog,QLabel,QSpinBox,QMessageBox,QHBoxLayout,QComboBox,QCheckBox,QDialog,QSlider,QTextEdit,QListWidget,QSplitter,QListWidgetItem,QCompleter,QApplication
from PySide6.QtCore import Qt, QStringListModel, QSortFilterProxyModel
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
        self.lang=QComboBox(); self.lang.addItem("Русский","ru"); self.lang.addItem("English","en")
        self.appearance=QComboBox(); self.appearance.addItem("Abyss (тёмный синий)","abyss"); self.appearance.addItem("Ember (тёмный янтарный)","ember"); self.appearance.addItem("Slate (нейтральный серый)","slate"); self.appearance.addItem("Sakura (тёмно-розовый)","sakura"); self.appearance.addItem("PH (чёрный+оранжевый)","pornhub"); self.appearance.addItem("R34 (тёмный фиолетовый)","r34"); self.appearance.addItem("Light (светлый)","light")
        self.title=QLineEdit(); self.logo=QLineEdit(); self.logo_fit=QComboBox(); self.logo_fit.addItem("Crop","crop"); self.logo_fit.addItem("Contain","contain"); self.logo_choose=QPushButton(); self.logo_choose.clicked.connect(self.choose_logo); self.logo_crop=QPushButton(); self.logo_crop.clicked.connect(self.crop_logo)
        lrow=QHBoxLayout(); lrow.addWidget(self.logo,1); lrow.addWidget(self.logo_choose); lrow.addWidget(self.logo_crop)
        self.output_dir=QLineEdit(); self.choose_output=QPushButton("..."); self.choose_output.clicked.connect(self.choose_output_dir); outrow=QHBoxLayout(); outrow.addWidget(self.output_dir,1); outrow.addWidget(self.choose_output)
        self.copy_results=QCheckBox()
        self.debug_logging=QCheckBox()
        self.debug_logging.setChecked(False)

        self.cols=QSpinBox(); self.cols.setRange(1,12); self.rows=QSpinBox(); self.rows.setRange(1,20); self.card=QSpinBox(); self.card.setRange(100,700); self.ignore_numeric=QCheckBox(); self.show_preview=QCheckBox(); self.error_console=QCheckBox(); self.max_console_lines=QSpinBox(); self.max_console_lines.setRange(200,20000); self.max_console_lines.setSingleStep(100); self.manga_root=QLineEdit(); self.choose_manga=QPushButton("..."); self.choose_manga.clicked.connect(self.choose_manga_root); mrow=QHBoxLayout(); mrow.addWidget(self.manga_root,1); mrow.addWidget(self.choose_manga)
        self.games_root=QLineEdit(); self.choose_games=QPushButton("..."); self.choose_games.clicked.connect(self.choose_games_root); grow=QHBoxLayout(); grow.addWidget(self.games_root,1); grow.addWidget(self.choose_games)
        self.form = QFormLayout()
        self.form.setContentsMargins(8, 8, 8, 8)
        self.form.setSpacing(8)
        _ilay = QVBoxLayout(self._inner)
        _ilay.setContentsMargins(8, 8, 8, 8)
        _ilay.setSpacing(8)
        self.form_rows=[]
        for key, w, tip in [
            ("FlareSolverr URL", _fs_row, "tip_root"),
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
        self.save_btn=QPushButton(); self.save_btn.clicked.connect(self.save); _ilay.addWidget(self.save_btn)
        self.instruction_btn=QPushButton("Инструкция"); self.instruction_btn.clicked.connect(self.show_instruction); _ilay.addWidget(self.instruction_btn)
        self.rebuild_sql_btn=QPushButton("Пересобрать SQLite-индекс в фоне")
        self.rebuild_sql_btn.clicked.connect(self.rebuild_sql_index)
        _ilay.addWidget(self.rebuild_sql_btn)
        self.rebuild_vptree_btn = QPushButton("Пересобрать VP-tree (поиск похожих)")
        self.rebuild_vptree_btn.clicked.connect(self._rebuild_vptree)
        _ilay.addWidget(self.rebuild_vptree_btn)
        self.sql_status=QLabel("")
        self.sql_status.setWordWrap(True)
        _ilay.addWidget(self.sql_status)
        self.sql_optimize_btn=QPushButton("Оптимизировать SQLite")
        self.sql_optimize_btn.clicked.connect(self.optimize_sqlite)
        _ilay.addWidget(self.sql_optimize_btn)
        self.sql_stats_btn=QPushButton("Статистика SQLite")
        self.sql_stats_btn.clicked.connect(self.show_sqlite_stats)
        _ilay.addWidget(self.sql_stats_btn)
        self.danger=QLabel(); self.danger.setStyleSheet("font-size:20px;font-weight:900;color:#ff3838;margin-top:30px"); _ilay.addWidget(self.danger)

        self.tag_cleanup_info = QLabel(
            "Удаление по выбранному тегу или source. Начни писать — появится список как в галерее. "
            "Сначала выбери конкретный вариант из списка, потом удаляй связанные media/tags/source/searched/cache."
        )
        self.tag_cleanup_info.setWordWrap(True)
        _ilay.addWidget(self.tag_cleanup_info)

        self.tag_cleanup_scope = QComboBox()
        self.tag_cleanup_scope.addItem("Все результаты", "all")
        self.tag_cleanup_scope.addItem("Только АПТ", "tagger")
        self.tag_cleanup_scope.addItem("Только АСП", "downloader")
        self.tag_cleanup_scope.addItem("Только найденные", "found")
        self.tag_cleanup_scope.addItem("Только не найденные", "no_match")
        _ilay.addWidget(self.tag_cleanup_scope)

        self.tag_cleanup_kind = QComboBox()
        self.tag_cleanup_kind.addItem("Тег", "tag")
        self.tag_cleanup_kind.addItem("Source", "source")
        self.tag_cleanup_kind.currentIndexChanged.connect(self.refresh_cleanup_candidates)
        self.tag_cleanup_scope.currentIndexChanged.connect(self.refresh_cleanup_candidates)
        _ilay.addWidget(self.tag_cleanup_kind)

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
        _ilay.addWidget(self.tag_cleanup_query)

        self.tag_cleanup_find_btn = QPushButton("Показать связанные файлы")
        self.tag_cleanup_find_btn.clicked.connect(self.find_tag_cleanup_matches)
        _ilay.addWidget(self.tag_cleanup_find_btn)

        self.tag_cleanup_list = QListWidget()
        self.tag_cleanup_list.setMaximumHeight(170)
        _ilay.addWidget(self.tag_cleanup_list)

        self.tag_cleanup_delete_btn = QPushButton("Удалить всё связанное с выбранным тегом/source")
        self.tag_cleanup_delete_btn.setEnabled(False)
        self.tag_cleanup_delete_btn.setStyleSheet("QPushButton{background:#7f1d1d;border:1px solid #ff3838;color:white;font-weight:900}QPushButton:disabled{background:#2a2020;color:#777}")
        self.tag_cleanup_delete_btn.clicked.connect(self.delete_tag_cleanup_matches)
        _ilay.addWidget(self.tag_cleanup_delete_btn)

        self.video_note=QLabel(); _ilay.addWidget(self.video_note)

        self.controls_title=QLabel()
        self.controls_title.setStyleSheet("font-size:18px;font-weight:900;margin-top:18px")
        _ilay.addWidget(self.controls_title)

        self.controls_info=QLabel()
        self.controls_info.setWordWrap(True)
        _ilay.addWidget(self.controls_info)
        _ilay.addStretch(1)
        self._scroll.setWidget(self._inner)
        self._scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        lay.addWidget(self._scroll, 1)
        self.load_values(); self.retranslate(); self.apply_tips(); self.refresh_cleanup_candidates()
    def add_tip_row(self, label_key, widget, tip_key):
        lab=QLabel(self.main.t(label_key)+"  ?")
        lab.setToolTip(self.main.t(tip_key))
        _tc = self.main.settings.get("appearance","abyss")
        _label_colors = {"light": ("#1a1c2a","#5060d0"), "dark": ("#c0c8e0","#6c85e0"), "abyss": ("#c0c8e0","#6c85e0"),
                         "r34": ("#111111","#3a7a35"), "pornhub": ("#f5f5f5","#ff9000"), "pornhub": ("#f5f5f5","#ff9000"),
                         "ember": ("#c8b090","#c87040"), "slate": ("#b0c8d0","#5a8a9f"),
                         "sakura": ("#e0b0d0","#d060a0")}
        _lc, _hc = _label_colors.get(_tc, ("#c0c8e0","#6c85e0"))
        lab.setStyleSheet(f"QLabel{{font-weight:700;min-width:220px;color:{_lc};}} QLabel:hover{{color:{_hc};}}")
        lab.setMinimumWidth(220)
        if hasattr(widget, "setToolTip"):
            widget.setToolTip(self.main.t(tip_key))
        self.form.addRow(lab, widget)
        self.form_rows.append((lab,label_key,widget,tip_key))
    def set_tip(self, widget, key): widget.setToolTip(self.main.t(key))
    def apply_tips(self):
        self.set_tip(self.root,"tip_root"); self.set_tip(self.ignore_numeric,"tip_numeric"); self.set_tip(self.cols,"tip_root"); self.set_tip(self.rows,"tip_root")
    def retranslate(self):
        # Re-apply scroll background when theme changes
        try:
            pal = self.palette()
            bg = pal.color(pal.ColorRole.Window)
            css = f"background:{bg.name()};"
            self._inner.setStyleSheet(f"#SettingsInner{{{css}}}")
            self._scroll.setStyleSheet(f"QScrollArea{{{css}border:none;}}")
        except Exception:
            pass
        t=self.main.t; self.choose.setText(t("Choose")); self.logo_choose.setText(t("Choose")); self.logo_crop.setText(t("Crop logo")); self.save_btn.setText(t("Save settings")); self.danger.setText(t("Danger zone")); self.video_note.setText(t("Video tagging note"))

        if self.main.settings.get("language","ru") == "ru":
            self.controls_title.setText("Управление")
            self.controls_info.setText("A / D  предыдущий/следующий пост\nF  избранное\nW  режим по ширине/высоте\nE  выключить/включить звук видео\nQ  назад в галерею")
        else:
            self.controls_title.setText("Controls")
            self.controls_info.setText("A / D  previous/next post\nF  favorite\nW  fit width/height\nE  mute/unmute video\nQ  back to gallery")

        self.apply_tips()
        for lab, label_key, w, tip_key in getattr(self, "form_rows", []):
            lab.setText(t(label_key) + "  ?")
            lab.setToolTip(t(tip_key))

            if hasattr(w, "setToolTip"):
                w.setToolTip(t(tip_key))
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
        self.sql_status.setText("SQLite: индексация запущена...")
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
                from core.database.storage import candidate_sources
                return candidate_sources(self.main.settings, scope)
            from core.database.storage import candidate_tags
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
                from core.database.storage import find_images_by_source
                rows = find_images_by_source(self.main.settings, value, scope)
            else:
                from core.database.storage import find_images_by_tag
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
        self.tag_cleanup_delete_btn.setEnabled(False)
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
        self.tag_cleanup_delete_btn.setEnabled(bool(self._tag_cleanup_matches))
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
            from core.database.storage import delete_images
            result = delete_images(self.main.settings, matches, delete_files=True)
            deleted = result.get("deleted_files", result.get("files", 0))
            errors = result.get("errors", 0)
            records = result.get("deleted_records", result.get("records", 0))
        except Exception as e:
            QMessageBox.warning(self, "SQLite", f"Ошибка удаления из SQLite:\n{e}")
            return
        self.tag_cleanup_list.clear()
        self.tag_cleanup_delete_btn.setEnabled(False)
        self._tag_cleanup_matches = []
        try:
            self.main.gallery_page.items = []
            self.main.tags_page.items = []
            self.main.gallery_page.refresh_force()
        except Exception:
            pass
        self.refresh_cleanup_candidates()
        QMessageBox.information(self, self.main.t("Done"), f"Удалено записей: {records}\nУдалено файлов: {deleted}\nОшибки: {errors}")

    def delete_downloader_results(self):
        """Delete downloader output metadata/media safely."""
        mode = self.downloader_delete_mode.currentData() or "all"

        if QMessageBox.warning(
            self,
            self.main.t("Confirm"),
            "Удалить выбранные результаты Downloader из Local_Booru_Output/downloads?\n"
            "Будут удалены media/tags/source/searched/cache.",
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return

        deleted = 0
        errors = 0
        out = result_output_base(self.main.settings) / "downloads"

        if mode == "all":
            buckets = ["found", "partial_match", "no_match"]
        else:
            buckets = [mode]

        for bucket in buckets:
            bdir = out / bucket
            if not bdir.exists():
                continue

            stems = set()

            # First remove sidecars/cache and remember stems.
            for sub in ("tags", "source", "searched", "cache"):
                d = bdir / sub
                if not d.exists():
                    continue
                for f in d.rglob("*"):
                    if not f.is_file():
                        continue
                    stems.add(
                        f.stem
                        .replace(".searched", "")
                        .replace(".raw", "")
                        .replace(".sources", "")
                    )
                    try:
                        f.unlink()
                        deleted += 1
                    except Exception:
                        errors += 1

            # Then remove matching media. If full bucket cleanup, remove all media.
            media = bdir / "media"
            if media.exists():
                for f in media.rglob("*"):
                    if not f.is_file():
                        continue
                    if mode == "all" or f.stem in stems or bucket in buckets:
                        try:
                            f.unlink()
                            deleted += 1
                        except Exception:
                            errors += 1

            # Remove empty folders bottom-up.
            for sub in ("media", "tags", "source", "searched", "cache"):
                d = bdir / sub
                if d.exists():
                    for folder in sorted([x for x in d.rglob("*") if x.is_dir()], key=lambda x: len(str(x)), reverse=True):
                        try:
                            folder.rmdir()
                        except Exception:
                            pass

        QMessageBox.information(self, self.main.t("Done"), f"Downloader deleted: {deleted}\nErrors: {errors}")
        self.confirm.setText("")

    def delete_tags(self):
        """Delete generated output metadata/media safely.

        Modes:
        - all: found + partial_match + no_match
        - found: only found + partial_match
        - no_match: only no_match
        Source input folder is not touched except old sidecars cleanup.
        """
        mode = self.delete_mode.currentData() or "all"
        if QMessageBox.warning(
            self,
            self.main.t("Confirm"),
            "Удалить выбранные результаты из Local_Booru_Output?\n"
            "Если удаляются tags/source/searched, соответствующие медиа-файлы тоже будут удалены.\n"
            "Исходная папка не трогается, кроме старых .tags.txt/.sources.txt рядом с оригиналами.",
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return

        deleted = 0
        errors = 0
        out = result_output_base(self.main.settings)

        if mode == "found":
            buckets = ["found", "partial_match"]
        elif mode == "no_match":
            buckets = ["no_match"]
        else:
            buckets = ["found", "partial_match", "no_match"]

        for bucket in buckets:
            bdir = out / bucket
            if not bdir.exists():
                continue
            # Remove sidecars and their matching media by stem.
            stems = set()
            for sub in ("tags", "source", "searched", "cache"):
                d = bdir / sub
                if not d.exists():
                    continue
                for f in d.rglob("*"):
                    if f.is_file():
                        stems.add(f.stem.replace(".searched", ""))
                        try:
                            f.unlink(); deleted += 1
                        except Exception:
                            errors += 1
            media = bdir / "media"
            if media.exists():
                for f in media.rglob("*"):
                    if f.is_file() and (mode == "all" or f.stem in stems or bucket == "no_match"):
                        try:
                            f.unlink(); deleted += 1
                        except Exception:
                            errors += 1

        # Cleanup old source-folder sidecars left by older builds, but never delete
        # source media from the input folder.
        root = Path(self.main.settings.get("root", ""))
        if root.exists():
            for pat in ["*.tags.txt", "*.sources.txt", "*.tags.json", "*.nomatch"]:
                for f in root.rglob(pat):
                    try:
                        f.unlink(); deleted += 1
                    except Exception:
                        errors += 1

        try:
            if CACHE_FILE.exists():
                CACHE_FILE.unlink()
        except Exception:
            pass

        self.main.gallery_page.items = []
        self.main.tags_page.items = []
        QMessageBox.information(
            self,
            self.main.t("Done"),
            f"{self.main.t('Deleted')}: {deleted}\n{self.main.t('Errors')}: {errors}"
        )

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

Для Danbooru (Cloudflare):
  • Зайди на danbooru.donmai.us в Chrome
  • Установи расширение Cookie-Editor
  • Экспортируй в формате Netscape
  • В Парсере → Сайты → выбери danbooru → «📥 Импорт cookies.txt»""",

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

Cloudflare:
  rule34.xxx, rule34.us — нужен cf_clearance
  Войди через br34, сохрани куки — работает автоматически

  Danbooru — cf_clearance привязан к Chrome fingerprint
  Используй Cookie-Editor + Импорт cookies.txt""",

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
