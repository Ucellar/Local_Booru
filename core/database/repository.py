from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import logging
import re
import sqlite3
import time
from functools import wraps
from core.performance import timed as _perf_timed
from core.tag_utils import normalize_tag, should_hide_tag
from .connection import db

_LOG = logging.getLogger(__name__)


def _measure(operation):
    """Record only slow repository calls; never changes SQL semantics."""
    def deco(fn):
        @wraps(fn)
        def wrapped(settings, *args, **kwargs):
            detail = {}
            if operation.startswith("sql.gallery"):
                detail = {"source": str(kwargs.get("source", args[1] if len(args) > 1 else "all")), "bucket": str(kwargs.get("bucket", args[2] if len(args) > 2 else "all"))}
            with _perf_timed(operation, settings, **detail):
                return fn(settings, *args, **kwargs)
        return wrapped
    return deco


_TOKEN_RE = re.compile(r'"([^"]+)"|(\S+)')

def _parse_query(query):
    """Small booru-style parser.

    Space means intersection, while ``a/b`` is a Local Booru extension that
    returns either tag in one result set. Minus still excludes a tag/group.
    """
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


def _expand_relation_groups(settings, groups, source):
    """Expand query tokens through the selected site's official aliases.

    Source-specific gallery searches use that site's relation graph. The
    All-sources union intentionally uses rule34.xxx as the preferred canonical
    vocabulary, per the archive policy, while keeping original spellings in the
    option set so unresolved tags remain searchable.

    Returns one ``(names, patterns)`` pair per group: exact tag names (each typed name, its stored
    spellings in any letter case and their aliases) and LIKE patterns for options with "*".  The
    patterns are matched by SQL itself, so "tag_*" finds every such tag however many there are
    (the expanded list used to stop at 2000 names, silently), and a tag whose name really has a
    "*" in it ("c*ck") is still found by its exact name.
    """
    if not groups:
        return []
    patterns_of = [[_like_pattern(norm) for norm in (normalize_tag(v) for v in options) if norm and "*" in norm]
                   for options in groups]
    try:
        from core.tag_relations import canonical_site_for_scope
        site = canonical_site_for_scope(source)
        expanded = []
        with db(settings, readonly=True) as con:
            for options, patterns in zip(groups, patterns_of):
                seen = {normalize_tag(value) for value in options if normalize_tag(value)}
                seen |= _stored_spellings(con, options)
                frontier = set(seen)
                for _depth in range(16):
                    if not frontier:
                        break
                    ph = ",".join("?" for _ in frontier)
                    args = [site, *sorted(frontier), *sorted(frontier)]
                    rows = con.execute(
                        f"""SELECT source_tag,target_tag FROM tag_relations
                            WHERE source_site=? AND relation_type='alias' AND status='active'
                              AND (source_tag IN ({ph}) OR target_tag IN ({ph}))""",
                        args,
                    ).fetchall()
                    sibling_rows = con.execute(
                        f"""SELECT tag AS source_tag,canonical AS target_tag FROM tag_siblings
                            WHERE tag IN ({ph}) OR canonical IN ({ph})""",
                        (*sorted(frontier), *sorted(frontier)),
                    ).fetchall()
                    next_frontier = set()
                    for row in [*rows, *sibling_rows]:
                        for value in (row["source_tag"], row["target_tag"]):
                            norm = normalize_tag(value)
                            if norm and norm not in seen:
                                seen.add(norm); next_frontier.add(norm)
                    frontier = next_frontier
                expanded.append((sorted(seen), patterns))
        return expanded
    except sqlite3.Error as exc:   # search still works, only without aliases
        _LOG.warning("tag aliases for the search are unavailable: %s", exc)
        return [(list(options), patterns) for options, patterns in zip(groups, patterns_of)]


# A wildcard that matches at most this many tags also brings their aliases; a wider one is
# searched by its pattern alone (its matches are found all the same).
_WILDCARD_ALIAS_LIMIT = 200


def _like_pattern(tag: str) -> str:
    """LIKE pattern for a typed tag: "*" is any text; "_" and "%" are themselves (tags are full of "_")."""
    text = tag.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return text.replace("*", "%")


