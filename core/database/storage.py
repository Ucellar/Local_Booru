
from __future__ import annotations

from pathlib import Path
import json
import time
import re
from typing import Iterable
from urllib.parse import urlparse, parse_qs

from .connection import db
from core.media_utils import (
    VIDEO_EXTS,
    bucket_for_path,
    image_size,
    safe_stat,
    host_from_url,
)
from core.tag_utils import normalize_tag
from core.stability import swallow


_META_TAG_OVERRIDES = {"artist_request"}

def _category_for_tag(name: str, category: str) -> str:
    """Apply Local Booru presentation rules to technical booru tags."""
    norm = normalize_tag(str(name or ""))
    if norm in _META_TAG_OVERRIDES:
        return "meta"
    return str(category or "general")


def _has_categories(groups) -> bool:
    """The tags came with categories: at least one is not «general» (a site that sends none gives every tag so)."""
    return any(str(cat or "general").lower() != "general" and values for cat, values in (groups or {}).items())


def _tag_written(con, tag_id: int, norm: str, category: str, known: str, *, categorized: bool) -> None:
    """A write gave a tag a category: the library's category of the tag follows what most sites say
    (``tag_consensus.written``, v5.44.49) — it was the first category other than «general» anyone gave, for good."""
    if norm in _META_TAG_OVERRIDES:
        if str(known or "general").lower() != "meta":
            con.execute("UPDATE tags SET category='meta' WHERE id=?", (int(tag_id),))
        return
    from .tag_consensus import written
    written(con, int(tag_id), category, known, categorized=categorized)



_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def _safe_ident(name: str) -> str:
    name = str(name or "")
    if not _IDENT_RE.fullmatch(name):
        raise ValueError(f"unsafe SQLite identifier: {name!r}")
    return name

