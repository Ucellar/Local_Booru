# Hotfix 13 — strict HTML match guard

Problem: Hotfix 12 could accept a search/random/list page as a strict match if the wanted MD5 appeared in the URL, for example `/posts/random?tags=md5:<hash>`. That allowed HTML tag scraping to apply a few garbage tags.

Fix:

- HTML fallback now accepts only concrete post URLs.
- `/posts/random`, search/list pages, and URLs with `tags=md5` are rejected as tag sources.
- `_html_explicit_md5_value()` no longer accepts a bare occurrence of the wanted MD5 anywhere in HTML.
- Tags from HTML are used only after an explicit MD5/hash/file URL is found on a concrete post page.
- Added `tools/cleanup_false_random_matches.py` to remove metadata created by the bad random/search-page match class.

Run cleanup from project root:

```powershell
python tools/cleanup_false_random_matches.py
python tools/cleanup_false_random_matches.py --apply
```

The cleanup removes only SQLite tag/source links for suspicious records. It does not delete media files.
