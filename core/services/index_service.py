from __future__ import annotations

from typing import Callable


def rebuild_index(settings: dict, force: bool = False, with_md5: bool = False,
                  progress: Callable[[str], None] | None = None, stop_check=None):
    """Compatibility service for UI rebuilds, backed by the authoritative indexer.

    Older code had a second shortened index path that omitted sidecar tags, sources
    and thumbnail pre-generation. Rebuilds from Settings now use the same pipeline
    as watcher/library indexing, so an index has one meaning everywhere.
    """
    from core.database.indexer import index_library

    def report(indexed: int, skipped: int):
        if progress:
            try:
                progress(f"indexed={indexed} skipped={skipped}")
            except Exception:
                pass

    if progress:
        try:
            progress("scan roots...")
        except Exception:
            pass
    result = index_library(
        settings,
        force=force,
        progress=report,
        stop_check=stop_check,
        compute_md5=with_md5,
    )
    if progress:
        try:
            progress(
                f"done indexed={result.get('indexed', 0)} scanned={result.get('scanned', 0)} "
                f"skipped={result.get('skipped', 0)} removed={result.get('removed', 0)}"
            )
        except Exception:
            pass
    return result


def import_legacy_sidecar_metadata(settings: dict, progress=None, stop_check=None):
    """One-time migration: import old .tags/.sources files into SQLite.

    Live parser/gallery code never reads sidecars after this explicit action.
    """
    from core.library_diagnostics import create_forced_backup
    from core.database.indexer import index_library
    backup = create_forced_backup(settings, reason="legacy_sidecar_import")
    if progress:
        progress("backup создан; импорт старых sidecar...")
    result = index_library(settings, force=True, progress=lambda i, s: progress(f"indexed={i} skipped={s}") if progress else None, stop_check=stop_check, compute_md5=False, import_legacy_sidecars=True)
    result["backup"] = str(backup or "")
    return result
