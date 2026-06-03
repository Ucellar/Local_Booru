import tempfile
import unittest
from pathlib import Path

from core.database.connection import db
from core.database.indexer import index_library
from core.services.service_state import set_cooldown, get_cooldown
from core.services.media_storage_service import copy_into_managed
from core.nomatch_db import upsert_nomatch, list_nomatches, remove_nomatch
from core.deleted_registry import mark_deleted, has_deleted_md5


class CoreCleanupV128Tests(unittest.TestCase):
    def settings(self, root: Path):
        output = root / "Local_Booru_Output"
        return {"sqlite_db_folder": str(root / "db"), "sqlite_connection_pool": False, "output_dir": str(output), "root": str(root / "source")}

    def test_normal_index_does_not_read_sidecars_but_explicit_migration_does(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); settings = self.settings(root)
            media = Path(settings["output_dir"]) / "found" / "media"; media.mkdir(parents=True)
            image = media / "a.png"; image.write_bytes(b"not-a-real-png")
            image.with_suffix(".tags.txt").write_text("legacy_tag", encoding="utf-8")
            image.with_suffix(".sources.txt").write_text("https://old.example/post/1\n", encoding="utf-8")
            index_library(settings, force=True, import_legacy_sidecars=False)
            with db(settings, readonly=True) as con:
                self.assertEqual(int(con.execute("SELECT COUNT(*) FROM image_tags").fetchone()[0]), 0)
                self.assertEqual(int(con.execute("SELECT COUNT(*) FROM image_sources").fetchone()[0]), 0)
            index_library(settings, force=True, import_legacy_sidecars=True)
            with db(settings, readonly=True) as con:
                self.assertEqual(int(con.execute("SELECT COUNT(*) FROM image_tags").fetchone()[0]), 1)
                self.assertEqual(int(con.execute("SELECT COUNT(*) FROM image_sources").fetchone()[0]), 1)

    def test_service_cooldown_and_nomatch_are_sqlite_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); settings = self.settings(root)
            set_cooldown(settings, "saucenao", 12345, reason="quota")
            self.assertEqual(get_cooldown(settings, "saucenao")["cooldown_until"], 12345)
            source = root / "source" / "x.jpg"; source.parent.mkdir(); source.write_bytes(b"x")
            upsert_nomatch(source, settings=settings, media_path=root / "out.jpg")
            self.assertEqual(len(list_nomatches(settings=settings)), 1)
            remove_nomatch(source, settings=settings)
            self.assertEqual(len(list_nomatches(settings=settings)), 0)

    def test_auto_deleted_md5_does_not_block_but_manual_does(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); settings = self.settings(root); p = root / "x.jpg"; p.write_bytes(b"same")
            md5 = "51037a4a37730f52c8732586d3aaa316"
            mark_deleted(p, md5=md5, reason="exact_md5_auto_normalized", settings=settings, manual_delete=False)
            self.assertFalse(has_deleted_md5(md5, settings=settings))
            mark_deleted(p, md5=md5, reason="gallery_context_delete", settings=settings, manual_delete=True)
            self.assertTrue(has_deleted_md5(md5, settings=settings))

    def test_copy_to_output_never_overwrites_different_same_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); settings = self.settings(root)
            src1 = root / "source" / "same.jpg"; src1.parent.mkdir(); src1.write_bytes(b"one")
            src2 = root / "other" / "same.jpg"; src2.parent.mkdir(); src2.write_bytes(b"two")
            dest = Path(settings["output_dir"]) / "found" / "media" / "same.jpg"
            first = copy_into_managed(settings, src1, dest)
            second = copy_into_managed(settings, src2, dest)
            self.assertEqual(first.read_bytes(), b"one")
            self.assertNotEqual(first, second)
            self.assertEqual(second.read_bytes(), b"two")


if __name__ == "__main__":
    unittest.main()
