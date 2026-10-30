"""Build the enabled booru sites from settings (site table + custom sites)."""
from __future__ import annotations

from core.parser.context import ParserContext
from core.parser.sites.base import Site, SiteUnavailable
from core.parser.sites.danbooru import DanbooruSite
from core.parser.sites.e621 import E621Site
from core.parser.sites.gelbooru import GelbooruSite
from core.parser.sites.moebooru import MoebooruSite
from core.parser.sites.rule34us import Rule34UsSite
from core.parser.util import clean_host

__all__ = ["Site", "SiteUnavailable", "build_sites", "engine_class", "post_page_url", "site_for_host", "site_for_url",
           "site_rows"]

_BY_TYPE = {
    "danbooru": DanbooruSite, "danbooru2": DanbooruSite, "danbooru_html": DanbooruSite,
    "gelbooru": GelbooruSite, "gelbooru_html": GelbooruSite, "rule34xxx": GelbooruSite,
    "rule34.xxx": GelbooruSite, "dapi": GelbooruSite, "hypnohub": GelbooruSite,
    "moebooru": MoebooruSite,
    "e621": E621Site, "e926": E621Site,
    "rule34us": Rule34UsSite, "rule34.us": Rule34UsSite,
}

_BY_DOMAIN = (
    ("rule34.us", Rule34UsSite),
    ("e621.net", E621Site), ("e926.net", E621Site),
    ("yande.re", MoebooruSite), ("konachan", MoebooruSite),
    ("rule34.xxx", GelbooruSite), ("gelbooru", GelbooruSite), ("safebooru.org", GelbooruSite),
    ("xbooru", GelbooruSite), ("realbooru", GelbooruSite), ("tbib", GelbooruSite), ("hypnohub", GelbooruSite),
    ("donmai.us", DanbooruSite), ("allthefallen", DanbooruSite), ("aibooru", DanbooruSite),
    ("lolibooru", DanbooruSite),
)


def engine_class(cfg: dict, domain: str):
    kind = str(cfg.get("engine") or cfg.get("type") or cfg.get("kind") or "").strip().lower()
    host = clean_host(domain)
    # rule34.us is stored as "moebooru" in old site tables; the domain wins.
    for marker, cls in _BY_DOMAIN:
        if marker == "rule34.us" and marker in host:
            return cls
    if kind in _BY_TYPE:
        return _BY_TYPE[kind]
    for marker, cls in _BY_DOMAIN:
        if marker in host:
            return cls
    return None


def post_page_url(host: str, post_id, cfg: dict | None = None) -> str:
    """The page of a post on a site of a known engine (by the site table type or the domain);
    "" when the engine is unknown or there is no post id."""
    cls = engine_class(cfg or {}, host)
    pid = str(post_id or "").strip()
    if not cls or not cls.post_path or not pid or pid == "0":
        return ""
    return f"https://{clean_host(host)}" + cls.post_path.format(id=pid)


_AUTO_KINDS = {"", "auto", "unknown", "custom", "booru", "other"}


def detect_engine(ctx: ParserContext, host: str):
    """Which API a custom site speaks (asked once per run, a request or three):
    Danbooru ``/posts.json``, e621 ``/posts.json`` with ``file``, Gelbooru DAPI, Moebooru ``/post.json``."""
    from core.parser.http import Cancelled, HttpFailure
    from core.parser.util import json_body
    root = f"https://{clean_host(host)}"
    probes = (
        (f"{root}/posts.json", {"limit": 1}),
        (f"{root}/index.php", {"page": "dapi", "s": "post", "q": "index", "json": 1, "limit": 1}),
        (f"{root}/post.json", {"limit": 1}),
    )
    for url, params in probes:
        try:
            response = ctx.http.get(url, params=params, headers={"Accept": "application/json, text/xml, */*"}, attempts=1)
        except Cancelled:
            raise
        except (HttpFailure, OSError) as exc:
            ctx.log(f"  САЙТ {host}: {url.rsplit('/', 1)[-1]} не ответил ({exc})")
            continue
        if response.status_code != 200:
            continue
        data = json_body(response)
        text = (response.text or "")[:600].lower()
        rows = data if isinstance(data, list) else (data.get("posts") or data.get("post") or []) if isinstance(data, dict) else []
        first = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
        if url.endswith("/posts.json"):
            if isinstance(first.get("file"), dict):
                return E621Site
            if "tag_string" in first or "tag_string_general" in first:
                return DanbooruSite
        elif url.endswith("/index.php"):
            if first and ("hash" in first or "md5" in first) and "tags" in first:
                return GelbooruSite
            if data is None and "<posts" in text:
                return GelbooruSite
        elif first and "md5" in first and isinstance(first.get("tags"), str):
            return MoebooruSite
    return None


def site_rows(settings: dict) -> dict[str, dict]:
    """{host: its settings} of the enabled sites (the built-in table, then the person's own sites over it)."""
    rows: dict[str, dict] = {}
    for domain, cfg in (settings.get("sites") or {}).items():
        if isinstance(cfg, dict) and cfg.get("enabled", False):
            rows[clean_host(domain)] = dict(cfg)
    for cfg in settings.get("custom_sites") or []:
        if not isinstance(cfg, dict) or not cfg.get("enabled", False):
            continue
        host = clean_host(cfg.get("domain") or cfg.get("base_url") or cfg.get("login_url") or "")
        if host:
            merged = dict(rows.get(host, {}))
            merged.update({k: v for k, v in cfg.items() if v not in (None, "", [], {})})
            rows[host] = merged
    return rows


