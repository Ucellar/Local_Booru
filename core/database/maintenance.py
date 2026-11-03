from __future__ import annotations
from .connection import db, db_path
from .storage import _safe_ident, _tag_written


def _checkpoint_truncate(settings) -> str:
    """Move the write-ahead log into the database and empty its file.

    Must run outside a transaction: inside ``BEGIN IMMEDIATE`` SQLite refuses it
    ("database table is locked"), so «Оптимизировать» never shrank the WAL.
    """
    import logging
    import sqlite3
    path = db_path(settings)
    if not path.exists():
        return "skipped: no database file"          # never create one here (missing-database guard)
    # Its own short-lived connection with a short wait: while TRUNCATE waits for readers it holds
    # other writers back, so it must give up quickly instead of freezing the parser for a minute.
    # mode=rw: if the file vanishes in between, SQLite fails instead of creating an empty database.
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=2.0)
        try:
            con.execute("PRAGMA busy_timeout=2000")
            busy, _log_pages, _moved = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        finally:
            con.close()
        return "busy" if busy else "ok"
    except sqlite3.Error as exc:
        logging.getLogger("local_booru.maintenance").warning("WAL checkpoint failed: %s", exc)
        return f"error: {exc}"


def optimize(settings):
    import time
    before = storage_report(settings)
    checkpoint = _checkpoint_truncate(settings)
    with db(settings, write=True) as con:
        try: con.execute("ANALYZE")
        except Exception: pass
        try: con.execute("PRAGMA optimize")
        except Exception: pass
        try:
            con.execute(
                "INSERT INTO maintenance_history(operation,status,before_bytes,after_bytes,reclaimed_bytes,details,created_at) VALUES(?,?,?,?,?,?,?)",
                ("optimize", "completed", int(before.get("size_bytes",0)), int(before.get("size_bytes",0)), 0, f"WAL checkpoint: {checkpoint}; ANALYZE; PRAGMA optimize", int(time.time())),
            )
        except Exception:
            pass
    if checkpoint == "ok":
        _checkpoint_truncate(settings)              # ANALYZE wrote to the log again
    return {"db": str(db_path(settings)), "before": before, "after": storage_report(settings), "wal_checkpoint": checkpoint}


def stats(settings):
    with db(settings, readonly=True) as con:
        tables = {}
        for t in ("images","tags","image_tags","sources","image_sources","processed_files","delete_log"):
            try: tables[t] = int(con.execute(f"SELECT COUNT(*) c FROM {_safe_ident(t)}").fetchone()["c"] or 0)
            except Exception: tables[t] = 0
    return tables


