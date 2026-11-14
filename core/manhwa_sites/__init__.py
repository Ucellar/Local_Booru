"""(v5.44.168) The sites of manga — a module each; what a site is and what they share is
``core/manhwa_sources.py``, which imports them and whose names their classes are (``manhwa_sources.Remanga``).

That module first, whatever is asked for: a site's module uses its names (``S.Source`` — at import), so
``import core.manhwa_sites.remanga`` alone would find it half-made.
"""
from core import manhwa_sources as _sources  # noqa: F401
