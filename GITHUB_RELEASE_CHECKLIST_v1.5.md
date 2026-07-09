# GitHub release checklist — Local Booru v1.5

## Before publishing

- [ ] Confirm the repository README uses `README.md` from this package.
- [ ] Keep `README_RU.md` if you want Russian documentation in the repository.
- [ ] Confirm screenshots exist in the repository under `screenshots/` or update the image paths in README.
- [ ] Run tests:

```powershell
python -m pytest -q
```

- [ ] Build the Windows folder release:

```powershell
build_exe.bat
```

- [ ] Smoke-test a clean workspace:
  - first launch shows language picker;
  - English opens the app;
  - Russian opens the app;
  - restarting does not show the language picker again;
  - language can still be changed in Settings.

- [ ] Smoke-test intentional DB rebuild:
  - close the app;
  - delete `local_booru_index.sqlite3`, `local_booru_index.sqlite3-wal`, `local_booru_index.sqlite3-shm`;
  - launch app;
  - confirm no `m024_reverse_branch_status has no attribute NAME` error.

## GitHub release fields

- Tag: `v1.5`
- Title: `Local Booru v1.5`
- Body: paste `GITHUB_RELEASE_BODY_v1.5.md`
- Recommended asset: folder build archive from `dist\Local Booru`
- Optional asset: source/package archive `Local_Booru_v1.5_github_ready.zip`
