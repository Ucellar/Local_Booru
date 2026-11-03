"""Single-post imports submitted by the Local Booru browser companion.

The browser supplies page identity and the best media URLs visible in the real
logged-in page.  Local Booru remains the owner of downloading, exact-MD5
fanout, reverse search, deduplication and SQLite persistence.
"""
from __future__ import annotations

import hashlib
import queue
import threading
import time
import uuid
from core.background_tasks import get_background_task_registry
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.database.storage import (
    bind_site_md5_alias,
    found_media_path_by_md5,
    media_path_by_md5,
    media_path_by_site_md5,
    upsert_media_metadata,
)
from core.grabber_md5_cache import upsert_item
from core.network import is_permanently_disabled_url
from core.path_utils import path_key as _path_key, same_path as _same_path
from core.site_posts import PAGE_METHODS


def _host(value: Any) -> str:
    raw = str(value or "").strip()
    if "://" in raw:
        try:
            raw = urlparse(raw).netloc
        except Exception:
            pass
    raw = raw.lower().split("@")[ -1].split(":")[0].lstrip(".")
    if raw.startswith("www."):
        raw = raw[4:]
    aliases = {
        "api.rule34.xxx": "rule34.xxx",
        "api-cdn.rule34.xxx": "rule34.xxx",
        "img2.gelbooru.com": "gelbooru.com",
        "cdn.donmai.us": "danbooru.donmai.us",
        "static1.e621.net": "e621.net",
        "static1.e926.net": "e926.net",
    }
    return aliases.get(raw, raw)


def _url(value: Any) -> str:
    value = str(value or "").strip()
    if not value.lower().startswith(("http://", "https://")):
        return ""
    if is_permanently_disabled_url(value):
        return ""
    return value.rstrip("/")


def _looks_like_direct_media_url(value: Any) -> bool:
    url = _url(value)
    if not url:
        return False
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return False
    return Path(path).suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".mp4", ".webm"}


def _md5(value: Any) -> str:
    value = str(value or "").strip().lower()
    return value if len(value) == 32 and all(c in "0123456789abcdef" for c in value) else ""


def _groups(item: dict[str, Any]) -> dict[str, list[str]]:
    raw = item.get("groups") if isinstance(item.get("groups"), dict) else {}
    out: dict[str, list[str]] = {}
    for category, values in raw.items():
        cleaned: list[str] = []
        for value in values or []:
            tag = str(value or "").strip().replace(" ", "_")
            if tag and tag not in cleaned:
                cleaned.append(tag)
        if cleaned:
            out[str(category or "general").lower()] = cleaned
    tags = [str(x or "").strip().replace(" ", "_") for x in item.get("tags") or []]
    tags = [x for x in tags if x]
    assigned = {tag for values in out.values() for tag in values}
    remaining = [tag for tag in tags if tag not in assigned]
    if remaining:
        out.setdefault("general", []).extend(x for x in remaining if x not in out.get("general", []))
    return out


