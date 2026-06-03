import tempfile
import unittest
from pathlib import Path

from core.database.connection import db
from core.database.repository import count_search_items, search_items


class GalleryFavoritePaginationTests(unittest.TestCase):
    def test_favorites_filter_is_applied_before_pagination(self):
        """A favourite from page two must become page one of the filtered result."""
        with tempfile.TemporaryDirectory() as td:
            settings = {"sqlite_db_folder": td, "sqlite_connection_pool": False}
            with db(settings, write=True) as con:
                for n in range(64):
                    path = f"/archive/{n:03}.png"
                    con.execute(
                        "INSERT INTO images(path,file_name,bucket,deleted,favorite) VALUES(?,?,?,?,?)",
                        (path, f"{n:03}.png", "found", 0, 1 if n == 40 else 0),
                    )
            favorite_where = ["COALESCE(i.favorite, 0) = 1"]
            total = count_search_items(settings, "", "all", "all", extra_where=favorite_where, extra_params=[])
            first_page = search_items(settings, "", "all", "all", limit=32, offset=0, extra_where=favorite_where, extra_params=[])
            second_page = search_items(settings, "", "all", "all", limit=32, offset=32, extra_where=favorite_where, extra_params=[])
            self.assertEqual(total, 1)
            self.assertEqual([item["path"] for item in first_page], ["/archive/040.png"])
            self.assertEqual(second_page, [])

    def test_gallery_does_not_client_filter_only_visible_page(self):
        source = (Path(__file__).parents[1] / "ui" / "gallery_page.py").read_text(encoding="utf-8")
        apply_block = source[source.index("    def _apply_filter_impl"):source.index("    def current_result_image_ids")]
        render_block = source[source.index("    def _render_page"):source.index("    def adopt_viewer_page")]
        self.assertIn('_extra_where.append("COALESCE(i.favorite, 0) = 1")', apply_block)
        self.assertNotIn("batch = [x for x in batch if", render_block)


if __name__ == "__main__":
    unittest.main()
