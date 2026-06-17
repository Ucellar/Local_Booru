from __future__ import annotations

NAME = "v159_content_safe_filenames_collision_repair"


def _has_table(con, table: str) -> bool:
    try:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)).fetchone() is not None
    except Exception:
        return False


def _columns(con, table: str) -> set[str]:
    if not _has_table(con, table):
        return set()
    try:
        return {str(r[1]) for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _ensure_column(con, table: str, column: str, ddl: str) -> None:
    cols = _columns(con, table)
    if cols and column not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _exec_if(con, table: str, required: tuple[str, ...], sql: str) -> None:
    cols = _columns(con, table)
    if cols and set(required).issubset(cols):
        con.execute(sql)


def apply(con) -> None:
    """Store original names separately and add collision diagnostics indexes.

    v159 stops treating the original basename as unique.  New managed files are
    named with an MD5 suffix; old rows keep their current physical file_name but
    gain original_file_name for future repair/UI display.
    """
    _ensure_column(con, "images", "original_file_name", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(con, "images", "content_name_policy", "TEXT NOT NULL DEFAULT ''")
    if _has_table(con, "images") and "file_name" in _columns(con, "images"):
        con.execute("UPDATE images SET original_file_name=file_name WHERE COALESCE(original_file_name,'')='' AND COALESCE(file_name,'')<>''")

    _exec_if(con, "images", ("deleted", "path"), "CREATE INDEX IF NOT EXISTS idx_v159_images_live_path ON images(deleted, path)")
    _exec_if(con, "images", ("deleted", "file_name", "hash_md5"), "CREATE INDEX IF NOT EXISTS idx_v159_images_filename_md5 ON images(deleted, file_name, hash_md5)")
    _exec_if(con, "images", ("deleted", "original_file_name", "hash_md5"), "CREATE INDEX IF NOT EXISTS idx_v159_images_original_filename_md5 ON images(deleted, original_file_name, hash_md5)")

    con.executescript("""
    CREATE TABLE IF NOT EXISTS filename_collision_audit (
        id INTEGER PRIMARY KEY,
        issue_type TEXT NOT NULL,
        image_id INTEGER NOT NULL DEFAULT 0,
        path TEXT NOT NULL DEFAULT '',
        file_name TEXT NOT NULL DEFAULT '',
        hash_md5 TEXT NOT NULL DEFAULT '',
        details TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        created_at INTEGER NOT NULL DEFAULT 0,
        repaired_at INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_filename_collision_audit_status
        ON filename_collision_audit(status, issue_type, created_at);
    """)
