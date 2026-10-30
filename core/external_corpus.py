from __future__ import annotations

"""Read-only adapter for Booru Corpus Builder libraries.

Local Booru never mutates the Corpus Builder SQLite database or canonical files.
The adapter accepts the corpus root (preferred) or an explicit corpus.sqlite3
path, follows database_location.json when the Builder moved SQLite to SSD, and
opens a fresh SQLite read-only snapshot for each operation so a live WAL writer
can continue downloading while Local Booru browses it.
"""

from collections import Counter, defaultdict
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse

from core.tag_utils import normalize_tag, should_hide_tag


_TOKEN_RE = re.compile(r'"([^"]+)"|(\S+)')

_SITE_HOST = {
    "atf": "booru.allthefallen.moe",
    "allthefallen": "booru.allthefallen.moe",
    "booru.allthefallen.moe": "booru.allthefallen.moe",
    "danbooru": "danbooru.donmai.us",
    "danbooru.donmai.us": "danbooru.donmai.us",
    "gelbooru": "gelbooru.com",
    "gelbooru.com": "gelbooru.com",
    "rule34": "rule34.xxx",
    "rule34.xxx": "rule34.xxx",
    "r34": "rule34.xxx",
    "e621": "e621.net",
    "e621.net": "e621.net",
    "e926": "e926.net",
    "e926.net": "e926.net",
}

_CATEGORY_PRIORITY = {
    "artist": 0, "contributor": 1, "character": 2, "copyright": 3,
    "species": 4, "meta": 5, "lore": 6, "invalid": 7,
    "parody": 8, "language": 9, "category": 10, "pages": 11,
    "general": 99,
}


class CorpusUnavailableError(RuntimeError):
    pass


def _host_for_site(site: str) -> str:
    raw = str(site or "").strip().lower().replace("www.", "")
    if raw in _SITE_HOST:
        return _SITE_HOST[raw]
    if "://" in raw:
        try:
            raw = (urlparse(raw).hostname or raw).lower().replace("www.", "")
        except Exception:
            pass
    return raw


def _site_aliases_for_host(host: str) -> list[str]:
    selected = str(host or "all").strip().lower().replace("www.", "")
    if selected == "all":
        return []
    out = []
    for site, mapped in _SITE_HOST.items():
        if mapped == selected and site not in out:
            out.append(site)
    if selected not in out:
        out.append(selected)
    return out


def _path_from_pointer(root: Path) -> Path | None:
    pointer = root / "database_location.json"
    if not pointer.is_file():
        return None
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        raw = str(payload.get("path") or "").strip()
        return Path(raw) if raw else None
    except Exception:
        return None


def resolve_corpus(settings_or_path) -> tuple[Path, Path]:
    """Return (corpus_root, sqlite_path).

    The preferred setting is ``booru_corpus_path`` pointing at the Builder's
    ``corpus`` directory.  An explicit corpus.sqlite3 path is also accepted.
    """
    if isinstance(settings_or_path, dict):
        raw = str(settings_or_path.get("booru_corpus_path") or "").strip()
    else:
        raw = str(settings_or_path or "").strip()
    if not raw:
        raise CorpusUnavailableError("Booru Corpus не подключён")

    selected = Path(raw).expanduser()
    if selected.is_file():
        if selected.name.lower() == "database_location.json":
            root = selected.parent
            db_path = _path_from_pointer(root)
            if db_path is None:
                raise CorpusUnavailableError(f"Не удалось прочитать {selected}")
        else:
            db_path = selected
            # An SSD db path cannot reliably reveal the HDD corpus root.  This
            # is fine because Builder normally stores absolute files.local_path.
            root = selected.parent
    elif selected.is_dir():
        root = selected
        db_path = _path_from_pointer(root) or (root / "corpus.sqlite3")
    else:
        raise CorpusUnavailableError(f"Путь Booru Corpus не существует: {selected}")

    if not db_path.is_file():
        raise CorpusUnavailableError(
            "SQLite Booru Corpus не найден. Выбери корневую папку corpus, "
            "где лежит database_location.json, либо сам corpus.sqlite3.\n"
            f"Искали: {db_path}"
        )
    return root, db_path


