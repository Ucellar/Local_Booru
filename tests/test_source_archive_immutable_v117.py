import hashlib
import tempfile
import unittest
from pathlib import Path

from core.database.connection import db
from core.database import repository
from core.library_lifecycle import trash_media_paths, restore_from_trash
import core.source_protection as source_protection
import core.library_diagnostics as library_diagnostics


class SourceArchiveImmutableV117Tests(unittest.TestCase):
    def _settings(self, root: Path):
        source = root / "original_archive"
        output = root / "Local_Booru_Output"
        source.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)
        return {"root": str(source), "output_dir": str(output), "sqlite_db_folder": str(root / "db"), "sqlite_connection_pool": False}, source, output

    def test_original_media_cannot_be_moved_to_trash(self):
        old_log = source_protection.EVENT_LOG
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td); settings, source, _output = self._settings(root)
                source_protection.EVENT_LOG = root / "logs" / "blocked.jsonl"
                original = source / "original.jpg"; original.write_bytes(b"never touch")
                result = trash_media_paths(settings, [original], reason="gallery_context_delete", make_backup=False)
                self.assertTrue(original.exists())
                self.assertEqual(result["trashed_files"], 0)
                self.assertEqual(result["protected_source_skipped"], 1)
                self.assertTrue(source_protection.EVENT_LOG.exists())
        finally:
            source_protection.EVENT_LOG = old_log

    def test_generated_output_still_moves_to_trash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); settings, _source, output = self._settings(root)
            generated = output / "found" / "media" / "generated.jpg"
            generated.parent.mkdir(parents=True, exist_ok=True); generated.write_bytes(b"generated")
            with db(settings, write=True) as con:
                con.execute("INSERT INTO images(path,file_name,bucket,hash_md5,deleted,lifecycle) VALUES(?,?,?,?,0,'archive')", (str(generated), generated.name, "found", hashlib.md5(b"generated").hexdigest()))
            result = trash_media_paths(settings, [generated], reason="gallery_context_delete", make_backup=False)
            self.assertEqual(result["trashed_files"], 1)
            self.assertFalse(generated.exists())
            self.assertTrue(any((output / "trash" / "media").iterdir()))

    def test_tagger_always_copies_source_into_generated_gallery_even_if_legacy_setting_off(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); settings, source, output = self._settings(root)
            settings["copy_results_enabled"] = False
            original = source / "seed.jpg"; original.write_bytes(b"immutable source bytes")
            from core.tagger.engine import copy_result_files
            archived = copy_result_files(settings, original, "tagged")
            self.assertNotEqual(archived.resolve(), original.resolve())
            self.assertTrue(str(archived.resolve()).startswith(str(output.resolve())))
            self.assertEqual(archived.read_bytes(), original.read_bytes())
            self.assertTrue(original.exists())

    def test_restore_never_writes_back_to_original_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); settings, source, output = self._settings(root)
            original = source / "keep.jpg"; original.write_bytes(b"original remains")
            trashed = output / "trash" / "media" / "recovered.jpg"
            trashed.parent.mkdir(parents=True, exist_ok=True); trashed.write_bytes(b"working copy")
            with db(settings, write=True) as con:
                cur = con.execute("INSERT INTO images(path,file_name,bucket,hash_md5,deleted,lifecycle,original_media_path) VALUES(?,?,?,?,1,'trash',?)", (str(trashed), trashed.name, "found", hashlib.md5(b"working copy").hexdigest(), str(original)))
                image_id = int(cur.lastrowid)
            result = restore_from_trash(settings, [image_id])
            self.assertEqual(result["restored"], 1)
            self.assertEqual(original.read_bytes(), b"original remains")
            restored = output / "found" / "media" / "recovered.jpg"
            self.assertTrue(restored.exists())
            self.assertEqual(restored.read_bytes(), b"working copy")

    def test_legacy_repository_delete_does_not_unlink_original_or_drop_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); settings, source, _output = self._settings(root)
            original = source / "original.png"; original.write_bytes(b"immutable")
            with db(settings, write=True) as con:
                cur = con.execute("INSERT INTO images(path,file_name,bucket,hash_md5,deleted,lifecycle) VALUES(?,?,?,?,0,'archive')", (str(original), original.name, "original", hashlib.md5(b"immutable").hexdigest()))
                image_id = int(cur.lastrowid)
            result = repository.delete_images(settings, [{"id": image_id, "path": str(original)}], delete_files=True)
            self.assertEqual(result["protected_source_skipped"], 1)
            self.assertTrue(original.exists())
            with db(settings, readonly=True) as con:
                self.assertIsNotNone(con.execute("SELECT id FROM images WHERE id=?", (image_id,)).fetchone())

    def test_diagnostics_exposes_blocked_source_mutation_events(self):
        old_log = source_protection.EVENT_LOG
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td); settings, source, _output = self._settings(root)
                source_protection.EVENT_LOG = root / "logs" / "blocked.jsonl"
                # create schema without modifying source bytes
                with db(settings, write=True):
                    pass
                original = source / "nope.jpg"; original.write_bytes(b"safe")
                trash_media_paths(settings, [original], reason="test", make_backup=False)
                report = library_diagnostics.audit_library(settings)
                self.assertEqual(report["source_protection"]["source_root"], str(source.resolve()))
                self.assertEqual(report["source_protection"]["blocked_count_shown"], 1)
        finally:
            source_protection.EVENT_LOG = old_log


if __name__ == "__main__":
    unittest.main()
