import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from core.redaction import sanitize_text, sanitize_log_directory
from core.diagnostics import create_diagnostic_zip
from core.import_pipeline import register_media_import


class SecurityRedactionV121Tests(unittest.TestCase):
    def test_url_credentials_and_headers_are_removed(self):
        line = ("GET https://danbooru.donmai.us/posts/1.json?login=ucellar&api_key=SECRET&user_id=123 "
                "Authorization: Bearer abc.def.ghi\nCookie: auth=BAD; cf_clearance=BAD2")
        safe = sanitize_text(line)
        self.assertNotIn("SECRET", safe)
        self.assertNotIn("ucellar", safe)
        self.assertNotIn("abc.def.ghi", safe)
        self.assertNotIn("cf_clearance=BAD2", safe)
        self.assertIn("api_key=<removed>", safe)
        self.assertIn("login=<removed>", safe)

    def test_existing_raw_logs_are_sanitized_in_place(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "errors.log"
            p.write_text("url?api_key=RAWSECRET&login=ME&user_id=77", encoding="utf-8")
            result = sanitize_log_directory(td)
            clean = p.read_text(encoding="utf-8")
            self.assertEqual(result["changed"], 1)
            self.assertNotIn("RAWSECRET", clean)
            self.assertNotIn("login=ME", clean)

    def test_diagnostic_zip_never_exports_raw_historical_log_secrets(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"; logs.mkdir()
            raw_log = logs / "errors.log"
            raw_log.write_text("GET /?api_key=RAWSECRET&login=ME&user_id=77", encoding="utf-8")
            output = root / "diagnostic.zip"
            settings = {"sqlite_db_folder": str(root / "db"), "saucenao_api_key": "SAUCESECRET"}
            with patch("core.diagnostics.LOGS_DIR", logs), patch("core.diagnostics.ERROR_LOG_FILE", raw_log):
                create_diagnostic_zip(settings, output)
            with zipfile.ZipFile(output) as z:
                contents = "\n".join(z.read(name).decode("utf-8", errors="replace") for name in z.namelist())
            self.assertNotIn("RAWSECRET", contents)
            self.assertNotIn("SAUCESECRET", contents)
            self.assertNotIn("login=ME", contents)

    def test_exact_md5_reports_only_actually_new_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output = root / "Local_Booru_Output"
            media = output / "found" / "media"
            media.mkdir(parents=True)
            settings = {"sqlite_db_folder": str(root / "db"), "sqlite_connection_pool": False, "output_dir": str(output), "imports_to_inbox": False}
            a = media / "a.png"; b = media / "b.png"; c = media / "c.png"
            for p in (a, b, c): p.write_bytes(b"exact bytes")
            md5 = hashlib.md5(b"exact bytes").hexdigest()
            register_media_import(settings, a, sources=["https://site-a/post/1"], hash_md5=md5, status="tagged")
            added = register_media_import(settings, b, sources=["https://site-b/post/2"], hash_md5=md5, status="tagged")
            duplicate_metadata = register_media_import(settings, c, sources=["https://site-b/post/2"], hash_md5=md5, status="tagged")
            self.assertEqual(added["source_added"], 1)
            self.assertEqual(duplicate_metadata["source_added"], 0)


if __name__ == "__main__":
    unittest.main()
