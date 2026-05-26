from pathlib import Path
from urllib.parse import urlparse
import json
import time
import webbrowser
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,QPlainTextEdit,QProgressBar,QCheckBox,QDoubleSpinBox,QSpinBox,QLineEdit,QFileDialog,QGroupBox,QFormLayout,QSplitter,QTableWidget,QTableWidgetItem,QComboBox,QHeaderView,QMessageBox,QAbstractItemView
from PySide6.QtCore import QThread, Signal, Qt, QTimer
from core.settings import save_settings, DEFAULT_SITES
from core.tagger_engine import Tagger, MEDIA_EXTS, video_frame_image, output_processed_status, result_output_base, has_copy_suffix
from ui.login_browser import LoginBrowserDialog, open_br34, open_br34_multi
from ui.sites_widget import SitesWidget
from ui.memory_tools import bounded_append, set_bounded_log, soft_gc
from core.deleted_registry import should_skip_deleted_file, has_deleted_record_for_name


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

class TaggerWorker(QThread):
    log=Signal(str); progress=Signal(int,int); current_file=Signal(str); done=Signal()
    def __init__(self, settings): super().__init__(); self.settings=settings.copy(); self.paused=False
    def set_paused(self, paused):
        self.paused = bool(paused)

    def _wait_if_paused_or_delay(self, seconds=0):
        end = time.time() + max(0, float(seconds or 0))
        while not self.isInterruptionRequested():
            if self.paused:
                time.sleep(0.25)
                continue
            if time.time() >= end:
                break
            time.sleep(min(0.25, end - time.time()))

    def run(self):
        root=Path(self.settings.get("root","")); suffix=self.settings.get("output_suffix",".tags.txt")
        files=[p for p in root.rglob("*") if p.suffix.lower() in MEDIA_EXTS] if root.exists() else []
        self.log.emit(f"SCAN FOUND MEDIA: {len(files)} files")
        # Не сканируем Local_Booru_Output как обычный источник. Иначе один и тот же
        # файл может снова попасть в no_match/found второй копией. Исключение —
        # режим Retry NO_MATCH, когда корнем специально выбрана output/no_match/media.
        try:
            out_base = result_output_base(self.settings).resolve()
            root_resolved = root.resolve()
            is_retry_nm_root = root_resolved.name == "media" and root_resolved.parent.name == "no_match"
            # Do not recursively feed our own output back into APT when the user
            # selected a normal source folder. But if the selected root itself is
            # inside Local_Booru_Output, assume the user deliberately wants to
            # rescan/repair that bucket.
            try:
                root_is_output = (root_resolved == out_base) or (out_base in root_resolved.parents)
            except Exception:
                root_is_output = False
            if not is_retry_nm_root and not root_is_output:
                before_output_filter = len(files)
                files = [x for x in files if out_base not in x.resolve().parents]
                skipped_output = before_output_filter - len(files)
                if skipped_output:
                    self.log.emit(f"SKIP OUTPUT FOLDER FILES: {skipped_output}")
        except Exception as e:
            self.log.emit(f"OUTPUT FILTER WARNING: {e}")

        if self.settings.get("skip_copy_suffix_files", True):
            before = len(files)
            files = [x for x in files if not has_copy_suffix(x)]
            skipped_copy = before - len(files)
            if skipped_copy:
                self.log.emit(f"SKIP COPY-SUFFIX FILES: {skipped_copy}")
                if not files and before:
                    self.log.emit("NO FILES LEFT AFTER COPY-SUFFIX FILTER. Disable 'Skip files ending (1)/(2)' if these copies must be scanned.")

        # Files intentionally deleted by the duplicate cleaner are remembered by
        # exact name+hash. Important: checking MD5 for every file here freezes APT
        # on large archives, so we only do the expensive check when the filename
        # exists in the deleted registry.
        kept = []
        deleted_skips = 0
        deleted_candidates = 0
        for x in files:
            try:
                if has_deleted_record_for_name(x):
                    deleted_candidates += 1
                    if should_skip_deleted_file(x):
                        deleted_skips += 1
                        continue
            except Exception:
                pass
            kept.append(x)
        files = kept
        self.log.emit("QUEUE PREP: deleted-cache filter done")
        if deleted_candidates:
            self.log.emit(f"DELETED-DUPLICATE CACHE CHECKED: {deleted_candidates}")
        if deleted_skips:
            self.log.emit(f"SKIP DELETED-DUPLICATE CACHE: {deleted_skips}")
        def has_tags(path):
            tag_file = path.with_suffix(suffix)
            return tag_file.exists() and tag_file.stat().st_size > 0
        def has_nomatch(path):
            return path.with_suffix(".nomatch").exists()
        def processed_status(path):
            # Skip by original sidecars AND by output/archive cache.
            if has_tags(path):
                return "tagged"
            if has_nomatch(path):
                return "nomatch"
            return output_processed_status(self.settings, path)

        if self.settings.get("retry_nomatch"):
            # If root itself is output/no_match/media, retry all files in that folder.
            try:
                is_nm_folder = root.name == "media" and root.parent.name == "no_match"
            except Exception:
                is_nm_folder = False
            if not is_nm_folder:
                before_status = len(files)
                files=[p for p in files if processed_status(p) == "nomatch"]
                skipped_status = before_status - len(files)
                if skipped_status:
                    self.log.emit(f"SKIP NON-NOMATCH SQL STATUS: {skipped_status}")
        elif self.settings.get("tag_only_untagged") or self.settings.get("skip_existing"):
            before_status = len(files)
            self.log.emit(f"STATUS CHECK: {before_status} files")
            status_map = {}
            try:
                from core.database.storage import processed_status_many
                status_map = processed_status_many(self.settings, files)
                self.log.emit(f"STATUS CHECK DB MATCHES: {len(status_map)}")
            except Exception as e:
                self.log.emit(f"STATUS CHECK DB WARNING: {e}")
            filtered = []
            for p in files:
                if status_map.get(str(p)) is None:
                    # Keep cheap sidecar compatibility check only. Do not call
                    # output_processed_status() here, because in SQLite mode that
                    # would query per file and freeze queue preparation.
                    if has_tags(p) or has_nomatch(p):
                        continue
                    filtered.append(p)
            files = filtered
            skipped_status = before_status - len(files)
            if skipped_status:
                self.log.emit(f"SKIP ALREADY PROCESSED SQL STATUS: {skipped_status}")

        self.log.emit("QUEUE PREP: status filters done")
        limit=int(self.settings.get("limit_files",0))
        if limit>0: files=files[:limit]

        self.log.emit(f"SEARCH QUEUE: {len(files)} files")
        if not files:
            self.log.emit("NO FILES TO SEARCH. Check root folder, copy-suffix filter, skip-existing, and retry-no-match settings.")

        tagger=Tagger(self.settings, lambda m:self.log.emit(str(m)))
        total=len(files)
        tagged=0
        nomatch=0
        skipped=0
        errors=0
        stopped=False

        for i,p in enumerate(files,1):
            self._wait_if_paused_or_delay(0)
            if self.isInterruptionRequested():
                self.log.emit("STOPPED")
                stopped=True
                break

            try:
                self.current_file.emit(str(p))
                if self.isInterruptionRequested():
                    self.log.emit("STOPPED")
                    stopped=True
                    break
                tagger.cancel_callback = self.isInterruptionRequested
                result = tagger.process_image(p)
                if result == "tagged":
                    tagged += 1
                elif result == "nomatch":
                    nomatch += 1
                elif result == "skip":
                    skipped += 1
            except Exception as e:
                errors += 1
                nomatch += 1
                self.log.emit(f"ERROR {p.name}: {e}")

            self.progress.emit(i,total)
            self._wait_if_paused_or_delay(float(self.settings.get("delay_seconds", 0) or 0))

        self.log.emit(
            f"SUMMARY: TAGGED={tagged} NO_MATCH={nomatch} SKIPPED={skipped} ERRORS={errors} TOTAL={total}"
        )
        if stopped:
            self.log.emit("SUMMARY: stopped by user")
        self.done.emit()

