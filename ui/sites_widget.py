"""SitesWidget — collapsible engine groups + custom sites in one panel.

Replaces two separate QTableWidgets (sites + custom_sites) in tagger_page.
Each engine is a collapsible section header. Sites inside are rows with
checkbox (enabled), domain, login, api_key, notes.
Custom sites appear under "Свои сайты" group at the bottom.
"""
from __future__ import annotations
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QScrollArea, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QFrame, QSizePolicy, QLineEdit, QCheckBox, QApplication,
)

from core.settings import SITES_BY_ENGINE, ALL_KNOWN_SITES

# Column indices in the table
C_ENABLED  = 0
C_DOMAIN   = 1
C_ENGINE   = 2
C_LOGIN    = 3
C_APIKEY   = 4
C_USERID   = 5
C_LOGINURL = 6
C_NOTES    = 7
NCOLS      = 8

ENGINE_COLORS = {
    "Danbooru": "#7060c0",
    "Gelbooru": "#2080c0",
    "Moebooru": "#20a060",
    "e621":     "#d06020",
    "Свои":     "#808080",
}


def _bool_item(checked: bool) -> QTableWidgetItem:
    it = QTableWidgetItem()
    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
    it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
    it.setTextAlignment(Qt.AlignCenter)
    return it


def _text_item(text: str, editable: bool = True) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text or ""))
    if not editable:
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        it.setForeground(QBrush(QColor("#707090")))
    return it


