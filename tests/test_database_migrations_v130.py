import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.database.schema import init_db
from core.database.migrations import CURRENT_SCHEMA_VERSION
from core.database.migrations.runner import run_migrations, migration_status
from core.database.connection import db, set_writes_blocked, DatabaseWriteBlockedError
from core.database.maintenance import storage_report, vacuum
from core.stability import check_db_quick


class DatabaseMigrationsV130Tests(unittest.TestCase):
    def settings(self, root: str):
        return {"sqlite_db_folder": root, "sqlite_connection_pool": False}

    def test_fresh_database_runs_numbered_migrations(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        init_db(con, force=True)
        version = int(con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
        self.assertEqual(version, CURRENT_SCHEMA_VERSION)
        rows = con.execute("SELECT version,status FROM schema_migrations ORDER BY version").fetchall()
        self.assertEqual([(r[0], r[1]) for r in rows], [(13, "applied"), (14, "applied")])
        self.assertTrue(con.execute("SELECT 1 FROM sqlite_master WHERE name='maintenance_history'").fetchone())
        con.close()

    def test_existing_v13_working_database_is_baselined_then_upgraded(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript("CREATE TABLE images(id INTEGER PRIMARY KEY); CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL); INSERT INTO meta VALUES('schema_version','13');")
        result = run_migrations(con)
        self.assertEqual(result["version"], 14)
        self.assertIn("baseline:13", result["actions"])
        self.assertTrue(con.execute("SELECT 1 FROM maintenance_history LIMIT 1").fetchone() is None)
        self.assertEqual(migration_status(con)["current"], 14)
        con.close()

    def test_missing_db_quick_check_does_not_create_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = self.settings(tmp)
            path = Path(tmp) / "local_booru_index.sqlite3"
            self.assertTrue(check_db_quick(settings))
            self.assertFalse(path.exists())

    def test_write_safety_mode_blocks_database_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = self.settings(tmp)
            set_writes_blocked("test corruption")
            try:
                with self.assertRaises(DatabaseWriteBlockedError):
                    with db(settings, write=True):
                        pass
            finally:
                set_writes_blocked("")

    def test_storage_report_and_explicit_vacuum_are_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = self.settings(tmp)
            with db(settings, write=True) as con:
                con.execute("INSERT INTO images(path,file_name) VALUES(?,?)", (str(Path(tmp)/'x.jpg'), 'x.jpg'))
            report = storage_report(settings)
            self.assertEqual(report["schema_version"], CURRENT_SCHEMA_VERSION)
            result = vacuum(settings, make_backup=False)
            self.assertIn("reclaimed_bytes", result)


if __name__ == "__main__":
    unittest.main()
