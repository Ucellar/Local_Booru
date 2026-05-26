from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import time
from core.tag_utils import normalize_tag
from .connection import db


def _parse_query(query):
    plus, minus = [], []
    for raw in (query or "").replace(",", " ").split():
        raw = raw.strip()
        if not raw:
            continue
        if raw.startswith("-") and len(raw) > 1:
            minus.append(normalize_tag(raw[1:]))
        else:
            plus.append(normalize_tag(raw))
    return [x for x in plus if x], [x for x in minus if x]


def _bucket_clause(bucket):
    if not bucket or bucket == "all":
        return "", []
    if bucket == "found":
        return "i.bucket IN ('found','partial_match','downloaded_found','downloaded_partial_match')", []
    if bucket == "no_match":
        return "i.bucket IN ('no_match','downloaded_no_match')", []
    if bucket == "downloaded":
        return "i.bucket LIKE 'downloaded%'", []
    return "i.bucket=?", [bucket]


def _base_search_sql(query="", source="all", bucket="all", count=False):
    plus, minus = _parse_query(query)
    joins = []
    where = ["i.deleted=0"]
    args = []

    # Intersection of positive tags: one EXISTS per tag. SQLite uses idx_image_tags_tag.
    for tag in plus:
        where.append("""EXISTS (
            SELECT 1 FROM image_tags it
            JOIN tags t ON t.id=it.tag_id
            WHERE it.image_id=i.id AND t.normalized_name=?
        )""")
        args.append(tag)

    for tag in minus:
        where.append("""NOT EXISTS (
            SELECT 1 FROM image_tags it
            JOIN tags t ON t.id=it.tag_id
            WHERE it.image_id=i.id AND t.normalized_name=?
        )""")
        args.append(tag)

    if source and source != "all":
        where.append("""EXISTS (
            SELECT 1 FROM image_sources isrc
            JOIN sources s ON s.id=isrc.source_id
            WHERE isrc.image_id=i.id AND s.host=?
        )""")
        args.append(source)

    bsql, bargs = _bucket_clause(bucket)
    if bsql:
        where.append(bsql); args.extend(bargs)

    select = "COUNT(*) AS c" if count else "i.*"
    sql = "SELECT " + select + " FROM images i"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql, args


def count_search_items(settings, query="", source="all", bucket="all",
                       extra_where=None, extra_params=None):
    sql, args = _base_search_sql(query, source, bucket, count=True)
    if extra_where:
        for cond in extra_where:
            sql += f" AND {cond}"
        args = list(args) + list(extra_params or [])
    with db(settings, readonly=True) as con:
        return int(con.execute(sql, args).fetchone()["c"] or 0)


def search_items(settings, query="", source="all", bucket="all", limit=None, offset=0, order="path",
                 extra_where=None, extra_params=None):
    sql, args = _base_search_sql(query, source, bucket, count=False)
    if extra_where:
        for cond in extra_where:
            sql += f" AND {cond}"
        args = list(args) + list(extra_params or [])
    order_sql = "i.path COLLATE NOCASE"
    if order == "newest":
        order_sql = "i.mtime_ns DESC, i.path COLLATE NOCASE"
    elif order == "oldest":
        order_sql = "i.mtime_ns ASC, i.path COLLATE NOCASE"
    elif order == "filename":
        order_sql = "i.file_name COLLATE NOCASE"
    sql += " ORDER BY " + order_sql
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        args += [int(limit), int(offset)]
    with db(settings, readonly=True) as con:
        rows = con.execute(sql, args).fetchall()
        return [_item_from_row(r, load_details=False) for r in rows]


def _item_from_row(row, load_details=False):
    return {
        "id": int(row["id"]),
        "path": row["path"],
        "tags": [],
        "tag_groups": {},
        "sources": [],
        "source_hosts": [],
        "is_video": bool(row["is_video"]),
        "mtime_ns": int(row["mtime_ns"] or 0),
        "bucket": row["bucket"],
        "size_bytes": int(row["size_bytes"] or 0),
        "width": int(row["width"] or 0),
        "height": int(row["height"] or 0),
        "hash_md5": row["hash_md5"],
    }


