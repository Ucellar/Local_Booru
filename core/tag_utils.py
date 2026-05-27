import html
import re
import unicodedata

PREFIXES = ("artist/", "character/", "copyright/", "general/", "meta/", "parody/", "language/", "category/", "pages/")

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
    # Tags starting with '-' are booru search exclusion operators, not real tags.
    # "-short_hair" means "exclude short_hair" — drop entirely.
    if t.startswith("-"):
        return ""
    t = re.sub(r"\s+", "_", t)
    t = ascii_fold(t)
    return t

def canonical_tag_key(tag) -> str:
    return normalize_tag(tag).lower()
