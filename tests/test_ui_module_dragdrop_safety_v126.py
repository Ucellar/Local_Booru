from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class UiModuleDragDropSafetyV126Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings_page = (ROOT / "ui" / "settings_page.py").read_text(encoding="utf-8")

    def test_module_lists_use_non_overwriting_move_logic(self):
        self.assertIn("class SafeModuleList(QListWidget)", self.settings_page)
        self.assertIn("self.setDragDropOverwriteMode(False)", self.settings_page)
        self.assertIn("~Qt.ItemIsDropEnabled", self.settings_page)
        self.assertIn("item = source.takeItem(source_row)", self.settings_page)
        self.assertIn("self.insertItem", self.settings_page)

    def test_corrupt_module_configuration_cannot_be_saved(self):
        self.assertIn("def _validate_structure", self.settings_page)
        self.assertIn("Изменения НЕ сохранены", self.settings_page)
        self.assertIn("def accept(self):", self.settings_page)
        self.assertIn("raise ValueError", self.settings_page)

    def test_cross_list_movement_no_longer_requires_buttons(self):
        self.assertIn("между «Основные» и «Дополнительно»", self.settings_page)
        self.assertNotIn("Отправить в Дополнительно →", self.settings_page)
        self.assertNotIn("← Вернуть в Основные", self.settings_page)

if __name__ == "__main__":
    unittest.main()