class BrowserLoginWorker(QThread):
    log = Signal(str)

    def __init__(self, auth_url, wait_seconds):
        super().__init__()
        self.auth_url = auth_url
        self.wait_seconds = int(wait_seconds)

    def run(self):
        try:
            try:
                from playwright.sync_api import sync_playwright
            except Exception as e:
                self.log.emit(f"PLAYWRIGHT ERROR: {e}")
                self.log.emit("Install: pip install playwright && playwright install")
                return

            url = self.auth_url.strip()
            if not url:
                self.log.emit("BROWSER LOGIN ERROR: empty URL")
                return

            host = urlparse(url).netloc.lower().replace("www.", "") or "default"

            profile_dir = Path("data/runtime/browser_profile") / host
            cookies_dir = Path("data/runtime/browser_cookies")
            profile_dir.mkdir(parents=True, exist_ok=True)
            cookies_dir.mkdir(parents=True, exist_ok=True)

            self.log.emit(f"BROWSER LOGIN: {url}")
            self.log.emit(f"PROFILE: {profile_dir}")

            pw = sync_playwright().start()
            context = None

            try:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=False,
                    channel="msedge",
                )

                page = context.pages[0] if context.pages else context.new_page()
                page.goto(url, wait_until="load", timeout=120000)

                for i in range(self.wait_seconds):
                    if self.isInterruptionRequested():
                        self.log.emit("BROWSER LOGIN STOPPED")
                        return
                    left = self.wait_seconds - i
                    if left % 5 == 0 or left <= 5:
                        self.log.emit(f"LOGIN WAIT: {left}s")
                    time.sleep(1)

                try:
                    cookies = context.cookies()
                except Exception:
                    cookies = []

                try:
                    user_agent = page.evaluate("navigator.userAgent")
                except Exception:
                    user_agent = None

                data = {
                    "cookies": cookies,
                    "user_agent": user_agent,
                }

                # Main new per-host cookie bundle.
                host_file = cookies_dir / f"{host}.json"
                host_file.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )

                # Compatibility fallback for older code.
                legacy_file = Path("data/runtime/browser_cookies.json")
                legacy_file.parent.mkdir(parents=True, exist_ok=True)
                legacy_file.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )

                self.log.emit(f"COOKIES SAVED [{host}]: {len(cookies)}")
                self.log.emit(f"COOKIE FILE: {host_file}")

            finally:
                try:
                    if context is not None:
                        context.close()
                except Exception:
                    pass
                try:
                    pw.stop()
                except Exception:
                    pass

        except Exception as e:
            self.log.emit(f"BROWSER LOGIN ERROR: {e}")


