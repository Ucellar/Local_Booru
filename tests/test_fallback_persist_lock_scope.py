import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

if "imagehash" not in sys.modules:
    imagehash = types.ModuleType("imagehash")
    imagehash.phash = lambda image: ""
    imagehash.hex_to_hash = lambda value: value
    sys.modules["imagehash"] = imagehash

from core.tagger.engine import Tagger


class ProbeLock:
    def __init__(self):
        self.active = False
        self.enter_count = 0

    def __enter__(self):
        if self.active:
            raise AssertionError("unexpected recursive persistence lock")
        self.active = True
        self.enter_count += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self.active = False
        return False


class FallbackPersistLockScopeTests(unittest.TestCase):
    def test_reverse_network_runs_outside_persist_lock_but_save_is_locked(self):
        settings = {
            "enable_md5_lookup": False,
            "enable_saucenao": True,
            "saucenao_api_key": "test-key",
            "enable_iqdb": False,
            "enable_ascii2d": False,
            "request_timeout_seconds": 5,
            "skip_existing": False,
        }
        tagger = Tagger(settings, lambda _m: None)
        lock = ProbeLock()
        path = Path("fallback-lock-scope.png")

        def sauce_urls(_path):
            self.assertFalse(lock.active, "network lookup must not hold persistence lock")
            return [("https://danbooru.donmai.us/posts/1", 99.0)]

        def write_tags(*_args, **_kwargs):
            self.assertTrue(lock.active, "final output write must be serialized")

        tagger.saucenao_urls = sauce_urls
        tagger.tags_from_url = lambda _url: ["safe_tag"]
        tagger.grouped_tags_from_url = lambda _url: {"general": ["safe_tag"]}
        with patch("core.tagger.engine.video_frame_image", return_value=path), \
             patch("core.tagger.engine.file_phash", return_value=""), \
             patch("core.tagger.engine.write_sidecar_tags", side_effect=write_tags), \
             patch("core.tagger.engine.remove_nomatch"), \
             patch("core.tagger.engine.copy_result_files"), \
             patch("core.tagger.engine.cleanup_archived_result"):
            result = tagger.process_image(path, persist_lock=lock)
        self.assertEqual(result, "tagged")
        self.assertEqual(lock.enter_count, 1)
        self.assertFalse(lock.active)


if __name__ == "__main__":
    unittest.main()