def _stored_spellings(con, options) -> set:
    """Tag names in the library that a typed option means: any letter case; the tags a narrow "*"
    pattern matches (for their aliases; the pattern itself is searched in SQL)."""
    out = set()
    for value in options:
        norm = normalize_tag(value)
        if not norm:
            continue
        if "*" in norm:
            rows = con.execute(
                "SELECT normalized_name FROM tags WHERE normalized_name LIKE ? ESCAPE '\\' LIMIT ?",
                (_like_pattern(norm), _WILDCARD_ALIAS_LIMIT + 1),
            ).fetchall()
            if len(rows) > _WILDCARD_ALIAS_LIMIT:
                rows = con.execute("SELECT normalized_name FROM tags WHERE normalized_name = ?", (norm,)).fetchall()
        else:
            rows = con.execute(
                "SELECT normalized_name FROM tags WHERE normalized_name = ? COLLATE NOCASE", (norm,)
            ).fetchall()
        out.update(str(r[0]) for r in rows if r[0])
    return out


def _tag_condition(names, patterns) -> tuple[str, list]:
    """SQL condition on ``t.normalized_name`` for one group of options (any of them)."""
    parts, args = [], []
    if names:
        parts.append(f"t.normalized_name IN ({','.join('?' for _ in names)})")
        args.extend(names)
    for pattern in patterns:
        parts.append("t.normalized_name LIKE ? ESCAPE '\\'")
        args.append(pattern)
    return ("(" + " OR ".join(parts) + ")" if parts else "0"), args


def _bucket_clause(bucket):
    if not bucket or bucket == "all":
        return "", []
    if bucket == "found":
        return "i.bucket IN ('found','partial_match','downloaded_found','downloaded_partial_match')", []
    if bucket == "no_match":
        return "i.bucket IN ('no_match','downloaded_no_match')", []
    if bucket == "downloaded":
        return "i.bucket LIKE 'downloaded%'", []
    if bucket == "inbox":
        return "i.lifecycle='inbox'", []
    if bucket == "archive":
        return "COALESCE(i.lifecycle,'archive')='archive'", []
    if bucket == "trash":
        return "i.lifecycle IN ('trash','missing')", []
    return "i.bucket=?", [bucket]


# A сборка of cards (core/card_sets.py) is one tile of the gallery: of its cards found by a search only the first
# (by its place in the сборка) is listed.  ``hits`` — the cards the search finds (a WITH of the same conditions).
COLLAPSE_SETS = (
    "NOT EXISTS (SELECT 1 FROM card_set_members me JOIN card_set_members o ON o.set_id=me.set_id "
    "WHERE me.image_id=i.id AND (o.position<me.position OR (o.position=me.position AND o.image_id<me.image_id)) "
    "AND o.image_id IN (SELECT id FROM hits))"
)


def _collapse_sets(settings, sql, args, query, source, bucket, extra_where, extra_params):
    """``sql`` (a search over ``images i`` with its WHERE) listing one card per сборка."""
    conditions, cond_args = _search_conditions(settings, query, source, bucket)
    conditions = conditions + list(extra_where or [])
    cond_args = list(cond_args) + list(extra_params or [])
    head = "WITH hits(id) AS (SELECT i.id FROM images i WHERE " + " AND ".join(conditions) + ") "
    return head + sql + " AND " + COLLAPSE_SETS, cond_args + list(args)


def _base_search_sql(settings, query="", source="all", bucket="all", count=False):
    where, args = _search_conditions(settings, query, source, bucket)
    if count:
        select = "COUNT(*) AS c"
        sql = "SELECT " + select + " FROM images i"
    else:
        select = (
            "i.*, COALESCE(fi.presence_status,'unknown') AS file_presence_status, "
            "COALESCE(fi.root_status,'unknown') AS file_presence_root_status, "
            "COALESCE(fi.root_path,'') AS file_presence_root_path, "
            "COALESCE(fi.last_error,'') AS file_presence_error, "
            "COALESCE(fi.last_presence_check,0) AS file_presence_checked_at, "
            "COALESCE(gf.family_id,0) AS family_id, COALESCE(gfi.family_index,0) AS family_index, "
            "COALESCE(gf.total_items,0) AS family_total, COALESCE(gf.family_type,'') AS family_type, "
            "COALESCE(gf.family_source,'') AS family_source, COALESCE(gf.title,'') AS family_title, "
            "COALESCE(gf.completion_status,'unknown') AS family_completion_status, "
            "COALESCE(gf.sort_anchor,0) AS family_sort_anchor"
        )
        sql = (
            "SELECT " + select + " FROM images i "
            "LEFT JOIN image_file_integrity fi ON fi.image_id=i.id AND fi.checked_path=i.path "
            "LEFT JOIN media_family_items gfi ON gfi.image_id=i.id AND gfi.family_id=("
            " SELECT mfi2.family_id FROM media_family_items mfi2 "
            " JOIN media_families mf2 ON mf2.family_id=mfi2.family_id "
            " WHERE mfi2.image_id=i.id ORDER BY mf2.order_confidence DESC,mfi2.family_id LIMIT 1"
            ") LEFT JOIN media_families gf ON gf.family_id=gfi.family_id"
        )
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql, args