class TaggerPage(QWidget):
    def __init__(self, main):
        super().__init__(); self.main=main; self.worker=None; self.browser_worker=None
        lay=QVBoxLayout(self); split=QSplitter(); lay.addWidget(split,3)
        left=QWidget(); left_lay=QHBoxLayout(left); self.form_left=QFormLayout(); self.form_right=QFormLayout(); left_lay.addLayout(self.form_left,1); left_lay.addLayout(self.form_right,1); self._form_col=0
        row=QHBoxLayout(); self.root=QLineEdit(); self.choose_btn=QPushButton(); self.choose_btn.clicked.connect(self.choose); row.addWidget(self.root,1); row.addWidget(self.choose_btn)
        self.api=QLineEdit(); self.api.setEchoMode(QLineEdit.Password)
        self.min_sim=QDoubleSpinBox(); self.min_sim.setRange(50,99); self.min_sim.setSingleStep(0.5)
        self.skip=QCheckBox(); self.only_untagged=QCheckBox(); self.skip_copy_suffix=QCheckBox(); self.retry_nomatch=QCheckBox(); self.mark_nomatch=QCheckBox(); self.md5=QCheckBox(); self.sauce=QCheckBox(); self.ascii2d=QCheckBox(); self.ascii_api=QLineEdit()
        self.iqdb=QCheckBox(); self.browser=QCheckBox()
        self.iqdb_min=QDoubleSpinBox(); self.iqdb_min.setRange(50,99); self.iqdb_min.setSingleStep(0.5)
        self.delay=QDoubleSpinBox(); self.delay.setRange(0,120); self.limit=QSpinBox(); self.limit.setRange(0,1000000); self.req_timeout=QSpinBox(); self.req_timeout.setRange(5,300); self.sauce_cooldown=QSpinBox(); self.sauce_cooldown.setRange(1,1440)
        self.output_suffix=QLineEdit(); self.sources_suffix=QLineEdit(); self.browser_wait=QSpinBox(); self.browser_wait.setRange(10,600)
        self.form_rows=[]
        for label,w,tip in [("Folder",row,"tip_root"),("SauceNAO API key",self.api,"tip_saucenao"),("SauceNAO min similarity",self.min_sim,"tip_min_similarity"),("MD5 lookup",self.md5,"tip_md5"),("SauceNAO fallback",self.sauce,"tip_sauce"),("IQDB fuzzy fallback",self.iqdb,"tip_iqdb"),("IQDB min similarity",self.iqdb_min,"tip_iqdb"),("Ascii2D fallback",self.ascii2d,"tip_ascii2d"),("Ascii2D API key",self.ascii_api,"tip_ascii_api"),("Skip existing",self.skip,"tip_skip"),("Tag only untagged",self.only_untagged,"tip_only_untagged"),("Skip files ending (1)/(2)",self.skip_copy_suffix,"tip_skip_copy_suffix"),("Retry NO MATCH",self.retry_nomatch,"tip_retry_nomatch"),("Mark NO MATCH",self.mark_nomatch,"tip_mark_nomatch"),("Delay",self.delay,"tip_delay"),("Request timeout",self.req_timeout,"tip_delay"),("Sauce cooldown min",self.sauce_cooldown,"tip_sauce"),("Limit",self.limit,"tip_limit"),("Tag suffix",self.output_suffix,"tip_suffix"),("Source suffix",self.sources_suffix,"tip_sources_suffix"),("Use system browser cookies",self.browser,"tip_system_cookies")]: self.add_tip_row(label,w,tip)
        split.addWidget(left)
        right=QWidget(); rlay=QVBoxLayout(right)
        self.sites_widget = SitesWidget()
        self.sites_widget.save_btn.clicked.connect(self.sync)
        self.sites_widget.login_btn.clicked.connect(self.open_selected_login)
        self.sites_widget.all_login_btn.clicked.connect(self.open_all_logins)
        rlay.addWidget(self.sites_widget)
        split.addWidget(right); split.setSizes([520,820])
        row2=QHBoxLayout(); self.save_btn=QPushButton(); self.save_btn.clicked.connect(self.sync); self.start=QPushButton(); self.start.clicked.connect(self.run); self.pause_btn=QPushButton("PAUSE"); self.pause_btn.setCheckable(True); self.pause_btn.clicked.connect(self.pause_resume); self.pause_btn.setEnabled(False); self.stop_btn = QPushButton(); self.stop_btn.clicked.connect(self.stop); self.stop_btn.setEnabled(False); row2.addWidget(self.save_btn); row2.addWidget(self.start); row2.addWidget(self.pause_btn); row2.addWidget(self.stop_btn); lay.addLayout(row2)
        self.progress=QProgressBar(); lay.addWidget(self.progress)
        self.console_preview_split = QSplitter(Qt.Horizontal)
        self.log=QPlainTextEdit(); self.log.setReadOnly(True); set_bounded_log(self.log, int(self.main.settings.get("max_console_lines", 2500)))
        self.preview_box=QLabel("Preview"); self.preview_box.setAlignment(Qt.AlignCenter); self.preview_box.setMinimumWidth(280)
        self.preview_box.setStyleSheet("border:1px solid #2f3541;border-radius:8px;background:#07080c")
        self.console_preview_split.addWidget(self.log); self.console_preview_split.addWidget(self.preview_box)
        self.console_preview_split.setSizes([900,320])
        lay.addWidget(self.console_preview_split,2)
        self._last_site_table = None
        self._last_site_row = -1
        self.load_values(); self.retranslate(); self.update_preview_visibility()

    def add_tip_row(self, label_key, widget, tip_key):
        lab = QLabel(self.main.t(label_key) + "  ?")
        lab.setToolTip(self.main.t(tip_key))
        lab.setStyleSheet("QLabel{font-weight:700;} QLabel:hover{color:#ff54a7;}")

        if hasattr(widget, "setToolTip"):
            widget.setToolTip(self.main.t(tip_key))

        target_form = self.form_left if getattr(self, "_form_col", 0) % 2 == 0 else self.form_right
        target_form.addRow(lab, widget)
        self._form_col = getattr(self, "_form_col", 0) + 1
        self.form_rows.append((lab, label_key, widget, tip_key))

    def append_log(self, msg):
        bounded_append(self.log, msg, int(self.main.settings.get("max_console_lines", 2500)))

    def trim_ui_memory(self):
        try:
            from PySide6.QtGui import QPixmapCache
            QPixmapCache.clear()
        except Exception:
            pass
        soft_gc()

    def bool_item(self, checked):
        it=QTableWidgetItem(); it.setFlags(it.flags()|Qt.ItemIsUserCheckable); it.setCheckState(Qt.Checked if checked else Qt.Unchecked); return it
    def load_values(self):
        s=self.main.settings; self.root.setText(s.get("root","C:/Local_Booru_Input")); self.api.setText(s.get("saucenao_api_key","")); self.min_sim.setValue(float(s.get("min_similarity",85))); self.skip.setChecked(bool(s.get("skip_existing",True))); self.only_untagged.setChecked(bool(s.get("tag_only_untagged",True))); self.skip_copy_suffix.setChecked(bool(s.get("skip_copy_suffix_files",True))); self.retry_nomatch.setChecked(bool(s.get("retry_nomatch",False))); self.mark_nomatch.setChecked(bool(s.get("mark_no_match",True))); self.md5.setChecked(bool(s.get("enable_md5_lookup",True))); self.sauce.setChecked(bool(s.get("enable_saucenao",True))); self.ascii2d.setChecked(s.get("enable_ascii2d",False)); self.ascii_api.setText(s.get("ascii2d_api_key",""))
        self.iqdb.setChecked(bool(s.get("enable_iqdb",True))); self.iqdb_min.setValue(float(s.get("iqdb_min_similarity",75))); self.delay.setValue(float(s.get("delay_seconds",8))); self.req_timeout.setValue(int(float(s.get("request_timeout_seconds",20)))); self.sauce_cooldown.setValue(int(float(s.get("saucenao_cooldown_seconds",3600))/60)); self.limit.setValue(int(s.get("limit_files",0))); self.output_suffix.setText(s.get("output_suffix",".tags.txt")); self.sources_suffix.setText(s.get("sources_suffix",".sources.txt")); self.browser.setChecked(bool(s.get("use_system_browser_cookies", s.get("use_browser_auth",False)))); self.browser_wait.setValue(int(s.get("browser_auth_wait_seconds",60)))
        self.sites_widget.load(s)
    def retranslate(self):
        t=self.main.t; self.choose_btn.setText(t("Choose")); self.save_btn.setText(t("Save settings")); self.start.setText(t("START")); self.pause_btn.setText(t("RESUME") if self.pause_btn.isChecked() else t("PAUSE")); self.stop_btn.setText(t("STOP")); self.apply_tips()
        for lab, label_key, w, tip_key in getattr(self, "form_rows", []):
            lab.setText(t(label_key) + "  ?")
            lab.setToolTip(t(tip_key))

            if hasattr(w, "setToolTip"):
                w.setToolTip(t(tip_key))
    def apply_tips(self):
        t=self.main.t
        pairs=[(self.root,"tip_root"),(self.api,"tip_saucenao"),(self.min_sim,"tip_min_similarity"),(self.md5,"tip_md5"),(self.sauce,"tip_sauce"),(self.iqdb,"tip_iqdb"),(self.delay,"tip_delay"),(self.req_timeout,"tip_delay"),(self.sauce_cooldown,"tip_sauce"),(self.limit,"tip_limit"),(self.skip,"tip_skip"),(self.only_untagged,"tip_only_untagged"),(self.skip_copy_suffix,"tip_skip_copy_suffix"),(self.retry_nomatch,"tip_retry_nomatch"),(self.mark_nomatch,"tip_mark_nomatch"),(self.browser,"tip_browser")]
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


    def choose(self):
        f=QFileDialog.getExistingDirectory(self,self.main.t("Choose"),self.root.text())
        if f: self.root.setText(f)
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
        s["retry_nomatch"] = self.retry_nomatch.isChecked()
        s["mark_no_match"] = self.mark_nomatch.isChecked()
        s["enable_md5_lookup"] = self.md5.isChecked()
        s["enable_saucenao"] = self.sauce.isChecked()
        s["enable_iqdb"] = self.iqdb.isChecked()
        s["enable_ascii2d"] = self.ascii2d.isChecked()
        s["ascii2d_api_key"] = self.ascii_api.text()
        s["iqdb_min_similarity"] = self.iqdb_min.value()
        s["delay_seconds"] = self.delay.value()
        s["request_timeout_seconds"] = self.req_timeout.value()
        s["saucenao_cooldown_seconds"] = int(self.sauce_cooldown.value()) * 60
        s["limit_files"] = self.limit.value()
        s["output_suffix"] = self.output_suffix.text()
        s["sources_suffix"] = self.sources_suffix.text()
        s["tags_suffix"] = self.output_suffix.text()
        s["use_browser_auth"] = self.browser.isChecked()
        s["use_system_browser_cookies"] = self.browser.isChecked()
        s["browser_auth_wait_seconds"] = self.browser_wait.value()

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
        save_settings(s)
        if show_message:
            QMessageBox.information(self, self.main.t("Saved"), self.main.t("Settings saved"))
    def run(self):
        self.sync(show_message=False)
        self.log.clear()
        self.start.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.pause_btn.setChecked(False)
        self.stop_btn.setEnabled(True)

        self.worker = TaggerWorker(self.main.settings)
        self.worker.log.connect(self.append_log)
        self.worker.progress.connect(self.on_worker_progress)
        self.worker.current_file.connect(self.show_current_preview)
        self.worker.done.connect(self.on_worker_done)
        self.worker.start()

    def on_worker_progress(self, v, t):
        self.progress.setMaximum(max(1, t))
        self.progress.setValue(v)
        if v % 100 == 0:
            self.trim_ui_memory()

    def update_preview_visibility(self):
        try:
            self.preview_box.setVisible(bool(self.main.settings.get("show_search_preview", True)))
        except Exception:
            pass

    def show_current_preview(self, path):
        self.update_preview_visibility()
        self._current_preview_path = str(path)
        if not self.preview_box.isVisible():
            return
        # Show filename immediately, generate thumbnail in background
        self.preview_box.clear()
        self.preview_box.setText(Path(path).name)
        self.preview_box.setToolTip(str(path))
        # Use ThumbnailService so PIL/video work happens off UI thread
        from core.thumb_service import ThumbnailService
        svc = ThumbnailService.instance()
        size = self.preview_box.contentsRect().size()
        w = max(200, size.width() if size.width() > 20 else self.preview_box.width())
        h = max(200, size.height() if size.height() > 20 else self.preview_box.height())
        cached = svc.request(str(path), w, h, self._on_preview_ready)
        if cached is not None and not cached.isNull():
            self._on_preview_ready(str(path), cached)

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
            p = getattr(self, "_current_preview_path", "")
            if p:
                QTimer.singleShot(0, lambda p=p: self._render_preview(p))
        except Exception:
            pass

    def on_worker_done(self):
        self.trim_ui_memory()
        self.append_log("DONE")
        self.start.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setChecked(False)
        self.pause_btn.setText(self.main.t("PAUSE"))
        self.stop_btn.setEnabled(False)

    def pause_resume(self):
        if not self.worker or not self.worker.isRunning():
            return
        paused = self.pause_btn.isChecked()
        self.worker.set_paused(paused)
        self.pause_btn.setText(self.main.t("RESUME") if paused else self.main.t("PAUSE"))
        self.append_log("PAUSED" if paused else "RESUMED")

    def stop(self):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.append_log("STOPPING...")