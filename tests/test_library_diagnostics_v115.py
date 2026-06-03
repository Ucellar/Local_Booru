import hashlib
import json
import logging
import tempfile
import unittest
from pathlib import Path

from core.database.connection import db
from core.import_pipeline import register_media_import
import core.library_diagnostics as diagnostics


class LibraryDiagnosticsV115Tests(unittest.TestCase):
    def test_read_only_audit_reports_exact_duplicates_and_metadata_gaps(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            settings = {"sqlite_db_folder": str(root / "db"), "sqlite_connection_pool": False, "sites": {"danbooru.donmai.us": {"enabled": True}}}
            a = root / "a.png"; b = root / "b.png"; a.write_bytes(b"same"); b.write_bytes(b"same")
            md5 = hashlib.md5(b"same").hexdigest()
            with db(settings, write=True) as con:
                con.execute("INSERT INTO images(path,file_name,bucket,hash_md5,deleted,lifecycle) VALUES(?,?,?,?,0,'archive')", (str(a), a.name, "found", md5))
                con.execute("INSERT INTO images(path,file_name,bucket,hash_md5,deleted,lifecycle) VALUES(?,?,?,?,0,'archive')", (str(b), b.name, "found", md5))
                before = int(con.execute("SELECT COUNT(*) FROM images").fetchone()[0])
            report = diagnostics.audit_library(settings)
            self.assertTrue(report["read_only"])
            self.assertEqual(report["md5"]["duplicate_groups"], 1)
            self.assertEqual(report["md5"]["redundant_rows"], 1)
            self.assertEqual(report["library"]["without_source"], 2)
            self.assertEqual(report["library"]["without_tags"], 2)
            with db(settings, readonly=True) as con:
                after = int(con.execute("SELECT COUNT(*) FROM images").fetchone()[0])
            self.assertEqual(before, after, "audit must never mutate library rows")

    def test_obsolete_deleted_block_is_reported_then_explicitly_removed_with_json_backup(self):
        old_dir = diagnostics.SETTINGS_DIR
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td); diagnostics.SETTINGS_DIR = root / "settings"; diagnostics.SETTINGS_DIR.mkdir()
                settings = {"sqlite_db_folder": str(root / "db"), "sqlite_connection_pool": False}
                content = b"live bytes"; md5 = hashlib.md5(content).hexdigest(); live = root / "live.png"; live.write_bytes(content)
                with db(settings, write=True) as con:
                    con.execute("INSERT INTO images(path,file_name,bucket,hash_md5,deleted,lifecycle) VALUES(?,?,?,?,0,'archive')", (str(live), live.name, "found", md5))
                reg = diagnostics.SETTINGS_DIR / "deleted_files_ignore.json"
                reg.write_text(json.dumps({"items": [{"md5": md5, "reason": "duplicate_delete"}, {"md5": "dead", "reason": "gallery_context_delete"}]}), encoding="utf-8")
                report = diagnostics.audit_library(settings)
                self.assertEqual(report["md5"]["obsolete_live_blocks"], 1)
                self.assertEqual(len(json.loads(reg.read_text())["items"]), 2, "audit must not clean registry")
                result = diagnostics.clear_obsolete_live_md5_blocks(settings)
                self.assertEqual(result["removed"], 1)
                self.assertTrue(Path(result["backup"]).exists())
                self.assertEqual(len(json.loads(reg.read_text())["items"]), 1)
        finally:
            diagnostics.SETTINGS_DIR = old_dir

    def test_exact_merge_writes_proof_log(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); output = root / "Local_Booru_Output"; media = output / "found" / "media"; media.mkdir(parents=True)
            settings = {"sqlite_db_folder": str(root / "db"), "sqlite_connection_pool": False, "output_dir": str(output), "imports_to_inbox": False}
            a = media / "a.png"; b = media / "b.png"; a.write_bytes(b"same content"); b.write_bytes(b"same content")
            md5 = hashlib.md5(b"same content").hexdigest()
            register_media_import(settings, a, groups={"general": ["a"]}, sources=["https://one/post/1"], hash_md5=md5)
            with self.assertLogs("local_booru.md5_invariant", level=logging.INFO) as captured:
                register_media_import(settings, b, groups={"general": ["b"]}, sources=["https://two/post/2"], hash_md5=md5)
            self.assertTrue(any("EXACT MD5 MERGE:" in line and "no_physical_copy_created=1" in line for line in captured.output))

    def test_saucenao_retry_proof_is_visible_in_read_only_audit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            settings = {"sqlite_db_folder": str(root / "db"), "sqlite_connection_pool": False}
            with db(settings, write=True) as con:
                con.execute(
                    "INSERT INTO task_log(task_type,status,message,created_at,updated_at) VALUES(?,?,?,?,?)",
                    ("saucenao_retry", "started_after_cooldown", "queued_file.png", 123, 123),
                )
            report = diagnostics.audit_library(settings)
            self.assertEqual(report["queues"]["saucenao_retry_events"][0]["status"], "started_after_cooldown")
            self.assertIn("started_after_cooldown", report["summary_text"])

    def test_missing_database_audit_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            settings = {"sqlite_db_folder": str(root / "no_db_yet"), "sqlite_connection_pool": False}
            target = diagnostics.db_path(settings) if hasattr(diagnostics, "db_path") else None
            self.assertFalse(target.exists())
            report = diagnostics.audit_library(settings)
            self.assertTrue(report["read_only"])
            self.assertFalse(target.exists(), "read-only audit must not create a new database")
            self.assertIn("База SQLite не найдена", report["summary_text"])

    def test_index_restore_only_creates_missing_schema_indices_after_backup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            settings = {"sqlite_db_folder": str(root / "db"), "sqlite_connection_pool": False}
            with db(settings, write=True) as con:
                con.execute("DROP INDEX IF EXISTS idx_images_md5")
            report = diagnostics.audit_library(settings)
            self.assertIn("idx_images_md5", report["indices"]["missing"])
            result = diagnostics.restore_critical_indices(settings)
            self.assertIn("idx_images_md5", result["restored"])
            self.assertEqual(result["missing_after"], [])
            self.assertTrue(Path(result["backup"]).exists())


if __name__ == "__main__":
    unittest.main()
