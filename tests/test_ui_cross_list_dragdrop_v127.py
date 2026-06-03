from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class UiCrossListDragDropV127Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "ui" / "settings_page.py").read_text(encoding="utf-8")

    def test_both_module_columns_are_safe_drag_drop_lists(self):
        self.assertIn("return SafeModuleList(self)", self.src)
        self.assertIn("self.setDragDropMode(QAbstractItemView.DragDrop)", self.src)
        self.assertIn("def dropEvent(self, event):", self.src)
        self.assertIn("event.source()", self.src)

    def test_drop_uses_remove_and_insert_instead_of_item_overwrite(self):
        self.assertIn("item = source.takeItem(source_row)", self.src)
        self.assertIn("self.insertItem(max(0, min(target_row, self.count())), item)", self.src)
        self.assertNotIn("destination.addItem(item)", self.src)

    def test_validation_still_blocks_any_corrupted_layout(self):
        self.assertIn("missing = [key for key in expected if key not in keys]", self.src)
        self.assertIn("duplicated = sorted", self.src)
        self.assertIn("if self._validate_structure(repair=True):", self.src)

if __name__ == "__main__":
    unittest.main()
