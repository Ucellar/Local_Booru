"""Relocate Local Booru SQLite media paths after moving Local_Booru_Archive.

Usage from the project folder:
    python tools/relocate_library_paths.py D:\\Local_Booru_Archive --apply

Without --apply it only prints a dry-run report.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.settings import load_settings
from core.paths import normalize_archive_settings_root
from core.library_lifecycle import relocate_missing_library_paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Relocate Local Booru DB paths to a moved archive/output root")
    ap.add_argument("archive_or_output", help="New Local_Booru_Archive, output/, or settings/ path")
    ap.add_argument("--apply", action="store_true", help="Write changes to SQLite after creating a DB backup")
    ns = ap.parse_args()

    selected = Path(ns.archive_or_output)
    settings = load_settings()
    target_settings = normalize_archive_settings_root(selected)
    if target_settings is not None:
        new_output = target_settings.parent / "output"
    elif selected.name.lower() == "output":
        new_output = selected
    else:
        new_output = selected / "output"

    settings["output_dir"] = str(new_output)
    result = relocate_missing_library_paths(settings, new_output, apply=bool(ns.apply))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not ns.apply and result.get("found"):
        print("\nDRY RUN only. Run again with --apply to update SQLite.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
