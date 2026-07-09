from __future__ import annotations

import re

from core.tag_utils import normalize_tag as _shared_normalize_tag, canonical_tag_key

TAG_CATEGORY_MAP = {
    "0": "general", 0: "general", "general": "general",
    "1": "artist", 1: "artist", "artist": "artist",
    "3": "copyright", 3: "copyright", "copyright": "copyright", "series": "copyright",
    "4": "character", 4: "character", "character": "character",
    "5": "meta", 5: "meta", "metadata": "meta", "meta": "meta",
    "species": "species", "specie": "species",
    "contributor": "contributor", "contributors": "contributor",
    "lore": "lore", "invalid": "invalid",
}

GROUP_ORDER = [
    "artist", "contributor", "character", "copyright", "species",
    "general", "meta", "lore", "invalid", "parody", "language", "category", "pages",
]


def tag_is_numeric_symbol_only(tag) -> bool:
    return bool(re.match(r"^[\d\W_]+$", str(tag)))


def filter_numeric_tags(tags, enabled):
    if not enabled:
        return tags
    return [t for t in tags if not tag_is_numeric_symbol_only(t)]


def normalize_tag(tag):
    return _shared_normalize_tag(tag)


def unique_keep_order(items):
    seen = set()
    out = []
    for x in items:
        x = normalize_tag(x)
        key = canonical_tag_key(x)
        if x and key not in seen:
            seen.add(key)
            out.append(x)
    return out


def empty_tag_groups():
    return {key: [] for key in GROUP_ORDER}


def group_from_tag_type(value):
    if value is None:
        return "general"
    key = str(value).strip().lower()
    return TAG_CATEGORY_MAP.get(key, "general")


def add_tags_to_groups(groups, group, tags):
    if group not in groups:
        group = "general"
    for tag in tags or []:
        t = normalize_tag(str(tag))
        if t and t not in groups[group]:
            groups[group].append(t)


def merge_tag_groups(groups_list):
    merged = empty_tag_groups()
    for groups in groups_list:
        if not isinstance(groups, dict):
            continue
        for key in merged:
            merged[key] += groups.get(key, [])
    for key in merged:
        merged[key] = unique_keep_order(merged[key])
    return merged


def groups_to_tags(groups):
    tags = []
    for key in GROUP_ORDER:
        tags += groups.get(key, [])
    return unique_keep_order(tags)
