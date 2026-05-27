"""Per-author subscription system for Local Booru.

Subscriptions = автоматическое скачивание по автору/тегу.
Разница от граббера: подписки помнят что уже скачали (по post ID или дате)
и запускаются по расписанию или вручную.

Хранение: data/settings/subscriptions.json
Каждая подписка:
  {
    "id": "sub_001",
    "name": "seraziel",           # отображаемое имя
    "site": "gelbooru.com",        # сайт
    "query": "artist:seraziel",    # поисковый запрос / тег
    "enabled": true,
    "last_post_id": 12345678,      # последний скачанный ID
    "last_check": 1716800000,      # unix timestamp
    "check_interval_hours": 24,    # как часто проверять
    "max_pages": 3,                # страниц за раз
    "downloaded_count": 0,
    "created_at": 1716800000
  }
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Iterator

from core.paths import SETTINGS_DIR

SUBS_FILE = SETTINGS_DIR / "subscriptions.json"


# ── Storage ───────────────────────────────────────────────────────────────────

def load_subscriptions() -> list[dict]:
    try:
        return json.loads(SUBS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_subscriptions(subs: list[dict]) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SUBS_FILE.write_text(
        json.dumps(subs, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_subscription(sub_id: str) -> dict | None:
    for s in load_subscriptions():
        if s.get("id") == sub_id:
            return s
    return None


def add_subscription(name: str, site: str, query: str, **kwargs) -> dict:
    sub = {
        "id": f"sub_{uuid.uuid4().hex[:8]}",
        "name": name.strip(),
        "site": site.strip(),
        "query": query.strip(),
        "enabled": True,
        "last_post_id": 0,
        "last_check": 0,
        "check_interval_hours": kwargs.get("check_interval_hours", 24),
        "max_pages": kwargs.get("max_pages", 3),
        "downloaded_count": 0,
        "created_at": int(time.time()),
    }
    subs = load_subscriptions()
    subs.append(sub)
    save_subscriptions(subs)
    return sub


def update_subscription(sub_id: str, **fields) -> bool:
    subs = load_subscriptions()
    for i, s in enumerate(subs):
        if s.get("id") == sub_id:
            subs[i].update(fields)
            save_subscriptions(subs)
            return True
    return False


def delete_subscription(sub_id: str) -> bool:
    subs = load_subscriptions()
    new = [s for s in subs if s.get("id") != sub_id]
    if len(new) < len(subs):
        save_subscriptions(new)
        return True
    return False


def due_subscriptions() -> list[dict]:
    """Return subscriptions that are due for a check."""
    now = int(time.time())
    result = []
    for s in load_subscriptions():
        if not s.get("enabled", True):
            continue
        interval_sec = int(s.get("check_interval_hours", 24)) * 3600
        last = int(s.get("last_check", 0))
        if now - last >= interval_sec:
            result.append(s)
    return result


# ── Runner ────────────────────────────────────────────────────────────────────

def run_subscription(sub: dict, settings: dict,
                     log=None, progress=None) -> int:
    """Check one subscription, download new posts. Returns count of new files."""
    from core.downloader_utils import download_posts_for_query
    log = log or (lambda m: None)

    site = sub.get("site", "")
    query = sub.get("query", "")
    last_id = int(sub.get("last_post_id", 0))
    max_pages = int(sub.get("max_pages", 3))

    log(f"SUB [{sub['name']}]: checking {site} for '{query}' (last_id={last_id})")

    try:
        new_count, new_last_id = download_posts_for_query(
            site=site,
            query=query,
            settings=settings,
            since_post_id=last_id,
            max_pages=max_pages,
            log=log,
            progress=progress,
        )
    except Exception as e:
        log(f"SUB [{sub['name']}] ERROR: {e}")
        update_subscription(sub["id"], last_check=int(time.time()))
        return 0

    update_subscription(
        sub["id"],
        last_post_id=new_last_id or last_id,
        last_check=int(time.time()),
        downloaded_count=sub.get("downloaded_count", 0) + new_count,
    )
    log(f"SUB [{sub['name']}]: done, {new_count} new files")
    return new_count
