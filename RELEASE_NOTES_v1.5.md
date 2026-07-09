# Local Booru v1.5

This release focuses on first-run usability and safer SQLite rebuilds.

## Highlights

- First-run language picker: choose **English** or **Russian** before the main window opens.
- The selected language is saved and the dialog is not shown again for the same workspace/profile.
- Fixed the `m024_reverse_branch_status` migration crash that could happen after deleting and rebuilding the SQLite database.
- Reduced long SQLite writer locks during clean database rebuilds by batching legacy NO_MATCH and deleted-file registry imports.
- Parser startup no longer treats a locked SQLite database as an empty database.
- Added safer SQLite startup checks and low-memory parser profile behavior for large archives.
- Added English README content for GitHub users.

## Notes for existing users

Your existing workspace should keep using its current language setting. The first-run language dialog is intended for fresh profiles/workspaces.

If you intentionally rebuild the database, close Local Booru first and delete the SQLite sidecar files together:

```text
local_booru_index.sqlite3
local_booru_index.sqlite3-wal
local_booru_index.sqlite3-shm
```

Media files are not duplicated by this release. The app still uses the existing `Local_Booru_Archive/output` and `Local_Booru_Archive/settings` layout.

## Source install

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

For the embedded browser / some protected sites:

```powershell
python -m playwright install
```

## Windows EXE build

```powershell
build_exe.bat
```

The folder build is still the recommended PySide6 build mode.
