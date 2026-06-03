import tempfile
import unittest
from pathlib import Path

from core.database.connection import db
from core.performance_audit import audit_query_performance
from core.settings_bundle import export_profile, read_profile, exported_settings


class ReleaseCompletionV120Tests(unittest.TestCase):
    def test_settings_profile_redacts_credentials_by_default(self):
        settings = {
            "appearance": "abyss",
            "saucenao_api_key": "secret-key",
            "sites": {"site": {"login": "me", "api_key": "site-secret", "enabled": True}},
            "thumb_memory_items": 400,
        }
        safe = exported_settings(settings, include_secrets=False)
        self.assertEqual(safe["appearance"], "abyss")
        self.assertEqual(safe["saucenao_api_key"], "")
        self.assertEqual(safe["sites"]["site"]["api_key"], "")
        self.assertEqual(safe["sites"]["site"]["login"], "")
        self.assertEqual(exported_settings(settings, include_secrets=True)["saucenao_api_key"], "secret-key")

    def test_settings_profile_zip_never_contains_media_or_database(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "profile.zip"
            result = export_profile({"appearance": "slate", "api_key": "x"}, out)
            manifest, settings = read_profile(result)
            self.assertEqual(settings["appearance"], "slate")
            self.assertEqual(settings["api_key"], "")
            self.assertFalse(manifest["includes_database"])
            self.assertFalse(manifest["includes_media"])

    def test_sql_performance_audit_is_read_only_and_reports_queries(self):
        with tempfile.TemporaryDirectory() as td:
            settings = {"sqlite_db_folder": str(Path(td) / "db"), "sqlite_connection_pool": False, "performance_slow_ms": 0}
            with db(settings, write=True) as con:
                cur = con.execute("INSERT INTO images(path,file_name,bucket,hash_md5,deleted,lifecycle) VALUES(?,?,?,?,0,'archive')", (str(Path(td)/'a.png'), 'a.png', 'found', 'md5'))
                image_id = int(cur.lastrowid)
                source_id = int(con.execute("INSERT INTO sources(host,url) VALUES(?,?)", ('site', 'https://site/post/1')).lastrowid)
                con.execute("INSERT INTO image_sources(image_id,source_id) VALUES(?,?)", (image_id, source_id))
                tag_id = int(con.execute("INSERT INTO tags(name,normalized_name,category) VALUES(?,?,?)", ('tag', 'tag', 'general')).lastrowid)
                con.execute("INSERT INTO image_tags(image_id,tag_id) VALUES(?,?)", (image_id, tag_id))
                before = int(con.execute("SELECT COUNT(*) FROM images").fetchone()[0])
            result = audit_query_performance(settings)
            self.assertTrue(result["read_only"])
            self.assertGreaterEqual(len(result["queries"]), 8)
            self.assertTrue(all(not row.get("error") for row in result["queries"]), result["queries"])
            with db(settings, readonly=True) as con:
                after = int(con.execute("SELECT COUNT(*) FROM images").fetchone()[0])
            self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