def _search_conditions(settings, query="", source="all", bucket="all"):
    """The WHERE conditions (on ``images i``) and their parameters of a gallery search."""
    plus, minus = _parse_query(query)
    plus = _expand_relation_groups(settings, plus, source)
    minus = _expand_relation_groups(settings, minus, source)
    joins = []
    # Trash is intentionally viewable; every other gallery section hides deleted rows.
    where = ["i.deleted=1" if bucket == "trash" else "i.deleted=0"]
    args = []

    selected_source = str(source or "all").lower().replace("www.", "")
    # Space = intersection. Each slash-group accepts any one option. When a
    # site filter is active, search the selected site's independent tag set,
    # not a tag supplied by another site for the same image.
    for negate, groups in ((False, plus), (True, minus)):
        exists = "NOT EXISTS" if negate else "EXISTS"
        for names, patterns in groups:
            condition, cond_args = _tag_condition(names, patterns)
            if selected_source != "all":
                where.append(f"""{exists} (
                    SELECT 1 FROM image_source_tags ist
                    JOIN sources ts ON ts.id=ist.source_id
                    JOIN tags t ON t.id=ist.tag_id
                    WHERE ist.image_id=i.id AND ts.host=? AND {condition}
                )""")
                args.append(selected_source); args.extend(cond_args)
            else:
                where.append(f"""{exists} (
                    SELECT 1 FROM image_tags it
                    JOIN tags t ON t.id=it.tag_id
                    WHERE it.image_id=i.id AND {condition}
                )""")
                args.extend(cond_args)

    if selected_source != "all":
        where.append("""EXISTS (
            SELECT 1 FROM image_sources isrc
            JOIN sources s ON s.id=isrc.source_id
            WHERE isrc.image_id=i.id AND s.host=?
        )""")
        args.append(selected_source)

    bsql, bargs = _bucket_clause(bucket)
    if bsql:
        where.append(bsql); args.extend(bargs)
    return where, args


@_measure("sql.gallery.count")
def count_search_items(settings, query="", source="all", bucket="all",
                       extra_where=None, extra_params=None, collapse_sets=False):
    """How many cards a search finds (``collapse_sets`` — a сборка counts once: the gallery's tiles)."""
    sql, args = _base_search_sql(settings, query, source, bucket, count=True)
    if extra_where:
        for cond in extra_where:
            sql += f" AND {cond}"
        args = list(args) + list(extra_params or [])
    if collapse_sets:
        sql, args = _collapse_sets(settings, sql, args, query, source, bucket, extra_where, extra_params)
    with db(settings, readonly=True) as con:
        return int(con.execute(sql, args).fetchone()["c"] or 0)


