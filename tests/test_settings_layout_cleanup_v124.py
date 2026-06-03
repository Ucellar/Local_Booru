import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SettingsLayoutCleanupV124Tests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / 'ui' / 'settings_page.py').read_text(encoding='utf-8')

    def test_global_actions_are_pinned_in_footer_not_scroll_content(self):
        self.assertIn('self.settings_footer = QWidget()', self.text)
        self.assertIn('lay.addWidget(self.settings_footer)', self.text)
        self.assertIn('self.save_btn.setObjectName("PrimarySettingsAction")', self.text)
        self.assertNotIn('_ilay.addLayout(_primary_row)', self.text)
        self.assertLess(self.text.index('lay.addWidget(self._scroll, 1)'), self.text.index('lay.addWidget(self.settings_footer)'))

    def test_gallery_options_are_split_into_readable_cards(self):
        self.assertIn('self.preview_cache_box = QGroupBox("Превью и кэш")', self.text)
        self.assertIn('self.gallery_display_box = QGroupBox("Отображение и управление")', self.text)
        self.assertIn('self.preview_cache_box.setVisible(title == "Галерея и превью")', self.text)
        self.assertIn('self.gallery_display_box.setVisible(title == "Галерея и превью")', self.text)
        self.assertNotIn('self.workflow_box = QGroupBox("Новые файлы, кэш и экспорт")', self.text)

    def test_unrelated_actions_are_in_correct_sections(self):
        self.assertIn('self.library_policy_box = QGroupBox("Новые файлы и корзина")', self.text)
        self.assertIn('self.library_transfer_box = QGroupBox("Перенос и экспорт данных")', self.text)
        self.assertIn('self.developer_tools_box = QGroupBox("Логи и служебные инструменты")', self.text)
        self.assertIn('self.developer_tools_box.setVisible(title == "Для разработчика")', self.text)


if __name__ == '__main__':
    unittest.main()
