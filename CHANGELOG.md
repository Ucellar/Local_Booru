# Local Booru Changelog

## v1.5 — first-run language picker and safer SQLite rebuilds

Release date: 2026-07-09

### Added

- First-run language dialog for choosing **English** or **Russian** before the main window opens.
- Persistent language selection via the `language` and `language_selected_once` settings keys.
- English public README for GitHub publication.
- Russian companion documentation for users who prefer the Russian interface and release notes.

### Fixed

- Fixed `m024_reverse_branch_status` missing `NAME`, which could crash migration journaling after a clean SQLite database rebuild.
- Migration runner now falls back to a safe generated migration name if a migration module misses `NAME`, preventing a secondary `AttributeError` from masking the real migration error.
- Legacy NO_MATCH cache import and deleted-file registry import now commit in batches to avoid long SQLite writer locks during clean database rebuilds.
- Parser startup no longer treats a locked SQLite database as an empty database.

### Improved

- Safer SQLite startup and readiness checks after abnormal shutdowns.
- Low-memory parser profile for large archive runs.
- Better release documentation for GitHub users.

## v413 — first-run language picker

- Added a first-run startup dialog for choosing the interface language: Russian or English.
- The choice is saved into settings as `language` and `language_selected_once`, so the dialog appears only once per workspace/profile.
- The existing Settings page language switch remains available for later changes.
- Added Qt-free language bootstrap helpers and tests.

## v412 — SQLite migration m024 / deleted-DB startup fix

- Fixed `m024_reverse_branch_status` missing `NAME`, which crashed schema migration journaling after a database rebuild.
- Migration runner now uses a safe fallback name, so a future migration without `NAME` no longer masks the real migration error.
- Legacy NO_MATCH and deleted-registry imports now commit in batches to avoid holding SQLite writer locks for minutes during clean database rebuilds.
- This specifically targets the deleted-SQLite test path where the media archive remains intact but the working database is recreated.

## v411 — DB lock hard-stop and low-RAM parser profile

- Parser startup no longer treats `database is locked` during status/site-status checks as an empty database. If SQLite is still locked after crash recovery, the parser stops before queue construction.
- Added a startup SQLite readiness probe: a read test plus a short `BEGIN IMMEDIATE` / rollback write-lock test before scanning and status queue construction.
- Added runtime performance profiles: `auto`, `low_memory`, `balanced`, and `performance`. Auto resolves by detected RAM and only changes the worker session copy, not `app_settings.json`.
- The low-memory profile clamps the reverse admission window and worker count, disables pHash preflight for large runs, uses FILE temp store, applies a smaller SQLite cache, and sets RAM limits based on total system memory.
- Large-run local preflight is skipped on low-memory systems instead of warming pHash/video-frame caches over tens of thousands of files.
- Reverse scheduler stops advancing already-archived `skip_existing` files to later stages after a branch returns `SKIP ARCHIVED`.

## v405 — audit hotfixes

- Fixed tests shipped in v404: tag-group merge expectations now match preserved first spelling.
- Removed an engine side effect from `core.tagger` package import; helper modules can be imported without loading the full Tagger engine.
- Made `imagehash` optional for hashing helper imports; pHash functions return a safe fallback when it is missing.
- Removed the `save_settings()` side effect from SQLite `connect()` / `ensure_initialized()`; database initialization now writes only a marker file.
- Added startup handling for `DatabaseMissingError` with a clear dialog and exit code instead of silently continuing.
- Parser database error handler now treats `DatabaseMissingError` as a hard safe-stop.
- Fixed resumed reverse clean-state logic by counting only `_should_reverse()`-eligible already-journaled files.
- Added effective-category refresh after indexer tag rebuild, e621 metadata repair, and invariant repair.
- Replaced category-maintenance `URL LIKE '%host%'` scan with indexed `sources.host` matching.
- Release zip builder now runs pytest by default and has stronger nested-folder excludes.
- Added `ui/tagger/__init__.py` and PyInstaller hidden imports for `ui.tagger.workers` and the new tagger helper modules.
- `VACUUM` no longer force-closes pooled SQLite connections owned by other threads.

## v404 — full audit refactor

- First safe split of `core/tagger/engine.py`: hashing, tag groups, cookie I/O, and ATF HTML helpers were moved into separate testable modules, while `engine.py` remains a compatibility facade.
- Major split of `ui/tagger_page.py`: `TaggerWorker` and `BrowserLoginWorker` were moved into `ui/tagger/workers.py`, leaving the page as a UI layer.
- SQLite schema v23: added materialized `image_effective_tag_category` table to speed up all-source tag facets; storage now refreshes the cache when tags or categories change.
- DONE / clean-session logic is stricter: runs with errors, deferred items, or reverse retry rows are no longer marked clean.
- Release hygiene: `AI_MEMORY` was renamed to dev-only, old changelogs were consolidated, `tools/release/make_release_zip.py` was added, and release exclude rules were added.
- `reactor_scraper` and architecture leftovers were moved into `tools/deprecated` with compatibility shims.
- Added initial pytest smoke tests for helpers and release excludes.

