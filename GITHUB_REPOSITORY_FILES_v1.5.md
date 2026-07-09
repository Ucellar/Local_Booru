# GitHub repository files added for v1.5

This package adds the repository metadata that should exist beside the application source before publication.

## Added

- `.gitignore` — excludes Python caches, build output, SQLite databases, local archives, cookies, browser profiles, logs, model weights, and generated archives.
- `.gitattributes` — normalizes line endings and marks binary assets as binary.
- `.editorconfig` — basic editor formatting rules.
- `LICENSE` — MIT license file matching the README license note.
- `CONTRIBUTING.md` — lightweight contribution guide.
- `SECURITY.md` — security/reporting notes.
- `SUPPORT.md` — issue/support guidance.
- `.github/ISSUE_TEMPLATE/bug_report.md` — bug report template.
- `.github/ISSUE_TEMPLATE/feature_request.md` — feature request template.
- `.github/ISSUE_TEMPLATE/config.yml` — issue template config.
- `.github/pull_request_template.md` — pull request checklist.
- `CHANGELOG_RU.md` — Russian changelog.
- `CHANGELOG.md` — replaced with a clean English changelog without mixed Russian sections.

## Not added

No GitHub Actions workflow was added. The project has PySide6/browser/runtime dependencies, so a CI workflow should be added only after the command set is confirmed to work on GitHub-hosted Windows runners.
