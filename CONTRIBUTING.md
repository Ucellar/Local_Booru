# Contributing

Local Booru is an experimental Windows desktop archive manager. Contributions are welcome, but stability and data safety are more important than feature count.

## Before opening a pull request

- Test on a small copy of an archive, not on the only copy of real data.
- Do not commit local databases, media files, cookies, browser profiles, logs, model weights, or generated release archives.
- Keep parser/network fallbacks isolated from the main MD5 pipeline when possible.
- Avoid adding third-party cloud services to the core offline-first workflow.

## Development setup

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install
python app.py
```

## Test commands

```powershell
python -m pytest -q
python -m py_compile app.py
```

## Pull request notes

Please describe:

- what changed;
- why it is needed;
- how it was tested;
- whether it touches SQLite schema, parser queues, file deletion, duplicate cleanup, or downloader behavior.