@_measure("sql.gallery.page")
def search_items(settings, query="", source="all", bucket="all", limit=None, offset=0, order="path",
                 extra_where=None, extra_params=None, collapse_sets=False):
    sql, args = _base_search_sql(settings, query, source, bucket, count=False)
    if extra_where:
        for cond in extra_where:
            sql += f" AND {cond}"
        args = list(args) + list(extra_params or [])
    if collapse_sets:
        sql, args = _collapse_sets(settings, sql, args, query, source, bucket, extra_where, extra_params)
    # A discovered family is an atomic gallery block.  ``family_sort_anchor``
    # selects the block position while ``family_index`` keeps pages contiguous
    # in canonical order. Non-family rows get an image-specific anchor, so they
    # retain the requested global order without being grouped together.
    family_tail = "CASE WHEN COALESCE(gf.family_id,0)>0 THEN gfi.family_index ELSE 0 END ASC, i.id ASC"
    if order == "newest":
        anchor = "CASE WHEN COALESCE(gf.family_id,0)>0 THEN COALESCE(NULLIF(gf.sort_anchor,0),CAST(i.mtime_ns/1000000000 AS INTEGER)) ELSE CAST(i.mtime_ns/1000000000 AS INTEGER) END"
        order_sql = f"{anchor} DESC, CASE WHEN COALESCE(gf.family_id,0)>0 THEN gf.family_id ELSE -i.id END DESC, {family_tail}"
    elif order == "oldest":
        anchor = "CASE WHEN COALESCE(gf.family_id,0)>0 THEN COALESCE(NULLIF(gf.sort_anchor,0),CAST(i.mtime_ns/1000000000 AS INTEGER)) ELSE CAST(i.mtime_ns/1000000000 AS INTEGER) END"
        order_sql = f"{anchor} ASC, CASE WHEN COALESCE(gf.family_id,0)>0 THEN gf.family_id ELSE i.id END ASC, {family_tail}"
    elif order == "parsed_newest":
        anchor = "CASE WHEN COALESCE(gf.family_id,0)>0 THEN COALESCE(NULLIF(gf.sort_anchor,0),i.indexed_at) ELSE i.indexed_at END"
        order_sql = f"{anchor} DESC, CASE WHEN COALESCE(gf.family_id,0)>0 THEN gf.family_id ELSE -i.id END DESC, {family_tail}"
    elif order == "parsed_oldest":
        anchor = "CASE WHEN COALESCE(gf.family_id,0)>0 THEN COALESCE(NULLIF(gf.sort_anchor,0),i.indexed_at) ELSE i.indexed_at END"
        order_sql = f"{anchor} ASC, CASE WHEN COALESCE(gf.family_id,0)>0 THEN gf.family_id ELSE i.id END ASC, {family_tail}"
    elif order == "filename":
        order_sql = "CASE WHEN COALESCE(gf.family_id,0)>0 THEN LOWER(COALESCE(NULLIF(gf.title,''),i.file_name)) ELSE LOWER(i.file_name) END ASC, CASE WHEN COALESCE(gf.family_id,0)>0 THEN gf.family_id ELSE i.id END ASC, " + family_tail
    else:
        order_sql = "CASE WHEN COALESCE(gf.family_id,0)>0 THEN LOWER(COALESCE(NULLIF(gf.title,''),i.path)) ELSE LOWER(i.path) END ASC, CASE WHEN COALESCE(gf.family_id,0)>0 THEN gf.family_id ELSE i.id END ASC, " + family_tail
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
        "duration": float(row["duration"] or 0) if "duration" in row.keys() else 0.0,
        "hash_md5": row["hash_md5"],
        "lifecycle": row["lifecycle"] if "lifecycle" in row.keys() else "archive",
        "favorite": bool(row["favorite"]) if "favorite" in row.keys() else False,
        "rating": int(row["rating"] or 0) if "rating" in row.keys() else 0,
        "trashed_at": int(row["trashed_at"] or 0) if "trashed_at" in row.keys() else 0,
        "presence_status": str(row["file_presence_status"] or "unknown") if "file_presence_status" in row.keys() else "unknown",
        "presence_root_status": str(row["file_presence_root_status"] or "unknown") if "file_presence_root_status" in row.keys() else "unknown",
        "presence_root_path": str(row["file_presence_root_path"] or "") if "file_presence_root_path" in row.keys() else "",
        "presence_error": str(row["file_presence_error"] or "") if "file_presence_error" in row.keys() else "",
        "presence_checked_at": int(row["file_presence_checked_at"] or 0) if "file_presence_checked_at" in row.keys() else 0,
        "family_id": int(row["family_id"] or 0) if "family_id" in row.keys() else 0,
        "family_index": int(row["family_index"] or 0) if "family_index" in row.keys() else 0,
        "family_total": int(row["family_total"] or 0) if "family_total" in row.keys() else 0,
        "family_type": str(row["family_type"] or "") if "family_type" in row.keys() else "",
        "family_source": str(row["family_source"] or "") if "family_source" in row.keys() else "",
        "family_title": str(row["family_title"] or "") if "family_title" in row.keys() else "",
        "family_completion_status": str(row["family_completion_status"] or "unknown") if "family_completion_status" in row.keys() else "unknown",
        "family_sort_anchor": int(row["family_sort_anchor"] or 0) if "family_sort_anchor" in row.keys() else 0,
    }


_CATEGORY_PRIORITY = {
    "artist": 0, "contributor": 1, "character": 2, "copyright": 3,
    "species": 4, "meta": 5, "lore": 6, "invalid": 7,
    "parody": 8, "language": 9, "category": 10, "pages": 11, "general": 99,
}


