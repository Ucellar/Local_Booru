from __future__ import annotations

NAME = "duplicate_candidate_relations_v2"


def _columns(con, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _objects(con) -> set[tuple[str, str]]:
    names = (
        "duplicate_scan_runs",
        "idx_duplicate_relations_generation",
        "idx_duplicate_relations_distance",
        "idx_duplicate_scan_runs_started",
        "trg_duplicate_relations_normalized_insert",
        "trg_duplicate_relations_normalized_update",
        "trg_duplicate_relations_image_delete",
        "trg_duplicate_relations_image_deactivate",
    )
    placeholders = ",".join("?" for _ in names)
    return {
        (str(row[0]), str(row[1]))
        for row in con.execute(
            f"SELECT type,name FROM sqlite_master WHERE name IN ({placeholders})",
            names,
        ).fetchall()
    }


def is_applied(con) -> bool:
    relation_columns = {
        "id", "image_a", "image_b", "relation", "distance", "confidence", "status",
        "scan_generation", "source_revision", "md5_a", "md5_b", "last_seen_at",
        "created_at", "updated_at",
    }
    run_columns = {
        "id", "status", "threshold_percent", "max_distance", "source_revision",
        "vp_generation", "total_images", "processed_images", "candidate_pairs",
        "stored_pairs", "ignored_pairs", "stale_removed", "unindexed_files",
        "unindexed_samples_json", "started_at", "finished_at", "last_error",
    }
    required = {
        ("table", "duplicate_scan_runs"),
        ("index", "idx_duplicate_relations_generation"),
        ("index", "idx_duplicate_relations_distance"),
        ("index", "idx_duplicate_scan_runs_started"),
        ("trigger", "trg_duplicate_relations_normalized_insert"),
        ("trigger", "trg_duplicate_relations_normalized_update"),
        ("trigger", "trg_duplicate_relations_image_delete"),
        ("trigger", "trg_duplicate_relations_image_deactivate"),
    }
    return (
        relation_columns.issubset(_columns(con, "duplicate_relations"))
        and run_columns.issubset(_columns(con, "duplicate_scan_runs"))
        and required.issubset(_objects(con))
    )


def _ensure_column(con, table: str, column: str, ddl: str) -> None:
    if column not in _columns(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _normalise_existing_rows(con) -> None:
    """Normalise historical rows without discarding a reviewed decision.

    The table was experimental before v436 and could contain both orientations.
    Rebuild it in-place through Python so a reviewed status wins over an
    unreviewed duplicate when the two orientations collapse to one key.
    """
    rows = con.execute(
        """
        SELECT image_a,image_b,relation,distance,confidence,status,
               scan_generation,source_revision,md5_a,md5_b,last_seen_at,
               created_at,updated_at
        FROM duplicate_relations
        """
    ).fetchall()
    if not rows:
        return

    priority = {
        "confirmed_duplicate": 50,
        "alternative": 40,
        "not_duplicate": 40,
        "ignored": 30,
        "unreviewed": 0,
    }
    merged: dict[tuple[int, int, str], dict] = {}
    for row in rows:
        values = list(row)
        a = int(values[0] or 0)
        b = int(values[1] or 0)
        if a <= 0 or b <= 0 or a == b:
            continue
        md5_a = str(values[8] or "")
        md5_b = str(values[9] or "")
        if a > b:
            a, b = b, a
            md5_a, md5_b = md5_b, md5_a
        relation = str(values[2] or "potential_duplicate")
        key = (a, b, relation)
        item = {
            "image_a": a,
            "image_b": b,
            "relation": relation,
            "distance": int(values[3] or 0),
            "confidence": float(values[4] or 0.0),
            "status": str(values[5] or "unreviewed"),
            "scan_generation": int(values[6] or 0),
            "source_revision": int(values[7] or 0),
            "md5_a": md5_a,
            "md5_b": md5_b,
            "last_seen_at": int(values[10] or 0),
            "created_at": int(values[11] or 0),
            "updated_at": int(values[12] or 0),
        }
        old = merged.get(key)
        if old is None:
            merged[key] = item
            continue
        old["distance"] = min(int(old["distance"]), int(item["distance"]))
        old["confidence"] = max(float(old["confidence"]), float(item["confidence"]))
        old["scan_generation"] = max(int(old["scan_generation"]), int(item["scan_generation"]))
        old["source_revision"] = max(int(old["source_revision"]), int(item["source_revision"]))
        old["last_seen_at"] = max(int(old["last_seen_at"]), int(item["last_seen_at"]))
        old["created_at"] = min(x for x in (int(old["created_at"]), int(item["created_at"])) if x > 0) if (int(old["created_at"]) > 0 or int(item["created_at"]) > 0) else 0
        old["updated_at"] = max(int(old["updated_at"]), int(item["updated_at"]))
        if priority.get(item["status"], 10) > priority.get(old["status"], 10):
            old["status"] = item["status"]
            old["md5_a"] = item["md5_a"]
            old["md5_b"] = item["md5_b"]

    con.execute("DELETE FROM duplicate_relations")
    con.executemany(
        """
        INSERT INTO duplicate_relations(
            image_a,image_b,relation,distance,confidence,status,
            scan_generation,source_revision,md5_a,md5_b,last_seen_at,
            created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                item["image_a"], item["image_b"], item["relation"], item["distance"],
                item["confidence"], item["status"], item["scan_generation"],
                item["source_revision"], item["md5_a"], item["md5_b"],
                item["last_seen_at"], item["created_at"], item["updated_at"],
            )
            for item in merged.values()
        ],
    )


def apply(con) -> None:
    _ensure_column(con, "duplicate_relations", "scan_generation", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(con, "duplicate_relations", "source_revision", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(con, "duplicate_relations", "md5_a", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(con, "duplicate_relations", "md5_b", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(con, "duplicate_relations", "last_seen_at", "INTEGER NOT NULL DEFAULT 0")
    _normalise_existing_rows(con)
    con.execute(
        """
        DELETE FROM duplicate_relations
        WHERE NOT EXISTS(SELECT 1 FROM images a WHERE a.id=duplicate_relations.image_a AND a.deleted=0)
           OR NOT EXISTS(SELECT 1 FROM images b WHERE b.id=duplicate_relations.image_b AND b.deleted=0)
        """
    )

    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS duplicate_scan_runs (
            id INTEGER PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'running'
                CHECK(status IN ('running','completed','cancelled','failed')),
            threshold_percent INTEGER NOT NULL DEFAULT 85
                CHECK(threshold_percent BETWEEN 50 AND 100),
            max_distance INTEGER NOT NULL DEFAULT 9
                CHECK(max_distance BETWEEN 0 AND 32),
            source_revision INTEGER NOT NULL DEFAULT 0,
            vp_generation INTEGER NOT NULL DEFAULT 0,
            total_images INTEGER NOT NULL DEFAULT 0 CHECK(total_images>=0),
            processed_images INTEGER NOT NULL DEFAULT 0 CHECK(processed_images>=0),
            candidate_pairs INTEGER NOT NULL DEFAULT 0 CHECK(candidate_pairs>=0),
            stored_pairs INTEGER NOT NULL DEFAULT 0 CHECK(stored_pairs>=0),
            ignored_pairs INTEGER NOT NULL DEFAULT 0 CHECK(ignored_pairs>=0),
            stale_removed INTEGER NOT NULL DEFAULT 0 CHECK(stale_removed>=0),
            unindexed_files INTEGER NOT NULL DEFAULT 0 CHECK(unindexed_files>=0),
            unindexed_samples_json TEXT NOT NULL DEFAULT '[]',
            started_at INTEGER NOT NULL DEFAULT 0,
            finished_at INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_duplicate_relations_generation
            ON duplicate_relations(relation, scan_generation);
        CREATE INDEX IF NOT EXISTS idx_duplicate_relations_distance
            ON duplicate_relations(relation, status, distance, image_a, image_b);
        CREATE INDEX IF NOT EXISTS idx_duplicate_scan_runs_started
            ON duplicate_scan_runs(started_at DESC, id DESC);

        DROP TRIGGER IF EXISTS trg_duplicate_relations_normalized_insert;
        CREATE TRIGGER trg_duplicate_relations_normalized_insert
        BEFORE INSERT ON duplicate_relations
        WHEN NEW.image_a >= NEW.image_b
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_relations requires image_a < image_b');
        END;

        DROP TRIGGER IF EXISTS trg_duplicate_relations_normalized_update;
        CREATE TRIGGER trg_duplicate_relations_normalized_update
        BEFORE UPDATE OF image_a,image_b ON duplicate_relations
        WHEN NEW.image_a >= NEW.image_b
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_relations requires image_a < image_b');
        END;

        DROP TRIGGER IF EXISTS trg_duplicate_relations_image_delete;
        CREATE TRIGGER trg_duplicate_relations_image_delete
        AFTER DELETE ON images
        BEGIN
            DELETE FROM duplicate_relations WHERE image_a=OLD.id OR image_b=OLD.id;
        END;

        DROP TRIGGER IF EXISTS trg_duplicate_relations_image_deactivate;
        CREATE TRIGGER trg_duplicate_relations_image_deactivate
        AFTER UPDATE OF deleted ON images
        WHEN OLD.deleted=0 AND NEW.deleted<>0
        BEGIN
            DELETE FROM duplicate_relations WHERE image_a=NEW.id OR image_b=NEW.id;
        END;
        """
    )
