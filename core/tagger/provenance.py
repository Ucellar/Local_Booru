"""Parser provenance-mode helpers without Qt or network dependencies."""
from __future__ import annotations


def normalize_provenance_mode(value) -> str:
    mode = str(value or "full").strip().lower()
    return mode if mode in {"full", "fast"} else "full"


def normalize_authoritative_hosts(value):
    if isinstance(value, str):
        value = [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    return {str(host or "").strip().lower().replace("www.", "") for host in (value or []) if str(host or "").strip()}


def reactivate_fast_skips(site_done_map):
    """Remove fast-mode skip rows from an in-memory status map for full replay."""
    for _path_key, outcomes in list((site_done_map or {}).items()):
        if not isinstance(outcomes, dict):
            continue
        for site_key, outcome in list(outcomes.items()):
            if str(outcome or "").lower() == "skipped_fast_match":
                outcomes.pop(site_key, None)
    return site_done_map
