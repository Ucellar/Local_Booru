"""SQLite Hydrus-style seed cache for subscription imports.

Stores discovered post/file seeds durably so subscriptions can resume, retry,
explain failures, and avoid re-importing the same post forever.  A tiny JSON
migration is kept for compatibility with fixes_35.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Iterable

from core.paths import SETTINGS_DIR

SEED_CACHE_DB = SETTINGS_DIR / "subscription_seed_cache.sqlite3"
OLD_JSON_FILE = SETTINGS_DIR / "subscription_seed_cache.json"

TERMINAL_STATUSES = {
    "downloaded",
    "skipped_duplicate",
    "skipped_blacklist",
    "skipped_no_file",
    "failed_perm",
    "auth_required",
}
RETRYABLE_STATUSES = {"pending", "downloading", "failed_temp", "failed"}


def _now() -> int:
    return int(time.time())


def _connect() -> sqlite3.Connection:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(SEED_CACHE_DB))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    _init_db(con)
    _migrate_json_once(con)
    return con


def _init_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS subscription_seeds (
            key TEXT PRIMARY KEY,
            subscription_id TEXT NOT NULL,
            subscription_name TEXT DEFAULT '',
            site TEXT NOT NULL,
            query TEXT DEFAULT '',
            priority INTEGER DEFAULT 1,
            post_id INTEGER DEFAULT 0,
            md5 TEXT DEFAULT '',
            file_url TEXT DEFAULT '',
            post_url TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            post_json TEXT DEFAULT '{}',
            found_at INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0,
            retry_count INTEGER DEFAULT 0,
            retry_after INTEGER DEFAULT 0,
            last_error TEXT DEFAULT '',
            path TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_sub_seed_sub_status ON subscription_seeds(subscription_id, status);
        CREATE INDEX IF NOT EXISTS idx_sub_seed_md5 ON subscription_seeds(md5);
        CREATE INDEX IF NOT EXISTS idx_sub_seed_post ON subscription_seeds(site, post_id);

        CREATE TABLE IF NOT EXISTS subscription_runs (
            run_id TEXT PRIMARY KEY,
            subscription_id TEXT NOT NULL,
            subscription_name TEXT DEFAULT '',
            started_at INTEGER NOT NULL,
            finished_at INTEGER DEFAULT 0,
            mode TEXT DEFAULT '',
            direction TEXT DEFAULT '',
            status TEXT DEFAULT 'running',
            found INTEGER DEFAULT 0,
            queued INTEGER DEFAULT 0,
            downloaded INTEGER DEFAULT 0,
            skipped INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            note TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_sub_runs_sub ON subscription_runs(subscription_id, started_at);
        """
    )
    con.commit()


