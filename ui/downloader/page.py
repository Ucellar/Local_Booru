from pathlib import Path
import json
import re
import mimetypes
import time
import hashlib
from urllib.parse import urlparse, parse_qs, unquote, quote_plus

import requests
from bs4 import BeautifulSoup

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout,
    QLineEdit, QPlainTextEdit, QSpinBox, QMessageBox, QDialog, QGridLayout, QScrollArea
)

from ui.login_browser import open_br34
from ui.memory_tools import bounded_append
from core.paths import BROWSER_COOKIES_DIR, ensure_output_base
from core.settings import save_settings
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt
try:
    from PIL import Image
    import imagehash
except Exception:
    Image = None
    imagehash = None


DEFAULT_BLOCKLIST = (
    "obese, obesity, overweight, weight_gain, "
    "inflation, inflation_fetish, expansion, expansion_fetish, "
    "pregnant, pregnancy, mpreg, bloated, belly_inflation, "
    "nipple_expansion, huge_nipples, giant_nipples, "
    "cyst, cysts, cystitis, "
    "ai_generated, ai-assisted, ai_assisted, "
    "scat, coprophagia, poop, feces, "
    "necrophilia, corpse, guro, gore, vomit, fart, farting"
)



from ui.downloader.helpers import *
from ui.downloader.worker import DownloaderWorker
from ui.duplicates_page import delete_media_with_sidecars, image_size as _duplicate_image_size

