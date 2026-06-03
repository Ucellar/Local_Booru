import sys
import types
import unittest

# Isolate parser/lookup tests from optional image hashing dependency.
if "imagehash" not in sys.modules:
    imagehash = types.ModuleType("imagehash")
    imagehash.phash = lambda image: ""
    imagehash.hex_to_hash = lambda value: value
    sys.modules["imagehash"] = imagehash

from core.tagger.engine import Tagger, groups_to_tags


class FakeResponse:
    def __init__(self, data=None, text="", status_code=200, url="https://danbooru.donmai.us/posts.json"):
        self._data = data
        self.text = text
        self.status_code = status_code
        self.url = url
        self.headers = {"content-type": "application/json" if data is not None else "text/html"}

    def json(self):
        return self._data


class DanbooruRestrictedFallbackTests(unittest.TestCase):
    def make_tagger(self):
        self.logs = []
        tagger = Tagger({"request_timeout_seconds": 5, "sites": {}}, self.logs.append)
        tagger._lookup_cache_enabled = False
        self.session = object()
        tagger.session_for_host = lambda host: self.session
        return tagger

    def test_restricted_candidate_uses_href_tags_not_visible_text(self):
        tagger = self.make_tagger()
        api_calls = []
        html_calls = []
        post = {"id": 777, "file_url": None, "md5": None}
        html = '''
        <ul id="tag-list">
          <li class="flex tag-type-artist"><a class="search-tag" href="/posts?tags=artist_clean">artist clean</a> <span class="post-count">999k</span></li>
          <li class="flex tag-type-general"><a class="search-tag" href="/posts?tags=horse">horse</a> <span class="post-count">231k</span></li>
          <li class="flex tag-type-general"><a class="search-tag" href="/posts?tags=rating%3As">Safe</a></li>
        </ul>'''
        def api(session, url, host, **kwargs):
            api_calls.append((url, kwargs.get("params")))
            return FakeResponse([post])
        def html_get(session, url, **kwargs):
            html_calls.append(url)
            return FakeResponse(None, html, url=url)
        tagger._atf_get_cached = api
        tagger._http_get_cached = html_get

        tags, source, groups = tagger.engine_by_md5(
            {"domain": "danbooru.donmai.us", "type": "danbooru", "enabled": True},
            "0123456789abcdef0123456789abcdef",
        )
        self.assertEqual(tags, ["artist_clean", "horse"])
        self.assertEqual(groups["artist"], ["artist_clean"])
        self.assertEqual(groups["general"], ["horse"])
        self.assertNotIn("artist_clean_999k", tags)
        self.assertNotIn("horse_231k", tags)
        self.assertEqual(source, "https://danbooru.donmai.us/posts/777")
        self.assertEqual(len(api_calls), 1)
        self.assertEqual(len(html_calls), 1)
        self.assertTrue(any("RESTRICTED CANDIDATE" in x for x in self.logs))
        self.assertTrue(any("html_sidebar_href" in x for x in self.logs))

    def test_legacy_danbooru_entry_point_uses_same_restricted_path(self):
        tagger = self.make_tagger()
        post = {"id": 778, "md5": None}
        html = '<div class="tag-list categorized-tag-list"><ul class="general-tag-list"><li class="flex tag-type-general"><a class="search-tag" href="/posts?tags=clean_tag">junk</a><span class="post-count">4.2m</span></li></ul></div>'
        tagger._atf_get_cached = lambda *args, **kwargs: FakeResponse([post])
        tagger._http_get_cached = lambda *args, **kwargs: FakeResponse(None, html)
        tags, _, _ = tagger.danbooru_by_md5("0123456789abcdef0123456789abcdef")
        self.assertEqual(tags, ["clean_tag"])

    def test_restricted_candidate_does_not_parse_cloudflare_page(self):
        tagger = self.make_tagger()
        tagger._atf_get_cached = lambda *args, **kwargs: FakeResponse([{"id": 779, "md5": None}])
        tagger._http_get_cached = lambda *args, **kwargs: FakeResponse(None, '<title>Just a moment...</title><div class="tag-list"><li class="tag-type-general"><a class="search-tag" href="/posts?tags=garbage">garbage</a></li></div>')
        tags, _, groups = tagger.engine_by_md5(
            {"domain": "danbooru.donmai.us", "type": "danbooru", "enabled": True},
            "0123456789abcdef0123456789abcdef",
        )
        self.assertEqual(tags, [])
        self.assertEqual(groups_to_tags(groups), [])

    def test_normal_json_match_never_reads_html(self):
        tagger = self.make_tagger()
        post = {
            "id": 41,
            "md5": "0123456789abcdef0123456789abcdef",
            "tag_string_artist": "author_name",
            "tag_string_general": "solo",
        }
        tagger._atf_get_cached = lambda *args, **kwargs: FakeResponse([post])
        tagger._http_get_cached = lambda *args, **kwargs: self.fail("HTML must not be requested")
        tags, _, _ = tagger.engine_by_md5(
            {"domain": "danbooru.donmai.us", "type": "danbooru", "enabled": True},
            post["md5"],
        )
        self.assertEqual(tags, ["author_name", "solo"])
        self.assertTrue(any("TAG SOURCE: json_api" in x for x in self.logs))

    def test_empty_json_does_not_trigger_blind_html(self):
        tagger = self.make_tagger()
        tagger._atf_get_cached = lambda *args, **kwargs: FakeResponse([])
        tagger._http_get_cached = lambda *args, **kwargs: self.fail("HTML must not be requested")
        tags, source, groups = tagger.engine_by_md5(
            {"domain": "danbooru.donmai.us", "type": "danbooru", "enabled": True},
            "0123456789abcdef0123456789abcdef",
        )
        self.assertEqual(tags, [])
        self.assertEqual(source, "")
        self.assertEqual(groups_to_tags(groups), [])
        self.assertTrue(any("restricted HTML fallback not allowed" in x for x in self.logs))

    def test_different_exposed_md5_is_rejected_without_html(self):
        tagger = self.make_tagger()
        post = {"id": 99, "md5": "ffffffffffffffffffffffffffffffff", "tag_string_general": "wrong"}
        tagger._atf_get_cached = lambda *args, **kwargs: FakeResponse([post])
        tagger._http_get_cached = lambda *args, **kwargs: self.fail("HTML must not be requested")
        tags, _, _ = tagger.engine_by_md5(
            {"domain": "danbooru.donmai.us", "type": "danbooru", "enabled": True},
            "0123456789abcdef0123456789abcdef",
        )
        self.assertEqual(tags, [])
        self.assertTrue(any("JSON MD5 REJECT" in x for x in self.logs))


if __name__ == "__main__":
    unittest.main()
