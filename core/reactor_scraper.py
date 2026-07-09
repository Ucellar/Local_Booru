"""Deprecated compatibility shim.

Moved to tools.deprecated.reactor_scraper during v404 cleanup.
Keep this shim for one release so dynamic/old imports do not break.
"""
try:
    from tools.deprecated.reactor_scraper import *  # noqa: F401,F403
except Exception:
    pass
