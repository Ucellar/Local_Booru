"""Automatic media-family registration and durable missing-page completion."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from core.background_lane import BackgroundLane, EligibilityResult, LaneJob
from core.database.connection import db
from core.download_policy import session_status
from core.media_families import ensure_family, set_family_item


def normalize_family(item: dict[str, Any]) -> dict[str, Any]:
    """The family the item declares (a pixiv work, a booru pool), or {}.

    Several media URLs of one item are not a family: they are the original, its sample, the
    thumbnail of the same post.  (Before 5.3.1 they became «pages» of a ``multi_attachment``
    family, and the completion lane imported samples and thumbnails as separate cards.)
    """
    raw = item.get("family") if isinstance(item.get("family"), dict) else {}
    if not raw or str(raw.get("type") or raw.get("kind") or "").strip().lower() == "multi_attachment":
        return {}
    source = str(raw.get("source") or item.get("site") or "").strip().lower()
    family_type = str(raw.get("type") or raw.get("kind") or "related_set").strip().lower()
    external_id = str(raw.get("external_id") or raw.get("id") or "").strip()
    if not (source and external_id):
        return {}
    items = []
    for index, member in enumerate(raw.get("items") or []):
        if not isinstance(member, dict):
            continue
        idx = int(member.get("index", member.get("page_index", index)) or 0)
        media_url = str(member.get("media_url") or member.get("original_url") or member.get("file_url") or "").strip()
        post_url = str(member.get("post_url") or item.get("post_url") or "").strip()
        items.append({
            "index": idx,
            "external_item_id": str(member.get("external_item_id") or member.get("post_id") or f"{external_id}:{idx}"),
            "post_id": str(member.get("post_id") or item.get("post_id") or ""),
            "post_url": post_url,
            "media_url": media_url,
            "site_md5": str(member.get("md5") or "").lower(),
            "page_label": str(member.get("page_label") or f"{idx + 1}"),
            "metadata": member.get("metadata") if isinstance(member.get("metadata"), dict) else {},
        })
    total = max(int(raw.get("total") or 0), len(items), int(raw.get("index", 0) or 0) + 1)
    return {
        "source": source, "type": family_type, "external_id": external_id,
        "title": str(raw.get("title") or item.get("page_title") or "")[:500],
        "author": str(raw.get("author") or item.get("author_name") or "")[:300],
        "index": max(0, int(raw.get("index", item.get("page_index", 0)) or 0)),
        "total": max(1, total), "items": items,
        "auto_complete": bool(raw.get("auto_complete", True)),
    }


def _image_row_by_path(con, path: str | Path):
    return con.execute("SELECT id,width,height FROM images WHERE deleted=0 AND path=? ORDER BY id DESC LIMIT 1", (str(Path(path)),)).fetchone()


def register_family_for_path(settings: dict | None, *, canonical_path: str | Path, item: dict[str, Any], completion_lane: "FamilyCompletionLane | None" = None) -> dict[str, Any]:
    family = normalize_family(item)
    if not family:
        return {"ok": True, "family": False}
    with db(settings, write=True) as con:
        image = _image_row_by_path(con, canonical_path)
        if not image:
            return {"ok": False, "error": "image_not_indexed"}
        family_id = ensure_family(
            con, family_source=family["source"], family_type=family["type"], external_family_id=family["external_id"],
            title=family["title"], author=family["author"], total_items=family["total"], order_confidence=1.0,
        )
        con.execute(
            "UPDATE media_families SET sort_anchor=CASE WHEN sort_anchor=0 THEN ? ELSE MIN(sort_anchor,?) END,auto_complete=?,completion_status=? WHERE family_id=?",
            (int(time.time()), int(time.time()), 1 if family["auto_complete"] else 0, "partial", family_id),
        )
        current_index = int(family["index"])
        set_family_item(
            con, family_id=family_id, family_index=current_index, image_id=int(image["id"]),
            external_item_id=f"{family['external_id']}:{current_index}", page_label=str(current_index + 1),
            relation_type="page", missing=False, source_url=str(item.get("post_url") or item.get("file_url") or ""),
        )
        con.execute(
            "UPDATE media_family_items SET media_url=?,site_md5=?,post_id=?,metadata_json=? WHERE family_id=? AND family_index=?",
            (str(item.get("file_url") or ((item.get("media_urls") or [""])[0]) or ""), str(item.get("md5") or ""), str(item.get("post_id") or ""), json.dumps({"site": item.get("site") or "", "post_url": item.get("post_url") or ""}, ensure_ascii=False), family_id, current_index),
        )
        seen = {current_index}
        for member in family["items"]:
            idx = int(member["index"])
            seen.add(idx)
            existing = con.execute("SELECT image_id FROM media_family_items WHERE family_id=? AND family_index=?", (family_id, idx)).fetchone()
            image_id = int(existing[0]) if existing and existing[0] else None
            missing = image_id is None and idx != current_index
            set_family_item(
                con, family_id=family_id, family_index=idx, image_id=image_id,
                external_item_id=member["external_item_id"], page_label=member["page_label"],
                relation_type="page", missing=missing, source_url=member["post_url"],
            )
            con.execute(
                "UPDATE media_family_items SET media_url=?,site_md5=?,post_id=?,metadata_json=? WHERE family_id=? AND family_index=?",
                (member["media_url"], member["site_md5"], member["post_id"], json.dumps(member["metadata"], ensure_ascii=False), family_id, idx),
            )
        for idx in range(family["total"]):
            if idx not in seen:
                set_family_item(con, family_id=family_id, family_index=idx, image_id=None, external_item_id=f"{family['external_id']}:{idx}", page_label=str(idx + 1), relation_type="page", missing=True)
        missing_count = int(con.execute("SELECT COUNT(*) FROM media_family_items WHERE family_id=? AND is_missing=1", (family_id,)).fetchone()[0])
        con.execute("UPDATE media_families SET completion_status=? WHERE family_id=?", ("complete" if missing_count == 0 else "partial", family_id))
        missing_rows = con.execute("SELECT * FROM media_family_items WHERE family_id=? AND is_missing=1 AND media_url!='' ORDER BY family_index", (family_id,)).fetchall()
    queued = 0
    max_items = max(1, int((settings or {}).get("family_completion_max_items", 100) or 100))
    completion_allowed = bool(completion_lane and family["auto_complete"] and int(family["total"]) <= max_items)
    if completion_lane and family["auto_complete"] and not completion_allowed:
        with db(settings, write=True) as con:
            con.execute(
                "UPDATE media_families SET completion_status='partial_limit' WHERE family_id=?",
                (int(family_id),),
            )
    if completion_allowed:
        for row in missing_rows:
            payload = {
                "family_id": family_id, "family_index": int(row["family_index"]),
                "item": {
                    "site": family["source"], "post_id": str(row["post_id"] or ""),
                    "post_url": str(row["source_url"] or ""), "file_url": str(row["media_url"] or ""),
                    "media_urls": [str(row["media_url"] or "")], "md5": str(row["site_md5"] or ""),
                    "page_index": int(row["family_index"]),
                    "family": {**family, "index": int(row["family_index"]), "items": family["items"]},
                },
            }
            completion_lane.enqueue(payload)
            queued += 1
    return {"ok": True, "family": True, "family_id": family_id, "missing": missing_count, "queued": queued}


class FamilyCompletionLane:
    """Durable missing-page lane; execution hands a seed to BrowserImportManager."""
    def __init__(self, settings: dict, submit: Callable[[dict[str, Any]], dict[str, Any]], *, wait: Callable[[str, float], dict[str, Any]] | None = None, trace: Callable[[str], None] | None = None):
        self.settings = settings
        self.submit = submit
        self.wait = wait
        self.stop_event = threading.Event()
        self.lane = BackgroundLane(
            settings, "family-completion", classifier=self._classify, executor=self._execute,
            retry_policy=self._retry, trace=trace, page_size=int(settings.get("background_lane_page_size", 500) or 500),
            empty_backoff_seconds=float(settings.get("family_completion_empty_backoff_seconds", 30) or 30),
        )
        self.thread = threading.Thread(target=self._run, name="family-completion-lane", daemon=True)
        self.thread.start()

    def enqueue(self, payload: dict[str, Any]) -> int:
        fid = int(payload.get("family_id") or 0); idx = int(payload.get("family_index") or 0)
        return self.lane.enqueue(f"{fid}:{idx}", payload, priority=20, dedupe_key=f"family:{fid}:{idx}")

    def _classify(self, job: LaneJob, _ctx: Any) -> EligibilityResult:
        status = session_status(self.settings)
        if not bool(status.get("enabled", False)):
            return EligibilityResult(False, "downloads_disabled")
        payload = job.payload
        with db(self.settings, readonly=True) as con:
            row = con.execute("SELECT is_missing,image_id FROM media_family_items WHERE family_id=? AND family_index=?", (int(payload.get("family_id") or 0), int(payload.get("family_index") or 0))).fetchone()
        if not row:
            return EligibilityResult(False, "family_item_missing")
        if int(row["is_missing"] or 0) == 0 or row["image_id"]:
            return EligibilityResult(False, "already_complete")
        return EligibilityResult(True)

    def _execute(self, job: LaneJob, _ctx: Any) -> None:
        result = self.submit(dict(job.payload.get("item") or {}))
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "family import rejected"))
        task_id = str(result.get("task_id") or "")
        if self.wait and task_id:
            state = self.wait(task_id, float(self.settings.get("family_completion_task_timeout_seconds", 1800) or 1800))
            if str(state.get("status") or "") != "done":
                raise RuntimeError(str(state.get("message") or state.get("status") or "family import incomplete"))
        payload = job.payload
        with db(self.settings, readonly=True) as con:
            row = con.execute(
                "SELECT is_missing,image_id FROM media_family_items WHERE family_id=? AND family_index=?",
                (int(payload.get("family_id") or 0), int(payload.get("family_index") or 0)),
            ).fetchone()
        if not row or int(row["is_missing"] or 0) != 0 or not row["image_id"]:
            raise RuntimeError("family import finished without binding the missing page")

    @staticmethod
    def _retry(job: LaneJob, exc: BaseException) -> tuple[bool, int, str]:
        return True, min(3600, 30 * (2 ** min(6, int(job.attempts or 0)))), f"{type(exc).__name__}: {exc}"

    def _run(self) -> None:
        while not self.stop_event.is_set():
            self.lane.drain_once(target=4, should_stop=self.stop_event.is_set)

    def stop(self) -> None:
        self.stop_event.set(); self.thread.join(timeout=5)
