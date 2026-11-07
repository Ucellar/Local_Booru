from __future__ import annotations

import time
from typing import Callable

from . import m014_database_health_maintenance, m015_saucenao_quota_snapshot, m016_source_tag_provenance, m017_invalid_navigation_sources, m018_unverified_source_cleanup, m019_performance_indexes, m020_nomatch_reasons_source_only, m021_v158_maintenance_indexes, m022_v159_content_safe_filenames, m023_effective_tag_categories, m024_reverse_branch_status, m025_site_md5_aliases, m026_derived_cache_state, m027_file_presence_integrity, m028_duplicate_candidate_relations, m029_hash_integrity_recovery, m030_external_derived_cache, m031_saucenao_quota_governor, m032_archive_enrichment_foundation, m033_auto_heal_runtime, m034_quality_upgrade_runtime, m035_companion_family_workflows, m036_pixiv_ugoira_assets, m037_source_originals, m038_normalize_source_hosts, m039_missing_file_cards, m040_tag_search_and_category_cache, m041_variant_families, m042_downloaded_bucket, m043_card_sets, m044_drop_duplicate_indexes, m045_reduced_copies, m046_tag_category_review, m047_site_pools

# Version 13 is the consolidated SQLite schema shipped before the numbered
# runner existed.  v130 records that existing working state as a baseline and
# applies only subsequent migrations.
BASELINE_VERSION = 13
CURRENT_SCHEMA_VERSION = 47
MIGRATIONS = {
    14: m014_database_health_maintenance,
    15: m015_saucenao_quota_snapshot,
    16: m016_source_tag_provenance,
    17: m017_invalid_navigation_sources,
    18: m018_unverified_source_cleanup,
    19: m019_performance_indexes,
    20: m020_nomatch_reasons_source_only,
    21: m021_v158_maintenance_indexes,
    22: m022_v159_content_safe_filenames,
    23: m023_effective_tag_categories,
    24: m024_reverse_branch_status,
    25: m025_site_md5_aliases,
    26: m026_derived_cache_state,
    27: m027_file_presence_integrity,
    28: m028_duplicate_candidate_relations,
    29: m029_hash_integrity_recovery,
    30: m030_external_derived_cache,
    31: m031_saucenao_quota_governor,
    32: m032_archive_enrichment_foundation,
    33: m033_auto_heal_runtime,
    34: m034_quality_upgrade_runtime,
    35: m035_companion_family_workflows,
    36: m036_pixiv_ugoira_assets,
    37: m037_source_originals,
    38: m038_normalize_source_hosts,
    39: m039_missing_file_cards,
    40: m040_tag_search_and_category_cache,
    41: m041_variant_families,
    42: m042_downloaded_bucket,
    43: m043_card_sets,
    44: m044_drop_duplicate_indexes,
    45: m045_reduced_copies,
    46: m046_tag_category_review,
    47: m047_site_pools,
}


def _has_user_schema(con) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='images' LIMIT 1"
    ).fetchone()
    return bool(row)


def _ensure_journal(con) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'applied',
        error TEXT NOT NULL DEFAULT '',
        applied_at INTEGER NOT NULL DEFAULT 0
    );
    """)


def _version_from_meta(con) -> int:
    try:
        row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row[0]) if row and str(row[0]).strip() else 0
    except Exception:
        return 0


def run_migrations(con, *, log: Callable[[str], None] | None = None) -> dict:
    """Apply pending numbered migrations transactionally.

    Databases created by v129 and earlier already contain the consolidated
    v13 table set; they are baseline-recorded, not rebuilt or discarded.
    For the current single-developer phase the database may still be safely
    deleted and reconstructed from the protected source archive when desired.
    """
    log = log or (lambda _msg: None)
    _ensure_journal(con)
    recorded = {int(r[0]) for r in con.execute(
        "SELECT version FROM schema_migrations WHERE status='applied'"
    ).fetchall()}
    meta_version = _version_from_meta(con)
    actions: list[str] = []
    now = int(time.time())

    if BASELINE_VERSION not in recorded and _has_user_schema(con):
        con.execute(
            "INSERT OR REPLACE INTO schema_migrations(version,name,status,error,applied_at) VALUES(?,?,?,?,?)",
            (BASELINE_VERSION, "baseline_consolidated_schema_v13", "applied", "", now),
        )
        recorded.add(BASELINE_VERSION)
        actions.append("baseline:13")
        log("SQLite migrations: captured existing consolidated schema as baseline v13")

    current = max(recorded | {meta_version, BASELINE_VERSION if _has_user_schema(con) else 0})
    for version in sorted(MIGRATIONS):
        module = MIGRATIONS[version]
        name = str(getattr(module, "NAME", module.__name__.rsplit(".", 1)[-1]))
        declared_applied = bool(version in recorded or version <= current)
        checker = getattr(module, "is_applied", None)
        needs_repair = False
        if declared_applied and callable(checker):
            try:
                needs_repair = not bool(checker(con))
            except Exception:
                needs_repair = True
        if declared_applied and not needs_repair:
            continue
        try:
            module.apply(con)
            if callable(checker) and not bool(checker(con)):
                raise RuntimeError("migration objects are still incomplete after apply()")
            con.execute(
                "INSERT OR REPLACE INTO schema_migrations(version,name,status,error,applied_at) VALUES(?,?,?,?,?)",
                (version, name, "applied", "", int(time.time())),
            )
            action = "repaired" if needs_repair else "applied"
            actions.append(f"{action}:{version}:{name}")
            recorded.add(version)
            current = max(current, version)
            log(f"SQLite migration {action}: {version:03d} {name}")
        except Exception as exc:
            con.execute(
                "INSERT OR REPLACE INTO schema_migrations(version,name,status,error,applied_at) VALUES(?,?,?,?,?)",
                (version, name, "failed", str(exc), int(time.time())),
            )
            raise RuntimeError(f"SQLite migration {version:03d} {name} failed: {exc}") from exc

    final_version = max(current, BASELINE_VERSION if _has_user_schema(con) else 0)
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(final_version),))
    return {"version": final_version, "actions": actions}


def migration_status(con) -> dict:
    try:
        rows = con.execute(
            "SELECT version,name,status,error,applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        history = [dict(r) if hasattr(r, "keys") else {
            "version": r[0], "name": r[1], "status": r[2], "error": r[3], "applied_at": r[4]
        } for r in rows]
    except Exception:
        history = []
    return {"current": _version_from_meta(con), "target": CURRENT_SCHEMA_VERSION, "history": history}
