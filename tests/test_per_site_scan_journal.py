import sys
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

from core.database.storage import (mark_site_scanned, pending_site_scan_paths, site_scan_status_many, mark_processed, enqueue_tag_enrichment, seed_rule34_category_enrichment, pending_tag_enrichments, complete_tag_enrichment)
from core.tagger.engine import Tagger


class PerSiteScanJournalTests(unittest.TestCase):
    def test_new_site_leaves_previously_checked_site_done_but_file_pending(self):
        with tempfile.TemporaryDirectory() as td:
            settings = {"sqlite_db_folder": td, "sqlite_connection_pool": False}
            image = Path(td) / "image.png"
            mark_site_scanned(settings, image, "danbooru.donmai.us", engine="danbooru", outcome="match", checked_md5="a" * 32)
            pending, done, complete = pending_site_scan_paths(
                settings, [image], ["danbooru.donmai.us", "new.example"], scan_revision=1
            )
            self.assertEqual(pending, [image])
            self.assertEqual(done[str(image)]["danbooru.donmai.us"], "match")
            self.assertEqual(complete, 0)
            mark_site_scanned(settings, image, "new.example", engine="custom", outcome="miss", checked_md5="a" * 32)
            pending, done, complete = pending_site_scan_paths(
                settings, [image], ["danbooru.donmai.us", "new.example"], scan_revision=1
            )
            self.assertEqual(pending, [])
            self.assertEqual(complete, 1)
            self.assertEqual(done[str(image)]["new.example"], "miss")

    def test_revision_can_force_recheck_after_parser_change(self):
        with tempfile.TemporaryDirectory() as td:
            settings = {"sqlite_db_folder": td, "sqlite_connection_pool": False}
            image = Path(td) / "image.png"
            mark_site_scanned(settings, image, "e621.net", scan_revision=1, outcome="miss")
            self.assertTrue(site_scan_status_many(settings, [image], ["e621.net"], scan_revision=1))
            self.assertFalse(site_scan_status_many(settings, [image], ["e621.net"], scan_revision=2))

    def test_existing_found_is_enriched_without_new_media_copy(self):
        with tempfile.TemporaryDirectory() as td:
            archived = Path(td) / "found" / "media" / "image.png"
            archived.parent.mkdir(parents=True)
            archived.write_bytes(b"existing")
            original = Path(td) / "input" / "image.png"
            tagger = Tagger({"ignore_numeric_tags": False}, lambda _m: None)
            with patch("core.import_pipeline.register_media_import") as register:
                result = tagger.merge_conveyor_match_into_existing(
                    archived, original, ["new_tag"], ["https://new.example/posts/1"], [{"general": ["new_tag"]}]
                )
            self.assertEqual(result, "tagged")
            args, kwargs = register.call_args
            self.assertEqual(Path(args[1]), archived)
            self.assertEqual(kwargs["original_path"], str(original))
            self.assertTrue(kwargs["merge_existing"])
            self.assertFalse(kwargs["generate_thumbnail"])

    def test_rule34_category_jobs_are_durable_and_backfilled_from_journal(self):
        with tempfile.TemporaryDirectory() as td:
            settings = {"sqlite_db_folder": td, "sqlite_connection_pool": False}
            original = Path(td) / "input" / "image.png"
            archived = Path(td) / "found" / "media" / "image.png"
            archived.parent.mkdir(parents=True)
            archived.write_bytes(b"media")
            mark_processed(settings, archived, status="found", original_path=str(original))
            mark_site_scanned(settings, original, "rule34.xxx", outcome="match", source_url="https://rule34.xxx/index.php?page=post&s=view&id=77")
            seeded = seed_rule34_category_enrichment(settings)
            self.assertEqual(seeded, 1)
            jobs = pending_tag_enrichments(settings)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["source_url"], "https://rule34.xxx/index.php?page=post&s=view&id=77")
            complete_tag_enrichment(settings, original, jobs[0]["source_url"])
            self.assertEqual(pending_tag_enrichments(settings), [])

    def test_completed_category_job_is_not_reopened_by_duplicate_match(self):
        with tempfile.TemporaryDirectory() as td:
            settings = {"sqlite_db_folder": td, "sqlite_connection_pool": False}
            original = Path(td) / "input" / "image.png"
            archived = Path(td) / "found" / "media" / "image.png"
            url = "https://rule34.xxx/index.php?page=post&s=view&id=11"
            enqueue_tag_enrichment(settings, original, archived, url)
            complete_tag_enrichment(settings, original, url)
            enqueue_tag_enrichment(settings, original, archived, url)
            self.assertEqual(pending_tag_enrichments(settings), [])


if __name__ == "__main__":
    unittest.main()