def _effective_source_groups(source_groups, known=None):
    """Promote a source's ``general`` category from corroborating sources only.

    Source tag membership remains isolated: no source receives a tag that it did
    not return itself.  Categories are monotonic, however.  When Gelbooru has
    ``alan_smithee`` as ``general`` and Danbooru has the same tag as ``artist``
    for the same image, Gelbooru may display/use its existing tag as ``artist``.
    A classified tag is never demoted to ``general`` by another source.
    ``known`` (v5.44.11): {normalized tag: category} the library knows the tag by (the tags table — what the sites
    said of it on other cards): a card of one site that sent no categories (a download of the grabber or of the
    subscriptions from rule34.xxx before 5.44.11: every tag «Общие») shows them too.
    """
    normalized_by_source = {}
    best_classified = {}
    for host, groups in (source_groups or {}).items():
        own = {}
        for category, values in (groups or {}).items():
            cat = str(category or "general").lower()
            for name in values or []:
                norm = normalize_tag(name)
                if not norm:
                    continue
                previous = own.get(norm)
                if previous is None or _CATEGORY_PRIORITY.get(cat, 50) < _CATEGORY_PRIORITY.get(previous[0], 50):
                    own[norm] = (cat, name)
                if cat != "general":
                    prior = best_classified.get(norm)
                    if prior is None or _CATEGORY_PRIORITY.get(cat, 50) < _CATEGORY_PRIORITY.get(prior[0], 50):
                        best_classified[norm] = (cat, name)
        normalized_by_source[str(host)] = own

    effective = {}
    for host, own in normalized_by_source.items():
        groups = defaultdict(list)
        for norm, (cat, name) in own.items():
            promoted = best_classified.get(norm)
            if promoted is None and known and known.get(norm):
                promoted = (known[norm], name)
            effective_cat = promoted[0] if cat == "general" and promoted is not None else cat
            groups[effective_cat].append(name)
        for cat in groups:
            groups[cat].sort(key=lambda value: value.lower())
        effective[host] = dict(groups)
    return effective


def _merged_source_groups(source_groups, selected_source="all"):
    selected = str(selected_source or "all").lower()
    maps = source_groups.values() if selected == "all" else [source_groups.get(selected, {})]
    best = {}
    for groups in maps:
        for category, values in (groups or {}).items():
            cat = str(category or "general")
            for name in values or []:
                norm = normalize_tag(name)
                if not norm:
                    continue
                prior = best.get(norm)
                if prior is None or _CATEGORY_PRIORITY.get(cat, 50) < _CATEGORY_PRIORITY.get(prior[0], 50):
                    best[norm] = (cat, name)
    out = defaultdict(list)
    for _norm, (cat, name) in best.items():
        out[cat].append(name)
    for cat in out:
        out[cat].sort(key=lambda value: value.lower())
    return dict(out)


@_measure("sql.gallery.enrich")
def enrich_items(settings, items, tag_source="all"):
    if not items:
        return items
    by_id = {int(x.get("id")): x for x in items if x.get("id") is not None}
    if not by_id:
        return items
    ids = list(by_id.keys())
    selected_source = str(tag_source or "all").lower().replace("www.", "")
    with db(settings, readonly=True) as con:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i+500]
            ph = ",".join(["?"] * len(chunk))
            for item in (by_id[x] for x in chunk):
                item["tags"] = []
                item["tag_groups"] = {}
                item["tag_groups_by_source"] = {}
                item["sources"] = []
                item["source_hosts"] = []
            legacy_rows = con.execute(f"""
                SELECT it.image_id, t.name, t.category FROM image_tags it
                JOIN tags t ON t.id=it.tag_id
                WHERE it.image_id IN ({ph})
                ORDER BY t.category, t.name COLLATE NOCASE
            """, chunk).fetchall()
            source_rows = con.execute(f"""
                SELECT ist.image_id, s.host, s.url, t.name, ist.category, t.category AS known
                FROM image_source_tags ist
                JOIN sources s ON s.id=ist.source_id
                JOIN tags t ON t.id=ist.tag_id
                WHERE ist.image_id IN ({ph})
                ORDER BY s.host COLLATE NOCASE, ist.category, t.name COLLATE NOCASE
            """, chunk).fetchall()
            # Build the legacy fallback index once per chunk.  The old code
            # rescanned every legacy row for every item without provenance,
            # turning page enrichment into O(items * legacy_rows) Python work
            # while holding the GIL.  Grouping once keeps the fallback linear.
            legacy_by_image = defaultdict(list)
            for r in legacy_rows:
                legacy_by_image[int(r["image_id"])].append(r)

            has_provenance = set()
            known = {}                      # tag -> the category the library knows it by (v5.44.11)
            for r in source_rows:
                item = by_id.get(int(r["image_id"]))
                if item is None:
                    continue
                cat = str(r["category"] or "general")
                library_cat = str(r["known"] or "general").lower()
                if library_cat != "general":
                    known[normalize_tag(r["name"])] = library_cat
                if should_hide_tag(r["name"], cat, settings):
                    continue
                host = str(r["host"] or "").lower().replace("www.", "")
                has_provenance.add(int(r["image_id"]))
                item.setdefault("tag_groups_by_source", {}).setdefault(host, {}).setdefault(cat, []).append(r["name"])
            for item in (by_id[x] for x in chunk):
                if int(item["id"]) in has_provenance:
                    # Keep tag membership separated per source while allowing a
                    # more informative category from another confirmed source to
                    # promote only this source's existing general tag.
                    item["tag_groups_raw_by_source"] = item.get("tag_groups_by_source", {})
                    item["tag_groups_by_source"] = _effective_source_groups(item["tag_groups_raw_by_source"], known)
                    groups = _merged_source_groups(item.get("tag_groups_by_source", {}), selected_source)
                elif selected_source == "all":
                    groups = defaultdict(list)
                    for r in legacy_by_image.get(int(item["id"]), ()):
                        cat = str(r["category"] or "general")
                        if not should_hide_tag(r["name"], cat, settings):
                            groups[cat].append(r["name"])
                    groups = dict(groups)
                else:
                    groups = {}
                item["tag_groups"] = groups
                item["tags"] = [tag for values in groups.values() for tag in values]
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
                item["source_hosts"] = sorted({src.get("host", "") for src in item.get("sources", []) if src.get("host")})
    return items