def repair_e621_tag_metadata(settings):
    """Repair old e621/e926 tags polluted by visible sidebar counts and restore species.

    Old HTML fallbacks could turn ``horse 231k`` into ``horse_231k`` or
    ``yourumi Uploaded by the artist`` into ``yourumi_Uploaded_by_the_artist``.
    Older JSON handling also collapsed official e621 categories. This migration is
    deliberately limited to media that has an e621/e926 source.  Exact group
    restoration is performed only when saved raw JSON contains the original
    e621 tag map; otherwise the cleaned base tag keeps its existing category.

    The card's e621 tag set (``image_source_tags``) is repaired with its search index ``image_tags`` (renamed
    tags, categories): the index is filled from the sets on the next write of the card
    (``storage._sync_merged_image_tags``), and a fix of the index alone came undone then.  Another site's
    set keeps its own tags.
    """
    import json
    import re
    from core.tag_utils import normalize_tag

    e621_sources = ("SELECT id FROM sources WHERE REPLACE(LOWER(COALESCE(host,'')),'www.','') "
                    "IN ('e621.net','e926.net')")
    polluted_suffixes = [
        # the sidebar's post count — «231k», «1.2k», «2.4m»: thousands and millions only (a tag such as
        # «yorha_2b» is a real one; no e621 tag has billions of posts)
        re.compile(r"^(?P<base>.+?)_(?:\d{1,3}(?:[.,]\d)?[km])$", re.IGNORECASE),
        re.compile(r"^(?P<base>.+?)_uploaded_by_the_artist$", re.IGNORECASE),
    ]
    fixed_names = fixed_categories = raw_rows = 0
    affected_images: set[int] = set()

    def raw_tag_maps(con) -> dict:
        """Saved e621 answers: image id -> [{group: [tags]}, …]."""
        out: dict = {}
        for row in con.execute("""
            SELECT rm.image_id, rm.raw_json FROM raw_metadata rm
            JOIN images i ON i.id=rm.image_id
            WHERE LOWER(COALESCE(rm.site,'')) IN ('e621','e621.net','e926','e926.net')
               OR LOWER(COALESCE(rm.post_url,'')) LIKE '%e621.net/%'
               OR LOWER(COALESCE(rm.post_url,'')) LIKE '%e926.net/%'
        """).fetchall():
            try:
                data = json.loads(row['raw_json'] or '{}')
            except Exception:
                continue
            post = data.get('post', data) if isinstance(data, dict) else {}
            tag_map = post.get('tags') if isinstance(post, dict) else None
            if isinstance(tag_map, dict):
                out.setdefault(int(row['image_id']), []).append(tag_map)
        return out

    with db(settings, write=True) as con:
        saved = raw_tag_maps(con)
        site_names = {image_id: {normalize_tag(str(tag)) for tag_map in maps for tags in tag_map.values()
                                 if isinstance(tags, list) for tag in tags}
                      for image_id, maps in saved.items()}
        rows = con.execute("""
            SELECT DISTINCT i.id AS image_id, t.id AS tag_id, t.name, t.category
            FROM images i
            JOIN image_tags it ON it.image_id=i.id
            JOIN tags t ON t.id=it.tag_id
            WHERE EXISTS (
                SELECT 1 FROM image_sources xs
                JOIN sources s ON s.id=xs.source_id
                WHERE xs.image_id=i.id
                  AND (LOWER(s.host) IN ('e621.net','e926.net')
                       OR LOWER(s.url) LIKE '%e621.net/%'
                       OR LOWER(s.url) LIKE '%e926.net/%')
            ) OR EXISTS (
                SELECT 1 FROM raw_metadata rm
                WHERE rm.image_id=i.id
                  AND (LOWER(COALESCE(rm.site,'')) IN ('e621','e621.net','e926','e926.net')
                       OR LOWER(COALESCE(rm.post_url,'')) LIKE '%e621.net/%'
                       OR LOWER(COALESCE(rm.post_url,'')) LIKE '%e926.net/%')
            )
        """).fetchall()
        for row in rows:
            name = str(row["name"] or "")
            match = next((pattern.match(name) for pattern in polluted_suffixes if pattern.match(name)), None)
            if not match:
                continue
            base = normalize_tag(match.group("base"))
            if not base:
                continue
            image_id, old_id = int(row["image_id"]), int(row["tag_id"])
            if normalize_tag(name) in site_names.get(image_id, ()):
                continue                    # the site's own answer has this very tag: a real one
            in_set = "SELECT 1 FROM image_source_tags WHERE image_id=? AND tag_id=? AND source_id {} ({}) LIMIT 1"
            if con.execute(in_set.format("NOT IN", e621_sources), (image_id, old_id)).fetchone() and \
                    not con.execute(in_set.format("IN", e621_sources), (image_id, old_id)).fetchone():
                continue                    # another site's own tag: that site really calls it so
            category = str(row["category"] or "general")
            con.execute("INSERT OR IGNORE INTO tags(name, normalized_name, category) VALUES(?,?,?)", (base, base, category))
            target = con.execute("SELECT id FROM tags WHERE normalized_name=?", (base,)).fetchone()
            if target:
                new_id = int(target["id"])
                con.execute(f"""INSERT OR IGNORE INTO image_source_tags(image_id, source_id, tag_id, category, acquisition, updated_at)
                                SELECT image_id, source_id, ?, category, acquisition, updated_at FROM image_source_tags
                                WHERE image_id=? AND tag_id=? AND source_id IN ({e621_sources})""", (new_id, image_id, old_id))
                con.execute(f"DELETE FROM image_source_tags WHERE image_id=? AND tag_id=? AND source_id IN ({e621_sources})",
                            (image_id, old_id))
                con.execute("INSERT OR IGNORE INTO image_tags(image_id, tag_id) VALUES(?,?)", (image_id, new_id))
                if not con.execute("SELECT 1 FROM image_source_tags WHERE image_id=? AND tag_id=? LIMIT 1",
                                   (image_id, old_id)).fetchone():       # another site's set may have it as it is
                    con.execute("DELETE FROM image_tags WHERE image_id=? AND tag_id=?", (image_id, old_id))
                fixed_names += 1
                affected_images.add(image_id)

        category_map = {
            'artist': 'artist', 'contributor': 'contributor', 'character': 'character',
            'copyright': 'copyright', 'species': 'species', 'general': 'general',
            'meta': 'meta', 'lore': 'lore', 'invalid': 'invalid'
        }
        for image_id, tag_map in ((i, m) for i, maps in saved.items() for m in maps):
            raw_rows += 1
            for raw_group, tags in tag_map.items():
                category = category_map.get(str(raw_group).lower())
                if not category or not isinstance(tags, list):
                    continue
                for tag in tags:
                    name = normalize_tag(str(tag))
                    if not name:
                        continue
                    con.execute("INSERT OR IGNORE INTO tags(name, normalized_name, category) VALUES(?,?,?)", (name, name, category))
                    tag_row = con.execute("SELECT id, category FROM tags WHERE normalized_name=?", (name,)).fetchone()
                    if not tag_row:
                        continue
                    if str(tag_row['category'] or '') != category:
                        # (v5.44.49) the library's category of the tag is what most sites say: e621's word on this
                        # card is one answer among them (it overwrote the category of every tag of the card)
                        _tag_written(con, int(tag_row['id']), name, category, tag_row['category'], categorized=True)
                        fixed_categories += 1
                    # the category in the site's own set too: effective categories are taken from the sets
                    con.execute(f"UPDATE image_source_tags SET category=? WHERE image_id=? AND tag_id=? AND source_id IN ({e621_sources})",
                                (category, image_id, int(tag_row['id'])))
                    con.execute("INSERT OR IGNORE INTO image_tags(image_id, tag_id) VALUES(?,?)", (image_id, int(tag_row['id'])))
                    affected_images.add(image_id)
        try:
            from .storage import refresh_effective_tag_categories
            for _image_id in sorted(affected_images):
                refresh_effective_tag_categories(con, int(_image_id))
        except Exception:
            pass
        from .repository import cleanup_orphans
        cleanup_orphans(con)
    return {
        'renamed_links': fixed_names,
        'species_fixed': fixed_categories,
        'raw_rows': raw_rows,
        'images': len(affected_images),
    }


