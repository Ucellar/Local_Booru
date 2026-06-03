import sys
import types
import unittest

if "imagehash" not in sys.modules:
    imagehash = types.ModuleType("imagehash")
    imagehash.phash = lambda image: ""
    imagehash.hex_to_hash = lambda value: value
    sys.modules["imagehash"] = imagehash

from core.tagger.engine import Tagger, atf_parse_post_view_html, groups_to_tags


class FakeResponse:
    def __init__(self, data=None, text=""):
        self._data = data
        self.text = text
        self.status_code = 200
        self.headers = {"content-type": "application/json" if data is not None else "text/html"}

    def json(self):
        return self._data


class ATFDocumentedJsonOnlyTests(unittest.TestCase):
    def make_tagger(self):
        self.logs = []
        tagger = Tagger({"request_timeout_seconds": 5, "sites": {}, "strict_atf_md5": True}, self.logs.append)
        tagger._lookup_cache_enabled = False
        tagger.session_for_host = lambda host: object()
        return tagger

    def site(self):
        return {"domain": "booru.allthefallen.moe", "type": "danbooru", "enabled": True}

    def test_atf_uses_only_documented_tags_md5_query(self):
        tagger = self.make_tagger()
        wanted = "0123456789abcdef0123456789abcdef"
        attempts = tagger._engine_api_attempts(self.site(), wanted)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0][0], "https://booru.allthefallen.moe/posts.json")
        self.assertEqual(attempts[0][1]["tags"], f"md5:{wanted}")
        self.assertNotIn("search[md5]", attempts[0][1])
        self.assertNotIn("md5", attempts[0][1])

    def test_empty_api_result_never_runs_atf_html_search(self):
        tagger = self.make_tagger()
        calls = []
        tagger._atf_get_cached = lambda *args, **kwargs: calls.append(kwargs.get("params")) or FakeResponse([])
        tagger._engine_html_fallback_by_md5 = lambda *args, **kwargs: self.fail("ATF must not search HTML after empty exact JSON")
        tags, source, groups = tagger.engine_by_md5(self.site(), "0123456789abcdef0123456789abcdef")
        self.assertEqual(len(calls), 1)
        self.assertEqual(tags, [])
        self.assertEqual(source, "")
        self.assertEqual(groups_to_tags(groups), [])
        self.assertTrue(any("JSON only: no exact API MD5 match" in line for line in self.logs))

    def test_unrelated_atf_post_is_rejected_without_html_rescue(self):
        tagger = self.make_tagger()
        wanted = "0123456789abcdef0123456789abcdef"
        wrong = {"id": 1541942, "md5": "ffffffffffffffffffffffffffffffff", "tag_string": "wrong_tag"}
        tagger._atf_get_cached = lambda *args, **kwargs: FakeResponse([wrong])
        tagger._engine_html_fallback_by_md5 = lambda *args, **kwargs: self.fail("ATF mismatch must not trigger HTML")
        tags, _, _ = tagger.engine_by_md5(self.site(), wanted)
        self.assertEqual(tags, [])
        self.assertTrue(any("MD5 REJECT" in line for line in self.logs))
        self.assertTrue(any("JSON only: no exact API MD5 match" in line for line in self.logs))

    def test_exact_json_match_does_not_enrich_tags_from_html(self):
        tagger = self.make_tagger()
        wanted = "0123456789abcdef0123456789abcdef"
        post = {"id": 1463547, "md5": wanted, "tag_string": "clean_tag second_tag"}
        tagger._atf_get_cached = lambda *args, **kwargs: FakeResponse([post])
        tagger.grouped_tags_from_url = lambda *args, **kwargs: self.fail("ATF automatic metadata must remain JSON-only")
        tagger.tags_from_url = lambda *args, **kwargs: self.fail("ATF automatic metadata must remain JSON-only")
        tags, source, groups = tagger.engine_by_md5(self.site(), wanted)
        self.assertEqual(tags, ["clean_tag", "second_tag"])
        self.assertEqual(source, "https://booru.allthefallen.moe/posts/1463547")
        self.assertEqual(groups["general"], ["clean_tag", "second_tag"])

    def test_dormant_atf_html_parser_reads_href_not_visible_count_text(self):
        html = '''<ul>
        <li class="tag-type-general"><a class="search-tag" href="/posts?tags=horse">horse 231k</a></li>
        <li class="tag-type-artist"><a class="search-tag" href="/posts?tags=clean_artist">clean artist 4.2m</a></li>
        </ul>'''
        tags, groups = atf_parse_post_view_html(html)
        self.assertEqual(tags, ["clean_artist", "horse"])
        self.assertEqual(groups["artist"], ["clean_artist"])
        self.assertEqual(groups["general"], ["horse"])
        self.assertNotIn("horse_231k", tags)
        self.assertNotIn("clean_artist_4.2m", tags)


if __name__ == "__main__":
    unittest.main()
