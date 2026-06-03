import sys
import types
import unittest

if "imagehash" not in sys.modules:
    imagehash = types.ModuleType("imagehash")
    imagehash.phash = lambda image: ""
    imagehash.hex_to_hash = lambda value: value
    sys.modules["imagehash"] = imagehash

from core.tagger.engine import Tagger, empty_tag_groups, add_tags_to_groups
from core.downloader_utils import _posts_api_url, _posts_api_url_bound, _post_referrer


class FakeResponse:
    def __init__(self, data=None):
        self._data = data
        self.text = ""
        self.status_code = 200
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._data


class DocumentedDapiHostTests(unittest.TestCase):
    def make_tagger(self):
        self.logs = []
        tagger = Tagger({"request_timeout_seconds": 5, "sites": {}}, self.logs.append)
        tagger._lookup_cache_enabled = False
        tagger.session_for_host = lambda host: object()
        # Avoid tag-category network calls; the test concerns post lookup contract.
        def as_general(host, tags):
            groups = empty_tag_groups()
            add_tags_to_groups(groups, "general", tags)
            return groups
        tagger._categorize_flat_tags = as_general
        return tagger

    def sites(self):
        return [
            {"domain": "xbooru.com", "type": "gelbooru_html", "enabled": True},
            # Old persisted settings may still say hypnohub; it must normalize to DAPI.
            {"domain": "hypnohub.net", "type": "hypnohub", "enabled": True},
        ]

    def test_md5_attempt_is_single_documented_json_dapi_request(self):
        wanted = "0123456789abcdef0123456789abcdef"
        tagger = self.make_tagger()
        for site in self.sites():
            with self.subTest(site=site["domain"]):
                attempts = tagger._engine_api_attempts(site, wanted)
                self.assertEqual(len(attempts), 1)
                url, params, fmt = attempts[0]
                self.assertEqual(url, f"https://{site['domain']}/index.php")
                self.assertEqual(params["page"], "dapi")
                self.assertEqual(params["s"], "post")
                self.assertEqual(params["q"], "index")
                self.assertEqual(params["json"], "1")
                self.assertEqual(params["tags"], f"md5:{wanted}")
                self.assertEqual(params["limit"], 1)
                self.assertEqual(fmt, "json")
                self.assertNotIn("search[md5]", params)

    def test_exact_match_uses_dapi_json_and_builds_view_url(self):
        wanted = "0123456789abcdef0123456789abcdef"
        for site in self.sites():
            with self.subTest(site=site["domain"]):
                tagger = self.make_tagger()
                calls = []
                post = {"id": 12345, "md5": wanted, "tags": "clean_tag second_tag"}
                tagger._atf_get_cached = lambda *args, **kwargs: calls.append(kwargs.get("params")) or FakeResponse([post])
                tagger._engine_html_fallback_by_md5 = lambda *args, **kwargs: self.fail("documented DAPI match must not use HTML")
                tagger.tags_from_url = lambda *args, **kwargs: self.fail("documented DAPI tags must remain JSON-only")
                tags, source, groups = tagger.engine_by_md5(site, wanted)
                self.assertEqual(len(calls), 1)
                self.assertEqual(tags, ["clean_tag", "second_tag"])
                self.assertEqual(source, f"https://{site['domain']}/index.php?page=post&s=view&id=12345")
                self.assertEqual(groups["general"], ["clean_tag", "second_tag"])
                self.assertTrue(any("TAG SOURCE: dapi_json_exact_md5" in line for line in self.logs))

    def test_empty_or_wrong_result_does_not_use_html(self):
        wanted = "0123456789abcdef0123456789abcdef"
        for post in (None, {"id": 19, "md5": "ffffffffffffffffffffffffffffffff", "tags": "wrong"}):
            for site in self.sites():
                with self.subTest(site=site["domain"], post=post):
                    tagger = self.make_tagger()
                    data = [] if post is None else [post]
                    tagger._atf_get_cached = lambda *args, **kwargs: FakeResponse(data)
                    tagger._engine_html_fallback_by_md5 = lambda *args, **kwargs: self.fail("DAPI miss must not run HTML")
                    tags, source, _ = tagger.engine_by_md5(site, wanted)
                    self.assertEqual(tags, [])
                    self.assertEqual(source, "")
                    self.assertTrue(any("DAPI JSON only" in line for line in self.logs))

    def test_empty_local_match_reports_live_dapi_probe_once(self):
        wanted = "0123456789abcdef0123456789abcdef"
        for site in self.sites():
            with self.subTest(site=site["domain"]):
                tagger = self.make_tagger()
                calls = []
                def fake_get(*args, **kwargs):
                    calls.append(kwargs.get("params") or {})
                    if len(calls) == 1:
                        return FakeResponse([])
                    return FakeResponse([{"id": 99, "md5": "f" * 32, "tags": "probe"}])
                tagger._atf_get_cached = fake_get
                tags, source, _ = tagger.engine_by_md5(site, wanted)
                self.assertEqual(tags, [])
                self.assertEqual(source, "")
                self.assertEqual(len(calls), 2)
                self.assertTrue(any("DAPI ENDPOINT ACTIVE" in line for line in self.logs))

    def test_downloader_uses_dapi_pagination_and_dapi_post_page_for_hypnohub(self):
        url, params = _posts_api_url("hypnohub.net", "artist_name", 2, limit=100)
        self.assertEqual(url, "https://hypnohub.net/index.php")
        self.assertEqual(params["json"], "1")
        self.assertEqual(params["pid"], 2)
        self.assertEqual(params["tags"], "artist_name")
        bounded_url, bounded_params = _posts_api_url_bound("hypnohub.net", "artist_name", 3, cursor_id=999, run_mode="old")
        self.assertEqual(bounded_url, "https://hypnohub.net/index.php")
        self.assertEqual(bounded_params["pid"], 3)
        self.assertEqual(bounded_params["page"], "dapi")
        self.assertNotIn("search[md5]", bounded_params)
        self.assertEqual(_post_referrer("hypnohub.net", {"id": 77}), "https://hypnohub.net/index.php?page=post&s=view&id=77")


if __name__ == "__main__":
    unittest.main()