@contextmanager
def corpus_db(settings):
    root, db_path = resolve_corpus(settings)
    # mode=ro prevents accidental DB creation/writes. Do not use immutable=1:
    # a live Corpus Builder writes through WAL and Local Booru must see it.
    uri = db_path.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=15.0)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA busy_timeout=15000")
        con.execute("PRAGMA temp_store=MEMORY")
        con.execute("PRAGMA cache_size=-131072")
        yield con, root, db_path
    finally:
        con.close()


def validate_corpus(settings_or_path) -> dict:
    temp = settings_or_path if isinstance(settings_or_path, dict) else {"booru_corpus_path": str(settings_or_path)}
    required = {"visual_assets", "files", "source_posts", "tags", "source_post_tags"}
    with corpus_db(temp) as (con, root, db_path):
        names = {str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(required - names)
        if missing:
            raise CorpusUnavailableError("Это не поддерживаемая база Booru Corpus; нет таблиц: " + ", ".join(missing))
        images = int(con.execute(
            "SELECT COUNT(*) FROM visual_assets va JOIN files f ON f.id=va.canonical_file_id "
            "WHERE va.canonical_file_id IS NOT NULL AND COALESCE(f.local_path,'')<>''"
        ).fetchone()[0] or 0)
        posts = int(con.execute("SELECT COUNT(*) FROM source_posts WHERE visual_asset_id IS NOT NULL").fetchone()[0] or 0)
        sites = [str(r[0]) for r in con.execute(
            "SELECT DISTINCT site FROM source_posts WHERE visual_asset_id IS NOT NULL ORDER BY site"
        ).fetchall()]
    return {
        "root": str(root), "db_path": str(db_path), "images": images,
        "source_posts": posts, "sites": sites,
    }


def _parse_query(query: str):
    plus_groups, minus_groups = [], []
    text = (query or "").replace(",", " ")
    for m in _TOKEN_RE.finditer(text):
        raw = (m.group(1) or m.group(2) or "").strip()
        if not raw:
            continue
        neg = raw.startswith("-") and len(raw) > 1
        if neg:
            raw = raw[1:].strip()
        if raw.lower().startswith("tag:"):
            raw = raw[4:]
        options = []
        for part in raw.split("/"):
            norm = normalize_tag(part)
            if norm and norm not in options:
                options.append(norm)
        if options:
            (minus_groups if neg else plus_groups).append(options)
    return plus_groups, minus_groups


def _source_clause(source: str, alias: str = "sp") -> tuple[str, list[str]]:
    aliases = _site_aliases_for_host(source)
    if not aliases:
        return "", []
    ph = ",".join("?" for _ in aliases)
    return f" AND LOWER(REPLACE({alias}.site,'www.','')) IN ({ph})", aliases


def _translate_extra_condition(cond: str) -> str:
    """Translate safe numeric filters emitted by human_query_parser.

    Corpus contains static images and no Local-Booru star/lifecycle state.
    Unsupported local-only values deliberately evaluate to zero instead of
    touching the corpus database.
    """
    text = str(cond or "")
    replacements = {
        "COALESCE(i.size_bytes, 0)": "COALESCE(f.file_size, 0)",
        "COALESCE(i.width, 0)": "COALESCE(f.width, 0)",
        "COALESCE(i.height, 0)": "COALESCE(f.height, 0)",
        "COALESCE(i.rating, 0)": "0",
        "COALESCE(i.duration, 0)": "0",
        "COALESCE(i.favorite, 0)": "0",
        "(SELECT COUNT(*) FROM image_tags WHERE image_id=i.id)": (
            "(SELECT COUNT(DISTINCT sptx.tag_id) FROM source_posts spx "
            "JOIN source_post_tags sptx ON sptx.source_post_id=spx.id "
            "WHERE spx.visual_asset_id=va.id)"
        ),
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Refuse arbitrary Local-Booru table references: extra_where is generated by
    # our own parser/UI, but fail closed if another caller injects local columns.
    if "i." in text or ";" in text:
        raise CorpusUnavailableError(f"Фильтр не поддерживается в режиме Corpus: {cond}")
    return text


def _base_sql(query="", source="all", bucket="all", count=False, extra_where=None):
    # Corpus has no Inbox/Trash/Downloaded lifecycle. "archive" is equivalent
    # to all canonical files; local-only buckets intentionally return no rows.
    if bucket not in (None, "", "all", "archive"):
        return "SELECT 0 AS c" if count else "SELECT NULL WHERE 0", []

    plus, minus = _parse_query(query)
    where = ["va.canonical_file_id IS NOT NULL", "COALESCE(f.local_path,'')<>''"]
    args: list = []
    source_sql, source_args = _source_clause(source, "sp")

    for options in plus:
        ph = ",".join("?" for _ in options)
        where.append(
            "EXISTS (SELECT 1 FROM source_posts sp "
            "JOIN source_post_tags spt ON spt.source_post_id=sp.id "
            "JOIN tags t ON t.id=spt.tag_id "
            f"WHERE sp.visual_asset_id=va.id{source_sql} AND LOWER(t.name) IN ({ph}))"
        )
        args.extend(source_args); args.extend(options)
    for options in minus:
        ph = ",".join("?" for _ in options)
        where.append(
            "NOT EXISTS (SELECT 1 FROM source_posts sp "
            "JOIN source_post_tags spt ON spt.source_post_id=sp.id "
            "JOIN tags t ON t.id=spt.tag_id "
            f"WHERE sp.visual_asset_id=va.id{source_sql} AND LOWER(t.name) IN ({ph}))"
        )
        args.extend(source_args); args.extend(options)

    if str(source or "all").lower() != "all":
        where.append(f"EXISTS (SELECT 1 FROM source_posts sp WHERE sp.visual_asset_id=va.id{source_sql})")
        args.extend(source_args)

    for cond in extra_where or []:
        where.append(_translate_extra_condition(cond))

    if count:
        select = "COUNT(*) AS c"
    else:
        select = (
            "va.id AS visual_asset_id, f.id AS corpus_file_id, f.md5, f.phash, "
            "f.width, f.height, f.ext, f.file_size, f.local_path, f.created_at, va.updated_at"
        )
    sql = f"SELECT {select} FROM visual_assets va JOIN files f ON f.id=va.canonical_file_id"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql, args


def count_search_items(settings, query="", source="all", bucket="all", extra_where=None, extra_params=None):
    sql, args = _base_sql(query, source, bucket, count=True, extra_where=extra_where)
    if sql == "SELECT 0 AS c":
        return 0
    args.extend(list(extra_params or []))
    with corpus_db(settings) as (con, _root, _db_path):
        row = con.execute(sql, args).fetchone()
        return int((row["c"] if row else 0) or 0)


def _resolved_media_path(root: Path, raw_path: str) -> str:
    p = Path(str(raw_path or ""))
    if p.is_absolute():
        return str(p)
    return str(root / p)


def search_items(settings, query="", source="all", bucket="all", limit=None, offset=0, order="path", extra_where=None, extra_params=None):
    sql, args = _base_sql(query, source, bucket, count=False, extra_where=extra_where)
    if sql == "SELECT NULL WHERE 0":
        return []
    args.extend(list(extra_params or []))
    if order in ("newest", "parsed_newest"):
        sql += " ORDER BY COALESCE(f.created_at,va.updated_at,0) DESC, va.id DESC"
    elif order in ("oldest", "parsed_oldest"):
        sql += " ORDER BY COALESCE(f.created_at,va.updated_at,0) ASC, va.id ASC"
    elif order == "filename":
        sql += " ORDER BY LOWER(f.local_path) ASC, va.id ASC"
    else:
        sql += " ORDER BY LOWER(f.local_path) ASC, va.id ASC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        args.extend([int(limit), int(offset)])

    with corpus_db(settings) as (con, root, _db_path):
        rows = con.execute(sql, args).fetchall()
        out = []
        for r in rows:
            path = _resolved_media_path(root, r["local_path"])
            out.append({
                "id": None,
                "external_id": int(r["visual_asset_id"]),
                "corpus_file_id": int(r["corpus_file_id"]),
                "_provider": "corpus",
                "_external_corpus": True,
                "path": path,
                "file_name": Path(path).name,
                "tags": [], "tag_groups": {}, "tag_groups_by_source": {},
                "sources": [], "source_hosts": [],
                "is_video": False,
                "mtime_ns": int(r["created_at"] or 0) * 1_000_000_000,
                "bucket": "corpus",
                "size_bytes": int(r["file_size"] or 0),
                "width": int(r["width"] or 0),
                "height": int(r["height"] or 0),
                "hash_md5": str(r["md5"] or ""),
                "hash_phash": str(r["phash"] or ""),
                "lifecycle": "archive",
                "favorite": False,
                "trashed_at": 0,
                "presence_status": "unknown",
                "presence_root_status": "unknown",
                "presence_root_path": str(root),
                "presence_error": "",
                "presence_checked_at": 0,
                "family_id": 0, "family_index": 0, "family_total": 0,
                "family_type": "", "family_source": "", "family_title": "",
                "family_completion_status": "unknown", "family_sort_anchor": 0,
            })
        return out


def _merge_groups(source_groups: dict, selected_source="all") -> dict:
    selected = str(selected_source or "all").lower().replace("www.", "")
    maps = source_groups.values() if selected == "all" else [source_groups.get(selected, {})]
    best = {}
    for groups in maps:
        for category, values in (groups or {}).items():
            cat = str(category or "general").lower()
            for name in values or []:
                norm = normalize_tag(name)
                if not norm:
                    continue
                old = best.get(norm)
                if old is None or _CATEGORY_PRIORITY.get(cat, 50) < _CATEGORY_PRIORITY.get(old[0], 50):
                    best[norm] = (cat, name)
    out = defaultdict(list)
    for _norm, (cat, name) in best.items():
        out[cat].append(name)
    for cat in out:
        out[cat].sort(key=lambda x: str(x).lower())
    return dict(out)


def enrich_items(settings, items, tag_source="all"):
    if not items:
        return items
    by_asset = {
        int(item.get("external_id")): item
        for item in items
        if item.get("external_id") is not None and bool(item.get("_external_corpus"))
    }
    if not by_asset:
        return items
    ids = list(by_asset)
    selected = str(tag_source or "all").lower().replace("www.", "")
    with corpus_db(settings) as (con, _root, _db_path):
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            ph = ",".join("?" for _ in chunk)
            for aid in chunk:
                item = by_asset[aid]
                item["tags"] = []
                item["tag_groups"] = {}
                item["tag_groups_by_source"] = {}
                item["sources"] = []
                item["source_hosts"] = []

            rows = con.execute(
                f"""
                SELECT sp.visual_asset_id,sp.site,sp.post_id,sp.source_url,sp.file_url,
                       t.name,spt.category
                FROM source_posts sp
                LEFT JOIN source_post_tags spt ON spt.source_post_id=sp.id
                LEFT JOIN tags t ON t.id=spt.tag_id
                WHERE sp.visual_asset_id IN ({ph})
                ORDER BY sp.visual_asset_id,sp.site,sp.id,spt.category,t.name COLLATE NOCASE
                """, chunk,
            ).fetchall()
            seen_sources = defaultdict(set)
            for r in rows:
                aid = int(r["visual_asset_id"])
                item = by_asset.get(aid)
                if item is None:
                    continue
                host = _host_for_site(r["site"])
                source_url = str(r["source_url"] or "").strip()
                source_key = (host, source_url, str(r["post_id"] or ""))
                if source_key not in seen_sources[aid]:
                    seen_sources[aid].add(source_key)
                    item["sources"].append({
                        "host": host,
                        "url": source_url,
                        "post_id": str(r["post_id"] or ""),
                        "site": str(r["site"] or ""),
                    })
                name = str(r["name"] or "").strip()
                if not name:
                    continue
                cat = str(r["category"] or "general").lower()
                if should_hide_tag(name, cat, settings):
                    continue
                groups = item["tag_groups_by_source"].setdefault(host, {})
                bucket = groups.setdefault(cat, [])
                if name not in bucket:
                    bucket.append(name)

            for aid in chunk:
                item = by_asset[aid]
                for host_groups in item["tag_groups_by_source"].values():
                    for values in host_groups.values():
                        values.sort(key=lambda x: str(x).lower())
                groups = _merge_groups(item["tag_groups_by_source"], selected)
                item["tag_groups"] = groups
                item["tags"] = [tag for values in groups.values() for tag in values]
                item["source_hosts"] = sorted({s["host"] for s in item["sources"] if s.get("host")})
    return items


def candidate_tags(settings, scope="all"):
    # tags is already a compact dictionary table. Avoid source_post_tags tag-id
    # scans here because the Builder intentionally optimises that table for
    # source_post_id-first media ingestion.
    with corpus_db(settings) as (con, _root, _db_path):
        out = []
        for r in con.execute("SELECT name FROM tags ORDER BY name COLLATE NOCASE"):
            tag = normalize_tag(r["name"])
            if tag and not should_hide_tag(r["name"], "general", settings):
                out.append(tag)
        return out


def candidate_sources(settings, scope="all"):
    with corpus_db(settings) as (con, _root, _db_path):
        sites = [str(r["site"] or "") for r in con.execute(
            "SELECT DISTINCT site FROM source_posts WHERE visual_asset_id IS NOT NULL ORDER BY site"
        ).fetchall()]
    return sorted({_host_for_site(site) for site in sites if _host_for_site(site)})


def counts(settings):
    sc = {}
    with corpus_db(settings) as (con, _root, _db_path):
        for r in con.execute(
            "SELECT site,COUNT(DISTINCT visual_asset_id) c FROM source_posts "
            "WHERE visual_asset_id IS NOT NULL GROUP BY site"
        ).fetchall():
            host = _host_for_site(r["site"])
            sc[host] = sc.get(host, 0) + int(r["c"] or 0)
    # Global per-tag counts are intentionally handled by tag_group_counts in a
    # background task; returning a duplicate giant flat dict here wastes RAM.
    return {}, sc, {}


def source_unique_image_count(settings):
    with corpus_db(settings) as (con, _root, _db_path):
        row = con.execute(
            "SELECT COUNT(*) c FROM visual_assets va JOIN files f ON f.id=va.canonical_file_id "
            "WHERE va.canonical_file_id IS NOT NULL AND COALESCE(f.local_path,'')<>''"
        ).fetchone()
    return int((row["c"] if row else 0) or 0)


def tag_group_counts(settings, source="all"):
    selected = str(source or "all").lower().replace("www.", "")
    source_sql, source_args = _source_clause(selected, "sp")
    rank_case = """CASE LOWER(COALESCE(NULLIF(spt.category,''),'general'))
        WHEN 'artist' THEN 0 WHEN 'contributor' THEN 1 WHEN 'character' THEN 2
        WHEN 'copyright' THEN 3 WHEN 'species' THEN 4 WHEN 'meta' THEN 5
        WHEN 'lore' THEN 6 WHEN 'invalid' THEN 7 WHEN 'parody' THEN 8
        WHEN 'language' THEN 9 WHEN 'category' THEN 10 WHEN 'pages' THEN 11
        WHEN 'general' THEN 99 ELSE 50 END"""
    # Choose one effective category for each downloaded visual_asset/tag pair,
    # then count assets. This preserves source provenance and avoids double
    # counting a tag that multiple source posts attach to the same canonical.
    sql = f"""
        WITH ranked AS (
            SELECT sp.visual_asset_id,spt.tag_id,
                   LOWER(COALESCE(NULLIF(spt.category,''),'general')) category,
                   ROW_NUMBER() OVER (
                       PARTITION BY sp.visual_asset_id,spt.tag_id
                       ORDER BY {rank_case},sp.id
                   ) rn
            FROM source_posts sp
            JOIN source_post_tags spt ON spt.source_post_id=sp.id
            WHERE sp.visual_asset_id IS NOT NULL{source_sql}
        )
        SELECT ranked.category,t.name,COUNT(*) c
        FROM ranked JOIN tags t ON t.id=ranked.tag_id
        WHERE ranked.rn=1
        GROUP BY ranked.category,ranked.tag_id
        ORDER BY ranked.category,c DESC,t.name COLLATE NOCASE
    """
    out = defaultdict(Counter)
    with corpus_db(settings) as (con, _root, _db_path):
        for r in con.execute(sql, source_args).fetchall():
            cat = str(r["category"] or "general")
            name = normalize_tag(r["name"])
            if name and not should_hide_tag(r["name"], cat, settings):
                out[cat][name] = int(r["c"] or 0)
    return out
