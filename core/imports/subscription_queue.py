"""Small import planner used by subscriptions.

This is intentionally not a full Hydrus clone.  It groups site seeds that point
to the same logical file so the runner can try the best-priority source first,
then fallback to lower-priority sources.

Important: post ids are only comparable inside the same site family.  ATF,
e621, Danbooru and rule34 all have different id ranges, so a global sort by
post_id biases the import queue toward whichever site has numerically smaller
ids.  The queue therefore sorts by best site priority first, then by post id
inside that priority bucket.
"""
from __future__ import annotations


def _group_key(seed: dict) -> str:
    md5 = str(seed.get("md5") or "").strip().lower()
    if md5:
        return "md5:" + md5
    site = seed.get("site", "")
    pid = seed.get("post_id") or "?"
    return f"site-id:{site}:{pid}"


def _seed_priority(seed: dict) -> int:
    try:
        return int(seed.get("priority") or 1)
    except Exception:
        return 1


def _seed_post_id(seed: dict) -> int:
    try:
        return int(seed.get("post_id") or 0)
    except Exception:
        return 0


def build_import_groups(seeds: list[dict], direction: str = "newest_to_oldest") -> list[list[dict]]:
    groups: dict[str, list[dict]] = {}
    order: dict[str, int] = {}
    for idx, seed in enumerate(seeds):
        key = _group_key(seed)
        groups.setdefault(key, []).append(seed)
        order.setdefault(key, idx)

    result = list(groups.values())

    # Within one logical file group, try higher-priority sites first.
    # This is where Danbooru/e621/rule34 can beat ATF when they share MD5.
    for group in result:
        group.sort(key=lambda s: (-_seed_priority(s), _seed_post_id(s)))

    newest_first = direction != "oldest_to_newest"

    def sort_key(group: list[dict]):
        # Different sites use unrelated numeric id ranges.  Sorting only by id
        # globally made "oldest_to_newest" import thousands of ATF seeds before
        # ever reaching rule34/e621.  Priority must be the primary key.
        best_priority = max((_seed_priority(s) for s in group), default=1)
        ids = [_seed_post_id(s) for s in group if _seed_post_id(s) > 0]
        post_id = max(ids) if newest_first else min(ids) if ids else 0
        group_key = _group_key(group[0]) if group else ""
        return (-best_priority, -post_id if newest_first else post_id, order.get(group_key, 0))

    result.sort(key=sort_key)
    return result
