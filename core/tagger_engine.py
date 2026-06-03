"""Legacy import bridge for external extensions created before the modular tagger package.

Application code must import from :mod:`core.tagger`.  This module stays only so
old third-party snippets fail gracefully during migration; it is not a live
sidecar/output implementation.
"""
from core.tagger import *  # noqa: F401,F403 - compatibility surface only
from core.tagger.engine import write_sidecar_tags  # legacy alias -> SQLite metadata writer
