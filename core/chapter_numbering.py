"""Prove a chapter correspondence when one site restarts its numbers in every volume.

Equal lengths alone are not evidence. Both lists must be regular and their volume boundaries must have unique,
matching chapter names. The caller also checks that the titles are the same. No missing chapters are extrapolated.
"""
from __future__ import annotations

import re
from collections import Counter


def restarts(chapters: list) -> bool:
    """Chapter 1 in several volumes distinguishes a restarted count from a duplicated bonus or chapter 10."""
    return len({c.tome for c in chapters if str(c.number).strip() == "1"}) > 1


def different_counts(home: list, other: list) -> bool:
    return restarts(home) != restarts(other)


def _ordered(chapters: list, restart: bool) -> list:
    if not chapters or any(not re.fullmatch(r"[1-9]\d*", str(c.number)) for c in chapters):
        return []
    if any(re.match(r"(?i)^(?:extra|special|bonus|omake|экстра|спешл|бонус|омаке)\b", _caption(c)) for c in chapters):
        return []
    if any(not c.id or c.tome < 0 for c in chapters) or len({c.id for c in chapters}) != len(chapters):
        return []
    if len({(c.tome, c.number) for c in chapters}) != len(chapters):
        return []
    ordered = sorted(chapters, key=lambda c: (c.tome, int(c.number)))
    tomes = sorted({c.tome for c in ordered})
    if tomes != [0] and tomes != list(range(1, len(tomes) + 1)):
        return []
    if restart:
        if len(tomes) < 2:
            return []
        for tome in tomes:
            numbers = [int(c.number) for c in ordered if c.tome == tome]
            if len(numbers) < 2 or numbers != list(range(1, len(numbers) + 1)):
                return []
    elif [int(c.number) for c in ordered] != list(range(1, len(ordered) + 1)):
        return []
    return ordered


def _caption(chapter) -> str:
    text = str(chapter.name or "").strip()
    return re.sub(r"(?i)^(?:(?:том|vol(?:ume)?\.?)\s*\d+\s*[-:–—.]?\s*)?"
                  r"(?:(?:глава|chapter|ch\.?)\s*\d+(?:[.,]\d+)?\s*[-:–—.]?\s*)", "", text)


def _name(chapter) -> str:
    from core.manhwa_sources import name_key
    text = _caption(chapter)
    if sum(ch.isalpha() for ch in text) < 4:
        return ""
    return name_key(text)


def _boundaries(chapters: list) -> set:
    ends = {0, len(chapters) - 1}
    for i in range(1, len(chapters)):
        if chapters[i - 1].tome != chapters[i].tome:
            ends.update((i - 1, i))
    return ends


def reconcile(home: list, other: list) -> dict:
    """Home (volume, number) → original other-site ref, or {} when the correspondence is not proved."""
    if not different_counts(home, other) or len(home) != len(other):
        return {}
    left, right = _ordered(home, restarts(home)), _ordered(other, restarts(other))
    if not left or not right:
        return {}
    names_left, names_right = [_name(c) for c in left], [_name(c) for c in right]
    if any(a and b and a != b for a, b in zip(names_left, names_right)):
        return {}
    counts_left, counts_right = Counter(names_left), Counter(names_right)
    for i in _boundaries(left) | _boundaries(right):
        a, b = names_left[i], names_right[i]
        if not a or a != b or counts_left[a] != 1 or counts_right[b] != 1:
            return {}
    return {(a.tome, a.number): b for a, b in zip(left, right)}
