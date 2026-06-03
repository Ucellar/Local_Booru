import hashlib
import tempfile
import unittest
from pathlib import Path

from core.database.connection import db
from core.import_pipeline import register_media_import
from core.library_lifecycle import cleanup_live_exact_duplicates


class ExactMd5SingleMediaInvariantTests(unittest.TestCase):
    def test_second_identical_output_file_becomes_sources_and_tags_on_one_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output = root / "Local_Booru_Output"
            media = output / "found" / "media"
            media.mkdir(parents=True)
            settings = {
                "sqlite_db_folder": str(root / "db"),
                "sqlite_connection_pool": False,
                "output_dir": str(output),
                "imports_to_inbox": False,
            }
            a = media / "same_a.png"
            b = media / "same_b.png"
            content = b"same exact media bytes"
            a.write_bytes(content)
            b.write_bytes(content)
            md5 = hashlib.md5(content).hexdigest()

            first = register_media_import(
                settings, a, groups={"general": ["tag_a"]},
                sources=["https://danbooru.donmai.us/posts/1"],
                hash_md5=md5, status="tagged", merge_existing=True,
            )
            second = register_media_import(
                settings, b, groups={"general": ["tag_b"]},
                sources=["https://gelbooru.com/index.php?page=post&s=view&id=2"],
                hash_md5=md5, status="tagged", merge_existing=True,
            )

            self.assertEqual(first["action"], "imported")
            self.assertEqual(second["action"], "merged_exact_md5")
            self.assertEqual(second["canonical_path"], str(a))
            self.assertTrue(a.exists())
            self.assertFalse(b.exists(), "transient exact copy should not remain on disk")
            with db(settings, readonly=True) as con:
                live = con.execute("SELECT COUNT(*) FROM images WHERE deleted=0 AND hash_md5=?", (md5,)).fetchone()[0]
                sources = con.execute("SELECT COUNT(*) FROM image_sources WHERE image_id=?", (first["image_id"],)).fetchone()[0]
                tags = con.execute("SELECT COUNT(*) FROM image_tags WHERE image_id=?", (first["image_id"],)).fetchone()[0]
            self.assertEqual(int(live), 1)
            self.assertEqual(int(sources), 2)
            self.assertEqual(int(tags), 2)

    def test_normalization_calculates_missing_hashes_and_merges_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output = root / "Local_Booru_Output"
            media = output / "found" / "media"
            media.mkdir(parents=True)
            settings = {"sqlite_db_folder": str(root / "db"), "sqlite_connection_pool": False, "output_dir": str(output)}
            a = media / "old_a.png"
            b = media / "old_b.png"
            a.write_bytes(b"old exact duplicate")
            b.write_bytes(b"old exact duplicate")
            with db(settings, write=True) as con:
                con.execute("INSERT INTO images(path,file_name,bucket,deleted,lifecycle,indexed_at) VALUES(?,?,?,?,?,?)", (str(a), a.name, "found", 0, "archive", 1))
                keep_id = int(con.execute("SELECT id FROM images WHERE path=?", (str(a),)).fetchone()["id"])
                con.execute("INSERT INTO images(path,file_name,bucket,deleted,lifecycle,indexed_at) VALUES(?,?,?,?,?,?)", (str(b), b.name, "found", 0, "archive", 2))
                extra_id = int(con.execute("SELECT id FROM images WHERE path=?", (str(b),)).fetchone()["id"])
                con.execute("INSERT INTO sources(host,url) VALUES('a.example','https://a.example/post/1')")
                sa = int(con.execute("SELECT id FROM sources WHERE host='a.example'").fetchone()["id"])
                con.execute("INSERT INTO sources(host,url) VALUES('b.example','https://b.example/post/2')")
                sb = int(con.execute("SELECT id FROM sources WHERE host='b.example'").fetchone()["id"])
                con.execute("INSERT INTO image_sources(image_id,source_id) VALUES(?,?)", (keep_id, sa))
                con.execute("INSERT INTO image_sources(image_id,source_id) VALUES(?,?)", (extra_id, sb))
            result = cleanup_live_exact_duplicates(settings, make_backup=False)
            self.assertEqual(result["hashed_missing"], 2)
            self.assertEqual(result["groups"], 1)
            self.assertEqual(result["merged_existing"], 1)
            with db(settings, readonly=True) as con:
                self.assertEqual(int(con.execute("SELECT COUNT(*) FROM images WHERE deleted=0").fetchone()[0]), 1)
                self.assertEqual(int(con.execute("SELECT COUNT(*) FROM image_sources WHERE image_id=?", (keep_id,)).fetchone()[0]), 2)
                row = con.execute("SELECT deleted,lifecycle FROM images WHERE id=?", (extra_id,)).fetchone()
                self.assertEqual((int(row["deleted"]), row["lifecycle"]), (1, "trash"))

    def test_live_canonical_source_merge_overrides_stale_deleted_md5_block(self):
        import core.deleted_registry as registry
        old_path, old_cache, old_mtime = registry.DELETED_FILES_FILE, registry._CACHE, registry._CACHE_MTIME
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                output = root / "Local_Booru_Output"
                media = output / "found" / "media"
                media.mkdir(parents=True)
                registry.DELETED_FILES_FILE = root / "deleted_ignore.json"
                registry._CACHE = None; registry._CACHE_MTIME = None
                settings = {"sqlite_db_folder": str(root / "db"), "sqlite_connection_pool": False, "output_dir": str(output), "imports_to_inbox": False}
                live = media / "live.png"
                live.write_bytes(b"existing content")
                md5 = hashlib.md5(b"existing content").hexdigest()
                register_media_import(settings, live, groups={"general": ["a"]}, sources=["https://site-a/post/1"], hash_md5=md5, status="tagged", merge_existing=True)
                registry.mark_deleted(live, reason="legacy_wrong_block", md5=md5)
                self.assertTrue(registry.has_deleted_md5(md5))
                result = register_media_import(settings, live, groups={"general": ["b"]}, sources=["https://site-b/post/2"], hash_md5=md5, status="tagged", merge_existing=True)
                self.assertNotEqual(result["action"], "skip_deleted")
                self.assertTrue(live.exists())
                self.assertFalse(registry.has_deleted_md5(md5))
                with db(settings, readonly=True) as con:
                    image_id = int(con.execute("SELECT id FROM images WHERE path=? AND deleted=0", (str(live),)).fetchone()["id"])
                    self.assertEqual(int(con.execute("SELECT COUNT(*) FROM image_sources WHERE image_id=?", (image_id,)).fetchone()[0]), 2)
        finally:
            registry.DELETED_FILES_FILE, registry._CACHE, registry._CACHE_MTIME = old_path, old_cache, old_mtime


if __name__ == "__main__":
    unittest.main()
