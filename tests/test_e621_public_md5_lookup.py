import sys
import types
import unittest

# Isolate tagger tests from optional image hashing dependency.
if "imagehash" not in sys.modules:
    imagehash = types.ModuleType("imagehash")
    imagehash.phash = lambda image: ""
    imagehash.hex_to_hash = lambda value: value
    sys.modules["imagehash"] = imagehash

from core.tagger.engine import Tagger


class FakeResponse:
    def __init__(self, data):
        self._data = data
        self.text = ""
        self.status_code = 200
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._data


class E621PublicMd5LookupTests(unittest.TestCase):
    def test_public_json_md5_lookup_runs_without_login_or_api_key(self):
        logs = []
        tagger = Tagger({"request_timeout_seconds": 5, "sites": {}}, logs.append)
        tagger._lookup_cache_enabled = False
        tagger.session_for_host = lambda host: object()
        calls = []
        wanted = "0123456789abcdef0123456789abcdef"
        post = {
            "id": 600001,
            "file": {"md5": wanted, "url": "https://static1.e621.net/file.jpg"},
            "tags": {
                "artist": ["clean_artist"],
                "general": ["solo"],
                "species": ["canine"],
            },
            "sources": ["https://example.invalid/source"],
        }

        def json_get(session, url, host, **kwargs):
            calls.append((url, kwargs.get("params", {}), kwargs.get("headers", {})))
            return FakeResponse({"posts": [post]})

        tagger._atf_get_cached = json_get
        tagger.tags_from_url = lambda url: self.fail("e621 must not use HTML tag fallback")

        tags, source, groups = tagger.engine_by_md5(
            {"domain": "e621.net", "type": "e621", "enabled": True},
            wanted,
        )

        self.assertTrue(calls, "e621 query was silently skipped when API credentials were empty")
        self.assertEqual(calls[0][0], "https://e621.net/posts.json")
        self.assertEqual(calls[0][1]["tags"], f"md5:{wanted}")
        self.assertNotIn("login", calls[0][1])
        self.assertNotIn("api_key", calls[0][1])
        self.assertIn("LocalBooru/3.1", calls[0][2]["User-Agent"])
        self.assertEqual(source, "https://e621.net/posts/600001")
        self.assertIn("clean_artist", tags)
        self.assertIn("solo", tags)
        self.assertIn("canine", tags)
        self.assertEqual(groups["species"], ["canine"])


if __name__ == "__main__":
    unittest.main()