@_measure("sql.gallery.source_counts")
def counts(settings):
    with db(settings, readonly=True) as con:
        tc = {}
        for r in con.execute("""
            SELECT t.name, t.category, COUNT(*) c FROM tags t
            JOIN image_tags it ON it.tag_id=t.id
            JOIN images i ON i.id=it.image_id
            WHERE i.deleted=0
            GROUP BY t.id ORDER BY c DESC, t.name COLLATE NOCASE
        """).fetchall():
            if not should_hide_tag(r["name"], r["category"] or "general", settings):
                tc[r["name"]] = int(r["c"])
        sc = {r["host"]: int(r["c"]) for r in con.execute("""
            SELECT s.host, COUNT(DISTINCT i.id) c FROM sources s
            JOIN image_sources isrc ON isrc.source_id=s.id
            JOIN images i ON i.id=isrc.image_id
            WHERE i.deleted=0
            GROUP BY s.host ORDER BY s.host COLLATE NOCASE
        """)}
    return tc, sc, {}


@_measure("sql.gallery.unique_source_count")
def source_unique_image_count(settings):
    """Number of unique non-deleted gallery files with at least one source."""
    with db(settings, readonly=True) as con:
        row = con.execute("""
            SELECT COUNT(DISTINCT i.id) AS c
            FROM images i
            JOIN image_sources isrc ON isrc.image_id=i.id
            WHERE i.deleted=0
        """).fetchone()
    return int((row["c"] if row else 0) or 0)