def _has_column(con, table: str, column: str) -> bool:
    try:
        table = _safe_ident(table)
        return column in {str(r[1]) for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return False

# The same bytes are back at the place of a «файл пропал» card (an unknown MD5 on the card counts as same).
_MISSING_FILE_BACK = ("images.lifecycle='missing' AND (COALESCE(images.hash_md5,'')='' "
                      "OR lower(images.hash_md5)=lower(COALESCE(excluded.hash_md5,'')))")


def _ensure_image(con, media_path, status="", original_path="", hash_md5=None, lifecycle=None, inbox_until=0, import_origin=""):
    """Ensure row; lifecycle is only changed when the caller explicitly supplies it."""
    p = Path(media_path)
    size, mtime_ns = safe_stat(p)
    width, height = image_size(p) if p.exists() else (0, 0)
    bucket = bucket_for_path(p)
    initial_lifecycle = str(lifecycle or "archive")
    # A video's length (the gallery leaves the long ones to the tab «Видео»): read with its size from its headers.
    duration = 0.0
    if p.suffix.lower() in VIDEO_EXTS or p.suffix.lower() == ".m4v":
        from core.video_probe import duration_for_db
        duration = duration_for_db(p) if p.exists() else 0.0
    con.execute("""
        INSERT INTO images(path, file_name, bucket, size_bytes, width, height, hash_md5, mtime_ns, is_video, indexed_at, lifecycle, inbox_until, import_origin, duration)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(path) DO UPDATE SET
            file_name=excluded.file_name,
            bucket=excluded.bucket,
            size_bytes=excluded.size_bytes,
            width=excluded.width,
            height=excluded.height,
            hash_md5=COALESCE(excluded.hash_md5, images.hash_md5),
            mtime_ns=excluded.mtime_ns,
            is_video=excluded.is_video,
            -- a length read now wins; one that could not be read now (the file busy for a moment) keeps the known one
            duration=CASE WHEN excluded.duration>0 OR COALESCE(images.duration,0)<=0 THEN excluded.duration
                          ELSE images.duration END,
            indexed_at=excluded.indexed_at,
            -- a card marked «файл пропал» whose very file is written back is a live card again
            deleted=CASE WHEN {back} THEN 0 ELSE images.deleted END,
            trashed_at=CASE WHEN {back} THEN 0 ELSE images.trashed_at END,
            original_media_path=CASE WHEN {back} THEN '' ELSE images.original_media_path END,
            lifecycle=CASE WHEN {back} THEN 'archive' ELSE images.lifecycle END
    """.format(back=_MISSING_FILE_BACK), (str(p), p.name, bucket, size, width, height, hash_md5, mtime_ns, int(p.suffix.lower() in VIDEO_EXTS), int(time.time()), initial_lifecycle, int(inbox_until or 0), str(import_origin or ""), duration))
    image_id = int(con.execute("SELECT id FROM images WHERE path=?", (str(p),)).fetchone()["id"])
    with swallow("database.storage.ensure_image_original_name"):
        if _has_column(con, "images", "original_file_name"):
            original_name = Path(original_path).name if original_path else p.name
            con.execute(
                "UPDATE images SET original_file_name=CASE WHEN COALESCE(original_file_name,'')='' THEN ? ELSE original_file_name END, content_name_policy=CASE WHEN COALESCE(content_name_policy,'')='' AND COALESCE(hash_md5,'')<>'' AND file_name LIKE '%__%' THEN 'md5_suffix' ELSE COALESCE(content_name_policy,'') END WHERE id=?",
                (original_name, image_id),
            )
    if lifecycle is not None:
        con.execute("UPDATE images SET lifecycle=?, inbox_until=?, import_origin=CASE WHEN ?<>'' THEN ? ELSE import_origin END, deleted=0 WHERE id=?", (str(lifecycle), int(inbox_until or 0), str(import_origin or ""), str(import_origin or ""), image_id))
    if original_path or status:
        op = Path(original_path) if original_path else p
        # An import without a source file (grabber, browser) never erases the source recorded earlier.
        con.execute("""
            INSERT INTO processed_files(original_path, original_name, media_path, status, bucket, processed_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(media_path) DO UPDATE SET
                original_path=CASE WHEN excluded.original_path<>'' THEN excluded.original_path ELSE processed_files.original_path END,
                original_name=CASE WHEN excluded.original_path<>'' THEN excluded.original_name ELSE processed_files.original_name END,
                status=excluded.status,
                bucket=excluded.bucket,
                processed_at=excluded.processed_at
        """, (str(original_path or ""), op.name, str(p), status or "", bucket, int(time.time())))
    if original_path:
        remember_source_original(con, original_path, image_id=image_id, media_path=p, status=status or bucket, md5=hash_md5)
    return image_id


def rebind_source_originals(con, old_path, new_path, *, new_md5: str = "") -> None:
    """A card's file moved (relocation, rename, quality upgrade): its source files follow it."""
    con.execute(
        "UPDATE source_originals SET media_path=?, md5=COALESCE(NULLIF(?,''), md5) WHERE media_path=?",
        (str(new_path), str(new_md5 or "").lower(), str(old_path)),
    )


def remember_source_original(con, original_path, *, image_id: int, media_path, status: str, md5=None) -> None:
    """One row per source file: which library card it became and with what result."""
    value = str(md5 or "").strip().lower()
    if not value:
        row = con.execute("SELECT hash_md5 FROM images WHERE id=?", (int(image_id),)).fetchone()
        value = str((row["hash_md5"] if row else "") or "").lower()
    con.execute(
        """INSERT INTO source_originals(original_path, image_id, md5, status, media_path, processed_at)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(original_path) DO UPDATE SET
               image_id=excluded.image_id,
               md5=CASE WHEN excluded.md5<>'' THEN excluded.md5 ELSE source_originals.md5 END,
               status=excluded.status, media_path=excluded.media_path, processed_at=excluded.processed_at""",
        (str(Path(original_path)), int(image_id), value, str(status or ""), str(media_path), int(time.time())),
    )


def add_image_tags(con, image_id, groups):
    """Add tag links without dropping tags discovered by another source."""
    groups = groups or {}
    if isinstance(groups, list):
        groups = {"general": groups}
    categorized = _has_categories(groups)
    for category, tags in groups.items():
        for raw in tags or []:
            name = normalize_tag(str(raw))
            if not name:
                continue
            cat = _category_for_tag(name, category)
            norm = normalize_tag(name)
            con.execute("INSERT OR IGNORE INTO tags(name, normalized_name, category) VALUES(?,?,?)", (name, norm, cat))
            row = con.execute("SELECT id, category FROM tags WHERE normalized_name=?", (norm,)).fetchone()
            if not row:
                continue
            _tag_written(con, int(row["id"]), norm, cat, row["category"], categorized=categorized)
            con.execute("INSERT OR IGNORE INTO image_tags(image_id, tag_id) VALUES(?,?)", (image_id, int(row["id"])))


def replace_image_tags(con, image_id, groups):
    con.execute("DELETE FROM image_tags WHERE image_id=?", (image_id,))
    add_image_tags(con, image_id, groups)
    refresh_effective_tag_categories(con, image_id)


def _is_navigational_source_url(url: str) -> bool:
    """Reject gallery/search/navigation URLs that are not evidence for one post.

    Reverse services occasionally emit Gelbooru list links such as
    ``page=post&s=list&tags=all`` alongside a real result on another site.
    They must never become image sources because opening them shows the site
    gallery rather than the confirmed file.  This guard is deliberately narrow:
    concrete post URLs and ordinary external/file source URLs stay valid.
    """
    try:
        parsed = urlparse(str(url or ""))
        path = str(parsed.path or "").lower()
        query = {str(k).lower(): [str(v).lower() for v in vals] for k, vals in parse_qs(parsed.query).items()}
        full = (path + "?" + str(parsed.query or "")).lower()
        if "/posts/random" in full or "/post/random" in full:
            return True
        page = (query.get("page") or [""])[0]
        action = (query.get("s") or [""])[0]
        if page == "post" and action == "list":
            return True
        return False
    except Exception:
        return False




_SOURCE_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def _clean_source_url(value: object) -> str:
    url = str(value or "").strip().rstrip(".,;:)]}>'\"")
    if not url.startswith(("http://", "https://")) or _is_navigational_source_url(url):
        return ""
    return url


def _iter_metadata_source_urls(source_text: str, raw) -> Iterable[tuple[str, str]]:
    """Yield source URLs with provenance without inventing relationships.

    Only explicit URL-bearing fields and URLs already present in source_text are
    considered.  This deliberately avoids semantic guesses from titles/captions.
    """
    seen: set[str] = set()
    for match in _SOURCE_URL_RE.finditer(str(source_text or "")):
        url = _clean_source_url(match.group(0))
        if url and url not in seen:
            seen.add(url)
            yield url, "source_text"
    if not isinstance(raw, dict):
        return
    keys = (
        "source", "source_url", "source_urls", "original_source", "original_url",
        "original_urls", "pixiv_url", "twitter_url", "artist_url", "profile_url",
    )
    for key in keys:
        value = raw.get(key)
        values = value if isinstance(value, (list, tuple, set)) else [value]
        for item in values:
            if isinstance(item, dict):
                item = item.get("url") or item.get("source") or ""
            url = _clean_source_url(item)
            if url and url not in seen:
                seen.add(url)
                yield url, f"raw:{key}"


def _typed_role_for_url(url: str, *, post_url: str = "", file_url: str = "", site: str = "") -> tuple[str, bool]:
    clean = _clean_source_url(url)
    if not clean:
        return "", False
    from core.typed_sources import classify_source_role
    return classify_source_role(clean, post_url=post_url, file_url=file_url, site=site)


def _sync_typed_sources(con, *, image_id: int, source_text: str, raw, post_url: str, file_url: str, site: str) -> None:
    """Attach source semantics to the existing additive source history.

    The legacy sources/image_sources rows remain authoritative for compatibility;
    this function only adds roles.  Multiple original candidates are preserved,
    while the first explicit non-mirror original becomes preferred.
    """
    from core.typed_sources import add_typed_source

    candidates: list[tuple[str, str]] = []
    if _clean_source_url(post_url):
        candidates.append((_clean_source_url(post_url), "post_url"))
    if _clean_source_url(file_url):
        candidates.append((_clean_source_url(file_url), "file_url"))
    candidates.extend(_iter_metadata_source_urls(source_text, raw))
    seen: set[tuple[str, str]] = set()
    preferred_original_set = bool(con.execute(
        "SELECT 1 FROM image_source_roles WHERE image_id=? AND role='original_source' AND is_preferred=1 LIMIT 1",
        (int(image_id),),
    ).fetchone())
    for url, provenance in candidates:
        role, preferred = _typed_role_for_url(url, post_url=post_url, file_url=file_url, site=site)
        if not role or (url, role) in seen:
            continue
        seen.add((url, role))
        if role == "original_source":
            preferred = bool(preferred and not preferred_original_set)
            preferred_original_set = preferred_original_set or preferred
        add_typed_source(
            con, image_id=int(image_id), url=url, role=role, preferred=preferred,
            provenance=f"metadata:{provenance}",
        )


def _ensure_source(con, url: str):
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    if _is_navigational_source_url(url):
        return None
    host = host_from_url(url)
    if not host:
        return None
    con.execute("INSERT OR IGNORE INTO sources(host, url) VALUES(?,?)", (host, url))
    row = con.execute("SELECT id FROM sources WHERE host=? AND url=?", (host, url)).fetchone()
    return (int(row["id"]), host, url) if row else None


def add_image_sources(con, image_id, source_text="", extra_sources=None):
    """Add source links without dropping existing source history; return linked IDs."""
    urls = []
    for line in str(source_text or "").splitlines():
        for part in line.split():
            if part.startswith(("http://", "https://")):
                urls.append(part.strip())
    for u in extra_sources or []:
        if u:
            urls.append(str(u))
    seen = set()
    linked = []
    for url in urls:
        item = _ensure_source(con, url)
        if not item:
            continue
        source_id, host, url = item
        key = (host, url)
        if key in seen:
            continue
        seen.add(key)
        con.execute("INSERT OR IGNORE INTO image_sources(image_id, source_id) VALUES(?,?)", (image_id, source_id))
        linked.append({"source_id": source_id, "host": host, "url": url})
    return linked


def _iter_source_tag_entries(source_tag_groups):
    for entry in source_tag_groups or []:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        groups = entry.get("groups") or {}
        tags = entry.get("tags") or []
        if not groups and tags:
            groups = {"general": list(tags)}
        if url and groups:
            yield url, groups, str(entry.get("method") or "")


def _sync_merged_image_tags(con, image_id: int, dropped=()) -> None:
    """Keep the search index ``image_tags`` = union of the card's site sets + tags of no site.

    Tags that belong to no site set (old library tags, manual tags) stay.  A tag leaves
    ``image_tags`` only when it was dropped from a site set (``dropped``) and no other set of
    this card still has it.  (Rebuilding the index from the sets alone erased those tags.)
    """
    image_id = int(image_id)
    con.execute("""
        INSERT OR IGNORE INTO image_tags(image_id, tag_id)
        SELECT image_id, tag_id FROM image_source_tags WHERE image_id=?
    """, (image_id,))
    for tag_id in {int(t) for t in dropped or ()}:
        if not con.execute("SELECT 1 FROM image_source_tags WHERE image_id=? AND tag_id=? LIMIT 1",
                           (image_id, tag_id)).fetchone():
            con.execute("DELETE FROM image_tags WHERE image_id=? AND tag_id=?", (image_id, tag_id))


def refresh_effective_tag_categories(con, image_id: int | None = None) -> None:
    """Refresh the selected effective-category backend.

    External generation files are immutable and are rebuilt by maintenance.
    Source writes only advance the durable revision trigger, so this function
    must not recreate a second live copy inside main.db when external storage is
    selected.
    """
    try:
        from core.derived_cache_db import main_cache_writes_enabled
        if not main_cache_writes_enabled(con, "image_effective_tag_category"):
            return
    except Exception:
        pass
    try:
        con.execute("SELECT 1 FROM image_effective_tag_category LIMIT 1")
    except Exception:
        return
    now = int(time.time())
    params = []
    where = ""
    if image_id is not None:
        image_id = int(image_id)
        con.execute("DELETE FROM image_effective_tag_category WHERE image_id=?", (image_id,))
        where = "WHERE ist.image_id=?"
        params.append(image_id)
    else:
        con.execute("DELETE FROM image_effective_tag_category")
    try:
        con.execute(f"""
            INSERT OR REPLACE INTO image_effective_tag_category(image_id, tag_id, category, updated_at)
            WITH ranked AS (
                SELECT ist.image_id, ist.tag_id,
                       LOWER(COALESCE(NULLIF(ist.category, ''), 'general')) AS category,
                       ROW_NUMBER() OVER (
                           PARTITION BY ist.image_id, ist.tag_id
                           ORDER BY CASE LOWER(COALESCE(NULLIF(ist.category, ''), 'general'))
                               WHEN 'artist' THEN 0 WHEN 'contributor' THEN 1 WHEN 'character' THEN 2
                               WHEN 'copyright' THEN 3 WHEN 'species' THEN 4 WHEN 'meta' THEN 5
                               WHEN 'lore' THEN 6 WHEN 'invalid' THEN 7 WHEN 'parody' THEN 8
                               WHEN 'language' THEN 9 WHEN 'category' THEN 10 WHEN 'pages' THEN 11
                               WHEN 'general' THEN 99 ELSE 50 END,
                               ist.source_id
                       ) AS rn
                FROM image_source_tags ist {where}
            )
            SELECT image_id, tag_id, category, ? FROM ranked WHERE rn=1
        """, [*params, now])
    except Exception:
        return
    # Legacy no-provenance links remain visible.
    with swallow("database.storage.refresh_effective_legacy_links"):
        if image_id is not None:
            con.execute("""
                INSERT OR IGNORE INTO image_effective_tag_category(image_id, tag_id, category, updated_at)
                SELECT it.image_id, it.tag_id, LOWER(COALESCE(NULLIF(t.category, ''), 'general')), ?
                FROM image_tags it JOIN tags t ON t.id=it.tag_id WHERE it.image_id=?
            """, (now, image_id))
        else:
            con.execute("""
                INSERT OR IGNORE INTO image_effective_tag_category(image_id, tag_id, category, updated_at)
                SELECT it.image_id, it.tag_id, LOWER(COALESCE(NULLIF(t.category, ''), 'general')), ?
                FROM image_tags it JOIN tags t ON t.id=it.tag_id
            """, (now,))


def add_image_source_tag_groups(con, image_id, source_tag_groups, *, replace_sources=False):
    """Store one independent confirmed tag set per site/host.

    A site's newest confirmed lookup replaces that site's previous tag set for
    this image.  Different sites remain independent and their deduplicated union
    is mirrored in ``image_tags`` for fast ordinary search.
    """
    now = int(time.time())
    stored = 0
    touched_hosts = set()
    dropped = set()
    for url, groups, method in _iter_source_tag_entries(source_tag_groups):
        source = _ensure_source(con, url)
        if not source:
            continue
        source_id, host, _url = source
        con.execute("INSERT OR IGNORE INTO image_sources(image_id, source_id) VALUES(?,?)", (image_id, source_id))
        # Tags belong to the site, not to one historical result URL. If a later
        # exact MD5 match supersedes an earlier IQDB candidate on the same host,
        # never combine the two posts' metadata.  If a buggy caller submits two
        # candidates for one host in one transaction, the last confirmed set wins.
        # (The card's own sets of this host — not every source of the host: ``source_id IN (SELECT id FROM sources
        # WHERE host=?)`` listed all of a site's posts in the library on every save.)
        same_host = [int(r[0]) for r in con.execute(
            "SELECT DISTINCT ist.source_id FROM image_source_tags ist JOIN sources s ON s.id=ist.source_id "
            "WHERE ist.image_id=? AND s.host=?", (image_id, host))]
        if same_host:
            marks = ",".join("?" * len(same_host))
            dropped.update(int(r[0]) for r in con.execute(
                f"SELECT tag_id FROM image_source_tags WHERE image_id=? AND source_id IN ({marks})",
                (image_id, *same_host)))
            con.execute(f"DELETE FROM image_source_tags WHERE image_id=? AND source_id IN ({marks})",
                        (image_id, *same_host))
        touched_hosts.add(host)
        categorized = _has_categories(groups)
        for category, values in (groups or {}).items():
            for raw in values or []:
                name = normalize_tag(str(raw))
                if not name:
                    continue
                cat = _category_for_tag(name, category)
                norm = normalize_tag(name)
                con.execute("INSERT OR IGNORE INTO tags(name, normalized_name, category) VALUES(?,?,?)", (name, norm, cat))
                row = con.execute("SELECT id, category FROM tags WHERE normalized_name=?", (norm,)).fetchone()
                if not row:
                    continue
                tag_id = int(row["id"])
                _tag_written(con, tag_id, norm, cat, row["category"], categorized=categorized)
                con.execute("""
                    INSERT INTO image_source_tags(image_id, source_id, tag_id, category, acquisition, updated_at)
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(image_id, source_id, tag_id) DO UPDATE SET
                        category=excluded.category,
                        acquisition=CASE WHEN excluded.acquisition<>'' THEN excluded.acquisition ELSE image_source_tags.acquisition END,
                        updated_at=excluded.updated_at
                """, (image_id, source_id, tag_id, cat, method, now))
                stored += 1
    if touched_hosts:
        _sync_merged_image_tags(con, image_id, dropped)
        refresh_effective_tag_categories(con, image_id)
        drop_unused_tags(con, dropped)
    return stored


def drop_unused_tags(con, tag_ids) -> None:
    """The tags of ``tag_ids`` that no card uses any more (no picture, no site set) — only these, by their indexes.
    (The whole-table ``repository.cleanup_orphans`` read every card's tags: on a big library — on every save.)"""
    ids = sorted({int(t) for t in tag_ids or ()})
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        con.execute(
            f"DELETE FROM tags WHERE id IN ({','.join('?' * len(part))}) "
            "AND NOT EXISTS (SELECT 1 FROM image_tags it WHERE it.tag_id=tags.id) "
            "AND NOT EXISTS (SELECT 1 FROM image_source_tags ist WHERE ist.tag_id=tags.id)", part)


def drop_unused_sources(con, source_ids) -> None:
    """The sources of ``source_ids`` that no card uses any more (no link, no site set, no role) — only these."""
    ids = sorted({int(s) for s in source_ids or ()})
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        con.execute(
            f"DELETE FROM sources WHERE id IN ({','.join('?' * len(part))}) "
            "AND NOT EXISTS (SELECT 1 FROM image_sources x WHERE x.source_id=sources.id) "
            "AND NOT EXISTS (SELECT 1 FROM image_source_tags x WHERE x.source_id=sources.id) "
            "AND NOT EXISTS (SELECT 1 FROM image_source_roles x WHERE x.source_id=sources.id)", part)


def merge_card_metadata(con, keep_id: int, other_id: int) -> None:
    """Move what is known about card ``other_id`` onto ``keep_id`` (duplicate merge, restore).

    One tag set per site: a site set of the other card is copied only when the kept card has no
    set of that site.  Source links, site-MD5 aliases, tags of no site, rating and favorite are kept.
    The search index and effective categories of the kept card are rebuilt.
    """
    keep_id, other_id = int(keep_id), int(other_id)
    if keep_id == other_id:
        return
    def host_key(sql_host: str) -> str:
        return f"REPLACE(LOWER({sql_host}),'www.','')"
    con.execute("INSERT OR IGNORE INTO image_sources(image_id,source_id) SELECT ?,source_id FROM image_sources WHERE image_id=?",
                (keep_id, other_id))
    con.execute(f"""
        INSERT OR IGNORE INTO image_source_tags(image_id,source_id,tag_id,category,acquisition,updated_at)
        SELECT ?, x.source_id, x.tag_id, x.category, x.acquisition, x.updated_at
        FROM image_source_tags x JOIN sources s ON s.id=x.source_id
        WHERE x.image_id=? AND {host_key('s.host')} NOT IN (
            SELECT {host_key('s2.host')} FROM image_source_tags y JOIN sources s2 ON s2.id=y.source_id WHERE y.image_id=?)
    """, (keep_id, other_id, keep_id))
    # Tags of the other card that belong to none of its site sets (old library tags, manual tags).
    con.execute("""
        INSERT OR IGNORE INTO image_tags(image_id,tag_id)
        SELECT ?, it.tag_id FROM image_tags it
        WHERE it.image_id=? AND it.tag_id NOT IN (SELECT tag_id FROM image_source_tags WHERE image_id=?)
    """, (keep_id, other_id, other_id))
    _sync_merged_image_tags(con, keep_id)
    with swallow("database.storage.merge_card_metadata.aliases"):
        con.execute("UPDATE OR IGNORE image_site_md5_aliases SET image_id=? WHERE image_id=?", (keep_id, other_id))
    score = con.execute("SELECT rating,favorite FROM images WHERE id=?", (other_id,)).fetchone()
    if score:
        con.execute("UPDATE images SET rating=MAX(COALESCE(rating,0),?), favorite=MAX(COALESCE(favorite,0),?) WHERE id=?",
                    (int(score["rating"] or 0), int(score["favorite"] or 0), keep_id))
    with swallow("database.storage.merge_card_metadata.sets"):
        # what the sites said about the other card's posts, its place in a сборка (when the kept card has none) and
        # «never put into a сборка by itself» (core/card_sets.py)
        con.execute("INSERT OR IGNORE INTO site_posts(image_id,site,post_id,parent_id,uploader,source,state,checked_at) "
                    "SELECT ?,site,post_id,parent_id,uploader,source,state,checked_at FROM site_posts WHERE image_id=?",
                    (keep_id, other_id))
        if not con.execute("SELECT 1 FROM card_set_members WHERE image_id=?", (keep_id,)).fetchone():
            con.execute("UPDATE card_set_members SET image_id=? WHERE image_id=?", (keep_id, other_id))
        con.execute("INSERT OR IGNORE INTO card_set_optout(image_id,created_at) "
                    "SELECT ?,created_at FROM card_set_optout WHERE image_id=?", (keep_id, other_id))
    refresh_effective_tag_categories(con, keep_id)


def replace_image_sources(con, image_id, source_text="", extra_sources=None):
    con.execute("DELETE FROM image_sources WHERE image_id=?", (image_id,))
    con.execute("DELETE FROM image_source_tags WHERE image_id=?", (image_id,))
    add_image_sources(con, image_id, source_text, extra_sources)




def replace_media_tag_groups(settings, media_path, groups) -> bool:
    """Apply a user edit to one card using SQLite only; no sidecar files exist in live mode."""
    with db(settings, write=True) as con:
        row = con.execute("SELECT id FROM images WHERE path=? AND deleted=0", (str(Path(media_path)),)).fetchone()
        if not row:
            return False
        replace_image_tags(con, int(row["id"]), groups or {})
        _cleanup_orphan_rows(con)
    return True


def remove_media_tag_link(settings, media_path, tag_name, source_host="all") -> bool:
    """Remove one visible tag while respecting per-source metadata sets.

    In ``all`` mode the user is removing the tag from the image, so every
    source-specific link for that tag is removed.  In a source view only that
    site's copy is removed; other sites remain intact.  Legacy rows without
    provenance continue to use ``image_tags``.
    """
    path = str(Path(media_path))
    norm = normalize_tag(str(tag_name or ""))
    host = str(source_host or "all").strip().lower().replace("www.", "")
    if not norm:
        return False
    with db(settings, write=True) as con:
        image = con.execute("SELECT id FROM images WHERE path=? AND deleted=0", (path,)).fetchone()
        tag = con.execute("SELECT id FROM tags WHERE normalized_name=?", (norm,)).fetchone()
        if not image or not tag:
            return False
        image_id = int(image["id"]); tag_id = int(tag["id"])
        has_provenance = bool(con.execute("SELECT 1 FROM image_source_tags WHERE image_id=? LIMIT 1", (image_id,)).fetchone())
        if has_provenance:
            if host and host != "all":
                con.execute("""
                    DELETE FROM image_source_tags
                    WHERE image_id=? AND tag_id=? AND source_id IN (SELECT id FROM sources WHERE LOWER(REPLACE(host,'www.',''))=?)
                """, (image_id, tag_id, host))
            else:
                con.execute("DELETE FROM image_source_tags WHERE image_id=? AND tag_id=?", (image_id, tag_id))
            _sync_merged_image_tags(con, image_id, {tag_id})
            refresh_effective_tag_categories(con, image_id)
        else:
            con.execute("DELETE FROM image_tags WHERE image_id=? AND tag_id=?", (image_id, tag_id))
        _cleanup_orphan_rows(con)
    return True


def remove_media_source_link(settings, media_path, source_url="", source_host="") -> bool:
    """Remove selected source link from one live card while retaining other confirmations."""
    path = str(Path(media_path))
    url = str(source_url or "").strip()
    host = str(source_host or "").strip()
    with db(settings, write=True) as con:
        row = con.execute("SELECT id FROM images WHERE path=? AND deleted=0", (path,)).fetchone()
        if not row:
            return False
        image_id = int(row["id"])
        if url:
            source_ids = [int(r["id"]) for r in con.execute("SELECT id FROM sources WHERE url=?", (url,)).fetchall()]
        elif host:
            source_ids = [int(r["id"]) for r in con.execute("SELECT id FROM sources WHERE host=?", (host,)).fetchall()]
        else:
            return False
        dropped = set()
        for source_id in source_ids:
            dropped.update(int(r[0]) for r in con.execute(
                "SELECT tag_id FROM image_source_tags WHERE image_id=? AND source_id=?", (image_id, source_id)))
            con.execute("DELETE FROM image_source_tags WHERE image_id=? AND source_id=?", (image_id, source_id))
            con.execute("DELETE FROM image_sources WHERE image_id=? AND source_id=?", (image_id, source_id))
        _sync_merged_image_tags(con, image_id, dropped)
        refresh_effective_tag_categories(con, image_id)
        from .repository import cleanup_orphans
        cleanup_orphans(con)
    return True

def media_path_by_md5(settings, md5: str, *, preferred_bucket: str = "", require_bucket: bool = False, exclude_path: str = "") -> str:
    """Return one existing live canonical media path for an exact MD5.

    Exact byte-identical files are one library object with many sources/tags.
    ``preferred_bucket`` keeps a new FOUND result from being collapsed into an
    older NO_MATCH placeholder; callers can require that bucket when needed.
    """
    value = str(md5 or "").strip().lower()
    if not value:
        return ""
    # by the MD5's index: «+deleted» keeps the planner from walking every live card by ``deleted`` (without
    # statistics — a base never analysed — it chose that index and read the whole library on every save); MD5s are
    # kept in lower case, an old one in upper case is found too
    where = ["+deleted=0", "hash_md5 IN (?,?)"]
    params = [value, value.upper()]
    if exclude_path:
        where.append("path<>?")
        params.append(str(exclude_path))
    if preferred_bucket and require_bucket:
        where.append("+bucket=?")                # (not by the bucket's index: every card of the bucket)
        params.append(str(preferred_bucket))
    order = "indexed_at ASC, id ASC"
    if preferred_bucket and not require_bucket:
        order = "CASE WHEN bucket=? THEN 0 WHEN bucket='found' THEN 1 ELSE 2 END, indexed_at ASC, id ASC"
        params.append(str(preferred_bucket))
    with db(settings, readonly=True) as con:
        rows = con.execute(
            f"SELECT path FROM images WHERE {' AND '.join(where)} ORDER BY {order}",
            params,
        ).fetchall()
    for row in rows:
        try:
            if Path(row["path"]).exists():
                return str(row["path"])
        except Exception:
            continue
    return ""


def media_path_by_site_md5(settings, site_md5: str, *, exclude_path: str = "") -> str:
    """Return the local canonical media bound to an authoritative site MD5.

    ``images.hash_md5`` always describes the bytes stored locally.  A booru can
    publish an original-file MD5 while the browser/download path yields a
    transformed representation with another local MD5.  Migration v25 stores
    that explicit remote-to-local association here instead of corrupting the
    canonical local hash.
    """
    value = str(site_md5 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        return ""
    try:
        with db(settings, readonly=True) as con:
            params = [value]
            extra = ""
            if exclude_path:
                extra = " AND i.path<>?"
                params.append(str(exclude_path))
            rows = con.execute(
                f"""SELECT i.path
                    FROM image_site_md5_aliases a
                    JOIN images i ON i.id=a.image_id
                    WHERE lower(a.site_md5)=? AND i.deleted=0{extra}
                    ORDER BY i.indexed_at ASC, i.id ASC""",
                params,
            ).fetchall()
    except Exception:
        # Read-only test fixtures and pre-v25 databases may not have the table
        # yet.  The normal application startup migration creates it.
        return ""
    for row in rows:
        try:
            if Path(row["path"]).exists():
                return str(row["path"])
        except Exception:
            continue
    return ""


def bind_site_md5_alias(settings, media_path, site_md5: str, *, site: str = "", source_url: str = "") -> dict:
    """Bind one authoritative remote MD5 to an existing local image.

    The first live binding wins.  A later conflicting import does not silently
    reassign an exact remote hash to another live image; the caller receives the
    existing canonical path.  Bindings whose image row is deleted/orphaned or
    whose physical file is gone are reclaimed atomically.
    """
    value = str(site_md5 or "").strip().lower()
    path = str(Path(media_path)) if media_path else ""
    if not path or not re.fullmatch(r"[0-9a-f]{32}", value):
        return {"ok": False, "reason": "invalid_input", "canonical_path": ""}
    now = int(time.time())
    try:
        with db(settings, write=True) as con:
            image = con.execute(
                "SELECT id,path,lower(COALESCE(hash_md5,'')) AS local_md5 FROM images WHERE path=? AND deleted=0 LIMIT 1",
                (path,),
            ).fetchone()
            if not image:
                return {"ok": False, "reason": "image_not_found", "canonical_path": ""}
            existing = con.execute(
                """SELECT a.image_id,i.path,i.deleted
                   FROM image_site_md5_aliases a
                   LEFT JOIN images i ON i.id=a.image_id
                   WHERE lower(a.site_md5)=? LIMIT 1""",
                (value,),
            ).fetchone()
            if existing and int(existing["image_id"]) != int(image["id"]):
                existing_path = str(existing["path"] or "")
                existing_row_live = existing["deleted"] is not None and int(existing["deleted"] or 0) == 0
                try:
                    existing_file_alive = bool(
                        existing_row_live and existing_path and Path(existing_path).exists()
                    )
                except Exception:
                    # Be conservative when the filesystem cannot be inspected:
                    # a live binding must never be reassigned merely because
                    # stat() itself failed.  Deleted/orphan rows are reclaimable.
                    existing_file_alive = bool(existing_row_live)
                if existing_file_alive:
                    return {
                        "ok": False,
                        "reason": "already_bound",
                        "canonical_path": existing_path,
                        "image_id": int(existing["image_id"]),
                    }

                # The DB row is still live, but its physical canonical file was
                # removed outside Local Booru.  Reclaim the alias atomically so
                # a phantom first binding cannot block a newly downloaded live
                # canonical forever.
                con.execute(
                    """UPDATE image_site_md5_aliases
                       SET image_id=?,
                           site=CASE WHEN ?<>'' THEN ? ELSE site END,
                           source_url=CASE WHEN ?<>'' THEN ? ELSE source_url END,
                           local_md5=?,
                           observed_at=?
                       WHERE lower(site_md5)=?""",
                    (
                        int(image["id"]),
                        str(site or ""), str(site or ""),
                        str(source_url or ""), str(source_url or ""),
                        str(image["local_md5"] or ""),
                        now,
                        value,
                    ),
                )
                return {
                    "ok": True,
                    "reason": "stale_binding_reclaimed",
                    "canonical_path": str(image["path"] or path),
                    "image_id": int(image["id"]),
                    "stale_canonical_path": existing_path,
                    "stale_image_id": int(existing["image_id"]),
                }
            con.execute(
                """INSERT INTO image_site_md5_aliases(site_md5,image_id,site,source_url,local_md5,observed_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(site_md5) DO UPDATE SET
                       site=CASE WHEN excluded.site<>'' THEN excluded.site ELSE image_site_md5_aliases.site END,
                       source_url=CASE WHEN excluded.source_url<>'' THEN excluded.source_url ELSE image_site_md5_aliases.source_url END,
                       local_md5=CASE WHEN excluded.local_md5<>'' THEN excluded.local_md5 ELSE image_site_md5_aliases.local_md5 END,
                       observed_at=excluded.observed_at""",
                (value, int(image["id"]), str(site or ""), str(source_url or ""), str(image["local_md5"] or ""), now),
            )
            return {
                "ok": True,
                "reason": "bound",
                "canonical_path": str(image["path"] or path),
                "image_id": int(image["id"]),
            }
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "canonical_path": ""}


def found_media_path_by_md5(settings, md5: str, *, exclude_path: str = "") -> str:
    """Return a live FOUND-like canonical path for identical content."""
    return (
        media_path_by_md5(settings, md5, preferred_bucket="found", require_bucket=True, exclude_path=exclude_path)
        or media_path_by_md5(settings, md5, preferred_bucket="downloaded_found", require_bucket=True, exclude_path=exclude_path)
    )


def replaced_media_path(settings, md5: str) -> str:
    """(v5.44.13) The live card whose file took the place of these bytes — a quality upgrade put a better copy of its
    picture there (a site's sample gave way to its post's original: ``retired_media_tombstones``); "" — none.  The
    parser that meets the old bytes again gives its tags to that card instead of making a new one of a worse copy."""
    value = str(md5 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        return ""
    with db(settings, readonly=True) as con:
        rows = con.execute(
            "SELECT i.path FROM retired_media_tombstones t JOIN images i ON i.id=t.image_id "
            "WHERE t.old_md5 IN (?,?) AND +i.deleted=0 ORDER BY t.retired_at DESC", (value, value.upper())).fetchall()
    for row in rows:
        if Path(str(row["path"] or "")).is_file():
            return str(row["path"])
    return ""


def md5_exists(settings, md5: str) -> bool:
    """Return True if a file with this MD5 is already in the library (or its Trash).

    The MD5 may be the bytes' MD5 or one a site published for the post (site-MD5 alias).
    """
    value = str(md5 or "").strip().lower()
    if not value:
        return False
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT 1 FROM images WHERE hash_md5 = ? LIMIT 1", (value,)).fetchone()
        if row is None:
            row = con.execute(
                "SELECT 1 FROM image_site_md5_aliases a JOIN images i ON i.id=a.image_id WHERE a.site_md5=? LIMIT 1",
                (value,),
            ).fetchone()
    return row is not None


def ensure_image(settings, media_path, status="", original_path="", hash_md5=None, lifecycle=None, inbox_until=0, import_origin=""):
    """Ensure an image row exists and return its id without replacing tags/source."""
    with db(settings, write=True) as con:
        return _ensure_image(con, media_path, status=status, original_path=original_path, hash_md5=hash_md5, lifecycle=lifecycle, inbox_until=inbox_until, import_origin=import_origin)


def _post_set_for_untracked_tags(groups, post_url: str, source_text: str, method: str) -> list:
    """Tags that came without per-site sets (subscriptions, grabber) belong to the post they came from.

    Without a set they were invisible on the card and the next merge of another site erased them.
    """
    if not any(values for values in (groups or {}).values()):
        return []
    url = str(post_url or "").strip()
    if not url:
        for line in str(source_text or "").splitlines():
            url = next((part for part in line.split() if part.startswith(("http://", "https://"))), "")
            if url:
                break
    if not url or _is_navigational_source_url(url):
        return []
    return [{"url": url, "groups": groups, "method": str(method or "import")}]


def upsert_media_metadata(settings, media_path, tags=None, groups=None, source_text="", status="tagged", original_path="", hash_md5=None, raw=None, post_url="", file_url="", site="", merge_existing=False, lifecycle=None, inbox_until=0, import_origin="", source_tag_groups=None, site_posts=None):
    suffix = Path(media_path).suffix.lower()
    if suffix in VIDEO_EXTS or suffix == ".m4v":
        # a video's length and size are read before the base is taken for writing (kept for ``_ensure_image``)
        from core.video_probe import probe
        probe(media_path)
    with db(settings, write=True) as con:
        image_id = _ensure_image(con, media_path, status=status, original_path=original_path, hash_md5=hash_md5, lifecycle=lifecycle, inbox_until=inbox_until, import_origin=import_origin)
        if groups is None:
            groups = {"general": list(tags or [])}
        if not source_tag_groups:
            source_tag_groups = _post_set_for_untracked_tags(groups, post_url, source_text, import_origin)
        if merge_existing:
            add_image_tags(con, image_id, groups)
            add_image_sources(con, image_id, source_text, [post_url, file_url])
            add_image_source_tag_groups(con, image_id, source_tag_groups, replace_sources=False)
        else:
            # a tag or a source only this card had goes with what is replaced — only these, not a sweep of the whole
            # library (``repository.cleanup_orphans``, ``drop_unused_tags``)
            had_tags = [int(r[0]) for r in con.execute(
                "SELECT tag_id FROM image_tags WHERE image_id=? "
                "UNION SELECT tag_id FROM image_source_tags WHERE image_id=?", (image_id, image_id))]
            had_sources = [int(r[0]) for r in con.execute(
                "SELECT source_id FROM image_sources WHERE image_id=? "
                "UNION SELECT source_id FROM image_source_tags WHERE image_id=?", (image_id, image_id))]
            replace_image_tags(con, image_id, groups)
            replace_image_sources(con, image_id, source_text, [post_url, file_url])
            add_image_source_tag_groups(con, image_id, source_tag_groups, replace_sources=True)
            drop_unused_tags(con, had_tags)
            drop_unused_sources(con, had_sources)
        safe_post_url = str(post_url or "").strip()
        if _is_navigational_source_url(safe_post_url):
            safe_post_url = ""
        refresh_effective_tag_categories(con, image_id)
        if raw is not None or safe_post_url or file_url or site:
            try:
                raw_json = json.dumps(raw if raw is not None else {}, ensure_ascii=False)
            except Exception:
                raw_json = "{}"
            con.execute("""
                INSERT INTO raw_metadata(image_id, site, post_url, file_url, raw_json, updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(image_id) DO UPDATE SET
                    site=excluded.site, post_url=excluded.post_url, file_url=excluded.file_url,
                    raw_json=excluded.raw_json, updated_at=excluded.updated_at
            """, (image_id, site or host_from_url(safe_post_url or file_url), safe_post_url, file_url or "", raw_json, int(time.time())))
        _sync_typed_sources(
            con, image_id=image_id, source_text=source_text, raw=raw,
            post_url=safe_post_url, file_url=str(file_url or "").strip(), site=str(site or ""),
        )
        if site_posts:
            # what the sites said about the card's posts (parent, uploader): сборки are made of it (core/card_sets.py)
            from core.site_posts import record as record_site_posts
            record_site_posts(con, image_id, site_posts)
        return image_id


def refine_source_tag_categories(settings, media_path, source_url, groups, *, method="category_refine"):
    """Refine categories for tags already stored for one source only.

    HTML/category lookups are never allowed to add new tags here.  This keeps a
    noisy sidebar or recommendation block from polluting an image record.
    """
    path = str(Path(media_path))
    url = str(source_url or "").strip()
    candidate_count = sum(len(v or []) for v in (groups or {}).values())
    if not path or not url:
        return {"updated": 0, "ignored": candidate_count, "known": 0}
    with db(settings, write=True) as con:
        image = con.execute("SELECT id FROM images WHERE path=? AND deleted=0", (path,)).fetchone()
        source = con.execute("SELECT id FROM sources WHERE url=?", (url,)).fetchone()
        if not image or not source:
            return {"updated": 0, "ignored": candidate_count, "known": 0}
        image_id, source_id = int(image["id"]), int(source["id"])
        existing = con.execute("""
            SELECT ist.tag_id, t.normalized_name FROM image_source_tags ist
            JOIN tags t ON t.id=ist.tag_id
            WHERE ist.image_id=? AND ist.source_id=?
        """, (image_id, source_id)).fetchall()
        known = {str(r["normalized_name"]): int(r["tag_id"]) for r in existing}
        updated = 0
        seen_candidates = set()
        now = int(time.time())
        for category, values in (groups or {}).items():
            cat = str(category or "general")
            for raw in values or []:
                norm = normalize_tag(str(raw))
                if not norm:
                    continue
                seen_candidates.add(norm)
                tag_id = known.get(norm)
                if tag_id is None:
                    continue
                cat = _category_for_tag(norm, cat)
                con.execute("UPDATE image_source_tags SET category=?, acquisition=?, updated_at=? WHERE image_id=? AND source_id=? AND tag_id=?",
                            (cat, str(method or "category_refine"), now, image_id, source_id, tag_id))
                old = con.execute("SELECT category FROM tags WHERE id=?", (tag_id,)).fetchone()
                _tag_written(con, tag_id, norm, cat, old["category"] if old else "general", categorized=True)
                updated += 1
        if updated:
            refresh_effective_tag_categories(con, image_id)
        ignored = len(seen_candidates - set(known))
        return {"updated": updated, "ignored": ignored, "known": len(known)}


def mark_processed(settings, media_path, status="nomatch", original_path=""):
    with db(settings, write=True) as con:
        return _ensure_image(con, media_path, status=status, original_path=original_path)


def processed_status(settings, original_path):
    """Result of the earlier processing of this source file, while that result is still in the library.

    The library card may have moved (restored from Trash, archive relocated): a live card with the
    same bytes still counts.  A card in Trash or deleted for good does not.
    """
    p = Path(original_path)
    with db(settings, readonly=True) as con:
        rows = [con.execute(
            "SELECT status, media_path, md5, image_id FROM source_originals WHERE original_path=?", (str(p),)
        ).fetchone()]
        # The older journal still knows sources recorded before source_originals existed.
        rows.append(con.execute("""
            SELECT status, media_path, '' AS md5, 0 AS image_id FROM processed_files
            WHERE original_path=?
            ORDER BY processed_at DESC LIMIT 1
        """, (str(p),)).fetchone())
        for row in rows:
            if row is None:
                continue
            if Path(str(row["media_path"] or "")).exists():
                return row["status"]
            # The card moved or its file was replaced by a better version: the card itself counts.
            candidates = []
            if int(row["image_id"] or 0):
                candidates += con.execute("SELECT path FROM images WHERE id=? AND deleted=0",
                                          (int(row["image_id"]),)).fetchall()
            md5 = str(row["md5"] or "")
            if md5:
                candidates += con.execute("SELECT path FROM images WHERE hash_md5 IN (?,?) AND deleted=0",
                                          (md5, md5.upper())).fetchall()
            if any(Path(str(live["path"] or "")).exists() for live in candidates):
                return row["status"]
    return None


def processed_kinds_many(settings, original_paths) -> dict:
    """The recorded result of each of these source files: {original_path: status} — from ``source_originals``,
    else from the older ``processed_files`` (its newest row).  Only what was written, not whether the card still
    lives (``processed_status`` checks that, for one file): for putting the files of a parser run in order
    (core/parser/runner.py), never for skipping one."""
    keys = list(dict.fromkeys(str(Path(x)) for x in (original_paths or [])))
    out: dict = {}
    if not keys:
        return out
    with db(settings, readonly=True) as con:
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            marks = ",".join(["?"] * len(chunk))
            for row in con.execute(f"SELECT original_path, status FROM processed_files WHERE original_path IN ({marks})"
                                   " ORDER BY processed_at", chunk):
                out[str(row["original_path"])] = str(row["status"] or "")      # the newest one stays
            for row in con.execute(f"SELECT original_path, status FROM source_originals WHERE original_path IN ({marks})",
                                   chunk):
                out[str(row["original_path"])] = str(row["status"] or "")
    return out


def processed_status_many(settings, original_paths):
    paths = [Path(x) for x in (original_paths or [])]
    if not paths:
        return {}
    result = {}
    # Uses write=True only to allow self-healing CREATE TABLE/INDEX for users
    # who opened a DB that predates the v024 migration import fix.
    with db(settings, write=True) as con:
        _ensure_reverse_branch_status_table(con)
        for i in range(0, len(paths), 500):
            chunk = paths[i:i+500]
            keys = [str(x) for x in chunk]
            placeholders = ",".join(["?"] * len(keys))
            rows = con.execute(f"""
                SELECT original_path, status, media_path, processed_at FROM processed_files
                WHERE original_path IN ({placeholders})
                ORDER BY processed_at DESC
            """, keys).fetchall()
            seen = set()
            for r in rows:
                op = r["original_path"]
                if op in seen:
                    continue
                seen.add(op)
                try:
                    if Path(r["media_path"]).exists():
                        result[op] = r["status"]
                except Exception:
                    pass
    return result


def processed_records_many(settings, original_paths):
    """Return latest existing archive status and media path for original inputs."""
    paths = [Path(x) for x in (original_paths or [])]
    if not paths:
        return {}
    result = {}
    # Uses write=True only to allow self-healing CREATE TABLE/INDEX for users
    # who opened a DB that predates the v024 migration import fix.
    with db(settings, write=True) as con:
        _ensure_reverse_branch_status_table(con)
        for i in range(0, len(paths), 500):
            keys = [str(x) for x in paths[i:i+500]]
            placeholders = ",".join(["?"] * len(keys))
            rows = con.execute(f"""
                SELECT original_path, status, media_path, processed_at FROM processed_files
                WHERE original_path IN ({placeholders})
                ORDER BY processed_at DESC
            """, keys).fetchall()
            for row in rows:
                original = str(row["original_path"] or "")
                if original in result:
                    continue
                try:
                    if Path(row["media_path"]).exists():
                        result[original] = {"status": str(row["status"] or ""), "media_path": str(row["media_path"] or "")}
                except Exception:
                    pass
    return result


def mark_site_scanned_many(settings, rows):
    """Persist completed exact-MD5 checks in one SQLite transaction.

    ``rows`` accepts dictionaries with the same fields as :func:`mark_site_scanned`
    or positional tuples in the order
    ``(original_path, site_key, engine, scan_revision, outcome, checked_md5, source_url, checked_at)``.
    Network failures must never be included because a missing row is the durable
    representation of a pending/retryable lane.
    """
    normalized = []
    now = int(time.time())
    for row in rows or []:
        if isinstance(row, dict):
            raw_original = str(row.get("original_path") or "").strip()
            original = str(Path(raw_original)) if raw_original else ""
            key = str(row.get("site_key") or "").strip().lower()
            engine = str(row.get("engine") or "")
            revision = int(row.get("scan_revision") or 1)
            outcome = str(row.get("outcome") or "miss")
            checked_md5 = str(row.get("checked_md5") or "").lower()
            source_url = str(row.get("source_url") or "")
            checked_at = int(row.get("checked_at") or now)
        else:
            values = list(row or [])
            values += [None] * max(0, 8 - len(values))
            raw_original = str(values[0] or "").strip()
            original = str(Path(raw_original)) if raw_original else ""
            key = str(values[1] or "").strip().lower()
            engine = str(values[2] or "")
            revision = int(values[3] or 1)
            outcome = str(values[4] or "miss")
            checked_md5 = str(values[5] or "").lower()
            source_url = str(values[6] or "")
            checked_at = int(values[7] or now)
        if original and key:
            normalized.append((original, key, engine, revision, outcome, checked_md5, source_url, checked_at))
    if not normalized:
        return 0
    with db(settings, write=True) as con:
        con.executemany("""
            INSERT INTO site_scan_status(original_path, site_key, engine, scan_revision, outcome, checked_md5, source_url, checked_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(original_path, site_key, scan_revision) DO UPDATE SET
                engine=excluded.engine, outcome=excluded.outcome, checked_md5=excluded.checked_md5,
                source_url=excluded.source_url, checked_at=excluded.checked_at
        """, normalized)
    return len(normalized)


def mark_site_scanned(settings, original_path, site_key, *, engine="", scan_revision=1, outcome="miss", checked_md5="", source_url=""):
    """Persist one completed exact-MD5 check for a single file/source lane."""
    return mark_site_scanned_many(settings, [{
        "original_path": original_path,
        "site_key": site_key,
        "engine": engine,
        "scan_revision": scan_revision,
        "outcome": outcome,
        "checked_md5": checked_md5,
        "source_url": source_url,
    }])


def matched_site_md5_samples(settings, site_key, *, scan_revision=1, limit=2):
    """Return previously verified exact-MD5 matches for a site capability probe."""
    key = str(site_key or "").strip().lower()
    if not key or int(limit or 0) <= 0:
        return []
    with db(settings, readonly=True) as con:
        rows = con.execute("""
            SELECT DISTINCT checked_md5
            FROM site_scan_status
            WHERE site_key=? AND scan_revision=? AND outcome='match'
              AND length(checked_md5)=32
            ORDER BY checked_at DESC
            LIMIT ?
        """, (key, int(scan_revision or 1), int(limit))).fetchall()
    return [str(row["checked_md5"] or "").lower() for row in rows if str(row["checked_md5"] or "")]


def site_md5_samples(settings, host, *, limit=4):
    """MD5 that the site is known to have (earlier exact matches of any parser version), newest first.

    The old parser keyed some sites with a revision suffix ("rule34.xxx::…-v6"); a match is a match
    in every revision."""
    key = str(host or "").strip().lower().replace("www.", "")
    if not key or int(limit or 0) <= 0:
        return []
    with db(settings, readonly=True) as con:
        rows = con.execute("""
            SELECT checked_md5, MAX(checked_at) AS seen FROM site_scan_status
            WHERE (site_key=? OR site_key LIKE ?) AND outcome='match' AND length(checked_md5)=32
            GROUP BY checked_md5 ORDER BY seen DESC LIMIT ?
        """, (key, key + "::%", int(limit))).fetchall()
    return [str(row["checked_md5"]).lower() for row in rows]


def site_scan_hosts(settings, original_path) -> set:
    """Sites that have answered for this source file (found or not there), in any parser version."""
    with db(settings, readonly=True) as con:
        rows = con.execute("SELECT DISTINCT site_key FROM site_scan_status WHERE original_path=?",
                           (str(Path(str(original_path))),)).fetchall()
    return {str(row[0]).split("::", 1)[0].strip().lower().replace("www.", "") for row in rows if row[0]}


def site_misses(settings, original_path, md5, *, since=0) -> set:
    """(v5.44.29) Sites that said «нет» about this very MD5 of this source file at ``since`` (seconds) or later — the
    parser does not ask them again about a file no run finished.  By the path's index only: the other conditions are
    ``+`` (rule 17: without statistics the planner would take the index by outcome and time — the whole table)."""
    md5 = str(md5 or "").strip().lower()
    if not md5:
        return set()
    with db(settings, readonly=True) as con:
        rows = con.execute(
            "SELECT site_key FROM site_scan_status WHERE original_path=? AND +outcome='miss' AND +checked_md5=? "
            "AND +checked_at>=?", (str(Path(str(original_path))), md5, int(since or 0))).fetchall()
    return {str(row[0]).split("::", 1)[0].strip().lower().replace("www.", "") for row in rows if row[0]}


def site_scan_status_many(settings, original_paths, site_keys, *, scan_revision=1):
    """Return {original_path: {site_key: outcome}} for the active site set."""
    paths = [str(Path(x)) for x in (original_paths or [])]
    keys = [str(x or "").strip().lower() for x in (site_keys or []) if str(x or "").strip()]
    if not paths or not keys:
        return {}
    result = {}
    with db(settings) as con:
        for i in range(0, len(paths), 400):
            chunk = paths[i:i+400]
            pp = ",".join(["?"] * len(chunk))
            kk = ",".join(["?"] * len(keys))
            rows = con.execute(f"""
                SELECT original_path, site_key, outcome FROM site_scan_status
                WHERE scan_revision=? AND original_path IN ({pp}) AND site_key IN ({kk})
            """, [int(scan_revision or 1), *chunk, *keys]).fetchall()
            for row in rows:
                result.setdefault(str(row["original_path"]), {})[str(row["site_key"])] = str(row["outcome"] or "")
    return result


def pending_site_scan_paths(settings, original_paths, site_keys, *, scan_revision=1):
    """Return paths that still need at least one of the requested site checks."""
    paths = [Path(x) for x in (original_paths or [])]
    keys = {str(x or "").strip().lower() for x in (site_keys or []) if str(x or "").strip()}
    if not keys:
        return paths, {}, 0
    done_map = site_scan_status_many(settings, paths, keys, scan_revision=scan_revision)
    pending = [p for p in paths if not keys.issubset(set(done_map.get(str(p), {})))]
    completed = len(paths) - len(pending)
    return pending, done_map, completed



# ── v158: shared category cache for flat-tag sites ───────────────────────────

def cached_tag_categories(settings, host, tags):
    """Return cached {normalized_tag: category} for a site.

    Category lookup by post used to repeat the same remote tag-list calls for
    thousands of files.  This cache is site-scoped and conservative: only
    normalized tag names explicitly stored here are reused.
    """
    host = str(host or "").strip().lower().replace("www.", "")
    names = []
    seen = set()
    for tag in tags or []:
        name = normalize_tag(str(tag))
        if name and name not in seen:
            seen.add(name); names.append(name)
    if not host or not names:
        return {}
    out = {}
    with db(settings, readonly=True) as con:
        for i in range(0, len(names), 500):
            chunk = names[i:i+500]
            ph = ",".join(["?"] * len(chunk))
            try:
                rows = con.execute(
                    f"SELECT tag_name, category FROM tag_category_cache WHERE site_key=? AND tag_name IN ({ph})",
                    [host, *chunk],
                ).fetchall()
            except Exception:
                return out
            for row in rows:
                name = normalize_tag(row["tag_name"])
                cat = str(row["category"] or "general").lower()
                if name:
                    out[name] = cat or "general"
    return out


def upsert_tag_category_cache(settings, host, mapping, *, method="dapi_tag_api"):
    host = str(host or "").strip().lower().replace("www.", "")
    if not host or not mapping:
        return 0
    now = int(time.time())
    rows = []
    for tag, category in dict(mapping or {}).items():
        name = normalize_tag(str(tag))
        if not name:
            continue
        cat = str(category or "general").strip().lower() or "general"
        rows.append((host, name, cat, str(method or ""), now))
    if not rows:
        return 0
    with db(settings, write=True) as con:
        con.executemany("""
            INSERT INTO tag_category_cache(site_key, tag_name, category, source_method, updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(site_key, tag_name) DO UPDATE SET
                category=excluded.category, source_method=excluded.source_method, updated_at=excluded.updated_at
        """, rows)
    return len(rows)


def enqueue_tag_enrichment(settings, original_path, media_path, source_url, *, job_key="rule34.xxx::html-categories-v1"):
    """Queue a durable low-priority category pass for a matched post URL."""
    original = str(Path(original_path))
    media = str(Path(media_path)) if media_path else ""
    source = str(source_url or "").strip()
    key = str(job_key or "").strip().lower()
    if not original or not source or not key:
        return
    now = int(time.time())
    with db(settings, write=True) as con:
        con.execute("""
            INSERT INTO tag_enrichment_queue
            (original_path, job_key, media_path, source_url, status, retry_after, attempts, last_error, queued_at, updated_at)
            VALUES(?,?,?,?, 'pending', 0, 0, '', ?, ?)
            ON CONFLICT(original_path, job_key, source_url) DO UPDATE SET
                media_path=excluded.media_path,
                status=CASE WHEN tag_enrichment_queue.status='done' THEN 'done' ELSE 'pending' END,
                updated_at=excluded.updated_at
        """, (original, key, media, source, now, now))


def seed_background_tag_enrichment(settings, *, job_key="flat-sites::tag-groups-v4-gelbooru-live-markup-guard", hosts=("gelbooru.com", "rule34.xxx", "xbooru.com", "hypnohub.net")):
    """Backfill low-priority category jobs for sources that return flat post tags.

    Native grouped JSON sources (Danbooru/e621/ATF where categories exist in the
    same response) do not need another network pass. Gelbooru-family flat-tag
    matches are enriched later so exact-search lanes never wait for category I/O.
    """
    key = str(job_key or "").strip().lower()
    normalized = []
    for host in hosts or ():
        host = str(host or "").strip().lower().replace("www.", "")
        if host and host not in normalized:
            normalized.append(host)
    if not key or not normalized:
        return 0
    clauses = []
    args = [key, int(time.time()), int(time.time())]
    for host in normalized:
        clauses.append("(s.site_key=? OR s.site_key LIKE ?)")
        args.extend([host, host + "::%"])
    where_hosts = " OR ".join(clauses)
    with db(settings, write=True) as con:
        before = con.total_changes
        con.execute(f"""
            INSERT OR IGNORE INTO tag_enrichment_queue
            (original_path, job_key, media_path, source_url, status, retry_after, attempts, last_error, queued_at, updated_at)
            SELECT s.original_path, ?, p.media_path, s.source_url, 'pending', 0, 0, '', ?, ?
            FROM site_scan_status s
            JOIN processed_files p ON p.original_path=s.original_path
            WHERE ({where_hosts})
              AND s.outcome='match' AND COALESCE(s.source_url, '') <> ''
              AND p.status IN ('found','tagged')
        """, tuple(args))
        return max(0, con.total_changes - before)


def seed_rule34_category_enrichment(settings, *, job_key="rule34.xxx::html-categories-v1"):
    """Compatibility wrapper for older rule34-only queue versions/tests."""
    return seed_background_tag_enrichment(settings, job_key=job_key, hosts=("rule34.xxx",))


def pending_tag_enrichments(settings, *, job_key="rule34.xxx::html-categories-v1", now=None, limit=10000):
    key = str(job_key or "").strip().lower()
    stamp = int(time.time() if now is None else now)
    with db(settings) as con:
        rows = con.execute("""
            SELECT original_path, media_path, source_url, job_key, attempts
            FROM tag_enrichment_queue
            WHERE job_key=? AND status='pending' AND retry_after<=?
            ORDER BY queued_at, original_path LIMIT ?
        """, (key, stamp, int(limit))).fetchall()
    return [dict(row) for row in rows]


def complete_tag_enrichment(settings, original_path, source_url, *, job_key="rule34.xxx::html-categories-v1", status="done", error=""):
    now = int(time.time())
    with db(settings, write=True) as con:
        con.execute("""
            UPDATE tag_enrichment_queue SET status=?, last_error=?, updated_at=?
            WHERE original_path=? AND job_key=? AND source_url=?
        """, (str(status or "done"), str(error or "")[:1000], now, str(Path(original_path)), str(job_key).lower(), str(source_url or "")))


def retry_tag_enrichment(settings, original_path, source_url, *, job_key="rule34.xxx::html-categories-v1", delay_seconds=300, error=""):
    now = int(time.time())
    with db(settings, write=True) as con:
        con.execute("""
            UPDATE tag_enrichment_queue SET status='pending', retry_after=?, attempts=attempts+1, last_error=?, updated_at=?
            WHERE original_path=? AND job_key=? AND source_url=?
        """, (now + int(delay_seconds), str(error or "")[:1000], now, str(Path(original_path)), str(job_key).lower(), str(source_url or "")))


def enqueue_reverse_retry(settings, original_path, *, service="saucenao", retry_after=0, reason="limit"):
    """Persist a reverse-search retry without marking the file as NO_MATCH."""
    original = str(Path(original_path))
    svc = str(service or "saucenao").strip().lower()
    when = int(retry_after or time.time())
    if not original or not svc:
        return
    with db(settings, write=True) as con:
        con.execute("""
            INSERT INTO reverse_retry_queue(original_path, service, retry_after, reason, queued_at, attempts)
            VALUES(?,?,?,?,?,1)
            ON CONFLICT(original_path, service) DO UPDATE SET
                retry_after=excluded.retry_after, reason=excluded.reason,
                queued_at=excluded.queued_at, attempts=reverse_retry_queue.attempts + 1
        """, (original, svc, when, str(reason or "limit"), int(time.time())))


def remove_reverse_retry(settings, original_path, *, service="saucenao"):
    original = str(Path(original_path))
    svc = str(service or "saucenao").strip().lower()
    with db(settings, write=True) as con:
        con.execute("DELETE FROM reverse_retry_queue WHERE original_path=? AND service=?", (original, svc))


def due_reverse_retry_paths(settings, *, service="saucenao", now=None, limit=10000):
    svc = str(service or "saucenao").strip().lower()
    stamp = int(time.time() if now is None else now)
    with db(settings) as con:
        rows = con.execute("""
            SELECT original_path, retry_after, reason FROM reverse_retry_queue
            WHERE service=? AND retry_after<=?
            ORDER BY retry_after, queued_at LIMIT ?
        """, (svc, stamp, int(limit))).fetchall()
    return [(Path(r["original_path"]), int(r["retry_after"] or 0), str(r["reason"] or "")) for r in rows]


def _reverse_retry_cursor_values(cursor_after):
    """Normalize a retry keyset cursor to the durable SQL sort tuple."""
    if not cursor_after:
        return None
    if isinstance(cursor_after, dict):
        values = (
            cursor_after.get("retry_after", 0),
            cursor_after.get("queued_at", 0),
            cursor_after.get("path") or cursor_after.get("original_path") or "",
            cursor_after.get("service") or "",
        )
    else:
        values = tuple(cursor_after)
    if len(values) != 4:
        raise ValueError("reverse retry cursor must contain 4 values")
    return (
        int(values[0] or 0),
        int(values[1] or 0),
        str(values[2] or ""),
        str(values[3] or "").strip().lower(),
    )


def _reverse_retry_cursor_clause(cursor):
    if cursor is None:
        return "", ()
    retry_after, queued_at, original_path, service = cursor
    return (
        """
        AND (
            retry_after > ?
            OR (retry_after = ? AND queued_at > ?)
            OR (retry_after = ? AND queued_at = ? AND original_path > ?)
            OR (retry_after = ? AND queued_at = ? AND original_path = ? AND service > ?)
        )
        """,
        (
            retry_after,
            retry_after, queued_at,
            retry_after, queued_at, original_path,
            retry_after, queued_at, original_path, service,
        ),
    )


def _reverse_retry_rows_to_dicts(rows):
    return [
        {
            "path": Path(row["original_path"]),
            "service": str(row["service"] or "").strip().lower(),
            "retry_after": int(row["retry_after"] or 0),
            "reason": str(row["reason"] or ""),
            "attempts": int(row["attempts"] or 0),
            "queued_at": int(row["queued_at"] or 0),
        }
        for row in rows
    ]


def due_reverse_retry_rows(settings, *, now=None, limit=1000, services=None, cursor_after=None):
    """Return one keyset page of due durable reverse retries.

    Filtering by service and keyset cursor happens inside SQL before ``LIMIT``.
    Callers may therefore keep reading pages until they have admitted enough
    eligible rows or the due set is exhausted; old rows from another parser run
    can no longer permanently hide a current-run retry behind the first page.

    Rows are not removed here.  The corresponding branch removes its row only
    after a final non-deferred result, so a process crash cannot lose retries.
    """
    stamp = int(time.time() if now is None else now)
    normalized = []
    for service in list(services or []):
        value = str(service or "").strip().lower()
        if value and value not in normalized:
            normalized.append(value)
    cursor = _reverse_retry_cursor_values(cursor_after)
    cursor_sql, cursor_args = _reverse_retry_cursor_clause(cursor)
    with db(settings) as con:
        if normalized:
            placeholders = ",".join("?" for _ in normalized)
            rows = con.execute(
                f"""
                SELECT original_path, service, retry_after, reason, attempts, queued_at
                FROM reverse_retry_queue
                WHERE retry_after<=? AND service IN ({placeholders})
                {cursor_sql}
                ORDER BY retry_after, queued_at, original_path, service
                LIMIT ?
                """,
                (stamp, *normalized, *cursor_args, int(limit)),
            ).fetchall()
        else:
            rows = con.execute(
                f"""
                SELECT original_path, service, retry_after, reason, attempts, queued_at
                FROM reverse_retry_queue
                WHERE retry_after<=?
                {cursor_sql}
                ORDER BY retry_after, queued_at, original_path, service
                LIMIT ?
                """,
                (stamp, *cursor_args, int(limit)),
            ).fetchall()
    return _reverse_retry_rows_to_dicts(rows)


def reverse_retry_cursor(row):
    """Return the stable keyset cursor for a row returned by retry queries."""
    return (
        int((row or {}).get("retry_after") or 0),
        int((row or {}).get("queued_at") or 0),
        str((row or {}).get("path") or ""),
        str((row or {}).get("service") or "").strip().lower(),
    )


def remove_reverse_retry_rows(settings, rows):
    """Delete exact ``(original_path, service)`` pairs in one write transaction."""
    pairs = []
    seen = set()
    for row in list(rows or []):
        if isinstance(row, dict):
            original = str(Path(row.get("path") or row.get("original_path") or ""))
            service = str(row.get("service") or "").strip().lower()
        else:
            original, service = row
            original = str(Path(original))
            service = str(service or "").strip().lower()
        token = (original, service)
        if original and service and token not in seen:
            seen.add(token)
            pairs.append(token)
    if not pairs:
        return 0
    removed = 0
    with db(settings, write=True) as con:
        for original, service in pairs:
            cur = con.execute(
                "DELETE FROM reverse_retry_queue WHERE original_path=? AND service=?",
                (original, service),
            )
            removed += max(0, int(cur.rowcount or 0))
    return removed


def pending_reverse_retry_paths(settings, *, service="saucenao", limit=100000):
    """Return every durable retry row, including entries still in cooldown.

    The live conveyor restores these rows on application restart so a file that
    already exhausted IQDB/Ascii2D is not sent through those services again just
    because SauceNAO was cooling down when the previous run stopped.
    """
    svc = str(service or "saucenao").strip().lower()
    with db(settings) as con:
        rows = con.execute("""
            SELECT original_path, retry_after, reason FROM reverse_retry_queue
            WHERE service=?
            ORDER BY retry_after, queued_at LIMIT ?
        """, (svc, int(limit))).fetchall()
    return [(Path(r["original_path"]), int(r["retry_after"] or 0), str(r["reason"] or "")) for r in rows]


def pending_reverse_retry_rows(settings, *, services=None, limit=1000000, cursor_after=None):
    """Return one keyset page of future and due durable reverse retries."""
    normalized = []
    for service in list(services or []):
        value = str(service or "").strip().lower()
        if value and value not in normalized:
            normalized.append(value)
    cursor = _reverse_retry_cursor_values(cursor_after)
    cursor_sql, cursor_args = _reverse_retry_cursor_clause(cursor)
    with db(settings) as con:
        if normalized:
            placeholders = ",".join("?" for _ in normalized)
            rows = con.execute(
                f"""
                SELECT original_path, service, retry_after, reason, attempts, queued_at
                FROM reverse_retry_queue
                WHERE service IN ({placeholders})
                {cursor_sql}
                ORDER BY retry_after, queued_at, original_path, service
                LIMIT ?
                """,
                (*normalized, *cursor_args, int(limit)),
            ).fetchall()
        else:
            rows = con.execute(
                f"""
                SELECT original_path, service, retry_after, reason, attempts, queued_at
                FROM reverse_retry_queue
                WHERE 1=1
                {cursor_sql}
                ORDER BY retry_after, queued_at, original_path, service
                LIMIT ?
                """,
                (*cursor_args, int(limit)),
            ).fetchall()
    return _reverse_retry_rows_to_dicts(rows)


def pending_reverse_retry_info(settings, *, service="saucenao"):
    svc = str(service or "saucenao").strip().lower()
    with db(settings) as con:
        row = con.execute("""
            SELECT COUNT(*) AS n, MIN(retry_after) AS next_retry FROM reverse_retry_queue
            WHERE service=?
        """, (svc,)).fetchone()
    return int(row["n"] or 0), int(row["next_retry"] or 0)


def delete_image_records(settings, media_paths):
    with db(settings, write=True) as con:
        for p in media_paths:
            originals = [r["original_path"] for r in con.execute("SELECT original_path FROM processed_files WHERE media_path=?", (str(p),)).fetchall()]
            con.execute("DELETE FROM images WHERE path=?", (str(p),))
            con.execute("DELETE FROM processed_files WHERE media_path=?", (str(p),))
            for original in originals:
                con.execute("DELETE FROM site_scan_status WHERE original_path=?", (str(original),))
        _cleanup_orphan_rows(con)


def get_nomatches_for_tineye(settings) -> list:
    """Return paths of files with no tags that are candidates for TinEye search."""
    from pathlib import Path as _P
    try:
        with db(settings) as con:
            rows = con.execute(
                "SELECT path FROM images WHERE "
                "(tags IS NULL OR tags = '' OR tags = '[]') "
                "ORDER BY indexed_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            p = str(row["path"] if hasattr(row, "keys") else row[0])
            if _P(p).exists():
                result.append(p)
        return result
    except Exception:
        return []


class TooManyMissing(RuntimeError):
    """Most of the cards looked at have no file: a library folder emptied or replaced (a drive letter taken
    by another disk, an output/ recreated empty by something) — not losses to delete."""

    def __init__(self, missing: int, total: int):
        super().__init__(f"файлов нет у {missing} карточек из {total} — больше половины: похоже, папку библиотеки "
                         "подменили или очистили; ничего не удалено")
        self.missing, self.total = int(missing), int(total)


MASS_MISSING_SHARE = 0.5          # above this share of «gone» files nothing is deleted (from 20 cards on)


def delete_missing_rows(settings, *, within=None, stop_check=None) -> int:
    """Delete gallery rows whose file is confirmed missing on an available drive.

    A row on an offline/unmounted root or with an unclear path is never deleted:
    a disconnected drive must not wipe the library (docs/map, trap 10).  ``within`` — only rows under
    these folders (the index judges the library's own folders, not a card pointing elsewhere).

    The files are probed before the base is locked (on a big library that is minutes) and probed once
    more right before each delete; «Остановить» before the deletes deletes nothing.  When most of them
    are «gone» (``MASS_MISSING_SHARE``), nothing is deleted: :class:`TooManyMissing`.

    Cards in the trash are left alone, file or not: they hold their pictures out of the library (rule 12),
    and only emptying the trash (``purge_trash``) turns that into a lasting «do not import again».
    """
    from core.storage_roots import path_is_within_root, probe_media_path

    with db(settings, readonly=True) as con:
        rows = [(int(r["id"]), str(r["path"] or "")) for r in con.execute(
            "SELECT id, path FROM images WHERE COALESCE(lifecycle,'')<>'trash' ORDER BY id")]
    if within is not None:
        roots = [str(root) for root in within]
        rows = [(i, path) for i, path in rows if any(path_is_within_root(path, root) for root in roots)]
    missing = []
    root_cache = {}
    for pos, (image_id, path) in enumerate(rows, start=1):
        if stop_check and stop_check():
            return 0
        if (pos - 1) % 250 == 0:
            root_cache = {}
        if probe_media_path(settings, path, root_cache=root_cache).get("status") == "missing":
            missing.append((image_id, path))
    if len(rows) >= 20 and len(missing) > MASS_MISSING_SHARE * len(rows):
        raise TooManyMissing(len(missing), len(rows))
    removed = 0
    for start in range(0, len(missing), 500):
        with db(settings, write=True) as con:
            for image_id, path in missing[start:start + 500]:
                if probe_media_path(settings, path).get("status") != "missing":     # back meanwhile
                    continue
                cur = con.execute("DELETE FROM images WHERE id=? AND path=? AND COALESCE(lifecycle,'')<>'trash'",
                                  (image_id, path))
                removed += max(cur.rowcount or 0, 0)
    return removed


def cleanup_missing(settings, *, stop_check=None):
    """Delete only confirmed missing rows; never treat an offline root as missing.

    None — «Остановить» came before anything was deleted (``delete_missing_rows`` probes the files first)."""
    removed = delete_missing_rows(settings, stop_check=stop_check)
    if stop_check is not None and not removed and stop_check():
        return None
    with db(settings, write=True) as con:
        con.execute("DELETE FROM processed_files WHERE media_path NOT IN (SELECT path FROM images)")
        _cleanup_orphan_rows(con)
    return removed


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


def candidate_tags(settings, scope="all"):
    with db(settings) as con:
        where, args = _scope_where(scope)
        sql = """
            SELECT t.name, COUNT(*) c FROM tags t
            JOIN image_tags it ON it.tag_id=t.id
            JOIN images i ON i.id=it.image_id
        """
        if where:
            sql += " WHERE " + where
        sql += " GROUP BY t.id ORDER BY t.name COLLATE NOCASE"
        return [r["name"] for r in con.execute(sql, args).fetchall()]


def candidate_sources(settings, scope="all"):
    with db(settings) as con:
        where, args = _scope_where(scope)
        sql = """
            SELECT s.url, s.host, COUNT(DISTINCT i.id) c FROM sources s
            JOIN image_sources isrc ON isrc.source_id=s.id
            JOIN images i ON i.id=isrc.image_id
        """
        if where:
            sql += " WHERE " + where
        sql += " GROUP BY s.id ORDER BY s.host COLLATE NOCASE, s.url COLLATE NOCASE"
        rows = con.execute(sql, args).fetchall()
        return [r["url"] or r["host"] for r in rows]


def find_images_by_tag(settings, tag, scope="all", limit=None):
    with db(settings) as con:
        where, args = _scope_where(scope)
        clauses = ["t.normalized_name=?"]
        params = [normalize_tag(tag)]
        if where:
            clauses.append(where); params += args
        sql = """
            SELECT DISTINCT i.id, i.path, i.file_name, i.bucket FROM images i
            JOIN image_tags it ON it.image_id=i.id
            JOIN tags t ON t.id=it.tag_id
            WHERE """ + " AND ".join(clauses) + " ORDER BY i.path COLLATE NOCASE"
        if limit:
            sql += " LIMIT ?"; params.append(int(limit))
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def find_images_by_source(settings, source_text, scope="all", limit=None):
    with db(settings) as con:
        q = "%" + str(source_text or "").lower() + "%"
        where, args = _scope_where(scope)
        clauses = ["(LOWER(s.url) LIKE ? OR LOWER(s.host) LIKE ?)"]
        params = [q, q]
        if where:
            clauses.append(where); params += args
        sql = """
            SELECT DISTINCT i.id, i.path, i.file_name, i.bucket FROM images i
            JOIN image_sources isrc ON isrc.image_id=i.id
            JOIN sources s ON s.id=isrc.source_id
            WHERE """ + " AND ".join(clauses) + " ORDER BY i.path COLLATE NOCASE"
        if limit:
            sql += " LIMIT ?"; params.append(int(limit))
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def delete_images(settings, image_rows, delete_files=True):
    from core.deleted_registry import mark_deleted
    from core.source_protection import require_managed_media_mutation
    from core.services.media_storage_service import unlink_managed, delete_bucket_artifacts
    deleted_files = errors = protected_source_skipped = 0
    ids = []
    paths = []
    for r in image_rows or []:
        candidate = Path(r["path"])
        if delete_files and not require_managed_media_mutation(settings, candidate, "storage.delete_images"):
            protected_source_skipped += 1
            continue
        ids.append(int(r["id"]))
        paths.append(candidate)
    if delete_files:
        for p in paths:
            try:
                if p.exists() and p.is_file():
                    with swallow("database.storage.mark_deleted_registry"):
                        mark_deleted(p, reason="sqlite_delete", settings=settings, manual_delete=True)
                    if unlink_managed(settings, p, operation="storage.delete_images"):
                        deleted_files += 1
            except Exception:
                errors += 1
            delete_bucket_artifacts(settings, p, operation="storage.delete_side_artifacts")
    with db(settings, write=True) as con:
        for image_id in ids:
            con.execute("DELETE FROM images WHERE id=?", (image_id,))
        for p in paths:
            con.execute("DELETE FROM processed_files WHERE media_path=?", (str(p),))
        _cleanup_orphan_rows(con)
    return {"deleted_files": deleted_files, "errors": errors, "deleted_records": len(ids), "protected_source_skipped": protected_source_skipped}


def _cleanup_orphan_rows(con):
    from core.database.repository import cleanup_orphans
    cleanup_orphans(con)

# --- v27 compatibility aliases -------------------------------------------------
def cleanup_orphan_rows(settings):
    from core.database.repository import cleanup_orphans
    with db(settings, write=True) as con:
        cleanup_orphans(con)


def database_stats(settings):
    with db(settings, readonly=True) as con:
        out = {}
        for name in ("images", "tags", "image_tags", "sources", "image_sources", "processed_files", "delete_log"):
            try:
                out[name] = int(con.execute(f"SELECT COUNT(*) AS c FROM {_safe_ident(name)}").fetchone()["c"] or 0)
            except Exception:
                out[name] = 0
        return out



def _ensure_reverse_branch_status_table(con) -> None:
    """Compatibility guard for databases opened before m024 was recorded.

    v408 shipped the table definition but the migration runner did not import
    m024, so older user databases may miss reverse_branch_status until the next
    successful migration.  Keep the storage path self-healing as an additional
    safety net: this table is a durable cache/journal, not destructive schema.
    """
    con.execute("""
        CREATE TABLE IF NOT EXISTS reverse_branch_status (
            original_path TEXT NOT NULL,
            branch_key TEXT NOT NULL,
            scan_revision INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(original_path, branch_key, scan_revision)
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_reverse_branch_status_path ON reverse_branch_status(original_path, scan_revision, status)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_reverse_branch_status_branch ON reverse_branch_status(branch_key, scan_revision, status)")

# --- v115 diagnostics event audit ------------------------------------------------
def record_task_event(settings, task_type, status, message=""):
    """Persist a lightweight operational proof for the diagnostics page."""
    now = int(time.time())
    with db(settings, write=True) as con:
        con.execute(
            "INSERT INTO task_log(task_type,status,message,created_at,updated_at) VALUES(?,?,?,?,?)",
            (str(task_type or ""), str(status or ""), str(message or ""), now, now),
        )


def mark_reverse_branch_status(settings, original_path, branch_key, *, status="done_miss", reason="", scan_revision=1):
    """Persist terminal progress for one reverse branch of one source file."""
    original = str(Path(original_path))
    branch = str(branch_key or "").strip().lower()
    if not original or not branch:
        return
    now = int(time.time())
    with db(settings, write=True) as con:
        _ensure_reverse_branch_status_table(con)
        con.execute(
            """
            INSERT INTO reverse_branch_status(original_path, branch_key, scan_revision, status, reason, updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(original_path, branch_key, scan_revision) DO UPDATE SET
                status=excluded.status, reason=excluded.reason, updated_at=excluded.updated_at
            """,
            (original, branch, int(scan_revision or 1), str(status or ""), str(reason or "")[:1000], now),
        )


def reverse_branch_rows(settings, original_path) -> list:
    """(v5.44.18) Every answer of the reverse services kept for one source file: [{branch_key, status,
    scan_revision, reason, updated_at}] — the journal of the old program and of this one (core/parser/
    reverse_journal.py).  Read-only; no table yet (a database never migrated past m024) — []."""
    import sqlite3
    original = str(Path(original_path))
    if not original:
        return []
    try:
        with db(settings, readonly=True) as con:
            rows = con.execute(
                "SELECT branch_key, status, scan_revision, reason, updated_at FROM reverse_branch_status "
                "WHERE original_path=?", (original,)).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return []
        raise
    return [dict(r) for r in rows]


def reverse_branch_status_many(settings, original_paths, branch_keys, *, scan_revision=1):
    """Return {original_path: {branch_key: status}} for a batch of paths/branches."""
    paths = [str(Path(p)) for p in list(original_paths or []) if str(p or "")]
    branches = [str(b or "").strip().lower() for b in list(branch_keys or []) if str(b or "").strip()]
    out = {p: {} for p in paths}
    if not paths or not branches:
        return out
    # Uses write=True only to allow self-healing CREATE TABLE/INDEX for users
    # who opened a DB that predates the v024 migration import fix.
    with db(settings, write=True) as con:
        _ensure_reverse_branch_status_table(con)
        for i in range(0, len(paths), 500):
            chunk = paths[i:i+500]
            ph_paths = ",".join("?" for _ in chunk)
            ph_br = ",".join("?" for _ in branches)
            rows = con.execute(
                f"""
                SELECT original_path, branch_key, status FROM reverse_branch_status
                WHERE scan_revision=? AND original_path IN ({ph_paths}) AND branch_key IN ({ph_br})
                """,
                [int(scan_revision or 1), *chunk, *branches],
            ).fetchall()
            for r in rows:
                out.setdefault(str(r["original_path"]), {})[str(r["branch_key"])] = str(r["status"] or "")
    return out
