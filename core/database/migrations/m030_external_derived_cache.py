from __future__ import annotations

NAME = "optional_external_disposable_derived_cache"

_REQUIRED_COLUMNS = {
    "storage_backend",
    "cache_file_id",
    "cache_schema_version",
    "published_at",
}


def _columns(con, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def is_applied(con) -> bool:
    if not _REQUIRED_COLUMNS.issubset(_columns(con, "derived_cache_state")):
        return False
    try:
        row = con.execute("SELECT value FROM meta WHERE key='derived_cache_owner_id'").fetchone()
        return bool(row and str(row[0] or '').strip())
    except Exception:
        return False


def _add_column(con, definition: str) -> None:
    name = definition.split()[0].strip('"`[]')
    if name not in _columns(con, "derived_cache_state"):
        con.execute(f"ALTER TABLE derived_cache_state ADD COLUMN {definition}")


def apply(con) -> None:
    # Main SQLite remains the sole authority.  These columns are only the
    # durable pointer/expectation for a disposable, self-describing cache file.
    # The cache file is never attached for writes and is never assumed to commit
    # atomically with this database.
    for definition in (
        "storage_backend TEXT NOT NULL DEFAULT 'main'",
        "cache_file_id TEXT NOT NULL DEFAULT ''",
        "cache_schema_version INTEGER NOT NULL DEFAULT 0",
        "published_at INTEGER NOT NULL DEFAULT 0",
    ):
        _add_column(con, definition)
    # Repair an explicitly empty legacy/test value as well as inserting the
    # identity for normal upgrades.  ``INSERT OR IGNORE`` alone would leave an
    # empty pre-existing row forever and make the migration non-idempotent.
    con.execute(
        """
        INSERT INTO meta(key,value)
        VALUES('derived_cache_owner_id', lower(hex(randomblob(16))))
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        WHERE TRIM(COALESCE(meta.value,''))=''
        """
    )
    con.execute(
        """
        UPDATE derived_cache_state
        SET storage_backend=CASE
                WHEN storage_backend IN ('main','external') THEN storage_backend
                ELSE 'main'
            END,
            cache_file_id=COALESCE(cache_file_id,''),
            cache_schema_version=COALESCE(cache_schema_version,0),
            published_at=COALESCE(published_at,0)
        """
    )
