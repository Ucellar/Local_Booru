import sys
import types
import unittest
import hashlib

if "imagehash" not in sys.modules:
    imagehash = types.ModuleType("imagehash")
    imagehash.phash = lambda image: ""
    imagehash.hex_to_hash = lambda value: value
    sys.modules["imagehash"] = imagehash

from core.tagger.engine import Tagger, empty_tag_groups, add_tags_to_groups
from core.downloader_utils import _posts_api_url


class FakeResponse:
    def __init__(self, data=None, text="", content_type=None):
        self._data = data
        self.text = text
        self.status_code = 200
        self.headers = {"content-type": content_type or ("application/json" if data is not None else "text/html")}

    def json(self):
        return self._data


class GelbooruAndRule34UsSafetyTests(unittest.TestCase):
    def make_tagger(self):
        self.logs = []
        tagger = Tagger({"request_timeout_seconds": 5, "sites": {}}, self.logs.append)
        tagger._lookup_cache_enabled = False
        tagger.session_for_host = lambda host: object()
        def as_general(host, tags):
            groups = empty_tag_groups()
            add_tags_to_groups(groups, "general", tags)
            return groups
        tagger._categorize_flat_tags = as_general
        return tagger

    def test_gelbooru_remains_single_exact_json_dapi_request(self):
        wanted = "0123456789abcdef0123456789abcdef"
        tagger = self.make_tagger()
        attempts = tagger._engine_api_attempts({"domain": "gelbooru.com", "type": "gelbooru_html"}, wanted)
        self.assertEqual(len(attempts), 1)
        url, params, fmt = attempts[0]
        self.assertEqual(url, "https://gelbooru.com/index.php")
        self.assertEqual(params["page"], "dapi")
        self.assertEqual(params["s"], "post")
        self.assertEqual(params["q"], "index")
        self.assertEqual(params["json"], "1")
        self.assertEqual(params["tags"], f"md5:{wanted}")
        self.assertEqual(params["limit"], 1)
        self.assertEqual(fmt, "json")

    def test_rule34us_has_no_automatic_api_attempts(self):
        tagger = self.make_tagger()
        attempts = tagger._engine_api_attempts({"domain": "rule34.us", "type": "rule34us"}, "a" * 32)
        self.assertEqual(attempts, [])
        api_url, params = _posts_api_url("rule34.us", "tag", 0)
        self.assertEqual(api_url, "")
        self.assertEqual(params, {})

    def test_rule34us_strict_html_accepts_only_verified_md5_and_href_tags(self):
        wanted = "0123456789abcdef0123456789abcdef"
        tagger = self.make_tagger()
        site = {"domain": "rule34.us", "type": "rule34us", "enabled": True}
        listing = """<a href='/index.php?page=post&s=view&id=71'>post</a>"""
        post = f"""
          <div>MD5: {wanted}</div>
          <li class='tag-type-general'><a href='/index.php?page=post&amp;s=list&amp;tags=horse'>horse 231k</a></li>
          <li class='tag-type-artist'><a href='/index.php?page=post&amp;s=list&amp;tags=clean_artist'>clean artist 14k</a></li>
        """
        search_params = []
        tagger._atf_get_cached = lambda *args, **kwargs: search_params.append(kwargs.get("params")) or FakeResponse(text=listing)
        tagger._http_get_cached = lambda *args, **kwargs: FakeResponse(text=post)
        tags, source, groups = tagger.engine_by_md5(site, wanted)
        self.assertEqual(search_params, [{"page": "post", "s": "list", "tags": f"md5:{wanted}"}])
        self.assertEqual(source, "https://rule34.us/index.php?page=post&s=view&id=71")
        self.assertIn("horse", tags)
        self.assertIn("clean_artist", tags)
        self.assertNotIn("horse_231k", tags)
        self.assertNotIn("clean_artist_14k", tags)
        self.assertEqual(groups["general"], ["horse"])
        self.assertEqual(groups["artist"], ["clean_artist"])
        self.assertTrue(any("TAG SOURCE: html_href_exact_md5" in line for line in self.logs))

    def test_rule34us_accepts_hidden_md5_after_hashing_original_media_bytes(self):
        payload = b"rule34.us exact original bytes"
        wanted = hashlib.md5(payload).hexdigest()
        tagger = self.make_tagger()
        site = {"domain": "rule34.us", "type": "rule34us", "enabled": True}
        listing = "<a href='/index.php?page=post&s=view&id=73'>post</a>"
        post = """
          <img id='image' src='https://img.rule34.us/files/original.bin'>
          <li class='tag-type-general'><a href='/index.php?page=post&amp;s=list&amp;tags=verified_tag'>verified tag 77k</a></li>
        """
        class MediaResponse:
            status_code = 200
            headers = {"content-type": "application/octet-stream"}
            def iter_content(self, chunk_size=1024):
                yield payload
        class MediaSession:
            def get(self, url, **kwargs):
                return MediaResponse()
        tagger.session_for_host = lambda host: MediaSession()
        tagger._atf_get_cached = lambda *args, **kwargs: FakeResponse(text=listing)
        tagger._http_get_cached = lambda *args, **kwargs: FakeResponse(text=post)
        tags, source, groups = tagger.engine_by_md5(site, wanted)
        self.assertEqual(source, "https://rule34.us/index.php?page=post&s=view&id=73")
        self.assertEqual(tags, ["verified_tag"])
        self.assertEqual(groups["general"], ["verified_tag"])
        self.assertTrue(any("REMOTE FILE MD5 VERIFIED" in line for line in self.logs))
        self.assertNotIn("verified_tag_77k", tags)

    def test_rule34us_html_rejects_post_without_explicit_md5(self):
        wanted = "0123456789abcdef0123456789abcdef"
        tagger = self.make_tagger()
        site = {"domain": "rule34.us", "type": "rule34us", "enabled": True}
        tagger._atf_get_cached = lambda *args, **kwargs: FakeResponse(text="<a href='/index.php?page=post&s=view&id=72'>post</a>")
        tagger._http_get_cached = lambda *args, **kwargs: FakeResponse(text="<a href='/index.php?page=post&s=list&tags=wrong'>wrong 100k</a>")
        tags, source, _ = tagger.engine_by_md5(site, wanted)
        self.assertEqual(tags, [])
        self.assertEqual(source, "")
        self.assertTrue(any("HTML MD5 REJECT" in line for line in self.logs))
        self.assertTrue(any("HTML only: no exact MD5-verified post match" in line for line in self.logs))

    def test_gelbooru_html_parser_remains_href_only(self):
        tagger = self.make_tagger()
        html = """
        <li class='tag-type-general'><a href='/index.php?page=post&amp;s=list&amp;tags=wolf'>wolf 2.5m</a></li>
        <li class='tag-type-artist'><a href='/index.php?page=post&amp;s=list&amp;tags=clean_artist'>clean artist 14k</a></li>
        """
        tags = tagger.gelbooru_tags_from_html(html)
        groups = tagger.gelbooru_groups_from_html(html)
        self.assertEqual(tags, ["wolf", "clean_artist"])
        self.assertEqual(groups["general"], ["wolf"])
        self.assertEqual(groups["artist"], ["clean_artist"])
        self.assertNotIn("wolf_2.5m", tags)


if __name__ == "__main__":
    unittest.main()