class DownloaderPage(QWidget):
    """
    Single-post and tag-query downloader.
    Output layout mirrors found/no_match:
    downloads/found/media, downloads/found/tags, downloads/found/source, downloads/found/searched, downloads/found/cache
    """

    # Dedicated signal for logs coming from worker threads.
    # Direct UI writes from a worker thread can close Qt apps on Windows
    # without a Python traceback.
    log_requested = Signal(str)

    def _append_log_direct(self, msg):
        bounded_append(self.info, str(msg), int(self.main.settings.get("max_console_lines", 2500)))

    def append_log(self, msg):
        if QThread.currentThread() != self.thread():
            self.log_requested.emit(str(msg))
            return
        self._append_log_direct(msg)

    def _make_worker_runtime(self):
        # Snapshot UI/settings before worker starts.
        # Worker thread must not read Qt widgets directly: on Windows/Qt this can
        # silently close the process instead of producing a Python traceback.
        base = self._runtime_base()
        raw_blocklist = self.blocklist.text() or ""
        return {
            "base": base,
            "downloads_root": base / "downloads",
            "blocklist": {x.strip().lower() for x in re.split(r"[,;\s]+", raw_blocklist) if x.strip()},
            "settings": dict(self.main.settings),
        }

    def _runtime_settings(self):
        rt = getattr(self, "_worker_runtime", None)
        if isinstance(rt, dict) and isinstance(rt.get("settings"), dict):
            return rt["settings"]
        return self.main.settings

    def _runtime_base(self):
        rt = getattr(self, "_worker_runtime", None)
        if isinstance(rt, dict) and rt.get("base") is not None:
            return Path(rt["base"])
        return ensure_output_base(
            self.main.settings.get("output_dir"),
            self.main.settings.get("root")
        )

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.log_requested.connect(self._append_log_direct)

        lay = QVBoxLayout(self)
        title = QLabel("Граббер")
        title.setStyleSheet("font-size:26px;font-weight:900")
        lay.addWidget(title)

        row = QHBoxLayout()
        self.url = QLineEdit("https://rule34.xxx/index.php?page=post&s=view&id=")
        self.open_btn = QPushButton("Открыть в br34")
        self.download_btn = QPushButton("Скачать пост")
        row.addWidget(self.url, 1)
        row.addWidget(self.open_btn)
        row.addWidget(self.download_btn)
        lay.addLayout(row)

        tag_row = QHBoxLayout()
        self.tag_site = QLineEdit("https://rule34.xxx")
        self.tag_query = QLineEdit("1girls")
        self.tag_limit = QSpinBox()
        self.tag_limit.setRange(1, 1000)
        self.tag_limit.setValue(50)
        self.tag_download_btn = QPushButton("Скачать тег")
        self.pause_btn = QPushButton("Пауза")
        self.stop_btn = QPushButton("Стоп")
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.dedupe_btn = QPushButton("Дубликаты")
        self.cleanup_btn = QPushButton("Очистка блоклиста")
        tag_row.addWidget(QLabel("Сайт:"))
        tag_row.addWidget(self.tag_site, 1)
        tag_row.addWidget(QLabel("Тег/поиск:"))
        tag_row.addWidget(self.tag_query, 2)
        tag_row.addWidget(QLabel("Лимит:"))
        tag_row.addWidget(self.tag_limit)
        tag_row.addWidget(self.tag_download_btn)
        tag_row.addWidget(self.pause_btn)
        tag_row.addWidget(self.stop_btn)
        tag_row.addWidget(self.dedupe_btn)
        tag_row.addWidget(self.cleanup_btn)
        lay.addLayout(tag_row)

        block_row = QHBoxLayout()
        self.blocklist = QLineEdit(
            self.main.settings.get("downloader_blocklist", DEFAULT_BLOCKLIST)
        )
        block_row.addWidget(QLabel("Блоклист тегов:"))
        block_row.addWidget(self.blocklist, 1)
        lay.addLayout(block_row)

        def _save_downloader_blocklist():
            try:
                self.main.settings["downloader_blocklist"] = self.blocklist.text()
                save_settings(self.main.settings)
            except Exception:
                pass

        self.blocklist.editingFinished.connect(_save_downloader_blocklist)

        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)
        self.info.setPlainText(
            "Downloader.\n\n"
            "Одиночный пост: вставь URL поста → Скачать пост.\n"
            "По тегу: укажи сайт и тег/поиск → Скачать тег.\n\n"
            "Структура:\n"
            "Local_Booru_Output/downloads/found/media\n"
            "Local_Booru_Output/downloads/found/tags\n"
            "Local_Booru_Output/downloads/found/source\n"
            "Local_Booru_Output/downloads/found/searched\n"
            "Local_Booru_Output/downloads/found/cache\n"
        )
        lay.addWidget(self.info, 1)

        self.open_btn.clicked.connect(self.open_br34)
        self.download_btn.clicked.connect(self.download_post)
        self.tag_download_btn.clicked.connect(self.download_tag_query)
        self.pause_btn.clicked.connect(self.toggle_pause_worker)
        self.stop_btn.clicked.connect(self.stop_worker)
        self.dedupe_btn.clicked.connect(lambda: self.main.go("Duplicates") if hasattr(self.main, "go") else self.scan_and_clean_duplicates())
        self.cleanup_btn.clicked.connect(self.cleanup_by_blocklist)

    def retranslate(self):
        pass

    def open_br34(self):
        open_br34(self.url.text().strip(), self, log_func=self.append_log)

    def start_downloader_worker(self, mode, payload):
        if getattr(self, "worker", None) and self.worker.isRunning():
            self.append_log("BUSY: downloader уже работает")
            return

        self.open_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        self.tag_download_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.pause_btn.setText("Пауза")
        self.dedupe_btn.setEnabled(False)
        self.cleanup_btn.setEnabled(False)

        self._worker_runtime = self._make_worker_runtime()
        self.worker = DownloaderWorker(self, mode, payload)
        self.worker.log.connect(self.log_requested.emit)
        self.worker.done.connect(self.on_worker_done)
        self.worker.start()

    def on_worker_done(self):
        self.open_btn.setEnabled(True)
        self.download_btn.setEnabled(True)
        self.tag_download_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setText("Пауза")
        self.dedupe_btn.setEnabled(True)
        self.cleanup_btn.setEnabled(True)
        self._worker_runtime = None
        self.append_log("WORKER DONE")

    def current_worker(self):
        w = getattr(self, "worker", None)
        if w is not None and w.isRunning():
            return w
        return None

    def should_stop(self):
        w = self.current_worker()
        return bool(w and getattr(w, "stop_requested", False))

    def wait_if_paused(self):
        w = self.current_worker()
        if w and hasattr(w, "wait_if_paused"):
            w.wait_if_paused()

    def toggle_pause_worker(self):
        w = self.current_worker()
        if not w:
            return
        paused = self.pause_btn.text() != "Продолжить"
        w.set_paused(paused)
        self.pause_btn.setText("Продолжить" if paused else "Пауза")
        self.append_log("PAUSE" if paused else "RESUME")

    def stop_worker(self):
        w = self.current_worker()
        if not w:
            return
        w.request_stop()
        self.append_log("STOP REQUESTED: задача остановится после текущего файла")

    def downloads_root(self):
        rt = getattr(self, "_worker_runtime", None)
        if isinstance(rt, dict) and rt.get("downloads_root") is not None:
            root = Path(rt["downloads_root"])
        else:
            base = self._runtime_base()
            root = base / "downloads"
        for status in ("found", "partial_match", "no_match"):
            for sub in ("media", "tags", "source", "searched", "cache"):
                (root / status / sub).mkdir(parents=True, exist_ok=True)
        return root

    def status_dirs(self, status="found"):
        root = self.downloads_root() / status
        return {
            "media": root / "media",
            "tags": root / "tags",
            "source": root / "source",
            "searched": root / "searched",
            "cache": root / "cache",
        }

    def blocklist_set(self):
        rt = getattr(self, "_worker_runtime", None)
        if isinstance(rt, dict) and isinstance(rt.get("blocklist"), set):
            return set(rt["blocklist"])
        raw = self.blocklist.text() or ""
        return {x.strip().lower() for x in re.split(r"[,;\s]+", raw) if x.strip()}

    def has_blocked_tag(self, tags):
        bad = self.blocklist_set()
        low = {str(t).lower() for t in tags or []}
        return bool(bad & low)

    def _write_sidecars(self, stem, post_url, file_url, post, groups):
        """Compatibility name: store downloader metadata in SQLite only."""
        dirs = self.status_dirs("found")
        media_path = None
        # The caller writes media first, then calls this with final stem. Locate the media file.
        try:
            for f in dirs["media"].iterdir():
                if f.is_file() and f.stem == stem:
                    media_path = f
                    break
        except Exception:
            media_path = None
        if media_path is None:
            return
        groups = _dedupe_group_dict(groups or _groups_from_post(post))
        tags = []
        for g in ("artist", "character", "copyright", "general", "meta"):
            tags += groups.get(g, [])
        try:
            from core.database.storage import upsert_media_metadata
            upsert_media_metadata(
                self.main.settings if hasattr(self, "main") else self.settings,
                media_path,
                tags=tags,
                groups=groups,
                source_text="\n".join([x for x in (post_url, file_url) if x]),
                status="downloaded_found",
                original_path="",
                hash_md5=str(post.get("md5") or "") if isinstance(post, dict) else None,
                raw=post,
                post_url=post_url,
                file_url=file_url,
                site=_host(post_url or file_url),
            )
        except Exception as e:
            try:
                self.append_log(f"SQLITE METADATA ERROR: {type(e).__name__}: {e}")
            except Exception:
                pass


    def _download_file(self, session, file_url, post_url, stem_hint="download", post=None, groups=None):
        dirs = self.status_dirs("found")
        remote_md5 = ""
        if isinstance(post, dict):
            remote_md5 = str(post.get("md5") or "")
        dup, reason = self.is_duplicate_download(file_url, stem_hint, remote_md5)
        if dup:
            self.append_log(f"SKIP DUPLICATE: {reason}")
            return None

        headers = {"Referer": post_url or file_url}
        r = session.get(file_url, timeout=60, stream=True, headers=headers)
        self.append_log(f"FILE STATUS: {r.status_code} {r.headers.get('content-type', '')}")

        if r.status_code >= 400:
            raise RuntimeError(f"file status {r.status_code}")

        ext = _ext_from_url_or_type(file_url, r.headers.get("content-type"))
        stem = _safe_name(stem_hint or Path(urlparse(file_url).path).stem or "download")
        out = dirs["media"] / (stem + ext)
        n = 1
        while out.exists():
            out = dirs["media"] / f"{stem}_{n}{ext}"
            n += 1

        total = 0
        with out.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 512):
                self.wait_if_paused()
                if self.should_stop():
                    self.append_log("STOPPED DURING FILE DOWNLOAD")
                    try:
                        out.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return None
                if chunk:
                    f.write(chunk)
                    total += len(chunk)

        # Exact duplicate safety after download. Delete media and sidecars so the
        # gallery/tag database does not keep orphan metadata.
        try:
            is_dup, old_path = self.is_exact_existing_file(out)
            if is_dup:
                delete_media_with_sidecars(out)
                self.append_log(f"DELETE JUST-DOWNLOADED DUPLICATE: same as {old_path}")
                return None
        except Exception:
            pass

        final_stem = out.stem
        if post is not None:
            self._write_sidecars(final_stem, post_url, file_url, post, groups or _groups_from_post(post))

        self.append_log(f"SAVED: {out} ({total} bytes)")
        return out

    def _merge_html_groups_into_post(self, session, post_url, post):
        """
        DAPI/rule34 XML often returns only flat 'tags'.
        The visible post page has grouped sidebar tags. Merge those groups
        into the post dict before writing sidecars.
        """
        try:
            # Only bother if groups are empty or everything is general.
            cur_groups = _groups_from_post(post)
            has_grouped = bool(
                cur_groups.get("artist")
                or cur_groups.get("character")
                or cur_groups.get("copyright")
                or cur_groups.get("meta")
            )

            if has_grouped:
                return post, cur_groups

            self.append_log(f"HTML GROUPS TRY: {post_url}")
            r = session.get(post_url, timeout=30)
            self.append_log(f"HTML GROUPS STATUS: {r.status_code} {r.headers.get('content-type', '')}")

            if r.status_code >= 400:
                return post, cur_groups

            html_groups = self._groups_from_html(r.text)
            if any(html_groups.values()):
                post = dict(post or {})
                post["tag_string_artist"] = " ".join(html_groups.get("artist", []))
                post["tag_string_character"] = " ".join(html_groups.get("character", []))
                post["tag_string_copyright"] = " ".join(html_groups.get("copyright", []))
                post["tag_string_general"] = " ".join(html_groups.get("general", []))
                post["tag_string_meta"] = " ".join(html_groups.get("meta", []))

                all_tags = []
                for g in ("artist", "character", "copyright", "general", "meta"):
                    all_tags += html_groups.get(g, [])
                post["tag_string"] = " ".join(all_tags)

                self.append_log(
                    "HTML GROUPS OK: "
                    f"artist={len(html_groups.get('artist', []))} "
                    f"character={len(html_groups.get('character', []))} "
                    f"copyright={len(html_groups.get('copyright', []))} "
                    f"general={len(html_groups.get('general', []))} "
                    f"meta={len(html_groups.get('meta', []))}"
                )

                return post, html_groups

            return post, cur_groups

        except Exception as e:
            self.append_log(f"HTML GROUPS ERROR: {type(e).__name__}: {e}")
            return post, _groups_from_post(post)

    def _find_post_data_and_file_url(self, post_url, session):
        for api in _candidate_api_urls(post_url):
            try:
                self.append_log(f"API TRY: {_mask_sensitive_url(api)}")
                r = session.get(api, timeout=30)
                self.append_log(f"API STATUS: {r.status_code} {r.headers.get('content-type', '')}")
                if r.status_code >= 400:
                    continue
                posts = _posts_from_json_response(r)
                if posts:
                    post = posts[0]
                    file_url = _extract_file_url_from_json(post)
                    if file_url:
                        merged_post, _merged_groups = self._merge_html_groups_into_post(session, post_url, post)
                        return merged_post, file_url
            except Exception as e:
                self.append_log(f"API ERROR: {type(e).__name__}: {e}")

        self.append_log(f"HTML TRY: {post_url}")
        r = session.get(post_url, timeout=30)
        self.append_log(f"HTML STATUS: {r.status_code} {r.headers.get('content-type', '')}")
        if r.status_code >= 400:
            raise RuntimeError(f"post page status {r.status_code}")

        file_url = _extract_file_url_from_html(r.text, post_url)
        html_groups = self._groups_from_html(r.text)
        html_tags = []
        for g in ("artist", "character", "copyright", "general", "meta"):
            html_tags += html_groups.get(g, [])
        post = {
            "tag_string": " ".join(html_tags),
            "tag_string_artist": " ".join(html_groups.get("artist", [])),
            "tag_string_character": " ".join(html_groups.get("character", [])),
            "tag_string_copyright": " ".join(html_groups.get("copyright", [])),
            "tag_string_general": " ".join(html_groups.get("general", [])),
            "tag_string_meta": " ".join(html_groups.get("meta", [])),
            "source": post_url,
        }
        return post, file_url

    def _groups_from_html(self, html_text):
        groups = {"artist": [], "character": [], "copyright": [], "general": [], "meta": []}

        try:
            soup = BeautifulSoup(html_text or "", "html.parser")

            selector_map = {
                "artist": [
                    "li.tag-type-artist a[href*='tags=']",
                    ".tag-type-artist a[href*='tags=']",
                    "li[class*='artist'] a[href*='tags=']",
                ],
                "character": [
                    "li.tag-type-character a[href*='tags=']",
                    ".tag-type-character a[href*='tags=']",
                    "li[class*='character'] a[href*='tags=']",
                ],
                "copyright": [
                    "li.tag-type-copyright a[href*='tags=']",
                    ".tag-type-copyright a[href*='tags=']",
                    "li[class*='copyright'] a[href*='tags=']",
                ],
                "general": [
                    "li.tag-type-general a[href*='tags=']",
                    ".tag-type-general a[href*='tags=']",
                    "li[class*='general'] a[href*='tags=']",
                ],
                "meta": [
                    "li.tag-type-metadata a[href*='tags=']",
                    "li.tag-type-meta a[href*='tags=']",
                    ".tag-type-metadata a[href*='tags=']",
                    ".tag-type-meta a[href*='tags=']",
                    "li[class*='metadata'] a[href*='tags=']",
                    "li[class*='meta'] a[href*='tags=']",
                ],
            }

            def candidates_from_link(a):
                out = []
                text = a.get_text(" ", strip=True)
                if text:
                    out.append(text)
                href = a.get("href", "") or ""
                try:
                    q = parse_qs(urlparse(href).query)
                    for raw in q.get("tags", []):
                        out += str(raw).replace("+", " ").split()
                except Exception:
                    pass
                return out

            for group, selectors in selector_map.items():
                for sel in selectors:
                    for a in soup.select(sel):
                        for raw in candidates_from_link(a):
                            tag = _clean_download_tag(raw)
                            if tag:
                                groups[group].append(tag)

            # If classes are absent, use fallback only for real tag sidebar links.
            if not any(groups.values()):
                for a in soup.select("#tag-sidebar a[href*='tags='], #tag-list a[href*='tags=']"):
                    for raw in candidates_from_link(a):
                        tag = _clean_download_tag(raw)
                        if tag:
                            groups["general"].append(tag)

        except Exception:
            pass

        return _dedupe_group_dict(groups)

    def _tags_from_html(self, html_text):
        groups = self._groups_from_html(html_text)
        out = []
        for g in ("artist", "character", "copyright", "general", "meta"):
            out += groups.get(g, [])
        return list(dict.fromkeys(out))

    def all_known_media_files(self):
        base = self._runtime_base()
        roots = [
            base / "found" / "media",
            base / "partial_match" / "media",
            base / "no_match" / "media",
            base / "downloads" / "found" / "media",
            base / "downloads" / "partial_match" / "media",
            base / "downloads" / "no_match" / "media",
        ]
        exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mov"}
        out = []
        for root in roots:
            if root.exists():
                out += [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts]
        return out

    def is_duplicate_download(self, file_url, stem_hint, remote_md5="", post_url=""):
        """Pre-download duplicate check.

        Checks in order (fastest first):
        1. DB: exact URL match in raw_metadata.file_url or post_url
        2. DB: MD5 match in images table
        3. Filesystem: filename/MD5 check (legacy)
        """
        # 1. URL-based check (fast, O(1) with index)
        if file_url or post_url:
            try:
                from core.database.connection import get_connection
                conn = get_connection(self.main.settings)
                for url in [u for u in [file_url, post_url] if u]:
                    row = conn.execute(
                        "SELECT i.path FROM raw_metadata rm "
                        "JOIN images i ON i.id=rm.image_id "
                        "WHERE rm.file_url=? OR rm.post_url=?",
                        (url, url)
                    ).fetchone()
                    if row:
                        return True, f"URL already in DB: {row[0]}"
            except Exception:
                pass

        # 2. MD5-based check in DB (fast if indexed)
        remote_md5 = str(remote_md5 or "").lower().strip()
        if remote_md5:
            try:
                from core.database.connection import get_connection
                conn = get_connection(self.main.settings)
                row = conn.execute(
                    "SELECT path FROM images WHERE hash_md5=?", (remote_md5,)
                ).fetchone()
                if row:
                    return True, f"MD5 already in DB: {row[0]}"
            except Exception:
                pass

        # 3. Filesystem fallback (legacy)
        wanted_stem = _safe_name(stem_hint)
        wanted_base = _base_without_copy_suffix(wanted_stem).lower()

        for p in self.all_known_media_files():
            try:
                stem = p.stem.lower()
                base = _base_without_copy_suffix(stem).lower()

                if remote_md5:
                    if remote_md5 == stem:
                        return True, f"same remote md5/name: {p}"
                    try:
                        if _file_md5(p).lower() == remote_md5:
                            return True, f"same remote md5/content: {p}"
                    except Exception:
                        pass

                if stem == wanted_stem.lower():
                    return True, f"same name: {p}"

                # Do not create Windows-style duplicates when the original name exists.
                if base == wanted_base and (_is_copy_suffix(stem) or _is_copy_suffix(wanted_stem)):
                    return True, f"copy-suffix duplicate: {p}"
            except Exception:
                pass
        return False, ""

    def is_exact_existing_file(self, new_file: Path) -> tuple[bool, str]:
        """Post-download 100% duplicate check: md5 + bytes + pixels + base-name.

        This is intentionally stricter than a plain pHash comparison. It prevents
        downloader from keeping files that are already in the gallery/found/no_match.
        """
        try:
            new_md5 = _file_md5(new_file).lower()
            new_bytes = int(new_file.stat().st_size)
            new_pixels = _duplicate_image_size(new_file)
            new_base = _base_without_copy_suffix(new_file.stem).lower()
        except Exception:
            return False, ""

        for oldp in self.all_known_media_files():
            if oldp == new_file or not oldp.exists():
                continue
            try:
                if int(oldp.stat().st_size) != new_bytes:
                    continue
                if _file_md5(oldp).lower() != new_md5:
                    continue
                if _duplicate_image_size(oldp) != new_pixels:
                    continue
                old_base = _base_without_copy_suffix(oldp.stem).lower()
                if old_base == new_base or oldp.stem.lower() == new_md5:
                    return True, str(oldp)
            except Exception:
                pass
        return False, ""


    def cleanup_by_blocklist(self):
        self.start_downloader_worker("cleanup", {})

    def scan_and_clean_duplicates(self):
        self.start_downloader_worker("dedupe", {})

    def _cleanup_by_blocklist_impl(self):
        bad = self.blocklist_set()
        if not bad:
            self.append_log("BLOCKLIST EMPTY")
            return

        base = self._runtime_base()

        roots = [
            base / "found" / "media",
            base / "downloads" / "found" / "media",
        ]

        deleted = 0

        for media_root in roots:
            if not media_root.exists():
                continue

            bucket = media_root.parent

            for media in media_root.rglob("*"):
                if not media.is_file():
                    continue

                stem = media.stem
                tags_json = bucket / "tags" / f"{stem}.tags.json"
                tags_txt = bucket / "tags" / f"{stem}.tags.txt"
                source_txt = bucket / "source" / f"{stem}.sources.txt"

                tags = set()

                try:
                    if tags_json.exists():
                        import json
                        d = json.loads(tags_json.read_text(encoding="utf-8"))
                        for t in d.get("tags", []):
                            tags.add(str(t).lower())
                except Exception:
                    pass

                if bad & tags:
                    try:
                        media.unlink(missing_ok=True)
                        tags_json.unlink(missing_ok=True)
                        tags_txt.unlink(missing_ok=True)
                        source_txt.unlink(missing_ok=True)
                        deleted += 1
                        self.append_log(f"BLOCKLIST DELETE: {media.name}")
                    except Exception as e:
                        self.append_log(f"BLOCKLIST DELETE ERROR: {e}")

        self.append_log(f"BLOCKLIST CLEANUP DONE: {deleted}")

    def _scan_and_clean_duplicates_impl(self):
        files = self.all_known_media_files()
        self.append_log(f"DUP SCAN: files={len(files)}")
        by_md5 = {}
        auto_deleted = 0
        candidates = []

        for p in files:
            try:
                md = _file_md5(p)
                by_md5.setdefault(md, []).append(p)
            except Exception as e:
                self.append_log(f"DUP HASH ERROR: {p}: {e}")

        for md, group in by_md5.items():
            if len(group) < 2:
                continue
            group = sorted(group, key=lambda x: (len(str(x)), str(x).lower()))
            keep = group[0]
            for p in group[1:]:
                if _base_without_copy_suffix(p.stem).lower() == _base_without_copy_suffix(keep.stem).lower() and _is_copy_suffix(p.stem):
                    try:
                        delete_media_with_sidecars(p)
                        auto_deleted += 1
                        self.append_log(f"AUTO DELETE EXACT COPY: {p}")
                    except Exception as e:
                        self.append_log(f"DELETE ERROR: {p}: {e}")
                else:
                    candidates.append((keep, p, "exact md5"))

        # visual duplicate candidates: conservative only distance <= 3
        vh = {}
        for p in files:
            if not p.exists() or p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            h = _visual_hash(p)
            if h:
                vh.setdefault(h, []).append(p)
        for h, group in vh.items():
            if len(group) > 1:
                group = sorted(group, key=lambda x: (len(str(x)), str(x).lower()))
                for p in group[1:]:
                    if p.exists() and group[0].exists():
                        candidates.append((group[0], p, "same visual hash"))

        self.append_log(f"DUP SCAN DONE: auto_deleted={auto_deleted}, manual_candidates={len(candidates)}")
        for a, b, reason in candidates[:50]:
            self.show_duplicate_choice(a, b, reason)

    def show_duplicate_choice(self, a, b, reason):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Дубликат: {reason}")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Выбери, что удалить. Если не уверен — нажми оставить оба."))

        grid = QGridLayout()
        for col, p in enumerate([a, b]):
            lab = QLabel()
            lab.setAlignment(Qt.AlignCenter)
            pix = QPixmap(str(p))
            if not pix.isNull():
                lab.setPixmap(pix.scaled(260, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                lab.setText("NO PREVIEW")
            grid.addWidget(lab, 0, col)
            grid.addWidget(QLabel(_media_size_text(p)), 1, col)

        lay.addLayout(grid)
        row = QHBoxLayout()
        del_a = QPushButton("Удалить левую")
        del_b = QPushButton("Удалить правую")
        keep = QPushButton("Оставить оба")
        row.addWidget(del_a); row.addWidget(del_b); row.addWidget(keep)
        lay.addLayout(row)

        del_a.clicked.connect(lambda: (delete_media_with_sidecars(Path(a)), dlg.accept()))
        del_b.clicked.connect(lambda: (delete_media_with_sidecars(Path(b)), dlg.accept()))
        keep.clicked.connect(dlg.reject)
        dlg.exec()

    def download_post(self):
        post_url = self.url.text().strip()
        if not post_url:
            self.append_log("ERROR: URL пустой")
            return
        self.start_downloader_worker("post", {"post_url": post_url})

    def _download_post_impl(self, post_url):
        try:
            session = _session_for_url(post_url, self.append_log)
            post, file_url = self._find_post_data_and_file_url(post_url, session)

            if not file_url:
                self.append_log("ERROR: не нашёл file_url на странице/API")
                return

            tags = _tag_list_from_post(post)
            if self.has_blocked_tag(tags):
                self.append_log(f"SKIP BLOCKED TAGS: {sorted(set(tags) & self.blocklist_set())}")
                return

            self.append_log(f"FILE URL: {file_url}")
            stem = str(post.get("md5") or post.get("id") or Path(urlparse(file_url).path).stem or "download")
            self._download_file(session, file_url, post_url, stem, post, _groups_from_post(post))

        except Exception as e:
            self.append_log(f"DOWNLOAD ERROR: {type(e).__name__}: {e}")

    def _post_url_from_post(self, base_url, post):
        host = _host(base_url)
        pid = str(post.get("id") or "")
        if not pid:
            return base_url
        if "allthefallen" in host or "danbooru" in host or "donmai" in host or host == "e621.net":
            return f"https://{host}/posts/{pid}"
        if host == "gelbooru.com":
            return f"https://gelbooru.com/index.php?page=post&s=view&id={pid}"
        if host in ("rule34.xxx", "api.rule34.xxx"):
            return f"https://rule34.xxx/index.php?page=post&s=view&id={pid}"
        if host == "rule34.us":
            return f"https://rule34.us/index.php?page=post&s=view&id={pid}"
        return base_url

    def download_tag_query(self):
        site = self.tag_site.text().strip()
        tags = self.tag_query.text().strip()
        limit_total = int(self.tag_limit.value())

        if not site or not tags:
            self.append_log("ERROR: сайт или тег пустой")
            return

        self.start_downloader_worker(
            "tag",
            {"site": site, "tags": tags, "limit_total": limit_total},
        )

    def _download_tag_query_impl(self, site, tags, limit_total):
        if not site or not tags:
            self.append_log("ERROR: сайт или тег пустой")
            return

        try:
            session = _session_for_url(site, self.append_log)
            got = 0
            page = 0
            per_page = min(100, max(1, limit_total))

            while got < limit_total:
                self.wait_if_paused()
                if self.should_stop():
                    self.append_log(f"STOPPED TAG DOWNLOAD: {got}")
                    return
                api = _tag_search_api(site, tags, page=page, limit=min(per_page, limit_total - got), settings=self._runtime_settings())
                if not api:
                    self.append_log("ERROR: этот сайт пока не поддержан для tag download")
                    return

                self.append_log(f"TAG API TRY: {_mask_sensitive_url(api)}")
                r = session.get(api, timeout=40)
                self.append_log(f"TAG API STATUS: {r.status_code} {r.headers.get('content-type', '')}")

                if r.status_code >= 400:
                    self.append_log(f"ERROR: tag api status {r.status_code}")
                    return

                posts = _posts_from_json_response(r)

                if not posts:
                    raw_preview = (r.text or "")[:300].replace("\n", " ")
                    self.append_log(f"NO POSTS PARSED RAW: {raw_preview}")
                    if "Missing authentication" in raw_preview:
                        self.append_log("AUTH ERROR: проверь login/api_key/user_id в APT Sites для этого сайта")
                    self.append_log("DONE: посты закончились")
                    break

                for post in posts:
                    self.wait_if_paused()
                    if self.should_stop():
                        self.append_log(f"STOPPED TAG DOWNLOAD: {got}")
                        return
                    if got >= limit_total:
                        break

                    post_tags = _tag_list_from_post(post)
                    if self.has_blocked_tag(post_tags):
                        self.append_log(f"SKIP BLOCKED: post={post.get('id')}")
                        continue

                    file_url = _extract_file_url_from_json(post)
                    if not file_url:
                        self.append_log(f"SKIP NO FILE_URL: post={post.get('id')}")
                        continue

                    post_url = self._post_url_from_post(site, post)
                    stem = str(post.get("md5") or post.get("id") or Path(urlparse(file_url).path).stem or "download")

                    try:
                        self.append_log(f"DOWNLOAD {got + 1}/{limit_total}: {post_url}")
                        post, merged_groups = self._merge_html_groups_into_post(session, post_url, post)
                        self._download_file(session, file_url, post_url, stem, post, merged_groups)
                        got += 1
                        time.sleep(0.3)
                    except Exception as e:
                        self.append_log(f"SKIP DOWNLOAD ERROR: {type(e).__name__}: {e}")

                page += 1

            self.append_log(f"DONE TAG DOWNLOAD: {got}")

        except Exception as e:
            self.append_log(f"TAG DOWNLOAD ERROR: {type(e).__name__}: {e}")
