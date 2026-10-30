from __future__ import annotations

NAME = "file_presence_integrity"


def _columns(con, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def is_applied(con) -> bool:
    required = {
        ("table", "image_file_integrity"),
        ("table", "file_integrity_runs"),
        ("index", "idx_image_file_integrity_status"),
        ("index", "idx_image_file_integrity_checked"),
        ("index", "idx_file_integrity_runs_kind_started"),
    }
    names = tuple(name for _kind, name in required)
    placeholders = ",".join("?" for _ in names)
    rows = con.execute(
        f"SELECT type,name FROM sqlite_master WHERE name IN ({placeholders})", names
    ).fetchall()
    present = {(str(row[0]), str(row[1])) for row in rows}
    integrity_columns = {
        "image_id", "checked_path", "presence_status", "root_kind", "root_path",
        "root_status", "last_presence_check", "last_error", "updated_at",
    }
    run_columns = {
        "id", "kind", "status", "started_at", "finished_at", "total", "checked",
        "present", "missing", "offline_root", "path_error", "stale_skipped",
        "last_image_id", "last_error",
    }
    return (
        required.issubset(present)
        and integrity_columns.issubset(_columns(con, "image_file_integrity"))
        and run_columns.issubset(_columns(con, "file_integrity_runs"))
    )


def apply(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS image_file_integrity (
            image_id INTEGER PRIMARY KEY,
            checked_path TEXT NOT NULL DEFAULT '',
            presence_status TEXT NOT NULL DEFAULT 'unknown',
            root_kind TEXT NOT NULL DEFAULT '',
            root_path TEXT NOT NULL DEFAULT '',
            root_status TEXT NOT NULL DEFAULT 'unknown',
            last_presence_check INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE,
            CHECK(presence_status IN ('unknown','present','missing','offline_root','path_error'))
        );
        CREATE INDEX IF NOT EXISTS idx_image_file_integrity_status
            ON image_file_integrity(presence_status, last_presence_check DESC);
        CREATE INDEX IF NOT EXISTS idx_image_file_integrity_checked
            ON image_file_integrity(last_presence_check DESC, image_id);

        CREATE TABLE IF NOT EXISTS file_integrity_runs (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL DEFAULT 'presence',
            status TEXT NOT NULL DEFAULT 'running',
            started_at INTEGER NOT NULL DEFAULT 0,
            finished_at INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0,
            checked INTEGER NOT NULL DEFAULT 0,
            present INTEGER NOT NULL DEFAULT 0,
            missing INTEGER NOT NULL DEFAULT 0,
            offline_root INTEGER NOT NULL DEFAULT 0,
            path_error INTEGER NOT NULL DEFAULT 0,
            stale_skipped INTEGER NOT NULL DEFAULT 0,
            last_image_id INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_file_integrity_runs_kind_started
            ON file_integrity_runs(kind, started_at DESC);
        """
    )