def enrich_items(settings, items):
    if not items:
        return items
    by_id = {int(x.get("id")): x for x in items if x.get("id") is not None}
    if not by_id:
        return items
    ids = list(by_id.keys())
    with db(settings, readonly=True) as con:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i+500]
            ph = ",".join(["?"] * len(chunk))
            for item in (by_id[x] for x in chunk):
                item["tags"] = []
                item["tag_groups"] = {}
                item["sources"] = []
                item["source_hosts"] = []
            tag_rows = con.execute(f"""
                SELECT it.image_id, t.name, t.category FROM image_tags it
                JOIN tags t ON t.id=it.tag_id
                WHERE it.image_id IN ({ph})
                ORDER BY t.category, t.name COLLATE NOCASE
            """, chunk).fetchall()
            for r in tag_rows:
                item = by_id.get(int(r["image_id"]))
                if item is None:
                    continue
                cat = r["category"] or "general"
                item.setdefault("tag_groups", {}).setdefault(cat, []).append(r["name"])
                item.setdefault("tags", []).append(r["name"])
            src_rows = con.execute(f"""
                SELECT isrc.image_id, s.host, s.url FROM image_sources isrc
                JOIN sources s ON s.id=isrc.source_id
                WHERE isrc.image_id IN ({ph})
            """, chunk).fetchall()
            for r in src_rows:
                item = by_id.get(int(r["image_id"]))
                if item is None:
                    continue
                item.setdefault("sources", []).append({"host": r["host"], "url": r["url"]})
            for item in (by_id[x] for x in chunk):
                item["source_hosts"] = sorted({s.get("host", "") for s in item.get("sources", []) if s.get("host")})
    return items


def counts(settings):
    with db(settings, readonly=True) as con:
        tc = {r["name"]: int(r["c"]) for r in con.execute("""
            SELECT t.name, COUNT(*) c FROM tags t
            JOIN image_tags it ON it.tag_id=t.id
            JOIN images i ON i.id=it.image_id
            WHERE i.deleted=0
            GROUP BY t.id ORDER BY c DESC, t.name COLLATE NOCASE
        """)}
        sc = {r["host"]: int(r["c"]) for r in con.execute("""
            SELECT s.host, COUNT(DISTINCT i.id) c FROM sources s
            JOIN image_sources isrc ON isrc.source_id=s.id
            JOIN images i ON i.id=isrc.image_id
            WHERE i.deleted=0
            GROUP BY s.host ORDER BY s.host COLLATE NOCASE
        """)}
    return tc, sc, {}


def tag_group_counts(settings):
    with db(settings, readonly=True) as con:
        out = defaultdict(Counter)
        rows = con.execute("""
            SELECT COALESCE(NULLIF(t.category, ''), 'general') AS category,
                   t.name AS name,
                   COUNT(it.image_id) AS c
            FROM tags t
            JOIN image_tags it ON it.tag_id = t.id
            JOIN images i ON i.id = it.image_id
            WHERE i.deleted=0
            GROUP BY t.id
            ORDER BY category, c DESC, name COLLATE NOCASE
        """).fetchall()
    for r in rows:
        out[r["category"] or "general"][r["name"]] = int(r["c"])
    return out


def candidate_tags(settings, scope="all"):
    with db(settings, readonly=True) as con:
        where, args = _scope_where(scope)
        sql = """
            SELECT t.name, COUNT(*) c FROM tags t
            JOIN image_tags it ON it.tag_id=t.id
            JOIN images i ON i.id=it.image_id
            WHERE i.deleted=0
        """
        if where:
            sql += " AND " + where
        sql += " GROUP BY t.id ORDER BY t.name COLLATE NOCASE"
        return [r["name"] for r in con.execute(sql, args).fetchall()]


def candidate_sources(settings, scope="all"):
    with db(settings, readonly=True) as con:
        where, args = _scope_where(scope)
        sql = """
            SELECT s.url, s.host, COUNT(DISTINCT i.id) c FROM sources s
            JOIN image_sources isrc ON isrc.source_id=s.id
            JOIN images i ON i.id=isrc.image_id
            WHERE i.deleted=0
        """
        if where:
            sql += " AND " + where
        sql += " GROUP BY s.id ORDER BY s.host COLLATE NOCASE, s.url COLLATE NOCASE"
        rows = con.execute(sql, args).fetchall()
        return [r["url"] or r["host"] for r in rows]


