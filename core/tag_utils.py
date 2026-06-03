import html
import re
import unicodedata

PREFIXES = ("artist/", "contributor/", "character/", "copyright/", "species/", "general/", "meta/", "lore/", "invalid/", "parody/", "language/", "category/", "pages/")

def strip_tag_prefix(tag: str) -> str:
    t = html.unescape(str(tag or "")).strip()
    low = t.lower()
    for prefix in PREFIXES:
        if low.startswith(prefix):
            return t.split("/", 1)[1]
    return t

def ascii_fold(text: str) -> str:
    """Remove accent/combining marks. Used to avoid duplicates like pokemon/pokémon."""
    text = str(text or "")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))

def normalize_tag(tag) -> str:
    t = strip_tag_prefix(tag)
    t = html.unescape(t).strip()
    # Bare separators sometimes leak in from scraped sidebars and are not tags.
    if re.fullmatch(r"[-–—_]+", t):
        return ""
    # Tags starting with '-' are booru search exclusion operators, not real tags.
    # "-short_hair" means "exclude short_hair" — drop entirely.
    if t.startswith("-"):
        return ""
    t = re.sub(r"\s+", "_", t)
    t = ascii_fold(t)
    return t

def canonical_tag_key(tag) -> str:
    return normalize_tag(tag).lower()


def should_hide_tag(tag: str, category: str = "general", settings: dict | None = None) -> bool:
    """Return whether a stored tag should be hidden in visible tag lists only.

    Tags remain in SQLite and stay searchable; this is a UI cleanliness filter.
    """
    settings = settings or {}
    raw = str(tag or "").strip()
    norm = normalize_tag(raw)
    cat = str(category or "general").strip().lower()
    if not norm:
        return True
    if bool(settings.get("hide_single_char_tags", True)) and len(norm) <= 1:
        return True
    if bool(settings.get("hide_meta_tags", False)) and cat == "meta":
        return True
    if bool(settings.get("hide_rating_tags", False)) and (cat == "rating" or norm.lower().startswith("rating:")):
        return True
    if bool(settings.get("hide_technical_tags", True)):
        low = norm.lower()
        technical_prefixes = ("uploaded_by_", "md5:", "source:", "file:", "status:", "checksum:")
        if low.startswith(technical_prefixes) or low in {"tagme", "tag_me", "unknown_tag"}:
            return True
    return False


DEFAULT_TAG_GROUP_COLORS = {
    "artist": "#ff3838", "contributor": "#e67e22", "character": "#00a000", "copyright": "#ff54a7",
    "species": "#22a6b3", "general": "#004cff", "meta": "#ff9900", "lore": "#9b59b6", "invalid": "#7f8c8d",
    "parody": "#ff54a7", "language": "#cc8800", "category": "#00aaaa",
    "pages": "#888888",
}

def tag_display_color(tag: str, category: str = "general", settings: dict | None = None, group_colors: dict | None = None) -> str:
    """Resolve a visible tag colour: user override first, category colour second."""
    settings = settings or {}
    custom = settings.get("tag_colors") or {}
    key = canonical_tag_key(tag)
    if isinstance(custom, dict) and key in custom and str(custom.get(key) or "").strip():
        return str(custom[key])
    colors = dict(DEFAULT_TAG_GROUP_COLORS)
    if isinstance(group_colors, dict):
        colors.update(group_colors)
    else:
        colors.update(settings.get("tag_group_colors") or {})
    return str(colors.get(str(category or "general"), colors["general"]))
