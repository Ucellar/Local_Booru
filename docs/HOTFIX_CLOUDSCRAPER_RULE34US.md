# Hotfix: ASCII2D cloudscraper + rule34.us endpoint

- ASCII2D now tries `cloudscraper` before `curl_cffi`.
- File upload keeps the `cloudscraper` session instead of copying it to plain `requests`.
- The 403 message no longer incorrectly says to install cloudscraper when it is already installed.
- `rule34.us` no longer uses Moebooru `/post/index.json` endpoints that return nginx 404.
- `rule34.us` now uses DAPI/Gelbooru-like `index.php?page=dapi&s=post&q=index` attempts with strict MD5 verification.

Run check:

```powershell
python -m pip show cloudscraper
python app.py
```
