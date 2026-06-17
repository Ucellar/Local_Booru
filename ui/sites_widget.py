"""Управление источниками парсера.

v132: основная таблица показывает только оперативную информацию
(включение, ссылка, движок, логин и описание). Ключи API, User ID и
переопределения адресов API редактируются в отдельном диалоге сайта.
Редкие действия перенесены в контекстное меню, чтобы не занимать место.
"""
from __future__ import annotations

import json
import uuid
import base64
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import Qt, Signal, QSize, QThread
from PySide6.QtGui import QColor, QBrush, QIcon, QAction
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit,
    QPushButton, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from core.settings import SITES_BY_ENGINE, ALL_KNOWN_SITES

C_ENABLED = 0
C_URL = 1
C_ENGINE = 2
C_LOGIN = 3
C_DESCRIPTION = 4
NCOLS = 5
HEADERS = ["Вкл", "Ссылка на сайт", "Движок", "Логин", "Описание"]

ENGINE_COLORS = {
    "Danbooru": "#7060c0",
    "Gelbooru": "#2080c0",
    "Moebooru": "#20a060",
    "e621": "#d06020",
    "Свой": "#808080",
}
ENGINE_TYPES = {
    "Danbooru": "danbooru",
    "Gelbooru": "gelbooru",
    "Moebooru": "moebooru",
    "e621": "e621",
    "Свой": "custom",
}


