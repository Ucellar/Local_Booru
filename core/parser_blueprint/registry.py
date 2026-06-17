"""Built-in and user-defined parser blueprint module registry."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Any

from .schema import NodeTypeSpec, PortSpec


def _p(name: str, type_name: str, label: str | None = None, *, multi: bool = False) -> PortSpec:
    return PortSpec(name=name, type=type_name, label=label or name, multi=multi)



def _runtime(workers: int = 1, delay_ms: int = 1100, rate_group: str = "") -> dict[str, Any]:
    return {
        "workers": int(workers),
        "min_delay_ms": int(delay_ms),
        "timeout_ms": 30000,
        "retry_count": 0,
        "rate_group": rate_group,
        "enabled_source": "site_settings",
        "on_disabled": "skip",
    }


def _site_default(domain: str, *, workers: int = 1, delay_ms: int = 1100, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = {"domain": domain}
    cfg.update(_runtime(workers=workers, delay_ms=delay_ms, rate_group=domain))
    if extra:
        cfg.update(extra)
    return cfg

def builtin_node_types() -> dict[str, NodeTypeSpec]:
    specs: list[NodeTypeSpec] = [
        NodeTypeSpec(
            "file_input", "Файлы из корня", "Старт", (), (_p("files", "FileSet", "Файлы"),),
            kind="file_input", action="builtin.file_input", color="#2563eb", editable_ports=False,
            description="Стартовый блок. Получает список файлов из выбранной папки.",
        ),
        NodeTypeSpec(
            "local_preflight", "Локальная подготовка", "Локально", (_p("files", "FileSet"),), (_p("files", "FileSet"), _p("hash", "HashInfo", "MD5/pHash")),
            kind="local_preflight", action="builtin.local_preflight", color="#0f766e",
            description="Локальный кэш MD5/pHash/видео-кадров. Не ходит в интернет.",
        ),
        NodeTypeSpec(
            "md5_site_danbooru", "MD5: Danbooru", "MD5 сайты", (_p("hash", "HashInfo"),), (_p("match", "SiteMatch"), _p("miss", "HashInfo")),
            kind="exact_md5_site", action="builtin.exact_md5_site", color="#7c3aed", default_config=_site_default("danbooru.donmai.us", workers=1, delay_ms=1100),
            description="Официальный JSON API Danbooru.",
        ),
        NodeTypeSpec(
            "md5_site_gelbooru", "MD5: Gelbooru", "MD5 сайты", (_p("hash", "HashInfo"),), (_p("match", "SiteMatch"), _p("miss", "HashInfo")),
            kind="exact_md5_site", action="builtin.exact_md5_site", color="#4f46e5", default_config=_site_default("gelbooru.com", workers=1, delay_ms=1100),
        ),
        NodeTypeSpec(
            "md5_site_rule34", "MD5: rule34.xxx", "MD5 сайты", (_p("hash", "HashInfo"),), (_p("match", "SiteMatch"), _p("variant", "HashInfo", "image-key/SHA1"), _p("miss", "HashInfo")),
            kind="exact_md5_site", action="builtin.exact_md5_site", color="#b45309", default_config=_site_default("rule34.xxx", workers=1, delay_ms=1100),
        ),
        NodeTypeSpec(
            "md5_site_e621", "MD5: e621", "MD5 сайты", (_p("hash", "HashInfo"),), (_p("match", "SiteMatch"), _p("miss", "HashInfo")),
            kind="exact_md5_site", action="builtin.exact_md5_site", color="#0369a1", default_config=_site_default("e621.net", workers=1, delay_ms=1100),
            description="e621 exact-MD5 через official API/companion fallback.",
        ),
        NodeTypeSpec(
            "md5_site_atf", "MD5: ATF", "MD5 сайты", (_p("hash", "HashInfo"),), (_p("match", "SiteMatch"), _p("miss", "HashInfo")),
            kind="exact_md5_site", action="builtin.exact_md5_site", color="#6d28d9", default_config=_site_default("booru.allthefallen.moe", workers=1, delay_ms=1100),
        ),
        NodeTypeSpec(
            "rule34_image_key", "rule34 image-key locator", "Боковые очереди", (_p("variant", "HashInfo"),), (_p("match", "SiteMatch"), _p("site_md5", "HashInfo")),
            kind="rule34_image_key", action="builtin.rule34_image_key_locator", color="#92400e",
            description="Hotlink-only locator, без sample/bucket sweep.",
        ),
        NodeTypeSpec(
            "atf_pixel_hash", "ATF pixel_hash locator", "Боковые очереди", (_p("miss", "HashInfo"),), (_p("match", "SiteMatch"), _p("site_md5", "HashInfo"), _p("miss", "HashInfo")),
            kind="atf_pixel_hash", action="builtin.atf_pixel_hash_locator", color="#7e22ce",
            default_config=_runtime(workers=2, delay_ms=1100, rate_group="booru.allthefallen.moe"),
            description="Локальный Danbooru/ATF pixel_hash → /media_assets.json → asset.md5 → ATF post/tags.",
        ),
        NodeTypeSpec(
            "md5_relay_all", "MD5 relay по всем сайтам", "Боковые очереди", (_p("site_md5", "HashInfo", multi=True),), (_p("bundle", "MatchBundle"),),
            kind="md5_relay_all", action="builtin.md5_relay_all", color="#475569",
            description="Полученный site_md5 прогоняется по включённым MD5-сайтам и мержится.",
        ),
        NodeTypeSpec(
            "source_url_md5_relay", "Source/Post URL → MD5 relay", "Боковые очереди",
            (_p("url", "SourceUrl", "URL", multi=True),),
            (_p("bundle", "MatchBundle"), _p("source_only", "SourceUrl", "source-only"), _p("miss", "HashInfo")),
            kind="source_url_md5_relay", action="builtin.source_url_md5_relay", color="#0f766e",
            default_config=_runtime(workers=1, delay_ms=1100, rate_group="source-url-relay"),
            description="URL из TinEye/SauceNAO/IQDB → post/API/static file → authoritative MD5 → обычный MD5 relay по включённым сайтам. Нерешаемые URL остаются source-only.",
        ),
        NodeTypeSpec(
            "reverse_iqdb", "Reverse: IQDB", "Reverse", (_p("miss", "HashInfo"),), (_p("match", "SiteMatch"), _p("miss", "HashInfo")),
            kind="reverse_iqdb", action="builtin.reverse_iqdb", color="#a16207", default_config=_runtime(workers=1, delay_ms=1200, rate_group="iqdb.org"),
        ),
        NodeTypeSpec(
            "reverse_danbooru_iqdb", "Reverse: Danbooru IQDB", "Reverse", (_p("miss", "HashInfo"),), (_p("match", "SiteMatch"), _p("miss", "HashInfo")),
            kind="reverse_danbooru_iqdb", action="builtin.reverse_danbooru_iqdb", color="#9333ea", default_config=_runtime(workers=1, delay_ms=1100, rate_group="danbooru.donmai.us"),
        ),
        NodeTypeSpec(
            "reverse_e621_iqdb", "Reverse: e621 IQDB", "Reverse", (_p("miss", "HashInfo"),), (_p("match", "SiteMatch"), _p("miss", "HashInfo")),
            kind="reverse_e621_iqdb", action="builtin.reverse_e621_iqdb", color="#0ea5e9", default_config=_runtime(workers=1, delay_ms=1100, rate_group="e621.net"),
        ),
        NodeTypeSpec(
            "reverse_saucenao", "Reverse: SauceNAO", "Reverse", (_p("miss", "HashInfo"),), (_p("match", "SiteMatch"), _p("miss", "HashInfo")),
            kind="reverse_saucenao", action="builtin.reverse_saucenao", color="#be123c", default_config=_runtime(workers=1, delay_ms=1500, rate_group="saucenao"),
        ),
        NodeTypeSpec(
            "reverse_tineye", "Reverse: TinEye locator", "Reverse", (_p("miss", "HashInfo"),), (_p("source", "SourceUrl"), _p("miss", "HashInfo")),
            kind="reverse_tineye", action="builtin.reverse_tineye", color="#854d0e", default_config=_runtime(workers=1, delay_ms=3000, rate_group="tineye"),
            description="Только locator/source-only fallback, не tag source.",
        ),
        NodeTypeSpec(
            "merge_tags", "Merge tags/sources", "Сохранение", (_p("input", "MatchBundle", multi=True),), (_p("tags", "TagBundle"),),
            kind="merge_tags", action="builtin.merge_tags", color="#15803d",
        ),
        NodeTypeSpec(
            "save_found", "Сохранить FOUND", "Сохранение", (_p("tags", "TagBundle", multi=True),), (_p("done", "SaveCommand"),),
            kind="save_found", action="builtin.save_found", color="#16a34a",
        ),
        NodeTypeSpec(
            "save_no_match", "Сохранить NO_MATCH", "Сохранение", (_p("miss", "HashInfo", multi=True),), (_p("done", "SaveCommand"),),
            kind="save_no_match", action="builtin.save_no_match", color="#dc2626",
        ),
        NodeTypeSpec(
            "stop", "Стоп", "Служебные", (_p("input", "Any", multi=True),), (),
            kind="stop", action="builtin.stop", color="#64748b",
        ),
    ]
    return {s.type_id: s for s in specs}


def load_custom_node_types(module_dir: Path) -> dict[str, NodeTypeSpec]:
    out: dict[str, NodeTypeSpec] = {}
    try:
        module_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return out
    for path in sorted(module_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            spec = NodeTypeSpec.from_dict(data)
            out[spec.type_id] = spec
        except Exception:
            continue
    return out


def registry_with_custom(module_dir: Path | None = None, settings: dict | None = None) -> dict[str, NodeTypeSpec]:
    reg = builtin_node_types()
    reg.update(site_node_types_from_settings(settings))
    if module_dir is not None:
        reg.update(load_custom_node_types(module_dir))
    return reg



def _safe_type_id(domain: str) -> str:
    clean = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(domain or ""))
    clean = "_".join(part for part in clean.split("_") if part)
    return "md5_site_" + (clean or "custom")


def site_node_types_from_settings(settings: dict | None) -> dict[str, NodeTypeSpec]:
    """Generate MD5 site blocks for user-added/edited site table entries.

    Built-in sites already have prettier stable type IDs; custom sites get
    deterministic IDs like md5_site_mybooru_local.  Disabled sites still get
    blocks: the runtime treats them as skip/pass-through unless the user forces
    enabled in the block config.
    """
    out: dict[str, NodeTypeSpec] = {}
    if not isinstance(settings, dict):
        return out
    raw_items: list[dict[str, Any]] = []
    sites = settings.get("sites", {})
    if isinstance(sites, dict):
        for domain, cfg in sites.items():
            if isinstance(cfg, dict):
                item = dict(cfg)
                item.setdefault("domain", str(domain))
                raw_items.append(item)
    custom = settings.get("custom_sites", [])
    if isinstance(custom, list):
        raw_items.extend([dict(x) for x in custom if isinstance(x, dict)])
    builtin_domains = {
        "danbooru.donmai.us", "gelbooru.com", "rule34.xxx", "e621.net", "booru.allthefallen.moe"
    }
    for item in raw_items:
        domain = str(item.get("domain") or item.get("url") or "").strip().lower().replace("www.", "")
        if not domain or domain in builtin_domains:
            continue
        type_id = _safe_type_id(domain)
        engine = str(item.get("type") or item.get("engine") or "site").strip() or "site"
        out[type_id] = NodeTypeSpec(
            type_id, f"MD5: {domain}", "MD5 сайты",
            (_p("hash", "HashInfo"),), (_p("match", "SiteMatch"), _p("miss", "HashInfo"), _p("error", "Error")),
            kind="exact_md5_site", action="builtin.exact_md5_site", color="#334155",
            default_config=_site_default(domain, workers=1, delay_ms=1100, extra={"engine": engine, "custom_site": True}),
            description=f"Автоматический блок сайта из таблицы сайтов: {domain} ({engine}).",
        )
    return out

def categories(registry: dict[str, NodeTypeSpec]) -> list[str]:
    return sorted({s.category for s in registry.values()})
