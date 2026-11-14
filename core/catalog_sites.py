"""Site descriptions for the combined feed.

Manga sites own their names, capabilities and order in their Source classes. The remaining
gallery/video sources have no chapter Source and are described here, once. The public tables
keep the catalog's existing interface without another independent list of manga sites.
"""
from __future__ import annotations

from dataclasses import dataclass

from core import manhwa_sources as S


@dataclass(frozen=True)
class CatalogSite:
    name: str
    label: str
    kinds: tuple
    order: int
    filtering: bool = True
    adult_only: bool = False


EXTRA = (
    CatalogSite("nhentai", "nhentai", ("manga",), 60, adult_only=True),
    CatalogSite("yummy", "YummyAnime", ("video",), 10),
    CatalogSite("hentasis", "Hentasis", ("video",), 20, adult_only=True),
    CatalogSite("haven", "HentaiHaven", ("video",), 30, adult_only=True),
    CatalogSite("hentaixs", "Hentai-XS", ("video",), 40, adult_only=True),
    CatalogSite("hfshare", "HFShare", ("video",), 50, adult_only=True),
)


def descriptions(sources=None) -> tuple:
    sources = S.SOURCES if sources is None else sources
    manga = (CatalogSite(s.name, s.label, s.catalog_kinds, s.catalog_order, s.catalog_filters, s.adult_only)
             for s in sources if s.catalog_kinds)
    return tuple(sorted((*manga, *EXTRA), key=lambda s: s.order))


def tables(sources=None) -> tuple:
    sites = descriptions(sources)
    by_kind = {kind: tuple(s.name for s in sites if kind in s.kinds) for kind in ("manga", "manhwa", "video")}
    labels = {s.name: s.label for s in sites}
    adult = tuple(s.name for s in sites if s.adult_only)
    filtering = tuple(s.name for s in sites if s.filtering)
    return by_kind, labels, adult, filtering


SITES, LABELS, ADULT_SITES, FILTERING = tables()
