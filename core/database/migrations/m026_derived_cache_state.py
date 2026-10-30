from __future__ import annotations

NAME = "derived_cache_generation_state"

_STATE_TABLE = "derived_cache_state"
_REVISION_TABLE = "derived_source_revision"
_TRIGGER_NAMES = {
    "trg_derived_rev_vp_images_insert",
    "trg_derived_rev_vp_images_update",
    "trg_derived_rev_vp_images_delete",
    "trg_derived_rev_effective_ist_insert",
    "trg_derived_rev_effective_ist_update",
    "trg_derived_rev_effective_ist_delete",
    "trg_derived_rev_effective_it_insert",
    "trg_derived_rev_effective_it_update",
    "trg_derived_rev_effective_it_delete",
    "trg_derived_rev_effective_tags_category",
}


def _columns(con, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def is_applied(con) -> bool:
    required_objects = {
        ("table", _STATE_TABLE),
        ("table", _REVISION_TABLE),
        ("index", "idx_derived_cache_state_status"),
        *{("trigger", name) for name in _TRIGGER_NAMES},
    }
    names = tuple(name for _kind, name in required_objects)
    placeholders = ",".join("?" for _ in names)
    rows = con.execute(
        f"SELECT type,name FROM sqlite_master WHERE name IN ({placeholders})",
        names,
    ).fetchall()
    present = {(str(row[0]), str(row[1])) for row in rows}
    required_columns = {
        "cache_name", "generation", "source_revision", "content_hash", "status",
        "source_rows", "cache_rows", "built_at", "duration_ms", "last_error", "updated_at",
    }
    return required_objects.issubset(present) and required_columns.issubset(_columns(con, _STATE_TABLE))


def apply(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS derived_cache_state (
            cache_name TEXT PRIMARY KEY,
            generation INTEGER NOT NULL DEFAULT 0,
            source_revision INTEGER NOT NULL DEFAULT 0,
            content_hash TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'unknown',
            source_rows INTEGER NOT NULL DEFAULT 0,
            cache_rows INTEGER NOT NULL DEFAULT 0,
            built_at INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS derived_source_revision (
            cache_name TEXT PRIMARY KEY,
            revision INTEGER NOT NULL DEFAULT 1,
            updated_at INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_derived_cache_state_status
            ON derived_cache_state(status, updated_at DESC);
        INSERT OR IGNORE INTO derived_source_revision(cache_name,revision,updated_at)
            VALUES('vp_tree',1,CAST(strftime('%s','now') AS INTEGER));
        INSERT OR IGNORE INTO derived_source_revision(cache_name,revision,updated_at)
            VALUES('image_effective_tag_category',1,CAST(strftime('%s','now') AS INTEGER));
        """
    )
    columns = _columns(con, _STATE_TABLE)
    if "content_hash" not in columns:
        con.execute("ALTER TABLE derived_cache_state ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''")

    # Source revisions are durable and cheap.  A cache row records the revision
    # it was built from, so stale state is visible without hashing primary data
    # on every startup.  Trigger updates are in the same transaction as the
    # source change and therefore cannot get ahead of committed primary data.
    con.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS trg_derived_rev_vp_images_insert
        AFTER INSERT ON images
        WHEN NEW.deleted=0 AND COALESCE(NEW.hash_phash,'')!=''
        BEGIN
            UPDATE derived_source_revision
            SET revision=revision+1,updated_at=CAST(strftime('%s','now') AS INTEGER)
            WHERE cache_name='vp_tree';
        END;

        CREATE TRIGGER IF NOT EXISTS trg_derived_rev_vp_images_update
        AFTER UPDATE OF deleted,hash_phash ON images
        WHEN COALESCE(OLD.deleted,0)!=COALESCE(NEW.deleted,0)
          OR COALESCE(OLD.hash_phash,'')!=COALESCE(NEW.hash_phash,'')
        BEGIN
            UPDATE derived_source_revision
            SET revision=revision+1,updated_at=CAST(strftime('%s','now') AS INTEGER)
            WHERE cache_name='vp_tree';
        END;

        CREATE TRIGGER IF NOT EXISTS trg_derived_rev_vp_images_delete
        AFTER DELETE ON images
        WHEN OLD.deleted=0 AND COALESCE(OLD.hash_phash,'')!=''
        BEGIN
            UPDATE derived_source_revision
            SET revision=revision+1,updated_at=CAST(strftime('%s','now') AS INTEGER)
            WHERE cache_name='vp_tree';
        END;

        CREATE TRIGGER IF NOT EXISTS trg_derived_rev_effective_ist_insert
        AFTER INSERT ON image_source_tags BEGIN
            UPDATE derived_source_revision
            SET revision=revision+1,updated_at=CAST(strftime('%s','now') AS INTEGER)
            WHERE cache_name='image_effective_tag_category';
        END;
        CREATE TRIGGER IF NOT EXISTS trg_derived_rev_effective_ist_update
        AFTER UPDATE ON image_source_tags BEGIN
            UPDATE derived_source_revision
            SET revision=revision+1,updated_at=CAST(strftime('%s','now') AS INTEGER)
            WHERE cache_name='image_effective_tag_category';
        END;
        CREATE TRIGGER IF NOT EXISTS trg_derived_rev_effective_ist_delete
        AFTER DELETE ON image_source_tags BEGIN
            UPDATE derived_source_revision
            SET revision=revision+1,updated_at=CAST(strftime('%s','now') AS INTEGER)
            WHERE cache_name='image_effective_tag_category';
        END;

        CREATE TRIGGER IF NOT EXISTS trg_derived_rev_effective_it_insert
        AFTER INSERT ON image_tags BEGIN
            UPDATE derived_source_revision
            SET revision=revision+1,updated_at=CAST(strftime('%s','now') AS INTEGER)
            WHERE cache_name='image_effective_tag_category';
        END;
        CREATE TRIGGER IF NOT EXISTS trg_derived_rev_effective_it_update
        AFTER UPDATE ON image_tags BEGIN
            UPDATE derived_source_revision
            SET revision=revision+1,updated_at=CAST(strftime('%s','now') AS INTEGER)
            WHERE cache_name='image_effective_tag_category';
        END;
        CREATE TRIGGER IF NOT EXISTS trg_derived_rev_effective_it_delete
        AFTER DELETE ON image_tags BEGIN
            UPDATE derived_source_revision
            SET revision=revision+1,updated_at=CAST(strftime('%s','now') AS INTEGER)
            WHERE cache_name='image_effective_tag_category';
        END;

        CREATE TRIGGER IF NOT EXISTS trg_derived_rev_effective_tags_category
        AFTER UPDATE OF category ON tags
        WHEN COALESCE(OLD.category,'')!=COALESCE(NEW.category,'')
        BEGIN
            UPDATE derived_source_revision
            SET revision=revision+1,updated_at=CAST(strftime('%s','now') AS INTEGER)
            WHERE cache_name='image_effective_tag_category';
        END;
        """
    )