## v403 — safety/reverse/UI audit patch

- SQLite safety guard: a previously initialized database is not silently recreated as an empty file when the expected `local_booru_index.sqlite3` is missing. Intentional full reset: create `ALLOW_CREATE_EMPTY_DB.txt` beside the expected database or launch with `LOCAL_BOORU_ALLOW_NEW_DB=1`.
- Startup log moved from the app directory to `Local_Booru_Archive/settings/output/logs/startup_console.log`.
- Browser companion GET endpoints now require the same extension origin/header authorization as POST; extension status fetch was updated accordingly.
- Reverse temporary network defer is persisted into `reverse_retry_queue` instead of remaining only in RAM.
- e621 IQDB 429/temporary failures set a branch-local cooldown and retry row; the whole parser is not stopped.
- Reverse branch claim/cancel: when a file is already found by another branch, pending sibling branches skip it and the summary reports `REVERSE_CANCELED`.
- Summary now reports `REVERSE_RETRY_QUEUED`.
- Gallery/grabber source sidebars: collapsed view shows all + top 3 sources by count; expanded view shows all. No configurable sorting.
- r34 theme made more angular: gallery cards/containers in that theme no longer use rounded corners.
- Library diagnostics now includes source folder vs `site_scan_status` reconciliation.
- Added safe SQLite identifier validation for maintenance/count helper SQL.

## v399 — parser recovery uses latest completed files

- Fixed the “recheck last 10 files” recovery logic.
- Previous builds tracked files when they were submitted/started, so with a large conveyor window the recovery pass could recheck the first/oldest files in the queue.
- Recovery now tracks `completed_recent`: the last files that actually finished parser processing.
- On an unclean shutdown, the next parser run forces recheck for those latest completed files first.
- `recent_started` is kept only as a compatibility fallback for old recovery state files.

## v396 — general-only category maintenance

- Added a button in Settings → Library Maintenance: “Reclassify general-only tags”.
- The button finds already saved Gelbooru/rule34/xbooru/hypnohub source bundles where all source-specific tags are still under `general`.
- Starts a network category recheck using the already confirmed source URL.
- No new tags are added: only categories for existing tags in that specific source bundle are updated.
- A SQLite backup is created before the bulk update.
- The repair runs in the background through TaskManager, with progress shown in the maintenance status.

## v395 — current-run category drain

- Gelbooru/rule34 category background now prioritizes jobs created by the current parser run.
- The live parser no longer loads the full historical category-backfill backlog into RAM at startup by default. Old backlog can still be enabled explicitly with `tagger_category_startup_seed_backlog=true`.
- Before DONE, parser waits for current-run category jobs to finish, so freshly matched Gelbooru/rule34 posts do not remain visibly stuck in `general`.
- Added task accounting for category queue shutdown.

## v394 — Gelbooru category backfill fix

- Gelbooru source-specific tag view should no longer remain entirely under `general` when post DAPI returned flat tags.
- Background tag category queue key bumped to `flat-sites::tag-groups-v8-gelbooru-rule34-dapi-overlay` to re-run old Gelbooru/rule34 category jobs.
- Gelbooru category recovery now tries the tag catalogue per tag when the multi-name DAPI batch does not classify the confirmed post tags.
- Membership remains guarded: Gelbooru HTML/tag-catalogue may only classify tags already present in the exact Gelbooru post response; it cannot add sidebar/recommendation tags.

## v391 — embedded ExifTool bootstrap

- Added built-in ExifTool lookup locations:
  - `<app>/tools/exiftool/exiftool.exe`
  - `<app>/tools/exiftool/exiftool_files/`
  - `Local_Booru_Archive/settings/tools/exiftool/exiftool.exe`
- Added automatic Windows bootstrap for ExifTool when it is missing:
  - downloads the official 64-bit ExifTool archive into `settings/tools/exiftool`
  - extracts `exiftool.exe` and `exiftool_files`
  - requires no user-facing setting
- PyInstaller spec now includes the tools folder, so release builds can ship ExifTool beside the app.
- Gallery drag export still creates a copy even if ExifTool is unavailable, but metadata embedding waits for ExifTool.

## v388 — parser preview opens original via native Windows path

- Parser preview double-click still opens the original/source file, not the managed output copy.
- Removed `QDesktopServices` / `QUrl(file://...)` fallback for local parser preview opening on Windows.
- Uses `os.startfile(original_path)` first, then explicit `ShellExecuteW` with a UTF-16 native path.
- Adds clearer parser log messages when the original file path is missing or opening fails.

## v385 — parser pause restore / cleanup correction

- Restored the runtime **Pause / Continue** button in the parser.
- Start/Stop remain a single button.
- Only pause/limit settings were removed from the user-facing UI; the parser execution pause itself was not removed.
- Request timeout was not blindly reduced: it remains an internal safeguard against stuck network requests, not a user-facing setting.
