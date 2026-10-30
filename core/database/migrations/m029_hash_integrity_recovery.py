from __future__ import annotations

NAME = "hash_integrity_and_verified_recovery"

_HASH_STATUSES = ("unknown", "ok", "mismatch", "unreadable", "deferred")
_RECOVERY_STATUSES = (
    "none", "pending", "running", "recovered", "failed", "blocked", "no_source", "cancelled"
)


def _columns(con, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _objects(con) -> set[tuple[str, str]]:
    names = (
        "idx_image_file_integrity_hash",
        "idx_image_file_integrity_recovery",
        "file_recovery_attempts",
        "idx_file_recovery_attempts_image",
        "trg_image_file_integrity_hash_insert",
        "trg_image_file_integrity_hash_update",
        "trg_image_file_integrity_recovery_insert",
        "trg_image_file_integrity_recovery_update",
        "trg_file_recovery_attempt_status_insert",
        "trg_file_recovery_attempt_status_update",
    )
    rows = con.execute(
        f"SELECT type,name FROM sqlite_master WHERE name IN ({','.join('?' for _ in names)})",
        names,
    ).fetchall()
    return {(str(row[0]), str(row[1])) for row in rows}


def is_applied(con) -> bool:
    integrity_columns = {
        "hash_status", "expected_md5", "observed_md5", "last_hash_check",
        "hash_size_bytes", "hash_mtime_ns", "hash_error", "recovery_status",
        "recovery_source_url", "last_recovery_attempt", "recovery_error",
    }
    run_columns = {
        "hash_ok", "hash_mismatch", "hash_unreadable", "hash_deferred",
        "bytes_read", "recovered", "recovery_failed",
    }
    attempt_columns = {
        "id", "image_id", "expected_md5", "source_url", "status", "staging_path",
        "target_path", "quarantine_path", "bytes_downloaded", "observed_md5",
        "started_at", "finished_at", "last_error",
    }
    required_objects = {
        ("index", "idx_image_file_integrity_hash"),
        ("index", "idx_image_file_integrity_recovery"),
        ("table", "file_recovery_attempts"),
        ("index", "idx_file_recovery_attempts_image"),
        ("trigger", "trg_image_file_integrity_hash_insert"),
        ("trigger", "trg_image_file_integrity_hash_update"),
        ("trigger", "trg_image_file_integrity_recovery_insert"),
        ("trigger", "trg_image_file_integrity_recovery_update"),
        ("trigger", "trg_file_recovery_attempt_status_insert"),
        ("trigger", "trg_file_recovery_attempt_status_update"),
    }
    return (
        integrity_columns.issubset(_columns(con, "image_file_integrity"))
        and run_columns.issubset(_columns(con, "file_integrity_runs"))
        and attempt_columns.issubset(_columns(con, "file_recovery_attempts"))
        and required_objects.issubset(_objects(con))
    )


def _add_column(con, table: str, definition: str) -> None:
    name = definition.split()[0].strip('"`[]')
    if name not in _columns(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def apply(con) -> None:
    for definition in (
        "hash_status TEXT NOT NULL DEFAULT 'unknown'",
        "expected_md5 TEXT NOT NULL DEFAULT ''",
        "observed_md5 TEXT NOT NULL DEFAULT ''",
        "last_hash_check INTEGER NOT NULL DEFAULT 0",
        "hash_size_bytes INTEGER NOT NULL DEFAULT 0",
        "hash_mtime_ns INTEGER NOT NULL DEFAULT 0",
        "hash_error TEXT NOT NULL DEFAULT ''",
        "recovery_status TEXT NOT NULL DEFAULT 'none'",
        "recovery_source_url TEXT NOT NULL DEFAULT ''",
        "last_recovery_attempt INTEGER NOT NULL DEFAULT 0",
        "recovery_error TEXT NOT NULL DEFAULT ''",
    ):
        _add_column(con, "image_file_integrity", definition)

    for definition in (
        "hash_ok INTEGER NOT NULL DEFAULT 0",
        "hash_mismatch INTEGER NOT NULL DEFAULT 0",
        "hash_unreadable INTEGER NOT NULL DEFAULT 0",
        "hash_deferred INTEGER NOT NULL DEFAULT 0",
        "bytes_read INTEGER NOT NULL DEFAULT 0",
        "recovered INTEGER NOT NULL DEFAULT 0",
        "recovery_failed INTEGER NOT NULL DEFAULT 0",
    ):
        _add_column(con, "file_integrity_runs", definition)

    con.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_image_file_integrity_hash
            ON image_file_integrity(hash_status, last_hash_check DESC);
        CREATE INDEX IF NOT EXISTS idx_image_file_integrity_recovery
            ON image_file_integrity(recovery_status, last_recovery_attempt DESC);

        CREATE TABLE IF NOT EXISTS file_recovery_attempts (
            id INTEGER PRIMARY KEY,
            image_id INTEGER NOT NULL,
            expected_md5 TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'running',
            staging_path TEXT NOT NULL DEFAULT '',
            target_path TEXT NOT NULL DEFAULT '',
            quarantine_path TEXT NOT NULL DEFAULT '',
            bytes_downloaded INTEGER NOT NULL DEFAULT 0,
            observed_md5 TEXT NOT NULL DEFAULT '',
            started_at INTEGER NOT NULL DEFAULT 0,
            finished_at INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_file_recovery_attempts_image
            ON file_recovery_attempts(image_id, started_at DESC);

        DROP TRIGGER IF EXISTS trg_image_file_integrity_hash_insert;
        CREATE TRIGGER trg_image_file_integrity_hash_insert
        BEFORE INSERT ON image_file_integrity
        WHEN NEW.hash_status NOT IN ('unknown','ok','mismatch','unreadable','deferred')
        BEGIN
            SELECT RAISE(ABORT, 'invalid image_file_integrity.hash_status');
        END;

        DROP TRIGGER IF EXISTS trg_image_file_integrity_hash_update;
        CREATE TRIGGER trg_image_file_integrity_hash_update
        BEFORE UPDATE OF hash_status ON image_file_integrity
        WHEN NEW.hash_status NOT IN ('unknown','ok','mismatch','unreadable','deferred')
        BEGIN
            SELECT RAISE(ABORT, 'invalid image_file_integrity.hash_status');
        END;

        DROP TRIGGER IF EXISTS trg_image_file_integrity_recovery_insert;
        CREATE TRIGGER trg_image_file_integrity_recovery_insert
        BEFORE INSERT ON image_file_integrity
        WHEN NEW.recovery_status NOT IN ('none','pending','running','recovered','failed','blocked','no_source','cancelled')
        BEGIN
            SELECT RAISE(ABORT, 'invalid image_file_integrity.recovery_status');
        END;

        DROP TRIGGER IF EXISTS trg_image_file_integrity_recovery_update;
        CREATE TRIGGER trg_image_file_integrity_recovery_update
        BEFORE UPDATE OF recovery_status ON image_file_integrity
        WHEN NEW.recovery_status NOT IN ('none','pending','running','recovered','failed','blocked','no_source','cancelled')
        BEGIN
            SELECT RAISE(ABORT, 'invalid image_file_integrity.recovery_status');
        END;

        DROP TRIGGER IF EXISTS trg_file_recovery_attempt_status_insert;
        CREATE TRIGGER trg_file_recovery_attempt_status_insert
        BEFORE INSERT ON file_recovery_attempts
        WHEN NEW.status NOT IN ('running','recovered','failed','blocked','no_source','cancelled')
        BEGIN
            SELECT RAISE(ABORT, 'invalid file_recovery_attempts.status');
        END;

        DROP TRIGGER IF EXISTS trg_file_recovery_attempt_status_update;
        CREATE TRIGGER trg_file_recovery_attempt_status_update
        BEFORE UPDATE OF status ON file_recovery_attempts
        WHEN NEW.status NOT IN ('running','recovered','failed','blocked','no_source','cancelled')
        BEGIN
            SELECT RAISE(ABORT, 'invalid file_recovery_attempts.status');
        END;
        """
    )