def storage_report(settings):
    """Return safe read-only SQLite size/fragmentation information."""
    path = db_path(settings)
    if not path.exists():
        return {"db": str(path), "exists": False, "size_bytes": 0, "reclaimable_bytes": 0}
    with db(settings, readonly=True) as con:
        page_size = int(con.execute("PRAGMA page_size").fetchone()[0] or 0)
        page_count = int(con.execute("PRAGMA page_count").fetchone()[0] or 0)
        freelist = int(con.execute("PRAGMA freelist_count").fetchone()[0] or 0)
        try:
            version_row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            version = int(version_row[0]) if version_row else 0
        except Exception:
            version = 0
        try:
            migration_rows = con.execute("SELECT version,name,status,applied_at FROM schema_migrations ORDER BY version").fetchall()
            migrations = [dict(r) for r in migration_rows]
        except Exception:
            migrations = []
    return {
        "db": str(path), "exists": True, "size_bytes": int(path.stat().st_size),
        "page_size": page_size, "page_count": page_count,
        "freelist_pages": freelist, "reclaimable_bytes": freelist * page_size,
        "schema_version": version, "migrations": migrations,
    }


def force_backup(settings, reason="manual_maintenance"):
    from core.library_lifecycle import force_backup_database
    return force_backup_database(settings, reason)


def vacuum(settings, *, make_backup=True):
    """Compact the rebuildable SQLite library after explicit user confirmation.

    The UI must refuse to call this while the parser is active.  The source
    archive is never involved; only the working SQLite file is compacted.
    """
    import sqlite3
    import time
    path = db_path(settings)
    if not path.exists():
        return {"db": str(path), "before_bytes": 0, "after_bytes": 0, "reclaimed_bytes": 0, "backup": ""}
    backup = force_backup(settings, "vacuum") if make_backup else ""
    if make_backup and not backup:
        raise RuntimeError("Не удалось создать backup SQLite. VACUUM отменён.")
    before = int(path.stat().st_size)
    from .connection import close_thread_pooled_connections, writes_blocked, writes_blocked_reason
    if writes_blocked():
        raise RuntimeError("SQLite в безопасном режиме только чтения: " + writes_blocked_reason())
    # Do not force-close pooled connections owned by worker threads here.
    # VACUUM will fail cleanly with database-is-locked if another subsystem is
    # still writing; that is safer than closing another thread's active handle.
    close_thread_pooled_connections(settings)
    con = sqlite3.connect(str(path), timeout=120)
    try:
        con.execute("PRAGMA busy_timeout=120000")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("VACUUM")
        con.execute("PRAGMA optimize")
        con.commit()
        try:
            con.execute(
                "INSERT INTO maintenance_history(operation,status,before_bytes,after_bytes,reclaimed_bytes,backup_path,details,created_at) VALUES(?,?,?,?,?,?,?,?)",
                ("vacuum", "completed", before, int(path.stat().st_size), max(0, before - int(path.stat().st_size)), str(backup or ""), "explicit user operation", int(time.time())),
            )
            con.commit()
        except Exception:
            pass
    finally:
        con.close()
    # In WAL mode the compacted pages are in the journal: the file itself shrinks when it is moved into
    # the base (it waits for readers only briefly; else at the next checkpoint — closing, idle time).
    checkpoint = _checkpoint_truncate(settings)
    after = int(path.stat().st_size)
    return {"db": str(path), "before_bytes": before, "after_bytes": after, "reclaimed_bytes": max(0, before-after),
            "backup": str(backup or ""), "wal_checkpoint": checkpoint}