def _clean_domain(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        raw = urlparse(raw).netloc
    return raw.lower().replace("www.", "").strip("/")


def _engine_label(cfg: dict, fallback: str = "Свой") -> str:
    shown = str(cfg.get("engine_label") or "").strip()
    if shown in ENGINE_TYPES:
        return shown
    raw = str(cfg.get("engine") or cfg.get("type") or fallback).strip().lower()
    if raw in ("danbooru", "danbooru2", "danbooru_html"):
        return "Danbooru"
    if raw in ("gelbooru", "gelbooru_html", "rule34xxx", "rule34.xxx", "dapi", "hypnohub"):
        return "Gelbooru"
    if raw in ("moebooru", "rule34us", "rule34.us"):
        return "Moebooru"
    if raw in ("e621", "e926"):
        return "e621"
    return fallback if fallback in ENGINE_TYPES else "Свой"


def _item(text: str = "") -> QTableWidgetItem:
    it = QTableWidgetItem(str(text or ""))
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    return it


def _checked_item(value: bool) -> QTableWidgetItem:
    it = QTableWidgetItem()
    it.setFlags((it.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
    it.setCheckState(Qt.Checked if value else Qt.Unchecked)
    it.setTextAlignment(Qt.AlignCenter)
    return it


class _ReorderTable(QTableWidget):
    rows_reordered = Signal()

    def dropEvent(self, event):
        super().dropEvent(event)
        self.rows_reordered.emit()


class SiteEditDialog(QDialog):
    """Настройки одного сайта, включая скрытые из основной таблицы поля."""

    def __init__(self, cfg: dict | None = None, *, is_new: bool = False, parent=None):
        super().__init__(parent)
        self._source = deepcopy(cfg or {})
        self._is_new = is_new
        self.setWindowTitle("Добавить сайт" if is_new else "Изменить сайт")
        self.resize(700, 520)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        basic = QWidget()
        basic_form = QFormLayout(basic)
        self.enabled = QCheckBox("Использовать этот источник в парсере")
        self.enabled.setChecked(bool(self._source.get("enabled", True)))
        self.name = QLineEdit(str(self._source.get("name") or self._source.get("domain") or ""))
        self.domain = QLineEdit(str(self._source.get("domain") or ""))
        self.domain.setPlaceholderText("gelbooru.com")
        site_url = str(self._source.get("base_url") or self._source.get("login_url") or "")
        self.base_url = QLineEdit(site_url)
        self.base_url.setPlaceholderText("https://gelbooru.com")
        self.engine = QComboBox()
        self.engine.addItems(list(ENGINE_TYPES.keys()))
        self.engine.setCurrentText(_engine_label(self._source, "Свой" if is_new else "Danbooru"))
        self.description = QPlainTextEdit(str(self._source.get("description") or self._source.get("notes") or ""))
        self.description.setMaximumHeight(92)
        basic_form.addRow("", self.enabled)
        basic_form.addRow("Название:", self.name)
        basic_form.addRow("Домен:", self.domain)
        basic_form.addRow("Ссылка на сайт:", self.base_url)
        basic_form.addRow("Движок / обработчик:", self.engine)
        basic_form.addRow("Описание:", self.description)
        tabs.addTab(basic, "Основное")

        auth = QWidget()
        auth_form = QFormLayout(auth)
        self.login = QLineEdit(str(self._source.get("login") or ""))
        self.api_key = QLineEdit(str(self._source.get("api_key") or ""))
        self.api_key.setEchoMode(QLineEdit.Password)
        api_row = QWidget()
        api_lay = QHBoxLayout(api_row)
        api_lay.setContentsMargins(0, 0, 0, 0)
        api_lay.addWidget(self.api_key, 1)
        self.show_key = QPushButton("Показать")
        self.show_key.setCheckable(True)
        self.show_key.toggled.connect(self._toggle_api_key)
        api_lay.addWidget(self.show_key)
        self.user_id = QLineEdit(str(self._source.get("user_id") or ""))
        self.login_url = QLineEdit(str(self._source.get("login_url") or site_url))
        self.login_url.setPlaceholderText("Адрес страницы входа / cookies")
        auth_form.addRow("Логин:", self.login)
        auth_form.addRow("API ключ:", api_row)
        auth_form.addRow("User ID:", self.user_id)
        auth_form.addRow("Страница входа:", self.login_url)
        auth_hint = QLabel("API ключ и User ID хранятся в настройках сайта и не выводятся в общей таблице.")
        auth_hint.setWordWrap(True)
        auth_form.addRow("", auth_hint)
        tabs.addTab(auth, "Авторизация")

        api = QWidget()
        api_form = QFormLayout(api)
        self.api_endpoint = QLineEdit(str(self._source.get("api_endpoint") or ""))
        self.api_endpoint.setPlaceholderText("Не задано — использовать встроенный адрес движка")
        self.api_format = QComboBox()
        self.api_format.addItems(["json", "xml", "html"])
        self.api_format.setCurrentText(str(self._source.get("api_format") or "json").lower())
        params = self._source.get("api_params")
        params_text = json.dumps(params, ensure_ascii=False, indent=2) if isinstance(params, dict) else ""
        self.api_params = QPlainTextEdit(params_text)
        self.api_params.setPlaceholderText('{\n  "tags": "md5:{md5}",\n  "limit": 1\n}')
        self.api_params.setMaximumHeight(165)
        api_form.addRow("API endpoint:", self.api_endpoint)
        api_form.addRow("Формат ответа:", self.api_format)
        api_form.addRow("Параметры MD5 (JSON):", self.api_params)
        api_hint = QLabel(
            "Оставь API endpoint пустым для встроенной логики. Если сайт поменял путь API, "
            "впиши новый адрес и параметры; {md5} подставляется автоматически."
        )
        api_hint.setWordWrap(True)
        api_form.addRow("", api_hint)
        tabs.addTab(api, "API")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self._accept_validated)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _toggle_api_key(self, shown: bool):
        self.api_key.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        self.show_key.setText("Скрыть" if shown else "Показать")

    def _accept_validated(self):
        domain = _clean_domain(self.domain.text() or self.base_url.text())
        if not domain:
            QMessageBox.warning(self, "Настройка сайта", "Укажи домен или ссылку на сайт.")
            return
        endpoint = self.api_endpoint.text().strip()
        params_text = self.api_params.toPlainText().strip()
        if endpoint and params_text:
            try:
                parsed = json.loads(params_text)
                if not isinstance(parsed, dict):
                    raise ValueError("нужен объект JSON")
            except Exception as exc:
                QMessageBox.warning(self, "Настройка API", f"Параметры MD5 должны быть объектом JSON:\n{exc}")
                return
        self.accept()

    def result_config(self) -> dict:
        cfg = deepcopy(self._source)
        old_label = _engine_label(cfg, "Свой")
        new_label = self.engine.currentText()
        domain = _clean_domain(self.domain.text() or self.base_url.text())
        base_url = self.base_url.text().strip() or f"https://{domain}"
        cfg.update({
            "enabled": self.enabled.isChecked(),
            "name": self.name.text().strip() or domain,
            "domain": domain,
            "base_url": base_url,
            "login_url": self.login_url.text().strip() or base_url,
            "engine_label": new_label,
            "engine": ENGINE_TYPES[new_label],
            "login": self.login.text().strip(),
            "api_key": self.api_key.text().strip(),
            "user_id": self.user_id.text().strip(),
            "description": self.description.toPlainText().strip(),
            "notes": self.description.toPlainText().strip(),
            "api_endpoint": self.api_endpoint.text().strip(),
            "api_format": self.api_format.currentText(),
        })
        # Preserve special built-in paths (rule34.xxx/rule34.us) until the user
        # deliberately changes their engine family.
        if old_label != new_label or not cfg.get("type"):
            cfg["type"] = ENGINE_TYPES[new_label]
        params_text = self.api_params.toPlainText().strip()
        if params_text:
            cfg["api_params"] = json.loads(params_text)
        else:
            cfg.pop("api_params", None)
        return cfg


class _SiteCheckWorker(QThread):
    done = Signal(list)

    def __init__(self, sites: list[tuple[str, dict]], parent=None):
        super().__init__(parent)
        self.sites = sites

    @staticmethod
    def _probe_url(domain: str, cfg: dict) -> tuple[str, dict]:
        override = str(cfg.get("api_endpoint") or "").strip()
        if override:
            base = str(cfg.get("base_url") or f"https://{domain}").rstrip("/")
            url = override.replace("{root}", base).replace("{md5}", "0" * 32)
            params = cfg.get("api_params") if isinstance(cfg.get("api_params"), dict) else {}
            return url, {str(k): str(v).replace("{md5}", "0" * 32) for k, v in params.items()}
        typ = str(cfg.get("type") or cfg.get("engine") or "").lower()
        root = str(cfg.get("base_url") or f"https://{domain}").rstrip("/")
        params = {}
        if domain == "rule34.xxx" or typ == "rule34xxx":
            params = {"page": "dapi", "s": "post", "q": "index", "json": "1", "limit": "1", "tags": "all"}
            if cfg.get("user_id") and cfg.get("api_key"):
                params.update({"user_id": str(cfg.get("user_id")), "api_key": str(cfg.get("api_key"))})
            return "https://api.rule34.xxx/index.php", params
        if "e621" in domain or "e926" in domain or typ == "e621":
            # e621 API check: credentials are sent via Basic Auth in run(),
            # not in URL params, so the API key never appears in request URLs/logs.
            params = {"limit": "1", "v2": "true", "mode": "extended"}
            return f"{root}/posts.json", params
        if domain in ("danbooru.donmai.us", "donmai.us"):
            # Official Danbooru API check also uses Basic Auth in run() when
            # credentials are configured.  Do not leak login/api_key into URL.
            return "https://danbooru.donmai.us/posts.json", {"limit": "1"}
        if "allthefallen" in domain:
            # ATF is Danbooru-compatible. Use /posts.json, and send credentials
            # through Basic Auth in run(), not as URL query parameters.
            return "https://booru.allthefallen.moe/posts.json", {"limit": "1"}
        if typ in ("moebooru", "rule34us"):
            return f"{root}/post.json", {"limit": "1"}
        if typ in ("gelbooru", "gelbooru_html"):
            params = {"page": "dapi", "s": "post", "q": "index", "json": "1", "limit": "1", "tags": "all"}
            if cfg.get("user_id") and cfg.get("api_key"):
                params.update({"user_id": str(cfg.get("user_id")), "api_key": str(cfg.get("api_key"))})
            return f"{root}/index.php", params
        return f"{root}/posts.json", {"limit": "1"}

    def run(self):
        import requests
        results: list[str] = []
        for domain, cfg in self.sites:
            url, params = self._probe_url(domain, cfg)
            login = str(cfg.get("login") or "").strip()
            typ = str(cfg.get("type") or cfg.get("engine") or "").lower()
            auth = None
            if "e621" in domain or "e926" in domain or typ == "e621":
                identity = f"by {login} on e621" if login else "local archive manager"
                headers = {"User-Agent": f"LocalBooru/3.3 ({identity})", "Accept": "application/json"}
                api_key = str(cfg.get("api_key") or "").strip()
                if login and api_key:
                    auth = (login, api_key)
            elif domain in ("danbooru.donmai.us", "donmai.us"):
                identity = f"by {login} on Danbooru" if login else "local archive manager"
                headers = {"User-Agent": f"LocalBooru/3.6 ({identity})", "Accept": "application/json"}
                api_key = str(cfg.get("api_key") or "").strip()
                if login and api_key:
                    auth = (login, api_key)
            elif "allthefallen" in domain:
                identity = f"by {login} on ATF" if login else "local archive manager"
                headers = {"User-Agent": f"LocalBooru/3.6 ({identity})", "Accept": "application/json"}
                api_key = str(cfg.get("api_key") or "").strip()
                if login and api_key:
                    auth = (login, api_key)
            elif domain == "rule34.xxx" or typ == "rule34xxx":
                identity = f"by {login} on rule34.xxx" if login else (f"user_id {cfg.get('user_id')} on rule34.xxx" if cfg.get("user_id") else "local archive manager")
                headers = {"User-Agent": f"LocalBooru/3.5 ({identity})", "Accept": "application/json"}
            else:
                headers = {"User-Agent": "Local-Booru/site-check (desktop app)"}
            if auth:
                auth_mark = " + Basic Auth"
            elif domain == "rule34.xxx" or typ == "rule34xxx":
                auth_mark = " + user_id/api_key" if cfg.get("api_key") and cfg.get("user_id") else ""
            else:
                auth_mark = " + auth fields" if cfg.get("api_key") or cfg.get("login") else ""
            try:
                r = requests.get(url, params=params, headers=headers, auth=auth, timeout=10)
                content_type = str(r.headers.get("Content-Type", "")).lower()
                head = (r.text or "")[:300].replace("\n", " ").replace("\r", " ")
                low = head.lower()
                if r.status_code == 200 and ("json" in content_type or r.text.lstrip().startswith(("[", "{"))):
                    msg = f"{domain}: OK / API JSON отвечает{auth_mark}"
                elif ("cloudflare" in low or "just a moment" in low or "verify you are human" in low or "security verification" in low):
                    msg = f"{domain}: Cloudflare/HTML вместо API JSON — пройди проверку в браузере, Save cookies, проверь User-Agent/API auth"
                elif r.status_code == 200:
                    msg = f"{domain}: доступен, но ответ не похож на API ({content_type or 'без типа'})"
                elif r.status_code == 401:
                    msg = f"{domain}: 401 / неверный login/api_key или API доступ не включён"
                elif r.status_code == 403:
                    msg = f"{domain}: 403 / доступ запрещён; проверь официальный User-Agent, login/api_key и cookies"
                elif r.status_code == 429:
                    msg = f"{domain}: 429 / лимит запросов"
                else:
                    msg = f"{domain}: HTTP {r.status_code}"
            except requests.exceptions.Timeout:
                msg = f"{domain}: timeout"
            except requests.exceptions.ConnectionError:
                msg = f"{domain}: нет соединения / DNS / VPN"
            except Exception as exc:
                msg = f"{domain}: {type(exc).__name__}: {exc}"
            results.append(msg)
        self.done.emit(results)


class SitesWidget(QWidget):
    """Компактная панель источников парсера с отдельной карточкой настройки."""
    changed = Signal()
    login_selected_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: dict[str, dict] = {}
        self._manual_order: list[str] = []
        self._deleted_builtin_sites: set[str] = set()
        self._sort_column: int | None = None
        self._sort_desc = False
        self._loading = False
        self._check_worker = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self.table = _ReorderTable(0, NCOLS)
        self.table.setHorizontalHeaderLabels(HEADERS)
        header = self.table.horizontalHeader()
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._sort_by_header)
        header.setSectionResizeMode(C_ENABLED, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(C_URL, QHeaderView.Interactive)
        header.setSectionResizeMode(C_ENGINE, QHeaderView.Interactive)
        header.setSectionResizeMode(C_LOGIN, QHeaderView.Interactive)
        header.setSectionResizeMode(C_DESCRIPTION, QHeaderView.Stretch)
        self.table.setColumnWidth(C_URL, 245)
        self.table.setColumnWidth(C_ENGINE, 105)
        self.table.setColumnWidth(C_LOGIN, 100)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.itemDoubleClicked.connect(lambda *_: self._edit_selected())
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.rows_reordered.connect(self._capture_manual_order)
        outer.addWidget(self.table, 1)

        hint = QLabel("ПКМ по сайту или пустому месту — действия и настройка источников. Перетащи строку ЛКМ для порядка проверки; сортировка отключает перетаскивание.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#707890;font-size:10px;")
        outer.addWidget(hint)

        # Частые общие действия остаются видимыми. Операции с выбранной
        # строкой и редкие действия доступны через ПКМ и не перегружают панель.
        btn_row = QHBoxLayout()
        self.all_login_btn = QPushButton("Войти (все)")
        self.import_btn = QPushButton("Импорт cookies.txt")
        self.test_btn = QPushButton("Проверить сайты")
        for button in (self.all_login_btn, self.import_btn, self.test_btn):
            btn_row.addWidget(button)
        btn_row.addStretch(1)
        outer.addLayout(btn_row)
        self.test_result = QPlainTextEdit()
        self.test_result.setReadOnly(True)
        self.test_result.setMaximumHeight(110)
        self.test_result.setVisible(False)
        outer.addWidget(self.test_result)

        self.import_btn.clicked.connect(self._import_cookies_txt)
        self.test_btn.clicked.connect(self._start_site_check)
        self.apply_theme_style()
        self._set_drag_enabled(True)

    def apply_theme_style(self, theme_name: str | None = None):
        if theme_name is None:
            try:
                from ui.main_window import _CURRENT_THEME
                theme_name = _CURRENT_THEME
            except Exception:
                theme_name = "abyss"
        light = theme_name in ("light", "r34", "win95", "windows95")
        suffix = "_dark" if light else ""
        base = Path(__file__).parent.parent / "assets" / "icons"
        actions = [
            (self.all_login_btn, "action_login_all"),
            (self.import_btn, "action_cookie"),
        ]
        for button, name in actions:
            path = base / f"{name}{suffix}.png"
            if not path.exists():
                path = base / f"{name}.png"
            icon = QIcon(str(path)) if path.exists() else QIcon()
            button.setIcon(icon)
            if not icon.isNull():
                button.setIconSize(QSize(16, 16))

    def load(self, settings: dict):
        self._entries.clear()
        self._deleted_builtin_sites = set(str(x) for x in settings.get("deleted_builtin_sites", []) if str(x))
        saved_sites = settings.get("sites", {}) if isinstance(settings.get("sites"), dict) else {}
        saved_custom = settings.get("custom_sites", []) if isinstance(settings.get("custom_sites"), list) else []
        used_saved_keys: set[str] = set()

        for engine, sites in SITES_BY_ENGINE.items():
            for builtin_domain, defaults in sites.items():
                if builtin_domain in self._deleted_builtin_sites:
                    continue
                override_key = None
                for key, value in saved_sites.items():
                    if key == builtin_domain or (isinstance(value, dict) and value.get("builtin_id") == builtin_domain):
                        override_key = key
                        break
                saved = saved_sites.get(override_key, {}) if override_key else {}
                if override_key:
                    used_saved_keys.add(override_key)
                cfg = {**defaults, **(saved if isinstance(saved, dict) else {})}
                cfg.setdefault("domain", _clean_domain(cfg.get("base_url") or builtin_domain) or builtin_domain)
                cfg.setdefault("base_url", cfg.get("login_url") or f"https://{cfg['domain']}")
                cfg.setdefault("description", cfg.get("notes", ""))
                cfg["builtin_id"] = builtin_domain
                cfg["_custom"] = False
                cfg["_id"] = f"builtin:{builtin_domain}"
                cfg.setdefault("engine_label", engine)
                self._entries[cfg["_id"]] = cfg

        # Retain unrecognised flat configs as user sites instead of silently losing them.
        for domain, value in saved_sites.items():
            if domain in used_saved_keys or not isinstance(value, dict):
                continue
            if domain in self._deleted_builtin_sites or str(value.get("builtin_id") or "") in self._deleted_builtin_sites:
                continue
            cfg = deepcopy(value)
            cfg.setdefault("domain", domain)
            cfg.setdefault("base_url", cfg.get("login_url") or f"https://{domain}")
            cfg.setdefault("description", cfg.get("notes", ""))
            cfg["_custom"] = True
            cfg["_id"] = str(cfg.get("site_id") or f"custom:{uuid.uuid4().hex}")
            cfg["site_id"] = cfg["_id"]
            self._entries[cfg["_id"]] = cfg

        for value in saved_custom:
            if not isinstance(value, dict):
                continue
            cfg = deepcopy(value)
            cfg.setdefault("domain", _clean_domain(cfg.get("base_url") or ""))
            cfg.setdefault("base_url", cfg.get("login_url") or (f"https://{cfg['domain']}" if cfg.get("domain") else ""))
            cfg.setdefault("description", cfg.get("notes", ""))
            cfg["_custom"] = True
            cfg["_id"] = str(cfg.get("site_id") or f"custom:{uuid.uuid4().hex}")
            cfg["site_id"] = cfg["_id"]
            self._entries[cfg["_id"]] = cfg

        saved_order = [str(x) for x in settings.get("site_manual_order", []) if str(x) in self._entries]
        missing = [key for key in self._entries if key not in saved_order]
        self._manual_order = saved_order + missing
        self._sort_column = None
        self._sort_desc = False
        self._render()

    def _visible_ids(self) -> list[str]:
        ids = [key for key in self._manual_order if key in self._entries]
        ids += [key for key in self._entries if key not in ids]
        if self._sort_column is None:
            return ids

        def key(entry_id: str):
            cfg = self._entries[entry_id]
            if self._sort_column == C_ENABLED:
                return (bool(cfg.get("enabled", True)), str(cfg.get("domain", "")).casefold())
            if self._sort_column == C_URL:
                return str(cfg.get("base_url") or cfg.get("domain") or "").casefold()
            if self._sort_column == C_ENGINE:
                return (_engine_label(cfg).casefold(), str(cfg.get("domain", "")).casefold())
            if self._sort_column == C_LOGIN:
                login = str(cfg.get("login") or "")
                return (bool(login), login.casefold(), str(cfg.get("domain", "")).casefold())
            return str(cfg.get("description") or cfg.get("notes") or "").casefold()

        return sorted(ids, key=key, reverse=self._sort_desc)

    def _render(self, select_id: str | None = None):
        self._loading = True
        try:
            self.table.clearContents()
            self.table.setRowCount(0)
            for entry_id in self._visible_ids():
                cfg = self._entries[entry_id]
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.table.setRowHeight(row, 25)
                enabled = _checked_item(bool(cfg.get("enabled", True)))
                enabled.setData(Qt.UserRole, entry_id)
                self.table.setItem(row, C_ENABLED, enabled)
                url = str(cfg.get("base_url") or cfg.get("login_url") or ("https://" + str(cfg.get("domain", ""))))
                url_item = _item(url)
                url_item.setData(Qt.UserRole, entry_id)
                url_item.setToolTip(str(cfg.get("domain") or ""))
                self.table.setItem(row, C_URL, url_item)
                label = _engine_label(cfg)
                engine_item = _item(label)
                engine_item.setForeground(QBrush(QColor(ENGINE_COLORS.get(label, "#808080"))))
                self.table.setItem(row, C_ENGINE, engine_item)
                self.table.setItem(row, C_LOGIN, _item(str(cfg.get("login") or "")))
                desc_item = _item(str(cfg.get("description") or cfg.get("notes") or ""))
                desc_item.setToolTip(desc_item.text())
                self.table.setItem(row, C_DESCRIPTION, desc_item)
                if entry_id == select_id:
                    self.table.selectRow(row)
            self._refresh_headers()
            self._set_drag_enabled(self._sort_column is None)
        finally:
            self._loading = False

    def _refresh_headers(self):
        labels = list(HEADERS)
        if self._sort_column is not None:
            labels[self._sort_column] += " ▼" if self._sort_desc else " ▲"
        self.table.setHorizontalHeaderLabels(labels)

    def _set_drag_enabled(self, enabled: bool):
        self.table.setDragEnabled(enabled)
        self.table.setAcceptDrops(enabled)
        self.table.viewport().setAcceptDrops(enabled)
        self.table.setDropIndicatorShown(enabled)
        self.table.setDragDropMode(QAbstractItemView.InternalMove if enabled else QAbstractItemView.NoDragDrop)
        self.table.setDefaultDropAction(Qt.MoveAction)

    def _entry_id_for_row(self, row: int) -> str | None:
        item = self.table.item(row, C_URL) or self.table.item(row, C_ENABLED)
        return str(item.data(Qt.UserRole)) if item and item.data(Qt.UserRole) else None

    def _selected_id(self) -> str | None:
        return self._entry_id_for_row(self.table.currentRow()) if self.table.currentRow() >= 0 else None

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._loading or item.column() != C_ENABLED:
            return
        entry_id = item.data(Qt.UserRole)
        if entry_id in self._entries:
            self._entries[entry_id]["enabled"] = item.checkState() == Qt.Checked
            if self._sort_column == C_ENABLED:
                self._render(str(entry_id))
            self.changed.emit()

    def _sort_by_header(self, column: int):
        if self._sort_column != column:
            self._sort_column = column
            self._sort_desc = False
        elif not self._sort_desc:
            self._sort_desc = True
        else:
            self._sort_column = None
            self._sort_desc = False
        selected = self._selected_id()
        self._render(selected)

    def _capture_manual_order(self):
        if self._sort_column is not None:
            self._render(self._selected_id())
            return
        ordered = []
        for row in range(self.table.rowCount()):
            entry_id = self._entry_id_for_row(row)
            if entry_id and entry_id not in ordered:
                ordered.append(entry_id)
        if ordered:
            self._manual_order = ordered
            self.changed.emit()

    def _move_selected(self, delta: int):
        if self._sort_column is not None:
            QMessageBox.information(self, "Порядок сайтов", "Сначала отключи сортировку третьим нажатием по заголовку.")
            return
        entry_id = self._selected_id()
        if not entry_id or entry_id not in self._manual_order:
            return
        index = self._manual_order.index(entry_id)
        new_index = max(0, min(len(self._manual_order) - 1, index + delta))
        if new_index == index:
            return
        self._manual_order.pop(index)
        self._manual_order.insert(new_index, entry_id)
        self._render(entry_id)
        self.changed.emit()

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row >= 0:
            self.table.selectRow(row)
        else:
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
        entry_id = self._selected_id()
        menu = QMenu(self)
        if entry_id:
            menu.addAction("Изменить...", self._edit_selected)
            cfg = self._entries[entry_id]
            menu.addAction("Выключить" if cfg.get("enabled", True) else "Включить", self._toggle_selected)
            menu.addAction("Войти в выбранный сайт", self.login_selected_requested.emit)
            menu.addAction("Проверить подключение", self._check_selected)
            menu.addAction("Дублировать...", self._duplicate_selected)
            menu.addSeparator()
            menu.addAction("Переместить вверх", lambda: self._move_selected(-1))
            menu.addAction("Переместить вниз", lambda: self._move_selected(1))
            menu.addSeparator()
            menu.addAction("Удалить...", self._del_selected)
            menu.addSeparator()
        menu.addAction("Добавить сайт...", self._add_custom)
        restore_action = menu.addAction("Восстановить стандартные сайты", self.restore_builtin_sites)
        restore_action.setEnabled(bool(self._deleted_builtin_sites))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _toggle_selected(self):
        entry_id = self._selected_id()
        if not entry_id:
            return
        self._entries[entry_id]["enabled"] = not bool(self._entries[entry_id].get("enabled", True))
        self._render(entry_id)
        self.changed.emit()

    def _add_custom(self):
        cfg = {
            "enabled": True, "name": "Новый сайт", "domain": "", "base_url": "",
            "login_url": "", "engine_label": "Свой", "engine": "custom", "type": "custom",
            "login": "", "api_key": "", "user_id": "", "description": "", "notes": "",
            "_custom": True,
        }
        dialog = SiteEditDialog(cfg, is_new=True, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        cfg = dialog.result_config()
        entry_id = f"custom:{uuid.uuid4().hex}"
        cfg.update({"_custom": True, "_id": entry_id, "site_id": entry_id})
        self._entries[entry_id] = cfg
        self._manual_order.append(entry_id)
        self._render(entry_id)
        self.changed.emit()

    def _edit_selected(self):
        entry_id = self._selected_id()
        if not entry_id:
            return
        dialog = SiteEditDialog(self._entries[entry_id], parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        updated = dialog.result_config()
        updated["_id"] = entry_id
        updated["_custom"] = bool(self._entries[entry_id].get("_custom", False))
        if not updated["_custom"]:
            updated["builtin_id"] = self._entries[entry_id].get("builtin_id")
        else:
            updated["site_id"] = entry_id
        self._entries[entry_id] = updated
        self._render(entry_id)
        self.changed.emit()

    def _duplicate_selected(self):
        entry_id = self._selected_id()
        if not entry_id:
            return
        cfg = deepcopy(self._entries[entry_id])
        cfg.pop("builtin_id", None)
        cfg["name"] = (str(cfg.get("name") or cfg.get("domain") or "Сайт") + " (копия)")
        cfg["_custom"] = True
        dialog = SiteEditDialog(cfg, is_new=True, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        cfg = dialog.result_config()
        new_id = f"custom:{uuid.uuid4().hex}"
        cfg.update({"_custom": True, "_id": new_id, "site_id": new_id})
        self._entries[new_id] = cfg
        current = self._manual_order.index(entry_id) if entry_id in self._manual_order else len(self._manual_order) - 1
        self._manual_order.insert(current + 1, new_id)
        self._render(new_id)
        self.changed.emit()

    def _del_selected(self):
        entry_id = self._selected_id()
        if not entry_id:
            return
        cfg = self._entries[entry_id]
        title = str(cfg.get("name") or cfg.get("domain") or "этот сайт")
        extra = "\n\nЭто предустановленный сайт. Его можно будет вернуть через «Восстановить стандартные»." if not cfg.get("_custom") else ""
        answer = QMessageBox.question(self, "Удалить сайт", f"Удалить «{title}» из списка парсера?{extra}")
        if answer != QMessageBox.Yes:
            return
        if not cfg.get("_custom") and cfg.get("builtin_id"):
            self._deleted_builtin_sites.add(str(cfg["builtin_id"]))
        self._entries.pop(entry_id, None)
        self._manual_order = [key for key in self._manual_order if key != entry_id]
        self._render()
        self.changed.emit()

    def restore_builtin_sites(self):
        if not self._deleted_builtin_sites:
            QMessageBox.information(self, "Стандартные сайты", "Удалённых предустановленных сайтов нет.")
            return
        answer = QMessageBox.question(self, "Стандартные сайты", "Вернуть удалённые предустановленные сайты в список?")
        if answer != QMessageBox.Yes:
            return
        previous_deleted = set(self._deleted_builtin_sites)
        self._deleted_builtin_sites.clear()
        # Preserve edited/current rows and append only the missing templates.
        for engine, sites in SITES_BY_ENGINE.items():
            for builtin_domain, defaults in sites.items():
                if builtin_domain not in previous_deleted:
                    continue
                entry_id = f"builtin:{builtin_domain}"
                if entry_id in self._entries:
                    continue
                cfg = deepcopy(defaults)
                cfg.update({
                    "domain": builtin_domain,
                    "base_url": cfg.get("login_url") or f"https://{builtin_domain}",
                    "description": cfg.get("notes", ""),
                    "engine_label": engine,
                    "builtin_id": builtin_domain,
                    "_custom": False,
                    "_id": entry_id,
                })
                self._entries[entry_id] = cfg
                self._manual_order.append(entry_id)
        self._render()
        self.changed.emit()

    def _enabled_pairs(self, only_id: str | None = None) -> list[tuple[str, dict]]:
        result = []
        ids = [only_id] if only_id else self._visible_ids()
        for entry_id in ids:
            cfg = self._entries.get(entry_id)
            if cfg and cfg.get("enabled", True) and cfg.get("domain"):
                result.append((str(cfg["domain"]), cfg))
        return result

    def _start_site_check(self):
        self._run_check(self._enabled_pairs())

    def _check_selected(self):
        entry_id = self._selected_id()
        if entry_id:
            self._run_check(self._enabled_pairs(entry_id))

    def _run_check(self, sites: list[tuple[str, dict]]):
        if self._check_worker is not None and self._check_worker.isRunning():
            return
        self.test_result.setVisible(True)
        self.test_result.setPlainText("Проверка включённых сайтов..." if sites else "Нет включённых сайтов для проверки.")
        if not sites:
            return
        self.test_btn.setEnabled(False)
        self._check_worker = _SiteCheckWorker(sites, self)
        self._check_worker.done.connect(self._finish_site_check)
        self._check_worker.start()

    def _finish_site_check(self, lines: list[str]):
        self.test_btn.setEnabled(True)
        self.test_result.setVisible(True)
        self.test_result.setPlainText("\n".join(lines) if lines else "Нет включённых сайтов для проверки.")

    def _import_cookies_txt(self):
        import shutil
        cfg = self._entries.get(self._selected_id() or "", {})
        host_default = str(cfg.get("domain") or "danbooru.donmai.us")
        host, ok = QInputDialog.getText(self, "Импорт cookies.txt", "Домен сайта:", text=host_default)
        if not ok or not host.strip():
            return
        host = _clean_domain(host)
        src_file, _ = QFileDialog.getOpenFileName(self, "Выбери cookies.txt", "", "Cookie files (*.txt);;All (*.*)")
        if not src_file:
            return
        try:
            from core.paths import BROWSER_COOKIES_DIR
            BROWSER_COOKIES_DIR.mkdir(parents=True, exist_ok=True)
            dst = BROWSER_COOKIES_DIR / (host + ".txt")
            shutil.copy2(src_file, dst)
            content = dst.read_text(encoding="utf-8", errors="replace")
            has_cf = "cf_clearance" in content
            lines = [line for line in content.splitlines() if not line.startswith("#") and line.strip()]
            status = "OK" if has_cf else "НЕТ (нужен для Cloudflare!)"
            msg = f"Сохранено: {dst.name}\nСтрок куки: {len(lines)}\ncf_clearance: {status}"
            QMessageBox.information(self, "Импорт cookies.txt", msg)
        except Exception as exc:
            QMessageBox.warning(self, "Ошибка", "Не удалось скопировать:\n" + str(exc))

    def collect(self) -> tuple[dict, list]:
        try:
            fw = QApplication.focusWidget()
            if fw is not None:
                fw.clearFocus()
            QApplication.processEvents()
        except Exception:
            pass
        sites: dict = {}
        custom: list = []
        for entry_id in self._manual_order:
            cfg = deepcopy(self._entries.get(entry_id, {}))
            if not cfg or not cfg.get("domain"):
                continue
            cfg.pop("_id", None)
            is_custom = bool(cfg.pop("_custom", False))
            domain = _clean_domain(cfg.get("domain"))
            cfg["domain"] = domain
            cfg.setdefault("base_url", cfg.get("login_url") or f"https://{domain}")
            if is_custom:
                cfg.setdefault("site_id", entry_id)
                custom.append(cfg)
            else:
                sites[domain] = cfg
        return sites, custom

    def deleted_builtin_sites(self) -> list[str]:
        return sorted(self._deleted_builtin_sites)

    def manual_order(self) -> list[str]:
        return [key for key in self._manual_order if key in self._entries]

    def selected_login_urls(self) -> list[str]:
        cfg = self._entries.get(self._selected_id() or "", {})
        url = str(cfg.get("login_url") or cfg.get("base_url") or "").strip()
        return [url] if url else self.all_enabled_login_urls()

    def all_enabled_login_urls(self) -> list[str]:
        urls = []
        for entry_id in self._manual_order:
            cfg = self._entries.get(entry_id, {})
            if not cfg.get("enabled", True):
                continue
            url = str(cfg.get("login_url") or cfg.get("base_url") or "").strip()
            if url and url not in urls:
                urls.append(url)
        return urls
