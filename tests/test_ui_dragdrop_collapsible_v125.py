from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class UiDragDropCollapsibleV125Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings_page = (ROOT / 'ui' / 'settings_page.py').read_text(encoding='utf-8')
        cls.main_window = (ROOT / 'ui' / 'main_window.py').read_text(encoding='utf-8')
        cls.tagger = (ROOT / 'ui' / 'tagger_page.py').read_text(encoding='utf-8')
        cls.themes = (ROOT / 'ui' / 'styles' / 'themes.py').read_text(encoding='utf-8')
        cls.defaults = (ROOT / 'core' / 'settings.py').read_text(encoding='utf-8')

    def test_tag_groups_reorder_by_drag_drop(self):
        self.assertIn('self.list.setDragDropMode(QAbstractItemView.InternalMove)', self.settings_page)
        self.assertIn('Зажми группу левой кнопкой мыши и перетащи', self.settings_page)

    def test_sidebar_modules_have_drag_order_and_collapsible_extra_group(self):
        self.assertIn('self.primary=self._make_list("Основные разделы")', self.settings_page)
        self.assertIn('self.extra=self._make_list("Дополнительно (сворачивается)")', self.settings_page)
        self.assertIn('self._nav_extra_toggle = QPushButton("Дополнительно  ▸")', self.main_window)
        self.assertIn('"interface_module_order": []', self.defaults)
        self.assertIn('"interface_extra_collapsed": True', self.defaults)

    def test_parser_has_one_activity_view_per_power_mode(self):
        self.assertIn('show_single_preview =', self.tagger)
        self.assertIn('show_lanes = conveyor_enabled and not low_power', self.tagger)
        self.assertIn('self.preview_box.setVisible(show_single_preview)', self.tagger)
        self.assertIn('self.site_activity_table.setVisible(show_lanes)', self.tagger)

    def test_checkbox_theme_uses_solid_square_without_tick_glyph(self):
        self.assertNotIn('image:url(assets/check_dark.png);', self.themes)
        self.assertNotIn('image:url(assets/check_r34.png);', self.themes)
        self.assertIn('image:none;', self.themes)

if __name__ == '__main__':
    unittest.main()