def build_sites(ctx: ParserContext) -> list:
    """Enabled sites in the user's table order; ATF goes last (its tags are noisier)."""
    settings = ctx.settings
    rows = site_rows(settings)
    sites = []
    seen = set()
    for host, cfg in rows.items():
        cls = engine_class(cfg, host)
        kind = str(cfg.get("engine") or cfg.get("type") or cfg.get("kind") or "").strip().lower()
        if cls is None and kind in _AUTO_KINDS:
            cls = detect_engine(ctx, host)
            if cls is not None:
                ctx.log(f"САЙТ {host}: движок определён автоматически — {cls.engine}")
        if cls is None:
            ctx.log(f"САЙТ {host}: движок «{kind or 'не указан'}» не распознан — сайт пропущен "
                    "(укажи тип: danbooru, gelbooru, moebooru, e621)")
            continue
        site = cls(host, cfg, ctx)
        if site.host in seen:
            continue
        seen.add(site.host)
        site.preflight()
        sites.append(site)
    rank = {host: i for i, host in enumerate(_manual_order_hosts(settings))}
    sites.sort(key=lambda s: (1 if "allthefallen" in s.host else 0, rank.get(s.host, len(rank))))
    return sites


def _manual_order_hosts(settings: dict) -> list:
    """The site table stores drag&drop order as 'builtin:<domain>' or 'custom:<id>'."""
    id_to_host = {}
    for cfg in settings.get("custom_sites") or []:
        if isinstance(cfg, dict) and cfg.get("site_id"):
            id_to_host[str(cfg["site_id"])] = clean_host(cfg.get("domain") or cfg.get("base_url") or "")
    for domain, cfg in (settings.get("sites") or {}).items():
        if isinstance(cfg, dict) and cfg.get("site_id"):
            id_to_host[str(cfg["site_id"])] = clean_host(domain)
    hosts = []
    for key in settings.get("site_manual_order") or []:
        key = str(key or "").strip()
        if key.startswith("builtin:"):
            hosts.append(clean_host(key[len("builtin:"):]))
        elif key in id_to_host:
            hosts.append(id_to_host[key])
        elif key:
            hosts.append(clean_host(key))
    return [h for h in hosts if h]


def site_for_url(sites: list, url: str):
    from core.parser.util import host_of
    host = host_of(url)
    return next((s for s in sites if s.handles(host)), None)


# Other names of one site: its API host, the domain without the site's own prefix.
HOST_ALIASES = {"api.rule34.xxx": "rule34.xxx", "donmai.us": "danbooru.donmai.us",
                "safebooru.donmai.us": "danbooru.donmai.us"}


def table_config(settings: dict, host: str) -> dict | None:
    """The row of ``host`` in the site table (built-in and the person's own sites, ticked or not)."""
    found = None
    for domain, row in (settings.get("sites") or {}).items():
        if isinstance(row, dict) and HOST_ALIASES.get(clean_host(domain), clean_host(domain)) == host:
            found = dict(row)
    for row in settings.get("custom_sites") or []:
        if not isinstance(row, dict):
            continue
        domain = clean_host(row.get("domain") or row.get("base_url") or row.get("login_url") or "")
        if domain and HOST_ALIASES.get(domain, domain) == host:
            found = dict(found or {})
            found.update({k: v for k, v in row.items() if v not in (None, "", [], {})})
    return found


def site_for_host(ctx: ParserContext, host: str, *, parent_domains: bool = False, detect: bool = True):
    """A client for the site of ``host`` — any site of the table, ticked on «Сайты» or not, or of the program's
    catalogue (a post the person points at is asked for even on a site the parser does not use), with the
    table's keys and login.  None when the program cannot talk to that site.

    ``parent_domains``: the host may be a server of the site (``cdn.donmai.us``, ``img3.gelbooru.com``): the
    domains above it are tried too.  ``detect``: a site of the table whose engine is not set is asked which one
    it speaks (a request or three).  The site is not checked (``preflight``) — the caller does it."""
    from core.settings import ALL_KNOWN_SITES
    start = clean_host(host)
    if not start:
        return None
    candidates = [start]
    if parent_domains:
        labels = start.split(".")
        candidates += [".".join(labels[i:]) for i in range(1, len(labels) - 1)]
    for candidate in candidates:            # a site of the table or of the catalogue
        name = HOST_ALIASES.get(candidate, candidate)
        cfg = table_config(ctx.settings, name)
        catalogue = ALL_KNOWN_SITES.get(name)
        if cfg is None and catalogue is None:
            continue
        merged = {k: v for k, v in (catalogue or {}).items() if k not in ("notes", "engine")}
        merged.update(cfg or {})
        cls = engine_class(merged, name)
        kind = str(merged.get("engine") or merged.get("type") or merged.get("kind") or "").strip().lower()
        if cls is None and cfg is not None and detect and kind in _AUTO_KINDS:
            cls = detect_engine(ctx, name)
        if cls is not None:
            return cls(name, merged, ctx)
    name = HOST_ALIASES.get(start, start)
    cls = engine_class({}, name)            # another site of a known engine (``testbooru.donmai.us``)
    return cls(name, {}, ctx) if cls is not None else None