class _SectionHeader(QWidget):
    """Clickable section header that collapses/expands its rows."""
    toggled = Signal(str, bool)  # engine_name, expanded

    def __init__(self, engine: str, count: int, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._expanded = True
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(8)

        self._arrow = QLabel("▼")
        self._arrow.setFixedWidth(16)
        color = ENGINE_COLORS.get(engine, "#808080")
        self._arrow.setStyleSheet(f"color:{color};font-weight:bold;font-size:11px;")
        lay.addWidget(self._arrow)

        lbl = QLabel(engine)
        lbl.setStyleSheet(f"color:{color};font-weight:700;font-size:13px;")
        lay.addWidget(lbl)

        self._count_lbl = QLabel(f"({count})")
        self._count_lbl.setStyleSheet("color:#606080;font-size:11px;")
        lay.addWidget(self._count_lbl)
        lay.addStretch(1)

        self.setStyleSheet(
            "QWidget{background:#0f1220;border-radius:4px;margin:2px 0;}"
            "QWidget:hover{background:#141828;}"
        )
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(28)

    def mousePressEvent(self, e):
        self._expanded = not self._expanded
        self._arrow.setText("▼" if self._expanded else "▶")
        self.toggled.emit(self.engine, self._expanded)

    def set_count(self, n: int):
        self._count_lbl.setText(f"({n})")


class SitesWidget(QWidget):
    """All sites in one table with collapsible engine section headers."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._section_rows: dict[str, list[int]] = {}  # engine -> [row indices]
        self._section_headers: dict[str, _SectionHeader] = {}
        self._row_engine: dict[int, str] = {}  # row -> engine name
        self._custom_start_row: int = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # Table
        self.table = QTableWidget(0, NCOLS)
        self.table.setHorizontalHeaderLabels([
            "Вкл", "Домен", "Движок", "Логин", "API ключ", "User ID", "Login URL", "Описание"
        ])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(C_ENABLED,  QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(C_DOMAIN,   QHeaderView.Interactive)
        hh.setSectionResizeMode(C_ENGINE,   QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(C_LOGIN,    QHeaderView.Interactive)
        hh.setSectionResizeMode(C_APIKEY,   QHeaderView.Interactive)
        hh.setSectionResizeMode(C_USERID,   QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(C_LOGINURL, QHeaderView.Stretch)
        hh.setSectionResizeMode(C_NOTES,    QHeaderView.Interactive)
        self.table.setColumnWidth(C_DOMAIN,   180)
        self.table.setColumnWidth(C_LOGIN,    110)
        self.table.setColumnWidth(C_APIKEY,   110)
        self.table.setColumnWidth(C_NOTES,    160)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.itemChanged.connect(lambda _: self.changed.emit())
        outer.addWidget(self.table)

        # Buttons
        btn_row = QHBoxLayout()
        self.add_btn    = QPushButton("＋ Свой сайт")
        self.del_btn    = QPushButton("✕ Удалить")
        self.login_btn  = QPushButton("🔓 Войти (выбранный)")
        self.all_login_btn = QPushButton("🔓 Войти (все)")
        self.import_btn = QPushButton("📥 Импорт cookies.txt")
        self.save_btn   = QPushButton("💾 Сохранить")
        for b in [self.add_btn, self.del_btn, self.login_btn, self.all_login_btn, self.import_btn, self.save_btn]:
            btn_row.addWidget(b)
        outer.addLayout(btn_row)

        self.add_btn.clicked.connect(self._add_custom)
        self.del_btn.clicked.connect(self._del_selected)
        self.import_btn.clicked.connect(self._import_cookies_txt)

    # ── Population ────────────────────────────────────────────────────────────

    def load(self, settings: dict):
        """Populate table from settings dict."""
        self.table.setRowCount(0)
        self._section_rows.clear()
        self._section_headers.clear()
        self._row_engine.clear()

        saved_sites = settings.get("sites", {})
        saved_custom = settings.get("custom_sites", [])

        # Built-in sites grouped by engine
        for engine, engine_sites in SITES_BY_ENGINE.items():
            self._add_section_header(engine, len(engine_sites))
            for domain, defaults in engine_sites.items():
                saved = saved_sites.get(domain, {})
                cfg = {**defaults, **saved}
                self._add_site_row(engine, domain, cfg, custom=False)

        # Custom sites
        self._add_section_header("Свои", len(saved_custom))
        self._custom_start_row = self.table.rowCount()
        for site in saved_custom:
            self._add_custom_row(site)

    def _add_section_header(self, engine: str, count: int):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setRowHeight(row, 28)

        hdr = _SectionHeader(engine, count)
        hdr.toggled.connect(self._on_section_toggled)
        self._section_headers[engine] = hdr
        self._section_rows[engine] = []
        self._row_engine[row] = f"__header__{engine}"

        self.table.setCellWidget(row, 0, hdr)
        self.table.setSpan(row, 0, 1, NCOLS)

        # Style header row
        for c in range(NCOLS):
            it = self.table.item(row, c)
            if it:
                it.setFlags(Qt.NoItemFlags)

    def _add_site_row(self, engine: str, domain: str, cfg: dict, custom: bool = False):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setRowHeight(row, 24)
        self._section_rows.setdefault(engine, []).append(row)
        self._row_engine[row] = engine

        color = ENGINE_COLORS.get(engine, "#808080")

        self.table.setItem(row, C_ENABLED,  _bool_item(cfg.get("enabled", True)))
        dom_it = _text_item(domain, editable=custom)
        dom_it.setForeground(QBrush(QColor("#c0c8e0")))
        self.table.setItem(row, C_DOMAIN,   dom_it)
        eng_it = _text_item(engine, editable=False)
        eng_it.setForeground(QBrush(QColor(color)))
        self.table.setItem(row, C_ENGINE,   eng_it)
        self.table.setItem(row, C_LOGIN,    _text_item(cfg.get("login", "")))
        self.table.setItem(row, C_APIKEY,   _text_item(cfg.get("api_key", "")))
        self.table.setItem(row, C_USERID,   _text_item(cfg.get("user_id", "")))
        self.table.setItem(row, C_LOGINURL, _text_item(cfg.get("login_url", "")))
        notes_it = _text_item(ALL_KNOWN_SITES.get(domain, {}).get("notes", ""), editable=False)
        notes_it.setForeground(QBrush(QColor("#606080")))
        self.table.setItem(row, C_NOTES,    notes_it)

    def _add_custom_row(self, site: dict):
        self._add_site_row("Свои", site.get("domain", ""), {
            "enabled":   site.get("enabled", True),
            "login":     site.get("login", ""),
            "api_key":   site.get("api_key", ""),
            "user_id":   site.get("user_id", ""),
            "login_url": site.get("login_url", site.get("base_url", "")),
        }, custom=True)

    # ── Section collapse ──────────────────────────────────────────────────────

    def _on_section_toggled(self, engine: str, expanded: bool):
        for row in self._section_rows.get(engine, []):
            self.table.setRowHidden(row, not expanded)

    # ── Custom site ───────────────────────────────────────────────────────────

    def _add_custom(self):
        site = {"domain": "example.com", "enabled": True, "login": "",
                "api_key": "", "user_id": "", "login_url": "https://example.com"}
        self._add_custom_row(site)
        hdr = self._section_headers.get("Свои")
        if hdr:
            hdr.set_count(len(self._section_rows.get("Свои", [])))

    def _del_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        engine = self._row_engine.get(row, "")
        if engine.startswith("__header__"):
            return  # don't delete headers
        if engine != "Свои":
            return  # only delete custom sites
        self.table.removeRow(row)
        # Rebuild index
        self._rebuild_index()
        self.changed.emit()

    def _rebuild_index(self):
        self._row_engine.clear()
        self._section_rows.clear()
        for row in range(self.table.rowCount()):
            w = self.table.cellWidget(row, 0)
            if isinstance(w, _SectionHeader):
                self._row_engine[row] = f"__header__{w.engine}"
                self._section_rows.setdefault(w.engine, [])
            else:
                eng_it = self.table.item(row, C_ENGINE)
                eng = eng_it.text() if eng_it else "Свои"
                self._row_engine[row] = eng
                self._section_rows.setdefault(eng, []).append(row)

    def _import_cookies_txt(self):
        from PySide6.QtWidgets import QFileDialog, QMessageBox, QInputDialog
        from pathlib import Path
        import shutil
        
        # Pre-fill domain from selected row
        host_default = "danbooru.donmai.us"
        sel = self.table.currentRow()
        if sel >= 0:
            dom_it = self.table.item(sel, C_DOMAIN)
            if dom_it and dom_it.text().strip():
                host_default = dom_it.text().strip().lower().replace("www.", "")
        
        host, ok = QInputDialog.getText(
            self, "Импорт cookies.txt", "Домен сайта:", text=host_default)
        if not ok or not host.strip():
            return
        host = host.strip().lower().replace("www.", "")
        
        src_file, _ = QFileDialog.getOpenFileName(
            self, "Выбери cookies.txt", "", "Cookie files (*.txt);;All (*.*)")
        if not src_file:
            return
        
        try:
            from core.paths import BROWSER_COOKIES_DIR
            BROWSER_COOKIES_DIR.mkdir(parents=True, exist_ok=True)
            dst = BROWSER_COOKIES_DIR / (host + ".txt")
            shutil.copy2(src_file, dst)
            
            content = dst.read_text(encoding="utf-8", errors="replace")
            has_cf = "cf_clearance" in content
            lines = [c for c in content.splitlines() if not c.startswith("#") and c.strip()]
            
            cf_status = "OK" if has_cf else "НЕТ (нужен для Cloudflare!)"
            msg = ("Сохранено: " + str(dst.name) + "\n"
                   + "Строк куки: " + str(len(lines)) + "\n"
                   + "cf_clearance: " + cf_status + "\n\n"
                   + ("Готово! Запусти парсер." if has_cf
                      else "Нет cf_clearance.\nПройди Cloudflare в Chrome,\nсразу экспортируй куки и повтори."))
            QMessageBox.information(self, "Импорт cookies.txt", msg)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", "Не удалось скопировать:\n" + str(e))

        # ── Read back ─────────────────────────────────────────────────────────────

    def collect(self) -> tuple[dict, list]:
        """Return (sites_dict, custom_sites_list) for saving to settings.

        QTableWidget does not always commit the active cell editor before a
        QPushButton click is handled. Without this, the user edits a login/API
        key, clicks Save, sees "Settings saved", but the old value is written.
        Force focus out + process pending editor events before reading cells.
        """
        try:
            fw = QApplication.focusWidget()
            if fw is not None:
                fw.clearFocus()
            QApplication.processEvents()
        except Exception:
            pass

        sites: dict = {}
        custom: list = []

        for row in range(self.table.rowCount()):
            engine = self._row_engine.get(row, "")
            if engine.startswith("__header__"):
                continue

            enabled_it = self.table.item(row, C_ENABLED)
            dom_it     = self.table.item(row, C_DOMAIN)
            if not dom_it:
                continue
            domain = dom_it.text().strip()
            if not domain:
                continue

            enabled  = (enabled_it.checkState() == Qt.Checked) if enabled_it else False
            login    = (self.table.item(row, C_LOGIN)    or QTableWidgetItem()).text()
            api_key  = (self.table.item(row, C_APIKEY)   or QTableWidgetItem()).text()
            user_id  = (self.table.item(row, C_USERID)   or QTableWidgetItem()).text()
            login_url= (self.table.item(row, C_LOGINURL) or QTableWidgetItem()).text()

            base_cfg = ALL_KNOWN_SITES.get(domain, {})
            cfg = {
                "enabled":   enabled,
                "type":      base_cfg.get("type", "danbooru"),
                "login":     login,
                "api_key":   api_key,
                "user_id":   user_id,
                "login_url": login_url,
            }

            if engine == "Свои":
                custom.append({**cfg, "domain": domain, "name": domain,
                               "base_url": login_url, "md5_api": "posts_json"})
            else:
                sites[domain] = cfg

        return sites, custom

    def selected_login_urls(self) -> list[str]:
        """Return login URLs of selected rows (regardless of enabled checkbox)."""
        urls = []
        seen_rows = set()
        for index in self.table.selectedIndexes():
            r = index.row()
            if r in seen_rows:
                continue
            seen_rows.add(r)
            if self._row_engine.get(r, "").startswith("__header__"):
                continue
            url_it = self.table.item(r, C_LOGINURL)
            if url_it:
                u = url_it.text().strip()
                if u and u != "about:blank":
                    urls.append(u)
        # Fallback: if nothing selected, use all enabled
        if not urls:
            return self.all_enabled_login_urls()
        return list(dict.fromkeys(urls))

    def all_enabled_login_urls(self) -> list[str]:
        urls = []
        for row in range(self.table.rowCount()):
            if self._row_engine.get(row, "").startswith("__header__"):
                continue
            en = self.table.item(row, C_ENABLED)
            url_it = self.table.item(row, C_LOGINURL)
            if en and en.checkState() == Qt.Checked and url_it:
                u = url_it.text().strip()
                if u:
                    urls.append(u)
        return list(dict.fromkeys(urls))
