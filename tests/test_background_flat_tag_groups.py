import sys
import types
import tempfile
import unittest
from pathlib import Path

if "imagehash" not in sys.modules:
    imagehash = types.ModuleType("imagehash")
    imagehash.phash = lambda image: ""
    imagehash.hex_to_hash = lambda value: value
    sys.modules["imagehash"] = imagehash

from core.database.storage import mark_processed, mark_site_scanned, seed_background_tag_enrichment, pending_tag_enrichments
from core.tagger.engine import Tagger


class FakeResponse:
    def __init__(self, data):
        self._data = data
        self.text = ""
        self.status_code = 200
        self.headers = {"content-type": "application/json"}
    def json(self):
        return self._data


class BackgroundFlatTagGroupTests(unittest.TestCase):
    def test_gelbooru_live_lane_returns_flat_tags_without_category_requests(self):
        wanted = "0123456789abcdef0123456789abcdef"
        logs = []
        tagger = Tagger({"request_timeout_seconds": 5, "sites": {}}, logs.append)
        tagger._lookup_cache_enabled = False
        tagger.session_for_host = lambda host: object()
        tagger._atf_get_cached = lambda *a, **kw: FakeResponse([{"id": 8, "md5": wanted, "tags": "alpha beta"}])
        tagger._categorize_flat_tags = lambda *a, **kw: self.fail("Gelbooru live lane must defer grouping to background")
        tags, source, groups = tagger.engine_by_md5({"domain": "gelbooru.com", "type": "gelbooru_html", "enabled": True}, wanted)
        self.assertEqual(tags, ["alpha", "beta"])
        self.assertEqual(groups["general"], ["alpha", "beta"])
        self.assertEqual(source, "https://gelbooru.com/index.php?page=post&s=view&id=8")

    def test_generic_queue_backfills_gelbooru_and_rule34_matches(self):
        with tempfile.TemporaryDirectory() as td:
            settings = {"sqlite_db_folder": td, "sqlite_connection_pool": False}
            for host, post_id in (("gelbooru.com", "7"), ("rule34.xxx", "8")):
                original = Path(td) / "input" / f"{host}.png"
                media = Path(td) / "found" / "media" / f"{host}.png"
                media.parent.mkdir(parents=True, exist_ok=True)
                media.write_bytes(b"x")
                mark_processed(settings, media, status="found", original_path=str(original))
                mark_site_scanned(settings, original, host, outcome="match", source_url=f"https://{host}/index.php?page=post&s=view&id={post_id}")
            seeded = seed_background_tag_enrichment(settings)
            jobs = pending_tag_enrichments(settings, job_key="flat-sites::tag-groups-v2")
            self.assertEqual(seeded, 2)
            self.assertEqual({job["source_url"].split('/')[2] for job in jobs}, {"gelbooru.com", "rule34.xxx"})

    def test_fallback_flat_source_defers_category_network_work(self):
        tagger = Tagger({"tagger_background_tag_groups": True}, lambda _m: None)
        tagger.grouped_tags_from_url = lambda _u: self.fail("fallback must not classify flat source inline")
        groups = tagger.groups_or_defer_background("https://gelbooru.com/index.php?page=post&s=view&id=99", ["alpha", "beta"])
        self.assertEqual(groups["general"], ["alpha", "beta"])
        self.assertEqual(tagger.take_background_group_urls(), ["https://gelbooru.com/index.php?page=post&s=view&id=99"])

if __name__ == "__main__":
    unittest.main()
