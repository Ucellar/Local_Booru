"""First-run language selection helpers.

This module is intentionally Qt-free so it can be tested without a GUI and used
before the main window is constructed.
"""

SUPPORTED_LANGUAGES = {"ru", "en"}
LANGUAGE_SELECTED_KEY = "language_selected_once"
DEFAULT_LANGUAGE = "ru"


def normalize_language(value) -> str:
    """Return a supported UI language code.

    Unknown, empty or malformed values fall back to Russian because Local Booru's
    original UI language is Russian and existing settings already use it.
    """
    lang = str(value or "").strip().lower()
    if lang in SUPPORTED_LANGUAGES:
        return lang
    # Accept common full names just in case a config was edited manually.
    aliases = {
        "russian": "ru",
        "русский": "ru",
        "ru-ru": "ru",
        "english": "en",
        "английский": "en",
        "en-us": "en",
        "en-gb": "en",
    }
    return aliases.get(lang, DEFAULT_LANGUAGE)


def should_show_language_dialog(settings) -> bool:
    """Whether the startup language dialog should be shown.

    The dialog is shown exactly once per settings profile/archive. Users can
    still change the language later in Settings.
    """
    if not isinstance(settings, dict):
        return True
    return not bool(settings.get(LANGUAGE_SELECTED_KEY, False))


def apply_language_choice(settings, language: str) -> dict:
    """Persist a selected language into a settings dict and mark the dialog done."""
    if not isinstance(settings, dict):
        settings = {}
    settings["language"] = normalize_language(language)
    settings[LANGUAGE_SELECTED_KEY] = True
    return settings