def _migrate_json_once(con: sqlite3.Connection) -> None:
    if not OLD_JSON_FILE.exists():
        return
    marker = SETTINGS_DIR / ".subscription_seed_json_migrated"
    if marker.exists():
        return
    try:
        data = json.loads(OLD_JSON_FILE.read_text(encoding="utf-8"))
        seeds = data.get("seeds", {}) if isinstance(data, dict) else {}
        for seed in seeds.values():
            if not isinstance(seed, dict):
                continue
            con.execute(
                """
                INSERT OR IGNORE INTO subscription_seeds
                (key, subscription_id, subscription_name, site, query, priority, post_id, md5,
                 file_url, post_url, status, post_json, found_at, updated_at, retry_count,
                 retry_after, last_error, path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    seed.get("key") or seed_key(seed.get("subscription_id", ""), seed.get("site", ""), seed.get("post_id", 0), seed.get("md5", "")),
                    seed.get("subscription_id", ""), seed.get("subscription_name", ""), seed.get("site", ""), seed.get("query", ""),
                    int(seed.get("priority") or 1), int(seed.get("post_id") or 0), str(seed.get("md5") or "").lower(),
                    seed.get("file_url", ""), seed.get("post_url", ""), seed.get("status", "pending"),
                    json.dumps(seed.get("post") or {}, ensure_ascii=False), int(seed.get("found_at") or _now()), int(seed.get("updated_at") or _now()),
                    int(seed.get("retry_count") or 0), int(seed.get("retry_after") or 0), seed.get("last_error", ""), seed.get("path", ""),
                ),
            )
        con.commit()
        marker.write_text(str(_now()), encoding="utf-8")
    except Exception:
        pass


def _row_to_seed(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["post"] = json.loads(d.pop("post_json") or "{}")
    except Exception:
        d["post"] = {}
    return d


def seed_key(sub_id: str, site: str, post_id: int | str, md5: str = "") -> str:
    post_id_s = str(post_id or "").strip()
    md5_s = str(md5 or "").strip().lower()
    if post_id_s and post_id_s != "0":
        return f"{sub_id}|{site}|id:{post_id_s}"
    if md5_s:
        return f"{sub_id}|{site}|md5:{md5_s}"
    return f"{sub_id}|{site}|unknown:{_now()}"


def load_seeds(sub_id: str | None = None, limit: int | None = None) -> list[dict]:
    with _connect() as con:
        sql = "SELECT * FROM subscription_seeds"
        args: list = []
        if sub_id:
            sql += " WHERE subscription_id=?"
            args.append(sub_id)
        sql += " ORDER BY post_id DESC, found_at DESC"
        if limit:
            sql += " LIMIT ?"
            args.append(int(limit))
        return [_row_to_seed(r) for r in con.execute(sql, args).fetchall()]


def get_seed(sub_id: str, site: str, post_id: int | str, md5: str = "") -> dict | None:
    with _connect() as con:
        row = con.execute("SELECT * FROM subscription_seeds WHERE key=?", (seed_key(sub_id, site, post_id, md5),)).fetchone()
        return _row_to_seed(row) if row else None


def clear_seeds_for_subscription(subscription_id: str, settings: dict | None = None) -> int:
    """Delete all seeds for a subscription. Uses the same seed_cache DB as all other functions."""
    try:
        con = _connect()  # same DB as upsert_seed etc.
        cur = con.execute(
            "DELETE FROM subscription_seeds WHERE subscription_id=?",
            (subscription_id,)
        )
        con.commit()
        deleted = cur.rowcount
        con.close()
        return deleted
    except Exception as e:
        return 0


def has_seen_post(subscription_id: str, site: str, post_id: int | str, md5: str = "") -> bool:
    with _connect() as con:
        if post_id:
            row = con.execute(
                "SELECT 1 FROM subscription_seeds WHERE subscription_id=? AND site=? AND post_id=? LIMIT 1",
                (subscription_id, site, int(post_id or 0)),
            ).fetchone()
            if row:
                return True
        if md5:
            row = con.execute(
                "SELECT 1 FROM subscription_seeds WHERE subscription_id=? AND md5=? LIMIT 1",
                (subscription_id, str(md5).lower()),
            ).fetchone()
            return bool(row)
    return False


def upsert_seed(
    *, subscription_id: str, subscription_name: str, site: str, query: str,
    post: dict, priority: int = 1, file_url: str = "", post_url: str = "",
    md5: str = "", post_id: int = 0, status: str = "pending",
) -> dict:
    key = seed_key(subscription_id, site, post_id, md5)
    now = _now()
    with _connect() as con:
        old = con.execute("SELECT * FROM subscription_seeds WHERE key=?", (key,)).fetchone()
        old_status = old["status"] if old else ""
        # Keep completed imports final, but let changing conditions recover:
        # - a formerly inaccessible post may expose a file URL later;
        # - auth cookies may have been fixed since the previous run;
        # - blacklist is re-evaluated at group level by candidate_seeds().
        hard_terminal = {"downloaded", "skipped_duplicate", "failed_perm"}
        if old_status in hard_terminal:
            new_status = old_status
        elif old_status == "skipped_no_file" and not file_url:
            new_status = old_status
        else:
            # A fresh scan must re-evaluate blacklist/auth conditions: the user
            # may have edited excluded tags or fixed site credentials since the
            # previous run.  Terminal downloaded/duplicate/permanent rows above
            # remain final, everything else can become pending again.
            new_status = status or "pending"
        con.execute(
            """
            INSERT INTO subscription_seeds
            (key, subscription_id, subscription_name, site, query, priority, post_id, md5,
             file_url, post_url, status, post_json, found_at, updated_at, retry_count,
             retry_after, last_error, path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, '', '')
            ON CONFLICT(key) DO UPDATE SET
              subscription_name=excluded.subscription_name,
              query=excluded.query,
              priority=excluded.priority,
              md5=excluded.md5,
              file_url=CASE WHEN excluded.file_url!='' THEN excluded.file_url ELSE subscription_seeds.file_url END,
              post_url=CASE WHEN excluded.post_url!='' THEN excluded.post_url ELSE subscription_seeds.post_url END,
              status=?,
              post_json=excluded.post_json,
              updated_at=excluded.updated_at
            """,
            (
                key, subscription_id, subscription_name, site, query, int(priority), int(post_id or 0), str(md5 or "").lower(),
                file_url, post_url, new_status, json.dumps(post or {}, ensure_ascii=False), now, now, new_status,
            ),
        )
        con.commit()
        row = con.execute("SELECT * FROM subscription_seeds WHERE key=?", (key,)).fetchone()
        return _row_to_seed(row)


def mark_seed(key: str, status: str, *, error: str = "", path: str = "") -> None:
    now = _now()
    retry_count_delta = 1 if status in {"failed", "failed_temp", "auth_required"} else 0
    with _connect() as con:
        row = con.execute("SELECT retry_count FROM subscription_seeds WHERE key=?", (key,)).fetchone()
        if not row:
            return
        retry_count = int(row["retry_count"] or 0) + retry_count_delta
        retry_after = 0
        final_status = "failed_temp" if status == "failed" else status
        if final_status == "downloading":
            retry_after = now + 3600
        if final_status == "failed_temp":
            delays = [600, 1800, 7200, 43200]
            retry_after = now + delays[min(max(retry_count - 1, 0), len(delays) - 1)]
            if retry_count >= 6:
                final_status = "failed_perm"
        elif final_status == "auth_required":
            # Do not hammer a site with invalid/missing auth in the same run.
            # A new scan after credentials/cookies are fixed resets it to pending.
            retry_after = now + 3600
        con.execute(
            """
            UPDATE subscription_seeds
            SET status=?, updated_at=?, retry_count=?, retry_after=?,
                last_error=CASE WHEN ?!='' THEN ? ELSE last_error END,
                path=CASE WHEN ?!='' THEN ? ELSE path END
            WHERE key=?
            """,
            (final_status, now, retry_count, retry_after, str(error)[:500], str(error)[:500], path, path, key),
        )
        con.commit()


def mark_many(keys: Iterable[str], status: str, *, error: str = "", path: str = "") -> None:
    for key in keys:
        if key:
            mark_seed(key, status, error=error, path=path)


def candidate_seeds(sub_id: str, *, include_failed: bool = True, limit: int = 1000) -> list[dict]:
    now = _now()
    statuses = ["pending", "downloading"]
    if include_failed:
        statuses += ["failed", "failed_temp", "auth_required"]
    qmarks = ",".join("?" for _ in statuses)
    with _connect() as con:
        params = [sub_id, *statuses, now]
        sql = f"""
            SELECT * FROM subscription_seeds
            WHERE subscription_id=? AND status IN ({qmarks})
              AND (retry_after IS NULL OR retry_after=0 OR retry_after<=?)
            ORDER BY priority DESC, post_id DESC, found_at ASC
        """
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = list(con.execute(sql, params).fetchall())
        # Complete MD5 groups outside this page-sized batch.  A file found on
        # e621 and rule34 must merge metadata even if its seeds fall on opposite
        # sides of the batch boundary.
        md5_values = sorted({str(r["md5"] or "").lower() for r in rows if str(r["md5"] or "").strip()})
        if md5_values:
            ph = ",".join("?" for _ in md5_values)
            extra = con.execute(
                f"""
                SELECT * FROM subscription_seeds
                WHERE subscription_id=? AND md5 IN ({ph}) AND status IN ({qmarks})
                  AND (retry_after IS NULL OR retry_after=0 OR retry_after<=?)
                """,
                [sub_id, *md5_values, *statuses, now],
            ).fetchall()
            by_key = {str(r["key"]): r for r in rows}
            for row in extra:
                by_key.setdefault(str(row["key"]), row)
            rows = list(by_key.values())
        return [_row_to_seed(r) for r in rows]


def stats_for_subscription(sub_id: str) -> dict[str, int]:
    with _connect() as con:
        rows = con.execute(
            "SELECT status, COUNT(*) AS n FROM subscription_seeds WHERE subscription_id=? GROUP BY status",
            (sub_id,),
        ).fetchall()
        return {str(r["status"]): int(r["n"]) for r in rows}


def start_run(subscription_id: str, subscription_name: str, mode: str, direction: str) -> str:
    run_id = f"run_{subscription_id}_{_now()}"
    with _connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO subscription_runs(run_id, subscription_id, subscription_name, started_at, mode, direction) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, subscription_id, subscription_name, _now(), mode, direction),
        )
        con.commit()
    return run_id


def finish_run(run_id: str, *, status: str = "done", found: int = 0, queued: int = 0, downloaded: int = 0, skipped: int = 0, failed: int = 0, note: str = "") -> None:
    with _connect() as con:
        con.execute(
            """
            UPDATE subscription_runs
            SET finished_at=?, status=?, found=?, queued=?, downloaded=?, skipped=?, failed=?, note=?
            WHERE run_id=?
            """,
            (_now(), status, int(found), int(queued), int(downloaded), int(skipped), int(failed), note[:1000], run_id),
        )
        con.commit()