def _flat(groups: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    for values in groups.values():
        for tag in values:
            if tag not in out:
                out.append(tag)
    return out


def _media_urls(item: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("media_urls", "original_urls"):
        for value in item.get(key) or []:
            value = _url(value)
            if value and value not in out:
                out.append(value)
    for key in ("file_url", "original_url", "large_file_url", "sample_url", "preview_url"):
        value = _url(item.get(key))
        if value and value not in out:
            out.append(value)
    return out


def _canonical_post_url(site: str, post_url: str, post_id: Any) -> str:
    """A post's own address on a site of a known engine (v5.44.10): a grid's link to it carries the grid's search too
    (``…&id=5&tags=some_artist``) — one post, one address among the card's sources."""
    pid = str(post_id or "").strip()
    if not post_url or not pid.isdigit():
        return post_url
    from core.browser_companion_index import post_key
    key = post_key(site, post_url)
    if not key or key[1] != pid:
        return post_url
    try:
        from core.parser.sites import post_page_url
        canonical = post_page_url(site, pid)
    except Exception:
        return post_url
    if canonical and _host(canonical) == _host(post_url):
        return canonical
    return post_url


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    from core.browser_companion_index import resolve_post_identity
    item = dict(item or {})
    post_url = _url(item.get("post_url"))
    urls = _media_urls(item)
    site = _host(item.get("site") or post_url or (urls[0] if urls else ""))
    groups = _groups(item)
    try:
        trusted = int(item.get("identity_v") or 0) >= 2
    except (TypeError, ValueError):
        trusted = False
    # An older extension sent the search page as the post of a right-clicked grid card: that page
    # is never fetched as «the post» (its logo would become the original, its tag list the tags).
    post_url, post_id = resolve_post_identity(site, post_url, item.get("post_id"), trusted=trusted,
                                              is_post_page=bool(item.get("is_post_page")), drop_non_post_url=True)
    post_url = _canonical_post_url(site, _url(post_url), post_id)
    md5 = _md5(item.get("md5"))
    return {
        "site": site,
        "post_id": post_id,
        "post_url": post_url,
        "media_urls": urls,
        "file_url": urls[0] if urls else _url(item.get("file_url")),
        "md5": md5,
        "tags": _flat(groups),
        "groups": groups,
        "page_title": str(item.get("page_title") or "")[:500],
        "is_post_page": bool(item.get("is_post_page", False)),
        "width": max(0, int(item.get("width") or 0)),
        "height": max(0, int(item.get("height") or 0)),
        "file_size": max(0, int(item.get("file_size") or 0)),
        "format": str(item.get("format") or "")[:32].lower(),
        "author_name": str(item.get("author_name") or "")[:300],
        "author_id": str(item.get("author_id") or "")[:160],
        "caption": str(item.get("caption") or item.get("description") or "")[:20000],
        "published_at": str(item.get("published_at") or "")[:100],
        "original_source": _url(item.get("original_source")),
        "page_index": max(0, int(item.get("page_index") or 0)),
        "family": item.get("family") if isinstance(item.get("family"), dict) else {},
        "notes": item.get("notes") if isinstance(item.get("notes"), list) else [],
        "ugoira": item.get("ugoira") if isinstance(item.get("ugoira"), dict) else {},
    }


def _identity(item: dict[str, Any]) -> str:
    if item.get("site") and item.get("post_id"):
        # Multipage works share one post id but are distinct media slots.
        return f"{item['site']}:{item['post_id']}:page:{int(item.get('page_index') or 0)}"
    if item.get("post_url"):
        return str(item["post_url"])
    if item.get("md5"):
        return f"md5:{item['md5']}"
    first = (item.get("media_urls") or [""])[0]
    return first or uuid.uuid4().hex


def _source_bundle(item: dict[str, Any], groups: dict[str, list[str]], method: str) -> list[dict[str, Any]]:
    source = item.get("post_url") or item.get("file_url") or ""
    if not source or not groups:
        return []
    return [{"url": source, "groups": groups, "method": method}]


def _existing_path(settings: dict, md5: str) -> str:
    if not md5:
        return ""
    return (
        found_media_path_by_md5(settings, md5)
        or media_path_by_md5(settings, md5)
        or ""
    )


def _existing_path_for_site_md5(settings: dict, site_md5: str) -> str:
    """Resolve an authoritative site MD5 without confusing it with local bytes."""
    if not site_md5:
        return ""
    return _existing_path(settings, site_md5) or media_path_by_site_md5(settings, site_md5)


def _cleanup_staging(settings: dict, staging_path: str | Path, canonical_path: str | Path, *, operation: str) -> None:
    if not staging_path or not canonical_path or _same_path(staging_path, canonical_path):
        return
    try:
        from core.services.media_storage_service import unlink_managed
        unlink_managed(settings, Path(staging_path), operation=operation)
    except Exception:
        pass


# (v5.44.10) What a page of a site shows is not always what the site says about its post.  A card on a search grid is a
# link and a thumbnail — the ``tags=`` of its link is the search the grid was opened with («some_artist», ``all`` on the
# main list); the extension 0.9 and older took that for the post's tags, the program's check of the badges then put it in
# place of the card's tags of that site (the card lost them all — and its place in a сборка), and a post page gave its
# tags without categories (all of them «Общие») plus ``all`` of the site's menu.  So: the site's own answer is the tags
# of a site; what a page shows only fills in what the program has not got from the site.
JUNK_TAGS = {"all"}                         # «every post» of a Gelbooru-like site (its menu: tags=all) — no tag
SITE_METHOD = "site_post"                   # a set of tags the site itself gave for its post (the import asked it)


def _page_groups(settings: dict, item: dict[str, Any]) -> dict[str, list[str]]:
    """The tags a post page shows, as the program keeps them: none from a grid's card (its link carries the grid's
    search, not the post's tags), no :data:`JUNK_TAGS`, the categories the page gave (a page without them — every tag
    «general»: the card's page shows them in the categories most sites give them; 5.44.10–5.44.48 wrote the library's
    categories into the set)."""
    if not item.get("is_post_page"):
        return {}
    groups = {}
    for category, values in (item.get("groups") or {}).items():
        kept = [t for t in values or [] if str(t).strip().lower() not in JUNK_TAGS]
        if kept:
            groups[category] = kept
    return groups


def _card_site_sets(settings: dict, path: str | Path, site: str) -> str:
    """Whose tags of ``site`` the card at ``path`` has: "site" — the site's own (the parser, a site's answer), "page" —
    only what the extension saw on a page, "" — none."""
    from core.site_posts import canonical_site
    wanted = canonical_site(site)
    if not wanted:
        return ""
    try:
        from core.database.connection import db
        with db(settings, readonly=True) as con:
            row = con.execute("SELECT id FROM images WHERE deleted=0 AND path=? ORDER BY id DESC LIMIT 1",
                              (str(Path(path)),)).fetchone()
            if not row:
                return ""
            rows = con.execute(
                "SELECT DISTINCT s.host, ist.acquisition FROM image_source_tags ist JOIN sources s ON s.id=ist.source_id "
                "WHERE ist.image_id=?", (int(row[0]),)).fetchall()
    except Exception:
        return ""
    kinds = {("page" if str(acq or "") in PAGE_METHODS else "site") for host, acq in rows
             if canonical_site(str(host or "")) == wanted}
    return "site" if "site" in kinds else ("page" if kinds else "")


def _site_post_rows(settings: dict, site: str, site_post, remote_post: dict) -> list:
    """What the site said of the post (its uploader, parent, source links) for ``site_posts`` — from the parser's post
    (``Post.meta``), else from the post API's raw answer."""
    from core.site_posts import from_post, rows_from_candidates
    rows = []
    if site_post is not None:
        row = from_post(site_post)
        if row:
            rows.append(row)
    if not rows and remote_post and site:
        try:
            rows = rows_from_candidates(settings, [(0, site, remote_post)])
        except Exception:
            rows = []
    return rows


def _merge_metadata(settings: dict, path: str | Path, item: dict[str, Any], groups: dict[str, list[str]], *, method: str, md5: str = "") -> None:
    path = str(Path(path))
    tags = _flat(groups)
    if not path or not tags:
        return
    if item.get("_groups_from") == "site":
        method = SITE_METHOD
    post_url = str(item.get("post_url") or "")
    file_url = str(item.get("file_url") or ((item.get("media_urls") or [""])[0]) or "")
    upsert_media_metadata(
        settings,
        path,
        tags=tags,
        groups=groups,
        source_text=f"Browser extension {post_url or file_url}",
        status="tagged",
        hash_md5=md5 or None,
        post_url=post_url,
        file_url=file_url,
        site=str(item.get("site") or ""),
        merge_existing=True,
        import_origin="browser_extension",
        source_tag_groups=_source_bundle(item, groups, method),
        raw={"origin": "browser_extension", "post_id": item.get("post_id") or ""},
    )


def _file_md5(path: str | Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class BrowserImportManager:
    """One bounded background lane for manual browser imports and tag refreshes."""

    def __init__(self, settings: dict, log_fn=None):
        self.settings = dict(settings or {})
        self.log = log_fn or (lambda _m: None)
        self._queue: queue.Queue[tuple[str, str, dict[str, Any], str]] = queue.Queue(maxsize=1000)
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._pending_keys: set[str] = set()
        self._states: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._task_registry = get_background_task_registry()
        self._thread = threading.Thread(target=self._run, name="browser-import-lane", daemon=True)
        self._thread.start()
        self.family_lane = None
        if bool(self.settings.get("automatic_family_completion_enabled", True)):
            try:
                from core.family_workflows import FamilyCompletionLane
                self.family_lane = FamilyCompletionLane(self.settings, self.submit, wait=self.wait_task, trace=lambda m: self.log(f"[Family completion] {m}"))
            except Exception as exc:
                self.log(f"Family completion lane unavailable: {type(exc).__name__}: {exc}")

    def stop(self) -> None:
        if getattr(self, "family_lane", None) is not None:
            try:
                self.family_lane.stop()
            except Exception:
                pass
        self._stop.set()
        try:
            self._queue.put_nowait(("", "stop", {}, ""))
        except Exception:
            pass
        self._thread.join(timeout=5.0)

    def status(self) -> dict[str, Any]:
        with self._lock:
            recent = list(self._states.values())[-20:]
            active = sum(1 for x in self._states.values() if x.get("status") == "running")
            waiting = sum(1 for x in self._states.values() if x.get("status") == "queued")
        return {"queued": waiting, "active": active, "recent": recent}

    @property
    def alive(self) -> bool:
        """Its lane still takes tasks (not stopped)."""
        return self._thread.is_alive() and not self._stop.is_set()

    def task(self, task_id: str) -> dict[str, Any]:
        """A copy of the task's state ({} — no such task): status (queued, running, done, failed, deferred…),
        message, result_path."""
        with self._lock:
            return dict(self._states.get(str(task_id or "")) or {})

    def wait_task(self, task_id: str, timeout: float = 1800.0) -> dict[str, Any]:
        """Wait from a side lane for the real BrowserImport result.

        This prevents the durable family-completion job from being marked
        complete merely because an in-memory import was accepted.
        """
        deadline = time.monotonic() + max(1.0, float(timeout or 1800.0))
        while time.monotonic() < deadline and not self._stop.is_set():
            with self._lock:
                state = dict(self._states.get(str(task_id)) or {})
            if not state:
                return {"status": "failed", "message": "browser import task disappeared"}
            if str(state.get("status") or "") in {"done", "failed", "cancelled", "deferred"}:
                return state
            time.sleep(0.20)
        return {"status": "failed", "message": "browser import task timeout"}

    def submit(self, raw_item: dict[str, Any]) -> dict[str, Any]:
        item = normalize_item(raw_item)
        if not item.get("post_url") and not item.get("media_urls"):
            return {"ok": False, "error": "post_or_media_url_required"}
        key = "full:" + _identity(item)
        return self._enqueue("full", item, key)

    def refresh_visible_metadata(self, raw_item: dict[str, Any], canonical_path: str) -> dict[str, Any]:
        item = normalize_item(raw_item)
        if not canonical_path or not item.get("groups") or not item.get("post_url"):
            return {"ok": True, "skipped": True}
        if not item.get("is_post_page"):          # (v5.44.10) a grid's card: its link's tags= is the grid's search
            return {"ok": True, "skipped": True}
        key = "metadata:" + _identity(item) + ":" + str(canonical_path)
        return self._enqueue("metadata", item, key, canonical_path=str(canonical_path))

    def _enqueue(self, kind: str, item: dict[str, Any], key: str, canonical_path: str = "") -> dict[str, Any]:
        with self._lock:
            if key in self._pending_keys:
                for task_id, state in reversed(self._states.items()):
                    if state.get("key") == key and state.get("status") in {"queued", "running"}:
                        return {"ok": True, "queued": False, "duplicate": True, "task_id": task_id}
            task_id = "browser_" + uuid.uuid4().hex[:16]
            state = {
                "task_id": task_id,
                "key": key,
                "kind": kind,
                "site": item.get("site") or "",
                "post_id": item.get("post_id") or "",
                "post_url": item.get("post_url") or "",
                "status": "queued",
                "message": "ожидает",
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
            }
            self._states[task_id] = state
            self._pending_keys.add(key)
            try:
                self._task_registry.register(
                    f"browser-import:{kind}",
                    kind="browser_import",
                    task_id=task_id,
                    state="queued",
                    detail=str(item.get("post_url") or item.get("file_url") or ""),
                    progress_text="ожидает browser import",
                    cancellable=False,
                    metadata={
                        "site": str(item.get("site") or ""),
                        "post_id": str(item.get("post_id") or ""),
                    },
                )
            except Exception:
                pass
            while len(self._states) > 200:
                old_id, old = next(iter(self._states.items()))
                if old.get("status") in {"queued", "running"}:
                    break
                self._states.pop(old_id, None)
        try:
            self._queue.put_nowait((task_id, kind, item, canonical_path))
        except queue.Full:
            with self._lock:
                self._pending_keys.discard(key)
                self._states[task_id].update(status="failed", message="очередь переполнена", updated_at=int(time.time()))
            try:
                self._task_registry.fail(task_id, "browser_import_queue_full")
            except Exception:
                pass
            return {"ok": False, "error": "browser_import_queue_full"}
        return {"ok": True, "queued": True, "task_id": task_id}

    def _set(self, task_id: str, **fields: Any) -> None:
        with self._lock:
            state = self._states.get(task_id)
            if not state:
                return
            state.update(fields)
            state["updated_at"] = int(time.time())
            status = str(state.get("status") or "")
            message = str(state.get("message") or "")
        try:
            if status == "running":
                self._task_registry.start(task_id, detail=message, progress_text=message or "browser import")
            elif status == "done":
                self._task_registry.finish(task_id, detail=message, progress_text=message or "завершено")
            elif status == "failed":
                self._task_registry.fail(task_id, message or "browser import failed")
            elif status == "cancelled":
                self._task_registry.cancel(task_id, detail=message)
            elif status == "waiting":
                self._task_registry.set_state(task_id, "waiting", detail=message, progress_text=message or "ожидает")
            elif status == "queued":
                self._task_registry.set_state(task_id, "queued", detail=message, progress_text=message or "ожидает запуска")
            else:
                self._task_registry.progress(task_id, text=message, detail=message)
        except Exception:
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                task_id, kind, item, canonical_path = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if kind == "stop":
                break
            key = ""
            with self._lock:
                key = str((self._states.get(task_id) or {}).get("key") or "")
            self._set(task_id, status="running", message="обработка")
            try:
                from core.maintenance_coordinator import get_maintenance_coordinator
                with self._task_registry.bind(task_id), get_maintenance_coordinator().activity("browser_import"):
                    if kind == "metadata":
                        self._refresh_seen(task_id, item, canonical_path)
                    else:
                        self._process_full(task_id, item)
            except Exception as exc:
                self.log(f"Browser import error: {type(exc).__name__}: {exc}")
                self._set(task_id, status="failed", message=f"{type(exc).__name__}: {exc}"[:500])
            finally:
                with self._lock:
                    self._pending_keys.discard(key)
                self._queue.task_done()

    def _refresh_seen(self, task_id: str, item: dict[str, Any], canonical_path: str) -> None:
        """A post page of a card the library has, seen in the browser: its tags fill in the card's tags of that site
        only when the card has none from the site itself (v5.44.10: before, what the page showed replaced them — a
        grid's search word or the page's tags without categories)."""
        site = str(item.get("site") or "")
        if _card_site_sets(self.settings, canonical_path, site) == "site":
            self._set(task_id, status="done", message="у карточки есть теги этого сайта — не тронуты", result_path=canonical_path)
            return
        groups = _page_groups(self.settings, item)
        if not groups and item.get("post_url"):
            try:
                from core.tagger import Tagger
                tagger = Tagger(dict(self.settings), lambda m: self.log(f"[Browser page refresh] {m}"))
                post = tagger.site_post_from_url(str(item.get("post_url") or ""))
                if post is not None and _flat(post.groups or {}):
                    groups = {k: list(v) for k, v in (post.groups or {}).items() if v}
                    item["_groups_from"] = "site"
                    item["site_posts"] = _site_post_rows(self.settings, site, post, {})
            except Exception as exc:
                self.log(f"Browser page metadata refresh warning: {type(exc).__name__}: {exc}")
        if groups:
            # A page can expose the same post with a different file.  Never overwrite the canonical row's MD5 while
            # merely refreshing source-scoped tags.
            _merge_metadata(self.settings, canonical_path, item, groups, method="browser_page_seen", md5="")
            if item.get("site_posts"):
                self._persist_site_posts(canonical_path, item)
            self._set(task_id, status="done", message="теги обновлены", result_path=canonical_path)
        else:
            self._set(task_id, status="done", message="на странице нет новых тегов", result_path=canonical_path)

    def _persist_site_posts(self, canonical_path: str | Path, item: dict[str, Any]) -> int:
        """What the site said of the post (uploader, parent) kept with the card: what сборки are made of."""
        rows = [r for r in item.get("site_posts") or [] if isinstance(r, dict)]
        image_id = self._image_id_for_path(canonical_path) if rows else 0
        if not image_id:
            return 0
        from core.database.connection import db
        from core.site_posts import record
        with db(self.settings, write=True) as con:
            return record(con, image_id, rows)

    def _finish_alias_conflict(
        self,
        task_id: str,
        *,
        local_canonical_path: str,
        alias_canonical_path: str,
        staging_path: str | Path = "",
        parser_result: str = "",
    ) -> None:
        local_path = str(local_canonical_path or "")
        alias_path = str(alias_canonical_path or "")
        self.log(
            "Browser import site-MD5 alias conflict: "
            f"local_canonical={local_path or 'missing'} "
            f"alias_canonical={alias_path or 'missing'}; "
            "metadata was not merged; inspect Duplicates"
        )
        _cleanup_staging(
            self.settings,
            staging_path,
            local_path,
            operation="browser_import.alias_conflict_cleanup",
        )
        fields: dict[str, Any] = {
            "status": "done",
            "message": "конфликт site MD5; проверьте страницу «Дубликаты»",
            "result_path": local_path or alias_path,
            "alias_conflict": True,
            "alias_canonical_path": alias_path,
            "local_canonical_path": local_path,
        }
        if parser_result:
            fields["parser_result"] = parser_result
        self._set(task_id, **fields)

    def _bind_site_md5(
        self,
        task_id: str,
        canonical_path: str,
        site_md5: str,
        *,
        site: str,
        source_url: str,
    ) -> dict[str, Any]:
        if not site_md5:
            return {"ok": True, "reason": "not_required", "canonical_path": canonical_path}
        binding = bind_site_md5_alias(
            self.settings,
            canonical_path,
            site_md5,
            site=site,
            source_url=source_url,
        )
        reason = str(binding.get("reason") or "")
        if reason == "stale_binding_reclaimed":
            stale_path = str(binding.get("stale_canonical_path") or "")
            self.log(
                "Browser import site-MD5 stale binding reclaimed: "
                f"old={stale_path or 'missing'} new={canonical_path}"
            )
            self._set(
                task_id,
                alias_reclaimed=True,
                stale_alias_canonical_path=stale_path,
                alias_canonical_path=str(binding.get("canonical_path") or canonical_path),
            )
        elif not binding.get("ok"):
            self.log(
                "Browser import site-MD5 binding failed: "
                f"reason={reason or 'unknown'} "
                f"canonical={binding.get('canonical_path') or canonical_path}"
            )
            self._set(task_id, alias_binding_error=reason or "unknown")
        return binding

    def _image_id_for_path(self, path: str | Path) -> int:
        try:
            from core.database.connection import db
            with db(self.settings, readonly=True) as con:
                row = con.execute("SELECT id FROM images WHERE deleted=0 AND path=? ORDER BY id DESC LIMIT 1", (str(Path(path)),)).fetchone()
                return int(row[0]) if row else 0
        except Exception:
            return 0

    def _persist_enrichment(self, canonical_path: str | Path, item: dict[str, Any]) -> dict[str, Any]:
        image_id = self._image_id_for_path(canonical_path)
        if not image_id:
            return {"image_id": 0}
        from core.database.connection import db
        from core.typed_sources import add_typed_source, classify_source_role
        from core.image_notes import upsert_note
        with db(self.settings, write=True) as con:
            post_url = str(item.get("post_url") or "")
            file_url = str(item.get("file_url") or ((item.get("media_urls") or [""])[0]) or "")
            original = str(item.get("original_source") or "")
            if post_url:
                role, preferred = classify_source_role(post_url, post_url=post_url, file_url=file_url, site=str(item.get("site") or ""))
                if role:
                    add_typed_source(con, image_id=image_id, url=post_url, role=role, preferred=preferred and not bool(original), provenance="browser_companion")
            if file_url:
                add_typed_source(con, image_id=image_id, url=file_url, role="download_source", provenance="browser_companion")
            if original:
                role, _preferred = classify_source_role(original, post_url=post_url, file_url=file_url, site=str(item.get("site") or ""))
                if role == "mirror_post" and original != post_url:
                    role = "original_source"
                add_typed_source(con, image_id=image_id, url=original, role=role or "original_source", preferred=True, provenance="browser_companion")
            rows = [r for r in item.get("site_posts") or [] if isinstance(r, dict)]
            if rows:                                 # (v5.44.10) uploader and parent: the card's place in сборки
                from core.site_posts import record as record_site_posts
                record_site_posts(con, image_id, rows)
            author_id = str(item.get("author_id") or "")
            if author_id and str(item.get("site") or "") == "pixiv.net":
                add_typed_source(con, image_id=image_id, url=f"https://www.pixiv.net/users/{author_id}", role="author_profile", provenance="pixiv")
            width = height = 1
            row = con.execute("SELECT width,height FROM images WHERE id=?", (image_id,)).fetchone()
            if row:
                width = max(1, int(row["width"] or 1)); height = max(1, int(row["height"] or 1))
            for idx, note in enumerate(item.get("notes") or []):
                if not isinstance(note, dict):
                    continue
                upsert_note(
                    con, image_id=image_id, source_site=str(item.get("site") or ""), source_post_id=str(item.get("post_id") or ""),
                    external_note_id=str(note.get("id") or f"browser-{idx}"), x=float(note.get("x") or 0), y=float(note.get("y") or 0),
                    width=float(note.get("width") or note.get("w") or 0), height=float(note.get("height") or note.get("h") or 0),
                    image_width=width, image_height=height, body=str(note.get("body") or note.get("text") or ""),
                    language=str(note.get("language") or ""), active=bool(note.get("active", True)),
                )
        asset = item.get("_ugoira_asset") if isinstance(item.get("_ugoira_asset"), dict) else {}
        if asset:
            try:
                from core.pixiv_ugoira import persist_ugoira_asset
                asset = dict(asset)
                asset["converted_path"] = str(canonical_path)
                persist_ugoira_asset(self.settings, image_id=image_id, asset=asset)
            except Exception as exc:
                self.log(f"Pixiv ugoira asset persistence warning: {type(exc).__name__}: {exc}")
        family_result = {"family": False}
        if bool(self.settings.get("automatic_family_detection_enabled", True)):
            try:
                from core.family_workflows import register_family_for_path
                family_result = register_family_for_path(self.settings, canonical_path=canonical_path, item=item, completion_lane=getattr(self, "family_lane", None))
            except Exception as exc:
                self.log(f"Family registration warning: {type(exc).__name__}: {exc}")
        return {"image_id": image_id, **family_result}

    def _should_probe_upgrade(self, canonical: str, *, canonical_alias: str, site_md5: str, item: dict[str, Any]) -> tuple[bool, int, str]:
        if not bool(self.settings.get("automatic_quality_upgrade_enabled", True)):
            return False, 0, "disabled"
        image_id = self._image_id_for_path(canonical)
        if not image_id:
            return False, 0, "missing_image"
        family = item.get("family") if isinstance(item.get("family"), dict) else {}
        strong = bool(canonical_alias and site_md5) or bool(str(family.get("type") or family.get("kind") or "").startswith("pixiv") and family.get("external_id"))
        if not strong:
            return False, image_id, "weak_identity"
        # Known remote dimensions are a cheap positive signal.  With a site-MD5
        # alias the stored bytes may be a transformed preview, so absence of
        # dimensions still permits one staged verification; compare_quality is
        # conservative and rejects same/worse candidates.
        rw = int(item.get("width") or 0); rh = int(item.get("height") or 0)
        if rw > 0 and rh > 0:
            try:
                from core.database.connection import db
                with db(self.settings, readonly=True) as con:
                    row = con.execute("SELECT width,height FROM images WHERE id=?", (image_id,)).fetchone()
                if row and rw <= int(row["width"] or 0) and rh <= int(row["height"] or 0):
                    return False, image_id, "remote_not_larger"
            except Exception:
                pass
        basis = "site_md5_alias" if canonical_alias and site_md5 else "pixiv_work_page"
        return True, image_id, basis

    def _process_full(self, task_id: str, item: dict[str, Any]) -> None:
        from core.tagger import Tagger, groups_to_tags, merge_tag_groups
        from core.downloader_utils import download_post_file, fetch_posts_for_query, _file_url_variants, _post_id, _post_md5

        tagger = Tagger(dict(self.settings), lambda m: self.log(f"[Browser import] {m}"))
        post_url = str(item.get("post_url") or "")
        site = str(item.get("site") or "")

        # Resolve the site's own post JSON first.  Context-clicks on a search
        # thumbnail often expose only a sample URL; the API post gives the true
        # original and download_post_file already orders original > sample > preview.
        remote_post: dict[str, Any] = {}
        post_id = str(item.get("post_id") or "").strip()
        if site and post_id:
            try:
                posts, _highest = fetch_posts_for_query(
                    site, f"id:{post_id}", self.settings, max_pages=1, run_mode="all",
                    log=lambda m: self.log(f"[Browser import metadata] {m}"),
                )
                remote_post = next((p for p in posts if str(_post_id(p) or "") == post_id), posts[0] if posts else {})
                remote_md5 = _md5(_post_md5(remote_post)) if remote_post else ""
                if remote_md5:
                    observed_md5 = _md5(item.get("md5"))
                    if observed_md5 and observed_md5 != remote_md5:
                        self.log(
                            "Browser import site MD5 corrected by post API: "
                            f"page={observed_md5} api={remote_md5}"
                        )
                    item["md5"] = remote_md5
                resolved_urls = list(_file_url_variants(remote_post, site) or []) if remote_post else []
                for url in reversed(list(item.get("media_urls") or [])):
                    if url and url not in resolved_urls:
                        resolved_urls.append(url)
                if resolved_urls:
                    item["media_urls"] = resolved_urls
                    item["file_url"] = resolved_urls[0]
            except Exception as exc:
                self.log(f"Browser import original resolver warning: {type(exc).__name__}: {exc}")

        # A concrete post page often contains a direct original link even when
        # its public JSON endpoint is blocked.  Resolve it through the same
        # timeout/session wrapper as the parser and place it ahead of thumbnails.
        if post_url:
            try:
                from core.net import http_request
                session = tagger.session_for_host(site or _host(post_url))
                response = http_request(
                    session, "GET", post_url, timeout=tagger.timeout,
                    headers={"Accept": "text/html,application/xhtml+xml,*/*"},
                )
                raw_original = str(tagger._html_original_media_url(response.text or "", post_url) or "")
                if raw_original and is_permanently_disabled_url(raw_original):
                    self.log(
                        "SKIP DISABLED HOST [browser-import resolver]: "
                        "page exposed a forbidden Yandex URL instead of original media"
                    )
                elif raw_original and not _looks_like_direct_media_url(raw_original):
                    self.log(
                        "Browser import HTML original resolver ignored non-media URL: "
                        f"{_host(raw_original) or 'unknown-host'}"
                    )
                elif raw_original:
                    original = _url(raw_original)
                    current_urls = list(item.get("media_urls") or [])
                    item["media_urls"] = [original] + [u for u in current_urls if u != original]
                    item["file_url"] = original
            except Exception as exc:
                self.log(f"Browser import HTML original resolver warning: {type(exc).__name__}: {exc}")

        # The site's own post is the tags of that site (by category), its MD5, its uploader and parent (v5.44.10:
        # before, the page's tags went first — all of them general, a grid's card only its grid's search — and, merged
        # with the site's, a tag the site called an artist stayed «general»).  What the page shows fills in only when
        # the site gave nothing.
        site_post = None
        if post_url:
            try:
                site_post = tagger.site_post_from_url(post_url)
            except Exception as exc:
                self.log(f"Browser import current-site metadata warning: {type(exc).__name__}: {exc}")
        site_groups = {k: list(v) for k, v in ((site_post.groups or {}) if site_post is not None else {}).items() if v}
        if _flat(site_groups):
            groups = site_groups
            item["_groups_from"] = "site"
        else:
            groups = _page_groups(self.settings, item)
            item["_groups_from"] = "page" if groups else ""
        item["groups"] = groups
        item["tags"] = _flat(groups)
        item["site_posts"] = _site_post_rows(self.settings, site, site_post, remote_post)

        site_md5 = str(item.get("md5") or "")
        if not site_md5 and site_post is not None and _md5(getattr(site_post, "md5", "")):
            site_md5 = _md5(site_post.md5)
        item["md5"] = site_md5

        # Preserve the page metadata for parser reuse before any download starts.
        cache_item = {
            "md5": site_md5,
            "post_urls": [post_url] if post_url else [],
            "file_urls": list(item.get("media_urls") or []),
            "download_url": item.get("file_url") or "",
            "groups": groups,
            "tags": _flat(groups),
            "source_tag_groups": _source_bundle(item, groups, "browser_manual_import"),
            "sites": [site] if site else [],
            "post": {"id": item.get("post_id") or ""},
        }
        upsert_item(self.settings, cache_item, method="browser_manual_import")

        # The page/site MD5 is authoritative for remote exact lookup, but it is
        # not automatically the hash of the bytes Local Booru will store.  Keep
        # direct-local and alias resolution separate so an inconsistent state
        # cannot silently smear metadata over two canonical cards.
        canonical_direct = _existing_path(self.settings, site_md5)
        canonical_alias = media_path_by_site_md5(self.settings, site_md5)
        if canonical_direct and canonical_alias and not _same_path(canonical_direct, canonical_alias):
            self._finish_alias_conflict(
                task_id,
                local_canonical_path=canonical_direct,
                alias_canonical_path=canonical_alias,
            )
            return
        canonical = canonical_direct or canonical_alias
        upgrade_target = ""
        upgrade_image_id = 0
        upgrade_identity_basis = ""
        if canonical:
            probe, upgrade_image_id, upgrade_identity_basis = self._should_probe_upgrade(
                canonical, canonical_alias=canonical_alias, site_md5=site_md5, item=item
            )
            if probe:
                upgrade_target = canonical
                self._set(task_id, message="найдена возможная более качественная ревизия; проверка оригинала")
            else:
                binding = self._bind_site_md5(
                    task_id,
                    canonical,
                    site_md5,
                    site=site,
                    source_url=post_url or str(item.get("file_url") or ""),
                )
                if not binding.get("ok") and binding.get("canonical_path") and not _same_path(
                    canonical, str(binding.get("canonical_path") or "")
                ):
                    self._finish_alias_conflict(
                        task_id,
                        local_canonical_path=canonical,
                        alias_canonical_path=str(binding.get("canonical_path") or ""),
                    )
                    return

                self._set(task_id, message="файл уже есть; обновление источников")
                if groups:
                    # Never overwrite images.hash_md5 with a remote/site hash.  The
                    # canonical row keeps the MD5 of its real local bytes.
                    _merge_metadata(self.settings, canonical, item, groups, method="browser_manual_import", md5="")
                try:
                    tags2, srcs2, groups2 = tagger.md5_lookup_all(site_md5)
                    merged2 = merge_tag_groups(list(groups2 or []))
                    if tags2 and not groups_to_tags(merged2 or {}):
                        merged2 = {"general": list(tags2)}
                    if tags2:
                        upsert_media_metadata(
                            self.settings, canonical, tags=tags2, groups=merged2,
                            source_text="\n".join(str(x) for x in srcs2 or []), status="tagged",
                            hash_md5=None, merge_existing=True, import_origin="browser_extension",
                            source_tag_groups=list(getattr(tagger, "_last_md5_source_tag_groups", []) or []),
                        )
                except Exception as exc:
                    self.log(f"Browser import MD5 fanout warning: {type(exc).__name__}: {exc}")
                message = "уже был в LB; теги и источники обновлены"
                if site_md5 and not binding.get("ok"):
                    message = "уже был в LB; теги обновлены, привязка site MD5 не подтверждена"
                enrichment = self._persist_enrichment(canonical, item)
                self._set(task_id, status="done", message=message, result_path=canonical, enrichment=enrichment)
                return
        urls = list(item.get("media_urls") or [])
        if not urls:
            raise RuntimeError("не найден URL оригинала")
        self._set(task_id, message="скачивание оригинала")
        post = dict(remote_post or {})
        post.setdefault("id", item.get("post_id") or "")
        if site_md5:
            post.setdefault("md5", site_md5)
        # API-derived originals remain authoritative.  Browser/page candidates
        # are fallback-only; never write one into top-level file_url ahead of an
        # official nested original such as e621 file.url.
        post["browser_media_urls"] = list(urls)
        try:
            if isinstance(item.get("ugoira"), dict) and item.get("ugoira", {}).get("zip_url"):
                from core.pixiv_ugoira import process_ugoira
                self._set(task_id, message="скачивание и сборка Pixiv ugoira")
                asset = process_ugoira(
                    self.settings, item=item, stop_check=self._stop.is_set,
                    log=lambda m: self.log(f"[Pixiv ugoira] {m}"),
                )
                ok, downloaded = True, asset.get("converted_path")
            else:
                ok, downloaded = download_post_file(
                    site, post, self.settings, query="browser_extension",
                    operation="browser_import", log=lambda m: self.log(f"[Browser import] {m}"),
                    allow_reduced=True,
                )
        except Exception as exc:
            from core.download_policy import DownloadBlockedByPolicy
            if isinstance(exc, DownloadBlockedByPolicy):
                self._set(
                    task_id, status="deferred",
                    message=f"скачивание отключено для сессии; intent={exc.decision.intent_id or 0}",
                )
                self.log(f"[Browser import] DOWNLOAD BLOCKED BY SESSION POLICY: site={site} intent={exc.decision.intent_id or 0}")
                return
            raise
        downloaded_path = Path(downloaded) if downloaded else None
        if not downloaded_path or not downloaded_path.exists():
            canonical_direct = _existing_path(self.settings, site_md5)
            canonical_alias = media_path_by_site_md5(self.settings, site_md5)
            if canonical_direct and canonical_alias and not _same_path(canonical_direct, canonical_alias):
                self._finish_alias_conflict(
                    task_id,
                    local_canonical_path=canonical_direct,
                    alias_canonical_path=canonical_alias,
                )
                return
            canonical = canonical_direct or canonical_alias
            if canonical:
                binding = self._bind_site_md5(
                    task_id,
                    canonical,
                    site_md5,
                    site=site,
                    source_url=post_url or str(item.get("file_url") or ""),
                )
                if not binding.get("ok") and binding.get("canonical_path") and not _same_path(
                    canonical, str(binding.get("canonical_path") or "")
                ):
                    self._finish_alias_conflict(
                        task_id,
                        local_canonical_path=canonical,
                        alias_canonical_path=str(binding.get("canonical_path") or ""),
                    )
                    return
                if groups:
                    _merge_metadata(self.settings, canonical, item, groups, method="browser_manual_import", md5="")
                message = "дубль объединён с существующим файлом"
                if site_md5 and not binding.get("ok"):
                    message = "дубль найден; привязка site MD5 не подтверждена"
                enrichment = self._persist_enrichment(canonical, item)
                self._set(task_id, status="done", message=message, result_path=canonical, enrichment=enrichment)
                return
            raise RuntimeError("оригинал не скачан")

        actual_md5 = _file_md5(downloaded_path)
        self.log(
            "Browser import MD5 pair: "
            f"site={site_md5 or 'missing'} local={actual_md5}"
            + (" DIFFERENT" if site_md5 and site_md5 != actual_md5 else "")
        )

        if upgrade_target and upgrade_image_id:
            try:
                from core.quality_upgrade_service import execute_quality_upgrade, QualityUpgradeRejected
                result = execute_quality_upgrade(
                    self.settings, image_id=upgrade_image_id, staged_path=downloaded_path,
                    identity_basis=upgrade_identity_basis, identity_verified=True,
                    source_site=site, source_post_id=str(item.get("post_id") or ""),
                    stop_check=self._stop.is_set,
                    trace=lambda m: self.log(f"[Quality upgrade] {m}"),
                )
                _cleanup_staging(self.settings, downloaded_path, upgrade_target, operation="browser_import.quality_upgrade_candidate_cleanup")
                enrichment = self._persist_enrichment(upgrade_target, item)
                if groups:
                    _merge_metadata(self.settings, upgrade_target, item, groups, method="browser_quality_upgrade", md5="")
                self._set(task_id, status="done", message="автоматический quality upgrade выполнен; старая ревизия отправлена на нары", result_path=upgrade_target, quality_upgrade=result, enrichment=enrichment)
                return
            except QualityUpgradeRejected as exc:
                self.log(f"[Quality upgrade] rejected: {exc}")
                # Strong identity is known, but candidate is not objectively
                # better.  Keep the canonical bytes and discard the candidate.
                _cleanup_staging(self.settings, downloaded_path, upgrade_target, operation="browser_import.rejected_upgrade_cleanup")
                binding = self._bind_site_md5(task_id, upgrade_target, site_md5, site=site, source_url=post_url or str(item.get("file_url") or ""))
                if groups:
                    _merge_metadata(self.settings, upgrade_target, item, groups, method="browser_manual_import", md5="")
                enrichment = self._persist_enrichment(upgrade_target, item)
                self._set(task_id, status="done", message=f"существующая ревизия оставлена: {exc}", result_path=upgrade_target, enrichment=enrichment, alias_binding=binding)
                return

        # Cache both identities.  Site MD5 is used for remote exact lookup;
        # local MD5 is used for byte-level dedupe and canonical persistence.
        if site_md5:
            cache_item["md5"] = site_md5
            upsert_item(self.settings, cache_item, method="browser_manual_import_site_md5")
        local_cache_item = dict(cache_item)
        local_cache_item["md5"] = actual_md5
        upsert_item(self.settings, local_cache_item, method="browser_manual_import_local_md5")

        # Mandatory pre-parser dedupe.  Resolve the byte-identical local
        # canonical and both forms of the authoritative site identity
        # separately.  If they point to different live cards, do not choose one
        # silently: expose the conflict and leave reconciliation to Duplicates.
        canonical_actual = _existing_path(self.settings, actual_md5)
        canonical_site_direct = _existing_path(self.settings, site_md5)
        canonical_site_alias = media_path_by_site_md5(self.settings, site_md5)
        canonical_site = canonical_site_direct or canonical_site_alias

        conflict_local = ""
        conflict_alias = ""
        if canonical_actual and canonical_site and not _same_path(canonical_actual, canonical_site):
            conflict_local = canonical_actual
            conflict_alias = canonical_site
        elif canonical_site_direct and canonical_site_alias and not _same_path(
            canonical_site_direct, canonical_site_alias
        ):
            conflict_local = canonical_site_direct
            conflict_alias = canonical_site_alias
        if conflict_local and conflict_alias:
            self._finish_alias_conflict(
                task_id,
                local_canonical_path=conflict_local,
                alias_canonical_path=conflict_alias,
                staging_path=downloaded_path,
            )
            return

        canonical_pre = canonical_actual or canonical_site
        if canonical_pre:
            binding = self._bind_site_md5(
                task_id,
                canonical_pre,
                site_md5,
                site=site,
                source_url=post_url or str(item.get("file_url") or ""),
            )
            if not binding.get("ok") and binding.get("canonical_path") and not _same_path(
                canonical_pre, str(binding.get("canonical_path") or "")
            ):
                self._finish_alias_conflict(
                    task_id,
                    local_canonical_path=canonical_pre,
                    alias_canonical_path=str(binding.get("canonical_path") or ""),
                    staging_path=downloaded_path,
                )
                return

            self._set(task_id, message="дубль найден до парсера; обновление источников")
            if groups:
                _merge_metadata(self.settings, canonical_pre, item, groups, method="browser_manual_import", md5="")
            if site_md5:
                try:
                    tags2, srcs2, groups2 = tagger.md5_lookup_all(site_md5)
                    merged2 = merge_tag_groups(list(groups2 or []))
                    if tags2 and not groups_to_tags(merged2 or {}):
                        merged2 = {"general": list(tags2)}
                    if tags2:
                        upsert_media_metadata(
                            self.settings, canonical_pre, tags=tags2, groups=merged2,
                            source_text="\n".join(str(x) for x in srcs2 or []), status="tagged",
                            hash_md5=None, merge_existing=True, import_origin="browser_extension",
                            source_tag_groups=list(getattr(tagger, "_last_md5_source_tag_groups", []) or []),
                        )
                except Exception as exc:
                    self.log(f"Browser import site-MD5 fanout warning: {type(exc).__name__}: {exc}")
            _cleanup_staging(
                self.settings,
                downloaded_path,
                canonical_pre,
                operation="browser_import.preparser_dedupe_cleanup",
            )
            message = "дубль объединён до парсера"
            if site_md5 and binding.get("ok"):
                message += "; site MD5 проверен"
            elif site_md5:
                message += "; привязка site MD5 не подтверждена"
            enrichment = self._persist_enrichment(canonical_pre, item)
            self._set(
                task_id,
                status="done",
                message=message,
                result_path=canonical_pre,
                enrichment=enrichment,
            )
            return

        self._set(task_id, message="полный парсер Local Booru")
        run_settings = dict(self.settings)
        run_settings.update({
            "skip_existing": False,
            "retry_nomatch": True,
            "enable_md5_lookup": True,
            "session_folder": time.strftime("browser_%Y-%m-%d_%H-%M-%S"),
        })
        if site_md5:
            # External exact searches must use the authoritative post MD5.  The
            # parser still computes and persists actual_md5 for local dedupe.
            run_settings["_authoritative_site_md5s"] = [site_md5]
        result = Tagger(run_settings, lambda m: self.log(f"[Browser parser] {m}")).process_image(downloaded_path)
        canonical = _existing_path(self.settings, actual_md5)
        binding: dict[str, Any] = {"ok": True, "reason": "not_required", "canonical_path": canonical}
        if canonical and site_md5:
            binding = self._bind_site_md5(
                task_id,
                canonical,
                site_md5,
                site=site,
                source_url=post_url or str(item.get("file_url") or ""),
            )
            if not binding.get("ok") and binding.get("canonical_path") and not _same_path(
                canonical, str(binding.get("canonical_path") or "")
            ):
                self._finish_alias_conflict(
                    task_id,
                    local_canonical_path=canonical,
                    alias_canonical_path=str(binding.get("canonical_path") or ""),
                    staging_path=downloaded_path,
                    parser_result=str(result or ""),
                )
                return
        if canonical and groups:
            _merge_metadata(self.settings, canonical, item, groups, method="browser_manual_import", md5=actual_md5)

        enrichment = {}
        if canonical:
            _cleanup_staging(
                self.settings,
                downloaded_path,
                canonical,
                operation="browser_import.cleanup_staging",
            )
            enrichment = self._persist_enrichment(canonical, item)

        if result == "retry_network":
            self._set(task_id, status="waiting", message="сеть / повтор позже", result_path=canonical or str(downloaded_path))
        elif result in {"tagged", "nomatch", "skip"}:
            self._set(task_id, status="done", message="сохранено и обработано", result_path=canonical or str(downloaded_path), parser_result=result, enrichment=enrichment)
        else:
            self._set(task_id, status="done", message=f"обработка завершена: {result}", result_path=canonical or str(downloaded_path), enrichment=enrichment)
