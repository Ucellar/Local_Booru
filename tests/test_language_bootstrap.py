from core.language_bootstrap import apply_language_choice, normalize_language, should_show_language_dialog


def test_normalize_language_accepts_supported_and_aliases():
    assert normalize_language("ru") == "ru"
    assert normalize_language("en") == "en"
    assert normalize_language("English") == "en"
    assert normalize_language("Русский") == "ru"
    assert normalize_language("bad-value") == "ru"


def test_language_dialog_shows_until_first_choice_is_saved():
    settings = {"language": "ru"}
    assert should_show_language_dialog(settings) is True
    apply_language_choice(settings, "en")
    assert settings["language"] == "en"
    assert settings["language_selected_once"] is True
    assert should_show_language_dialog(settings) is False
