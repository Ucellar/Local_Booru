import json
import tempfile
import unittest
from pathlib import Path

from core.database.connection import db
import core.performance as performance
from core.library_diagnostics import audit_library
from core.settings import DEFAULT_SETTINGS


class PerformanceHardeningV116Tests(unittest.TestCase):
    def test_slow_operation_log_is_bounded_readable_and_visible_in_audit(self):
        old = performance.PERFORMANCE_LOG_FILE
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                performance.PERFORMANCE_LOG_FILE = root / "performance.jsonl"
                performance.record_slow_operation("sql.tags.group_counts", 321.5, detail={"rows": 10000}, force=True)
                rows = performance.recent_slow_operations(5)
                self.assertEqual(rows[0]["operation"], "sql.tags.group_counts")
                settings = {"sqlite_db_folder": str(root / "db"), "sqlite_connection_pool": False}
                with db(settings, write=True):
                    pass
                report = audit_library(settings)
                self.assertEqual(report["performance"]["slow_operations"][0]["operation"], "sql.tags.group_counts")
        finally:
            performance.PERFORMANCE_LOG_FILE = old

    def test_safe_thumbnail_and_performance_defaults_exist(self):
        self.assertEqual(DEFAULT_SETTINGS["thumb_quality_scale"], 2)
        self.assertEqual(DEFAULT_SETTINGS["thumb_memory_items"], 400)
        self.assertTrue(DEFAULT_SETTINGS["thumb_prefetch_pages"])
        self.assertEqual(DEFAULT_SETTINGS["performance_slow_ms"], 100)


if __name__ == "__main__":
    unittest.main()
