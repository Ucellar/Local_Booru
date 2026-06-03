import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class SettingsSubsectionsV122Tests(unittest.TestCase):
    def test_settings_page_has_top_subsection_tabs(self):
        text = (ROOT / 'ui' / 'settings_page.py').read_text(encoding='utf-8')
        for label in ('Основные', 'Библиотека', 'Галерея и превью', 'Обслуживание', 'Удаление и сброс', 'Для разработчика'):
            self.assertIn(label, text)
        self.assertIn('def _show_settings_section', text)
        self.assertIn('self.section_tabs = QTabBar()', text)
        self.assertIn('self.section_tabs.currentChanged.connect(self._show_settings_section)', text)
        self.assertNotIn('self.section_nav = QListWidget()', text)
        self.assertNotIn('self.settings_splitter = QSplitter(Qt.Horizontal)', text)

    def test_dangerous_controls_are_a_separate_subsection(self):
        text = (ROOT / 'ui' / 'settings_page.py').read_text(encoding='utf-8')
        self.assertIn('self.danger_box = QGroupBox("Опасные действия")', text)
        self.assertIn('self.danger_box.setVisible(title == "Удаление и сброс")', text)

    def test_diagnostics_navigation_uses_monochrome_asset(self):
        text = (ROOT / 'ui' / 'main_window.py').read_text(encoding='utf-8')
        self.assertIn('"Diagnostics": ("", "diagnostics")', text)
        self.assertTrue((ROOT / 'assets' / 'icons' / 'diagnostics.png').exists())
        self.assertTrue((ROOT / 'assets' / 'icons' / 'diagnostics_dark.png').exists())

if __name__ == '__main__':
    unittest.main()
