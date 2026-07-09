# Local Booru v1.5

This release focuses on first-run usability and safer SQLite database rebuilds.

## Highlights

- First-run language picker: **English** / **Russian**.
- Language choice is saved and shown only once per workspace/profile.
- Fixed `m024_reverse_branch_status` migration crash after clean SQLite DB rebuilds.
- Reduced SQLite lock time during legacy NO_MATCH / deleted-file registry imports.
- Parser startup no longer treats `database is locked` as an empty database.
- Added safer SQLite startup readiness checks and low-memory parser behavior.
- Added English README for GitHub publication.

## Upgrade notes

Existing workspaces should keep their current language setting. Fresh workspaces will show the language picker before the main window.

For intentional full DB rebuilds, close the app first and delete:

```text
local_booru_index.sqlite3
local_booru_index.sqlite3-wal
local_booru_index.sqlite3-shm
```

## Build / run from source

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

For Playwright/browser support:

```powershell
python -m playwright install
```
