import sys
import types
import unittest

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


class Rule34XxxFastConveyorLaneTests(unittest.TestCase):
    def make_tagger(self):
        self.logs = []
        tagger = Tagger({"request_timeout_seconds": 5, "sites": {}}, self.logs.append)
        tagger._lookup_cache_enabled = False
        tagger.session_for_host = lambda host: object()
        return tagger

    def site(self):
        return {"domain": "rule34.xxx", "type": "rule34xxx", "enabled": True}

    def test_rule34xxx_uses_one_exact_json_api_request(self):
        tagger = self.make_tagger()
        wanted = "0123456789abcdef0123456789abcdef"
        attempts = tagger._engine_api_attempts(self.site(), wanted)
        self.assertEqual(len(attempts), 1)
        url, params, fmt = attempts[0]
        self.assertEqual(url, "https://api.rule34.xxx/index.php")
        self.assertEqual(fmt, "json")
        self.assertEqual(params["tags"], f"md5:{wanted}")
        self.assertEqual(params["json"], "1")
        self.assertEqual(params["limit"], 1)

    def test_match_returns_flat_tags_without_category_or_html_requests(self):
        wanted = "0123456789abcdef0123456789abcdef"
        tagger = self.make_tagger()
        calls = []
        post = {"id": 77, "md5": wanted, "tags": "tag_one tag_two tag_three"}
        tagger._atf_get_cached = lambda *args, **kwargs: calls.append(kwargs.get("params")) or FakeResponse([post])
        tagger._categorize_flat_tags = lambda *args, **kwargs: self.fail("rule34.xxx live lane must not categorize tag-by-tag")
        tagger.tags_from_url = lambda *args, **kwargs: self.fail("rule34.xxx live lane must not use HTML tags")
        tags, source, groups = tagger.engine_by_md5(self.site(), wanted)
        self.assertEqual(len(calls), 1)
        self.assertEqual(tags, ["tag_one", "tag_two", "tag_three"])
        self.assertEqual(source, "https://rule34.xxx/index.php?page=post&s=view&id=77")
        self.assertEqual(groups["general"], ["tag_one", "tag_two", "tag_three"])
        self.assertTrue(any("dapi_json_exact_md5_flat_fast" in line for line in self.logs))

    def test_miss_does_not_try_raw_xml_or_html(self):
        tagger = self.make_tagger()
        calls = []
        tagger._atf_get_cached = lambda *args, **kwargs: calls.append(kwargs.get("params")) or FakeResponse([])
        tagger._engine_html_fallback_by_md5 = lambda *args, **kwargs: self.fail("rule34.xxx miss must remain JSON-only")
        tags, source, _ = tagger.engine_by_md5(self.site(), "a" * 32)
        self.assertEqual(len(calls), 1)
        self.assertEqual(tags, [])
        self.assertEqual(source, "")
        self.assertTrue(any("DAPI JSON fast lane" in line for line in self.logs))


if __name__ == "__main__":
    unittest.main()
