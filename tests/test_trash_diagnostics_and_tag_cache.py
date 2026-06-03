import tempfile
import unittest
from pathlib import Path

from core.database.connection import db
from core.library_lifecycle import trash_rows, restore_from_trash, cleanup_live_exact_duplicates, purge_trash


class TrashDiagnosticsAndTagCacheTests(unittest.TestCase):
    def test_trash_rows_exposes_recorded_reason(self):
        with tempfile.TemporaryDirectory() as td:
            settings = {"sqlite_db_folder": td, "sqlite_connection_pool": False}
            original = "/archive/found/media/image.png"
            trashed = "/archive/trash/media/image.png"
            with db(settings, write=True) as con:
                con.execute(
                    """INSERT INTO images(path,file_name,bucket,size_bytes,deleted,lifecycle,original_media_path,trashed_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (trashed, "image.png", "found", 123, 1, "trash", original, 100),
                )
                con.execute(
                    "INSERT INTO delete_log(path,file_name,reason,tag_or_source,deleted_at) VALUES(?,?,?,?,?)",
                    (original, "image.png", "subscription_visual_duplicate", "", 100),
                )
            rows = trash_rows(settings)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["delete_reason"], "subscription_visual_duplicate")

    def test_tags_tab_and_gallery_tag_sidebar_keep_loaded_cache(self):
        root = Path(__file__).parents[1]
        tags_src = (root / "ui" / "tags_page.py").read_text(encoding="utf-8")
        gallery_src = (root / "ui" / "gallery_page.py").read_text(encoding="utf-8")
        self.assertIn("self._loaded=False; self._dirty=True", tags_src)
        self.assertIn("if not self._loaded or self._dirty:", tags_src)
        self.assertIn("self._global_tag_groups_cache = None", gallery_src)
        self.assertIn('getattr(self, "_global_tag_groups_cache", None)', gallery_src)

    def test_restore_does_not_create_live_exact_duplicate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            settings = {"sqlite_db_folder": str(root / "db"), "sqlite_connection_pool": False}
            live = root / "found" / "media" / "same.png"
            trash = root / "trash" / "media" / "same_copy.png"
            original = root / "found" / "media" / "restored.png"
            live.parent.mkdir(parents=True); trash.parent.mkdir(parents=True)
            live.write_bytes(b"identical bytes")
            trash.write_bytes(b"identical bytes")
            md5 = __import__('hashlib').md5(b"identical bytes").hexdigest()
            with db(settings, write=True) as con:
                con.execute("INSERT INTO images(path,file_name,bucket,hash_md5,deleted,lifecycle) VALUES(?,?,?,?,?,?)", (str(live), live.name, "found", md5, 0, "archive"))
                keep_id = int(con.execute("SELECT id FROM images WHERE path=?", (str(live),)).fetchone()["id"])
                con.execute("INSERT INTO tags(name,normalized_name,category) VALUES('restored_tag','restored_tag','general')")
                tag_id = int(con.execute("SELECT id FROM tags WHERE normalized_name='restored_tag'").fetchone()["id"])
                con.execute("INSERT INTO images(path,file_name,bucket,hash_md5,deleted,lifecycle,original_media_path) VALUES(?,?,?,?,?,?,?)", (str(trash), trash.name, "found", md5, 1, "trash", str(original)))
                trash_id = int(con.execute("SELECT id FROM images WHERE path=?", (str(trash),)).fetchone()["id"])
                con.execute("INSERT INTO image_tags(image_id,tag_id) VALUES(?,?)", (trash_id, tag_id))
            result = restore_from_trash(settings, [trash_id])
            self.assertEqual(result["restored"], 0)
            self.assertEqual(result["skipped_existing"], 1)
            self.assertFalse(original.exists())
            self.assertTrue(trash.exists())
            with db(settings, readonly=True) as con:
                row = con.execute("SELECT deleted,lifecycle FROM images WHERE id=?", (trash_id,)).fetchone()
                self.assertEqual((int(row["deleted"]), row["lifecycle"]), (1, "trash"))
                self.assertIsNotNone(con.execute("SELECT 1 FROM image_tags WHERE image_id=? AND tag_id=?", (keep_id, tag_id)).fetchone())

    def test_cleanup_exact_live_duplicates_moves_only_extra_and_merges_tags(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output = root / "Local_Booru_Output"
            settings = {"sqlite_db_folder": str(root / "db"), "sqlite_connection_pool": False, "output_dir": str(output)}
            a = output / "found" / "media" / "file.png"
            b = output / "found" / "media" / "file_2.png"
            a.parent.mkdir(parents=True)
            a.write_bytes(b"same exact content")
            b.write_bytes(b"same exact content")
            md5 = __import__('hashlib').md5(b"same exact content").hexdigest()
            with db(settings, write=True) as con:
                con.execute("INSERT INTO images(path,file_name,bucket,hash_md5,deleted,lifecycle,indexed_at) VALUES(?,?,?,?,?,?,?)", (str(a), a.name, "found", md5, 0, "archive", 1))
                keep_id = int(con.execute("SELECT id FROM images WHERE path=?", (str(a),)).fetchone()["id"])
                con.execute("INSERT INTO images(path,file_name,bucket,hash_md5,deleted,lifecycle,indexed_at) VALUES(?,?,?,?,?,?,?)", (str(b), b.name, "found", md5, 0, "archive", 2))
                extra_id = int(con.execute("SELECT id FROM images WHERE path=?", (str(b),)).fetchone()["id"])
                con.execute("INSERT INTO tags(name,normalized_name,category) VALUES('extra_tag','extra_tag','general')")
                tag_id = int(con.execute("SELECT id FROM tags WHERE normalized_name='extra_tag'").fetchone()["id"])
                con.execute("INSERT INTO image_tags(image_id,tag_id) VALUES(?,?)", (extra_id, tag_id))
            result = cleanup_live_exact_duplicates(settings, make_backup=False)
            self.assertEqual(result["groups"], 1)
            self.assertEqual(result["trashed_records"], 1)
            with db(settings, readonly=True) as con:
                self.assertEqual(int(con.execute("SELECT COUNT(*) FROM images WHERE deleted=0 AND hash_md5=?", (md5,)).fetchone()[0]), 1)
                self.assertIsNotNone(con.execute("SELECT 1 FROM image_tags WHERE image_id=? AND tag_id=?", (keep_id, tag_id)).fetchone())
                moved = con.execute("SELECT deleted,lifecycle FROM images WHERE id=?", (extra_id,)).fetchone()
                self.assertEqual((int(moved["deleted"]), moved["lifecycle"]), (1, "trash"))

    def test_purging_automatic_duplicate_does_not_poison_deleted_md5_registry(self):
        import core.deleted_registry as registry
        old_path, old_cache, old_mtime = registry.DELETED_FILES_FILE, registry._CACHE, registry._CACHE_MTIME
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                registry.DELETED_FILES_FILE = root / "deleted_files_ignore.json"
                registry._CACHE = None; registry._CACHE_MTIME = None
                output = root / "Local_Booru_Output"
                settings = {"sqlite_db_folder": str(root / "db"), "sqlite_connection_pool": False, "output_dir": str(output)}
                trashed = output / "trash" / "media" / "copy.png"
                original = output / "found" / "media" / "copy.png"
                trashed.parent.mkdir(parents=True); trashed.write_bytes(b"auto duplicate")
                md5 = __import__('hashlib').md5(b"auto duplicate").hexdigest()
                with db(settings, write=True) as con:
                    con.execute("INSERT INTO images(path,file_name,bucket,hash_md5,deleted,lifecycle,original_media_path,trashed_at) VALUES(?,?,?,?,?,?,?,?)", (str(trashed), trashed.name, "found", md5, 1, "trash", str(original), 10))
                    image_id = int(con.execute("SELECT id FROM images WHERE path=?", (str(trashed),)).fetchone()["id"])
                    con.execute("INSERT INTO delete_log(path,file_name,reason,tag_or_source,deleted_at) VALUES(?,?,?,?,?)", (str(original), original.name, "downloader_exact_duplicate", "", 10))
                result = purge_trash(settings, [image_id], make_backup=False)
                self.assertEqual(result["removed_records"], 1)
                self.assertFalse(registry.has_deleted_md5(md5))
        finally:
            registry.DELETED_FILES_FILE, registry._CACHE, registry._CACHE_MTIME = old_path, old_cache, old_mtime


if __name__ == "__main__":
    unittest.main()
