"""Tag parser: finds booru tags for local images.

    runner.ParserRunner   — runs over a folder (thread pool, pause/stop, summary)
    engine.ParserEngine   — the per-file algorithm (see engine.py docstring)
    sites/                — one module per booru engine
    reverse/              — reverse image search services
    http.HttpClient       — sessions, throttling, retries, cancellation
"""
from core.parser.engine import FileJob, ParserEngine
from core.parser.runner import MEDIA_EXTS, ParserRunner

__all__ = ["ParserRunner", "ParserEngine", "FileJob", "MEDIA_EXTS"]