def _scope_where(scope):
    scope = scope or "all"
    if scope == "tagger":
        return "i.bucket IN ('found','partial_match','no_match')", []
    if scope == "downloader":
        return "i.bucket LIKE 'downloaded%'", []
    if scope == "found":
        return "i.bucket IN ('found','partial_match','downloaded_found','downloaded_partial_match')", []
    if scope == "no_match":
        return "i.bucket IN ('no_match','downloaded_no_match')", []
    return "", []


def find_images_by_tag(settings, tag, scope="all", limit=None):
    norm = normalize_tag(tag)
    where, args = _scope_where(scope)
    sql = """
        SELECT DISTINCT i.id, i.path, i.file_name, i.bucket FROM images i
        JOIN image_tags it ON it.image_id=i.id
        JOIN tags t ON t.id=it.tag_id
        WHERE i.deleted=0 AND t.normalized_name=?
    """
    params = [norm]
    if where:
        sql += " AND " + where
        params += args
    sql += " ORDER BY i.path COLLATE NOCASE"
    if limit:
        sql += " LIMIT ?"; params.append(int(limit))
    with db(settings, readonly=True) as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def find_images_by_source(settings, source_text, scope="all", limit=None):
    q = "%" + str(source_text or "").lower() + "%"
    where, args = _scope_where(scope)
    sql = """
        SELECT DISTINCT i.id, i.path, i.file_name, i.bucket FROM images i
        JOIN image_sources isrc ON isrc.image_id=i.id
        JOIN sources s ON s.id=isrc.source_id
        WHERE i.deleted=0 AND (LOWER(s.url) LIKE ? OR LOWER(s.host) LIKE ?)
    """
    params = [q, q]
    if where:
        sql += " AND " + where
        params += args
    sql += " ORDER BY i.path COLLATE NOCASE"
    if limit:
        sql += " LIMIT ?"; params.append(int(limit))
    with db(settings, readonly=True) as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def delete_images(settings, image_rows, delete_files=True, reason="delete", tag_or_source=""):
    from core.deleted_registry import mark_deleted
    deleted_files = errors = 0
    rows = list(image_rows or [])
    ids = [int(r["id"]) for r in rows if r.get("id") is not None]
    paths = [Path(r["path"]) for r in rows if r.get("path")]

    if delete_files:
        for p in paths:
            try:
                if p.exists() and p.is_file():
                    try:
                        mark_deleted(p, reason=reason)
                    except Exception:
                        pass
                    p.unlink()
                    deleted_files += 1
            except Exception:
                errors += 1
            _delete_side_artifacts(p)

    with db(settings, write=True) as con:
        now = int(time.time())
        for r, p in zip(rows, paths):
            con.execute(
                "INSERT INTO delete_log(path,file_name,reason,tag_or_source,deleted_at) VALUES(?,?,?,?,?)",
                (str(p), p.name, reason, tag_or_source, now),
            )
        if ids:
            ph = ",".join(["?"] * len(ids))
            # Hard delete from DB. Files deleted by tag/source must vanish from gallery immediately.
            con.execute(f"DELETE FROM images WHERE id IN ({ph})", ids)
        for p in paths:
            con.execute("DELETE FROM processed_files WHERE media_path=?", (str(p),))
        cleanup_orphans(con)
    return {"deleted_files": deleted_files, "errors": errors, "deleted_records": len(ids)}


def cleanup_orphans(con):
    con.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM image_tags)")
    con.execute("DELETE FROM sources WHERE id NOT IN (SELECT DISTINCT source_id FROM image_sources)")


def _delete_side_artifacts(p: Path):
    try:
        bucket = p.parent.parent if p.parent.name == "media" else p.parent
        for sub in ("cache", "searched", "tags", "source"):
            d = bucket / sub
            if d.exists():
                for f in d.glob(p.stem + "*"):
                    if f.is_file():
                        f.unlink(missing_ok=True)
    except Exception:
        pass
