import sys
import time
import types
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if "imagehash" not in sys.modules:
    imagehash = types.ModuleType("imagehash")
    imagehash.phash = lambda image: ""
    imagehash.hex_to_hash = lambda value: value
    sys.modules["imagehash"] = imagehash

from core.tagger.engine import Tagger
from core.database.storage import enqueue_reverse_retry, remove_reverse_retry, due_reverse_retry_paths, pending_reverse_retry_paths, pending_reverse_retry_info


class SauceNaoDeferredRetryTests(unittest.TestCase):
    def test_active_cooldown_returns_retry_not_nomatch(self):
        logs = []
        settings = {
            "enable_md5_lookup": False,
            "enable_saucenao": True,
            "saucenao_api_key": "test-key",
            "enable_iqdb": False,
            "enable_ascii2d": False,
            "request_timeout_seconds": 5,
            "skip_existing": False,
        }
        tagger = Tagger(settings, logs.append)
        future = int(time.time()) + 120
        tagger._load_saucenao_state = lambda: {"cooldown_until": future}
        fake_path = Path("not-written.png")
        with patch("core.tagger.engine.video_frame_image", return_value=fake_path), \
             patch("core.tagger.engine.file_phash", return_value=""), \
             patch("core.tagger.engine.upsert_nomatch") as no_match:
            result = tagger.process_image(fake_path)
        self.assertEqual(result, "retry_saucenao")
        self.assertFalse(no_match.called)
        self.assertGreaterEqual(tagger.saucenao_retry_after_epoch(), future)
        self.assertTrue(any("SAUCENAO DEFERRED" in line for line in logs))

    def test_sqlite_retry_queue_is_due_only_after_retry_time(self):
        with tempfile.TemporaryDirectory() as td:
            settings = {"sqlite_db_folder": td, "sqlite_connection_pool": False}
            path = Path(td) / "input.png"
            future = int(time.time()) + 120
            enqueue_reverse_retry(settings, path, service="saucenao", retry_after=future, reason="api_cooldown")
            count, next_retry = pending_reverse_retry_info(settings, service="saucenao")
            self.assertEqual(count, 1)
            self.assertEqual(next_retry, future)
            self.assertEqual(due_reverse_retry_paths(settings, service="saucenao", now=future - 1), [])
            due = due_reverse_retry_paths(settings, service="saucenao", now=future)
            self.assertEqual(due[0][0], path)
            remove_reverse_retry(settings, path, service="saucenao")
            self.assertEqual(pending_reverse_retry_info(settings, service="saucenao")[0], 0)

    def test_saved_future_retry_can_be_restored_on_restart(self):
        with tempfile.TemporaryDirectory() as td:
            settings = {"sqlite_db_folder": td, "sqlite_connection_pool": False}
            path = Path(td) / "waiting.png"
            future = int(time.time()) + 3600
            enqueue_reverse_retry(settings, path, service="saucenao", retry_after=future, reason="api_cooldown")
            restored = pending_reverse_retry_paths(settings, service="saucenao")
            self.assertEqual(restored, [(path, future, "api_cooldown")])
            self.assertEqual(due_reverse_retry_paths(settings, service="saucenao", now=future - 1), [])

    def test_saucenao_only_retry_does_not_replay_iqdb(self):
        settings = {
            "enable_md5_lookup": False,
            "enable_saucenao": True,
            "saucenao_api_key": "test-key",
            "enable_iqdb": True,
            "enable_ascii2d": True,
            "_saucenao_retry_only": True,
            "request_timeout_seconds": 5,
            "skip_existing": False,
        }
        tagger = Tagger(settings, lambda _m: None)
        tagger.saucenao_urls = lambda _p: []
        fake_path = Path("retry-only.png")
        with patch("core.tagger.engine.video_frame_image", return_value=fake_path), \
             patch("core.tagger.engine.file_phash", return_value=""), \
             patch.object(tagger, "iqdb_urls", side_effect=AssertionError("IQDB must not replay")), \
             patch.object(tagger, "ascii2d_urls", side_effect=AssertionError("Ascii2D must not replay")), \
             patch("core.tagger.engine.upsert_nomatch"), \
             patch("core.tagger.engine.copy_result_files"):
            result = tagger.process_image(fake_path)
        self.assertEqual(result, "nomatch")

if __name__ == "__main__":
    unittest.main()