@_measure("sql.tags.group_counts")
def tag_group_counts(settings, source="all"):
    """Return visible tag counters with monotonic cross-source categories.

    Tags remain members only of the sources which returned them.  For the same
    image/tag pair, a source-specific ``general`` classification may be
    promoted to a confirmed non-general classification returned by another
    source.  Non-general categories are never demoted to ``general``.
    """
    selected = str(source or "all").lower().replace("www.", "")
    out = defaultdict(Counter)
    rank_case = """CASE LOWER(COALESCE(NULLIF(ist.category, ''), 'general'))
        WHEN 'artist' THEN 0 WHEN 'contributor' THEN 1 WHEN 'character' THEN 2
        WHEN 'copyright' THEN 3 WHEN 'species' THEN 4 WHEN 'meta' THEN 5
        WHEN 'lore' THEN 6 WHEN 'invalid' THEN 7 WHEN 'parody' THEN 8
        WHEN 'language' THEN 9 WHEN 'category' THEN 10 WHEN 'pages' THEN 11
        WHEN 'general' THEN 99 ELSE 50 END"""
    with db(settings, readonly=True) as con:
        # v404: after migration v23 the all-source facet path can use a compact
        # materialized cache instead of the heavy ranked CTE over all provenance
        # rows.  Source-specific facets keep the old exact path.
        try:
            marker_row = con.execute("SELECT value FROM meta WHERE key='effective_tag_category_complete'").fetchone()
            materialized_backend = str(marker_row["value"] or "") if marker_row else ""
        except Exception:
            materialized_backend = ""
        if selected == "all" and materialized_backend in {"1", "external"}:
            table_name = "image_effective_tag_category"
            attached = None
            try:
                if materialized_backend == "external":
                    from core.derived_cache_db import attach_external_cache
                    attached = attach_external_cache(
                        con, settings, "image_effective_tag_category",
                        alias="derived_effective", require_fresh=True,
                    )
                    attached.__enter__()
                    table_name = "derived_effective.image_effective_tag_category"
                mat_count = int(con.execute(f"SELECT COUNT(*) AS c FROM {table_name}").fetchone()["c"] or 0)
                if mat_count:
                    rows = con.execute(f"""
                        SELECT etc.category AS category, t.normalized_name AS normalized_name,
                               MIN(t.name) AS name, COUNT(DISTINCT etc.image_id) AS c
                        FROM {table_name} etc
                        JOIN tags t ON t.id=etc.tag_id
                        JOIN images i ON i.id=etc.image_id
                        WHERE i.deleted=0
                        GROUP BY etc.category, t.normalized_name
                        ORDER BY etc.category, c DESC, name COLLATE NOCASE
                    """).fetchall()
                    for row in rows:
                        norm = normalize_tag(row["normalized_name"] or row["name"])
                        cat = str(row["category"] or "general")
                        if norm and not should_hide_tag(row["name"], cat, settings):
                            out[cat][norm] += int(row["c"] or 0)
                    return out
            except Exception:
                # Missing/stale/corrupt external cache is disposable.  Fall
                # through to the exact ranked query over primary tables.
                pass
            finally:
                if attached is not None:
                    try:
                        attached.__exit__(None, None, None)
                    except Exception:
                        pass

        provenance_count = int(con.execute("SELECT COUNT(*) AS c FROM image_source_tags").fetchone()["c"] or 0)
        if provenance_count:
            scope_join = ""
            scope_where = ""
            params = []
            if selected != "all":
                scope_join = " JOIN sources scope_source ON scope_source.id=ist.source_id"
                scope_where = " WHERE LOWER(REPLACE(scope_source.host, 'www.', ''))=?"
                params.append(selected)
            rows = con.execute(f"""
                WITH ranked_all AS (
                    SELECT ist.image_id, ist.tag_id,
                           LOWER(COALESCE(NULLIF(ist.category, ''), 'general')) AS category,
                           {rank_case} AS category_rank,
                           ROW_NUMBER() OVER (
                               PARTITION BY ist.image_id, ist.tag_id
                               ORDER BY {rank_case}, ist.source_id
                           ) AS rn
                    FROM image_source_tags ist
                ),
                best_all AS (
                    SELECT image_id, tag_id, category, category_rank
                    FROM ranked_all WHERE rn=1
                ),
                ranked_scope AS (
                    SELECT ist.image_id, ist.tag_id,
                           LOWER(COALESCE(NULLIF(ist.category, ''), 'general')) AS category,
                           {rank_case} AS category_rank,
                           ROW_NUMBER() OVER (
                               PARTITION BY ist.image_id, ist.tag_id
                               ORDER BY {rank_case}, ist.source_id
                           ) AS rn
                    FROM image_source_tags ist{scope_join}{scope_where}
                ),
                own AS (
                    SELECT image_id, tag_id, category, category_rank
                    FROM ranked_scope WHERE rn=1
                ),
                effective AS (
                    SELECT own.image_id, own.tag_id,
                           CASE
                               WHEN own.category='general' AND best_all.category<>'general' THEN best_all.category
                               ELSE own.category
                           END AS category
                    FROM own JOIN best_all
                      ON best_all.image_id=own.image_id AND best_all.tag_id=own.tag_id
                )
                SELECT effective.category AS category, t.normalized_name AS normalized_name,
                       MIN(t.name) AS name, COUNT(DISTINCT effective.image_id) AS c
                FROM effective
                JOIN tags t ON t.id=effective.tag_id
                JOIN images i ON i.id=effective.image_id
                WHERE i.deleted=0
                GROUP BY effective.category, t.normalized_name
                ORDER BY effective.category, c DESC, name COLLATE NOCASE
            """, params).fetchall()
            for row in rows:
                norm = normalize_tag(row["normalized_name"] or row["name"])
                cat = str(row["category"] or "general")
                if norm and not should_hide_tag(row["name"], cat, settings):
                    out[cat][norm] += int(row["c"] or 0)
            return out
        if selected != "all":
            return out
        rows = con.execute("""
            SELECT COALESCE(NULLIF(t.category, ''), 'general') AS category,
                   t.name AS name, COUNT(it.image_id) AS c
            FROM tags t
            JOIN image_tags it ON it.tag_id = t.id
            JOIN images i ON i.id = it.image_id
            WHERE i.deleted=0
            GROUP BY t.id
            ORDER BY category, c DESC, name COLLATE NOCASE
        """).fetchall()
    for row in rows:
        name = normalize_tag(row["name"])
        category = str(row["category"] or "general")
        if name and not should_hide_tag(row["name"], category, settings):
            out[category][name] = int(row["c"] or 0)
    return out


@_measure("sql.tags.candidates")
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
        out = []
        for r in con.execute(sql, args).fetchall():
            tag = normalize_tag(r["name"])
            if tag and not should_hide_tag(r["name"], "general", settings):
                out.append(tag)
        return out


@_measure("sql.sources.candidates")
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
        SELECT DISTINCT i.id, i.path, i.file_name, i.bucket, i.size_bytes FROM images i
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
    # Deletion UI works on a selected candidate. Use exact matching so typing a
    # common domain cannot accidentally remove every URL containing it.
    q = str(source_text or "").strip().lower()
    where, args = _scope_where(scope)
    sql = """
        SELECT DISTINCT i.id, i.path, i.file_name, i.bucket, i.size_bytes FROM images i
        JOIN image_sources isrc ON isrc.image_id=i.id
        JOIN sources s ON s.id=isrc.source_id
        WHERE i.deleted=0 AND (LOWER(s.url) = ? OR LOWER(s.host) = ?)
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


def find_images_by_buckets(settings, buckets, limit=None):
    """Return live indexed files in exact output buckets for reliable cleanup."""
    values = [str(b) for b in (buckets or []) if str(b)]
    if not values:
        return []
    ph = ",".join(["?"] * len(values))
    sql = f"SELECT DISTINCT id, path, file_name, bucket, size_bytes FROM images WHERE deleted=0 AND bucket IN ({ph}) ORDER BY path COLLATE NOCASE"
    params = list(values)
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    with db(settings, readonly=True) as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def delete_images(settings, image_rows, delete_files=True, reason="delete", tag_or_source=""):
    from core.deleted_registry import mark_deleted
    from core.source_protection import require_managed_media_mutation
    from core.services.media_storage_service import unlink_managed, delete_bucket_artifacts
    deleted_files = errors = 0
    rows = list(image_rows or [])
    protected_source_skipped = 0
    if delete_files:
        allowed_rows = []
        for row in rows:
            p = Path(str(row.get("path") or ""))
            if require_managed_media_mutation(settings, p, "repository.delete_images"):
                allowed_rows.append(row)
            else:
                protected_source_skipped += 1
        rows = allowed_rows
    ids = [int(r["id"]) for r in rows if r.get("id") is not None]
    paths = [Path(r["path"]) for r in rows if r.get("path")]

    if delete_files:
        for p in paths:
            try:
                if p.exists() and p.is_file():
                    try:
                        mark_deleted(p, reason=reason, settings=settings, manual_delete=True)
                    except Exception:
                        pass
                    if unlink_managed(settings, p, operation="repository.delete_images"):
                        deleted_files += 1
            except Exception:
                errors += 1
            delete_bucket_artifacts(settings, p, operation="repository.delete_side_artifacts")

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
    return {"deleted_files": deleted_files, "errors": errors, "deleted_records": len(ids), "protected_source_skipped": protected_source_skipped}


def cleanup_orphans(con):
    """Drop tags and sources that no picture uses any more.

    A tag still used by a site tag set (image_source_tags) or a source still used by a
    tag set or a source role is not an orphan: deleting it would cascade-delete that
    provenance through ON DELETE CASCADE (docs/map, trap 6).
    """
    con.execute("""
        DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM image_tags)
                           AND id NOT IN (SELECT tag_id FROM image_source_tags)
    """)
    con.execute("""
        DELETE FROM sources WHERE id NOT IN (SELECT source_id FROM image_sources)
                              AND id NOT IN (SELECT source_id FROM image_source_tags)
                              AND id NOT IN (SELECT source_id FROM image_source_roles)
    """)


