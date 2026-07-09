"""Persistent storage and compiler for parser blueprints."""
from __future__ import annotations

import copy
import json
import time
from collections import deque
from pathlib import Path
from typing import Any

from core.paths import persistent_base_dir
from .schema import BlueprintEdge, BlueprintGraph, BlueprintNode, validate_graph, analyze_graph, sorted_nodes_for_display
from .registry import registry_with_custom, site_node_types_from_settings




_REVERSE_KIND_TO_SETTING = {
    "reverse_iqdb": ("enable_iqdb", True),
    "reverse_danbooru_iqdb": ("enable_danbooru_iqdb", False),
    "reverse_e621_iqdb": ("enable_e621_iqdb", True),
    "reverse_saucenao": ("enable_saucenao", True),
    "reverse_tineye": ("enable_tineye", False),
}

_KIND_TO_SETTING = {
    **_REVERSE_KIND_TO_SETTING,
    "local_preflight": ("local_preflight_enabled", True),
    "rule34_image_key": ("rule34_variant_locator_side_queue_enabled", True),
    "atf_pixel_hash": ("atf_pixel_hash_locator_enabled", True),
}


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off", "выкл", "нет"}
    return bool(value)


def _node_ignores_global_toggle(node: BlueprintNode) -> bool:
    cfg = dict(getattr(node, "config", {}) or {})
    return _truthy(cfg.get("ignore_parser_toggle"), False) or _truthy(cfg.get("force_enabled"), False)


def _kind_enabled_by_settings(kind: str, node: BlueprintNode, settings: dict | None) -> bool:
    """Return whether a blueprint node is allowed by normal parser toggles.

    Blueprint blocks stay visible and connected, but the normal Tagger/Parser
    checkboxes are the master switches by default.  Advanced users can opt out
    per block with config.ignore_parser_toggle=true or force_enabled=true.
    """
    if not isinstance(settings, dict) or _node_ignores_global_toggle(node):
        return True
    key_default = _KIND_TO_SETTING.get(str(kind))
    if not key_default:
        return True
    key, default = key_default
    return _truthy(settings.get(key), default)


def _site_enabled_by_settings(domain: str, node: BlueprintNode, settings: dict | None) -> bool:
    """Return whether an exact-MD5 site block should be active.

    The block may stay in the graph while disabled; disabled means runtime skip,
    not network lane creation.  A block can deliberately ignore the site table
    with config.ignore_site_enabled=true / force_enabled=true.
    """
    if not isinstance(settings, dict) or _node_ignores_global_toggle(node):
        return True
    cfg = dict(getattr(node, "config", {}) or {})
    if _truthy(cfg.get("ignore_site_enabled"), False):
        return True
    wanted = str(domain or cfg.get("domain", "") or "").strip().lower().replace("www.", "")
    if not wanted:
        return True

    builtin_domains = {
        "danbooru.donmai.us", "gelbooru.com", "rule34.xxx", "e621.net", "booru.allthefallen.moe"
    }

    sites = settings.get("sites", {})
    if isinstance(sites, dict):
        for raw_domain, raw_cfg in sites.items():
            if not isinstance(raw_cfg, dict):
                continue
            dom = str(raw_cfg.get("domain") or raw_domain or "").strip().lower().replace("www.", "")
            if dom == wanted:
                return _truthy(raw_cfg.get("enabled"), True)

    custom_sites = settings.get("custom_sites", [])
    if isinstance(custom_sites, list):
        for raw_cfg in custom_sites:
            if not isinstance(raw_cfg, dict):
                continue
            dom = str(raw_cfg.get("domain") or raw_cfg.get("url") or "").strip().lower().replace("www.", "")
            if dom == wanted:
                return _truthy(raw_cfg.get("enabled"), False)

    # Built-in defaults are enabled if there is no explicit row.
    if wanted in builtin_domains:
        return True
    # Custom/user-generated site blocks without an enabled row should not start
    # network lanes implicitly.
    return False


def blueprint_root(settings: dict | None = None) -> Path:
    # private state belongs in Local_Booru_Archive/settings/config
    base = persistent_base_dir()
    return base / "config" / "parser_blueprints"


def modules_dir(settings: dict | None = None) -> Path:
    return blueprint_root(settings) / "modules"


def active_blueprint_file(settings: dict | None = None) -> Path:
    return blueprint_root(settings) / "active_parser_blueprint.json"


def presets_dir(settings: dict | None = None) -> Path:
    return blueprint_root(settings) / "presets"


def _safe_preset_id(name: str) -> str:
    raw = str(name or "").strip().lower()
    out = []
    for ch in raw:
        if ch.isalnum() or ch in "._-":
            out.append(ch)
        elif ch.isspace() or ch in ":;/\\|":
            out.append("_")
    safe = "".join(out).strip("._-")
    return safe or f"preset_{int(time.time())}"


def _clone_graph(graph: BlueprintGraph) -> BlueprintGraph:
    return BlueprintGraph.from_dict(copy.deepcopy(graph.to_dict()), registry_with_custom(None, None))


def _node_kind_for_graph(node: BlueprintNode, reg: dict[str, Any]) -> str:
    spec = reg.get(node.type_id)
    return str(spec.kind if spec else node.config.get("kind", "custom") or "custom")


def _rewire_standard_md5_order(graph: BlueprintGraph, order_domains: list[str]) -> BlueprintGraph:
    """Best-effort helper used by bundled presets.

    It only rewires the standard linear MD5-miss chain. Match/variant/merge edges
    are preserved. Unknown/custom sites are appended after the requested order.
    """
    reg = registry_with_custom(None, None)
    by_domain = {_node_domain(n, reg): n for n in graph.nodes if _node_kind(n, reg) == "exact_md5_site"}
    ordered: list[BlueprintNode] = []
    for d in order_domains:
        node = by_domain.get(str(d).lower().replace("www.", ""))
        if node and node not in ordered:
            ordered.append(node)
    for node in sorted([n for n in graph.nodes if _node_kind(n, reg) == "exact_md5_site"], key=lambda n: (n.x, n.y, n.id)):
        if node not in ordered:
            ordered.append(node)
    preflight = next((n for n in graph.nodes if n.type_id == "local_preflight"), None)
    reverse_nodes = [n for n in graph.nodes if _node_kind(n, reg).startswith("reverse_")]
    reverse_start = sorted(reverse_nodes, key=lambda n: (n.x, n.y, n.id))[0] if reverse_nodes else None
    md5_ids = {n.id for n in ordered}
    # Remove standard hash/miss chain edges involving MD5 nodes.
    graph.edges = [e for e in graph.edges if not (
        (e.source_node == (preflight.id if preflight else "") and e.source_port == "hash" and e.target_node in md5_ids)
        or (e.source_node in md5_ids and e.source_port == "miss")
        or (e.target_node in md5_ids and e.target_port == "hash" and e.source_node in md5_ids)
    )]
    if preflight and ordered:
        prev_id = preflight.id
        prev_port = "hash"
        for node in ordered:
            graph.edges.append(BlueprintEdge.from_dict(_edge(prev_id, prev_port, node.id, "hash")))
            prev_id = node.id
            prev_port = "miss"
        if reverse_start:
            graph.edges.append(BlueprintEdge.from_dict(_edge(prev_id, prev_port, reverse_start.id, "miss")))
    return graph


def _remove_reverse_chain(graph: BlueprintGraph) -> BlueprintGraph:
    reg = registry_with_custom(None, None)
    reverse_ids = {n.id for n in graph.nodes if _node_kind(n, reg).startswith("reverse_")}
    nomatch = next((n for n in graph.nodes if n.type_id == "save_no_match"), None)
    graph.edges = [e for e in graph.edges if e.source_node not in reverse_ids and e.target_node not in reverse_ids]
    graph.nodes = [n for n in graph.nodes if n.id not in reverse_ids]
    if nomatch:
        md5_nodes = [n for n in graph.nodes if _node_kind(n, reg) == "exact_md5_site"]
        if md5_nodes:
            last = sorted(md5_nodes, key=lambda n: (n.x, n.y, n.id))[-1]
            graph.edges.append(BlueprintEdge.from_dict(_edge(last.id, "miss", nomatch.id, "miss")))
    return graph


def builtin_preset_names() -> dict[str, str]:
    return {
        "standard": "Стандартный",
        "max_tags": "Максимум тегов",
        "fast_md5": "Быстрый MD5",
        "rule34_first": "rule34-first",
        "e621_first": "e621-first",
        "no_reverse": "Без reverse-поиска",
        "local_only": "Только локальная подготовка",
    }


def builtin_preset_blueprint(preset_id: str) -> BlueprintGraph:
    preset_id = str(preset_id or "standard")
    g = _clone_graph(default_blueprint())
    if preset_id == "max_tags":
        g.name = "Максимум тегов"
        for n in g.nodes:
            if n.type_id in {"reverse_iqdb", "reverse_danbooru_iqdb", "reverse_e621_iqdb", "reverse_saucenao", "reverse_tineye", "md5_relay_all", "rule34_image_key"}:
                n.enabled = True
                n.config["enabled"] = True
        return g
    if preset_id == "fast_md5":
        g.name = "Быстрый MD5"
        g = _remove_reverse_chain(g)
        return g
    if preset_id == "rule34_first":
        g.name = "rule34-first"
        return _rewire_standard_md5_order(g, ["rule34.xxx", "gelbooru.com", "danbooru.donmai.us", "e621.net", "booru.allthefallen.moe"])
    if preset_id == "e621_first":
        g.name = "e621-first"
        return _rewire_standard_md5_order(g, ["e621.net", "rule34.xxx", "gelbooru.com", "danbooru.donmai.us", "booru.allthefallen.moe"])
    if preset_id == "no_reverse":
        g.name = "Без reverse-поиска"
        return _remove_reverse_chain(g)
    if preset_id == "local_only":
        g.name = "Только локальная подготовка"
        keep = {"files", "preflight", "nomatch"}
        g.nodes = [n for n in g.nodes if n.id in keep]
        g.edges = [e for e in g.edges if e.source_node in keep and e.target_node in keep]
        return g
    g.name = "Стандартный"
    return g


def list_user_presets(settings: dict | None = None) -> list[str]:
    d = presets_dir(settings)
    try:
        return sorted(p.stem for p in d.glob("*.json"))
    except Exception:
        return []


def save_preset(graph: BlueprintGraph, settings: dict | None, name: str) -> Path:
    d = presets_dir(settings)
    d.mkdir(parents=True, exist_ok=True)
    safe = _safe_preset_id(name)
    path = d / f"{safe}.json"
    data = graph.to_dict()
    data["name"] = str(name or graph.name or safe)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_preset(settings: dict | None, preset_id: str) -> BlueprintGraph:
    if str(preset_id or "").startswith("builtin:"):
        return builtin_preset_blueprint(str(preset_id).split(":", 1)[1])
    path = presets_dir(settings) / f"{_safe_preset_id(preset_id)}.json"
    reg = registry_with_custom(modules_dir(settings), settings)
    return sync_graph_with_site_blocks(BlueprintGraph.from_dict(json.loads(path.read_text(encoding="utf-8")), reg), settings)


def delete_preset(settings: dict | None, preset_id: str) -> bool:
    if str(preset_id or "").startswith("builtin:"):
        return False
    path = presets_dir(settings) / f"{_safe_preset_id(preset_id)}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def _node(node_id: str, type_id: str, title: str, x: int, y: int, config: dict | None = None) -> dict:
    return {"id": node_id, "type_id": type_id, "title": title, "x": x, "y": y, "config": config or {}}


def _edge(src: str, sp: str, dst: str, tp: str) -> dict:
    return {"id": f"e-{src}-{sp}-{dst}-{tp}", "source_node": src, "source_port": sp, "target_node": dst, "target_port": tp}


WIDE_STANDARD_NODE_POSITIONS = {
    "files": (40, 420),
    "preflight": (410, 420),
    "danbooru": (820, 40),
    "gelbooru": (820, 250),
    "rule34": (820, 460),
    "e621": (820, 670),
    "atf": (820, 880),
    "r34key": (1230, 490),
    "atfpx": (1230, 760),
    "relay": (1640, 600),
    "merge": (2040, 420),
    "save": (2420, 420),
    "iqdb": (1230, 1160),
    "diqdb": (1640, 1160),
    "eiqdb": (2050, 1160),
    "sauce": (2460, 1160),
    "tineye": (2870, 1160),
    "source_relay": (3280, 1030),
    "nomatch": (3700, 1160),
}


def _apply_wide_standard_layout(graph: BlueprintGraph, *, force: bool = False) -> BlueprintGraph:
    """Spread the stock parser graph so it does not look like a node pile.

    Existing hand-made/custom graphs are left alone unless this is a v1 stock
    graph.  v328 upgrades the bundled standard layout only: large horizontal and
    vertical gaps, side queues visually separated, and TinEye URL relay visible
    as its own block.
    """
    node_map = graph.node_map()
    standard_ids = {"files", "preflight", "danbooru", "gelbooru", "rule34", "e621", "atf", "merge", "save", "iqdb", "sauce", "nomatch"}
    if not standard_ids.issubset(set(node_map)):
        return graph
    if not force:
        try:
            if int(getattr(graph, "version", 1) or 1) >= 2:
                return graph
        except Exception:
            return graph
    for node_id, (x, y) in WIDE_STANDARD_NODE_POSITIONS.items():
        node = node_map.get(node_id)
        if node:
            node.x = float(x)
            node.y = float(y)
    graph.version = max(2, int(getattr(graph, "version", 1) or 1))
    return graph


def _sync_source_url_md5_relay_node(graph: BlueprintGraph) -> BlueprintGraph:
    """Add the visual TinEye/SauceNAO URL→MD5 relay block to older blueprints.

    The runtime already resolves reverse URLs inside the reverse branch.  This
    block makes that contract explicit in the canvas and keeps the standard
    graph understandable: TinEye source output goes into URL relay, URL relay
    returns tags to Merge or source-only/miss to NO_MATCH.
    """
    node_map = graph.node_map()
    if "source_relay" in node_map or any(n.type_id == "source_url_md5_relay" for n in graph.nodes):
        return graph
    needed = {"tineye", "merge", "nomatch"}
    if not needed.issubset(set(node_map)):
        return graph
    reg = registry_with_custom(None, None)
    spec = reg.get("source_url_md5_relay")
    if not spec:
        return graph
    x, y = WIDE_STANDARD_NODE_POSITIONS.get("source_relay", (2140, 610))
    node = BlueprintNode(
        id="source_relay", type_id="source_url_md5_relay", title="Source URL → MD5 relay", x=x, y=y,
        config=dict(spec.default_config), workers=spec.default_workers, min_delay_ms=spec.default_min_delay_ms,
        timeout_ms=spec.default_timeout_ms, retry_count=spec.default_retry_count, rate_group=spec.rate_group or "source-url-relay",
        enabled=True, on_disabled="skip", inputs=list(spec.inputs), outputs=list(spec.outputs),
    )
    graph.nodes.append(node)

    def has_edge(src, sp, dst, tp):
        return any(e.source_node == src and e.source_port == sp and e.target_node == dst and e.target_port == tp for e in graph.edges)

    if not has_edge("tineye", "source", "source_relay", "url"):
        graph.edges.append(BlueprintEdge.from_dict(_edge("tineye", "source", "source_relay", "url")))
    if not has_edge("source_relay", "bundle", "merge", "input"):
        graph.edges.append(BlueprintEdge.from_dict(_edge("source_relay", "bundle", "merge", "input")))
    if not has_edge("source_relay", "miss", "nomatch", "miss"):
        graph.edges.append(BlueprintEdge.from_dict(_edge("source_relay", "miss", "nomatch", "miss")))
    return graph


def default_blueprint() -> BlueprintGraph:
    data = {
        "format": "local-booru-parser-blueprint-v1",
        "name": "Стандартный конвейер Local Booru",
        "version": 2,
        "active": True,
        "description": "Стандартный рабочий blueprint, повторяющий текущую схему парсера. Обычный парсер запускает именно этот граф; advanced-пользователь может заменить всё.",
        "nodes": [
            _node("files", "file_input", "Файлы", 40, 420),
            _node("preflight", "local_preflight", "Локальная подготовка", 410, 420),
            _node("danbooru", "md5_site_danbooru", "Danbooru", 820, 40),
            _node("gelbooru", "md5_site_gelbooru", "Gelbooru", 820, 250),
            _node("rule34", "md5_site_rule34", "rule34.xxx", 820, 460),
            _node("e621", "md5_site_e621", "e621", 820, 670),
            _node("atf", "md5_site_atf", "ATF", 820, 880),
            _node("r34key", "rule34_image_key", "rule34 image-key", 1230, 490),
            _node("atfpx", "atf_pixel_hash", "ATF pixel_hash", 1230, 760),
            _node("relay", "md5_relay_all", "site-MD5 relay", 1640, 600),
            _node("merge", "merge_tags", "Merge", 2040, 420),
            _node("iqdb", "reverse_iqdb", "IQDB", 1230, 1160),
            _node("diqdb", "reverse_danbooru_iqdb", "Danbooru IQDB", 1640, 1160),
            _node("eiqdb", "reverse_e621_iqdb", "e621 IQDB", 2050, 1160),
            _node("sauce", "reverse_saucenao", "SauceNAO", 2460, 1160),
            _node("tineye", "reverse_tineye", "TinEye", 2870, 1160),
            _node("source_relay", "source_url_md5_relay", "Source URL → MD5 relay", 3280, 1030),
            _node("save", "save_found", "FOUND", 2420, 420),
            _node("nomatch", "save_no_match", "NO_MATCH", 3700, 1160),
        ],
        "edges": [
            _edge("files", "files", "preflight", "files"),
            _edge("preflight", "hash", "danbooru", "hash"),
            _edge("danbooru", "miss", "gelbooru", "hash"),
            _edge("gelbooru", "miss", "rule34", "hash"),
            _edge("rule34", "miss", "e621", "hash"),
            _edge("e621", "miss", "atf", "hash"),
            _edge("rule34", "variant", "r34key", "variant"),
            _edge("r34key", "site_md5", "relay", "site_md5"),
            _edge("atfpx", "site_md5", "relay", "site_md5"),
            _edge("atfpx", "match", "merge", "input"),
            _edge("danbooru", "match", "merge", "input"),
            _edge("gelbooru", "match", "merge", "input"),
            _edge("rule34", "match", "merge", "input"),
            _edge("e621", "match", "merge", "input"),
            _edge("atf", "match", "merge", "input"),
            _edge("relay", "bundle", "merge", "input"),
            _edge("merge", "tags", "save", "tags"),
            _edge("atf", "miss", "atfpx", "miss"),
            _edge("atfpx", "miss", "iqdb", "miss"),
            _edge("iqdb", "miss", "diqdb", "miss"),
            _edge("diqdb", "miss", "eiqdb", "miss"),
            _edge("eiqdb", "miss", "sauce", "miss"),
            _edge("sauce", "miss", "tineye", "miss"),
            _edge("tineye", "source", "source_relay", "url"),
            _edge("source_relay", "bundle", "merge", "input"),
            _edge("source_relay", "miss", "nomatch", "miss"),
            _edge("tineye", "miss", "nomatch", "miss"),
            _edge("iqdb", "match", "merge", "input"),
            _edge("diqdb", "match", "merge", "input"),
            _edge("eiqdb", "match", "merge", "input"),
            _edge("sauce", "match", "merge", "input"),
        ],
    }
    graph = BlueprintGraph.from_dict(data, registry_with_custom(None, None))
    graph = _sync_source_url_md5_relay_node(graph)
    graph = _apply_wide_standard_layout(graph, force=True)
    return sync_graph_with_site_blocks(graph, None)


def load_active_blueprint(settings: dict | None = None) -> BlueprintGraph:
    path = active_blueprint_file(settings)
    reg = registry_with_custom(modules_dir(settings), settings)
    try:
        if path.exists():
            return BlueprintGraph.from_dict(json.loads(path.read_text(encoding="utf-8")), reg)
    except Exception:
        pass
    return sync_graph_with_site_blocks(default_blueprint(), settings)


def save_active_blueprint(graph: BlueprintGraph, settings: dict | None = None) -> None:
    root = blueprint_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    active_blueprint_file(settings).write_text(json.dumps(graph.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def create_custom_module(settings: dict | None, type_id: str, title: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(type_id).strip())
    if not safe:
        safe = f"custom_{int(time.time())}"
    data = {
        "type_id": safe,
        "title": title or safe,
        "category": "Пользовательские",
        "kind": "custom",
        "action": "custom.noop",
        "color": "#334155",
        "description": "Пользовательский блок. Настрой входы/выходы и action вручную в JSON или в инспекторе.",
        "inputs": [{"name": "in", "type": "Any", "label": "In", "multi": True}],
        "outputs": [{"name": "out", "type": "Any", "label": "Out", "multi": False}],
        "default_config": {},
        "editable_ports": True,
    }
    d = modules_dir(settings)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{safe}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path



def _node_domain(node: BlueprintNode, registry: dict[str, Any] | None = None) -> str:
    cfg = dict(node.config or {})
    return str(cfg.get("domain", "") or "").strip().lower().replace("www.", "")


def _node_kind(node: BlueprintNode, registry: dict[str, Any] | None = None) -> str:
    spec = (registry or {}).get(node.type_id)
    return str(spec.kind if spec else node.config.get("kind", "custom") or "custom")


def sync_graph_with_site_blocks(graph: BlueprintGraph, settings: dict | None = None) -> BlueprintGraph:
    """Auto-add custom/user site blocks to the graph when requested.

    Existing blocks are never deleted.  Disabled sites remain visible and the
    runtime treats them as skip/pass-through according to normal site settings.
    """
    graph = _sync_source_url_md5_relay_node(graph)
    graph = _apply_wide_standard_layout(graph)
    if isinstance(settings, dict) and not bool(settings.get("parser_blueprint_auto_add_sites", True)):
        return graph
    reg = registry_with_custom(modules_dir(settings), settings)
    site_specs = site_node_types_from_settings(settings)
    if not site_specs:
        return graph
    existing_domains = {_node_domain(n, reg) for n in graph.nodes if _node_kind(n, reg) == "exact_md5_site"}
    added: list[BlueprintNode] = []
    y = 1090
    try:
        md5_nodes = [n for n in graph.nodes if _node_kind(n, reg) == "exact_md5_site"]
        if md5_nodes:
            y = int(max(n.y for n in md5_nodes) + 210)
    except Exception:
        pass
    for type_id, spec in sorted(site_specs.items(), key=lambda kv: kv[1].title.lower()):
        domain = str(spec.default_config.get("domain", "") or "").strip().lower().replace("www.", "")
        if not domain or domain in existing_domains:
            continue
        node = BlueprintNode(
            id=f"site_{type_id}", type_id=type_id, title=spec.title, x=820, y=y,
            config=dict(spec.default_config), workers=spec.default_workers, min_delay_ms=spec.default_min_delay_ms,
            timeout_ms=spec.default_timeout_ms, retry_count=spec.default_retry_count, rate_group=spec.rate_group or domain,
            enabled=True, on_disabled="skip", inputs=list(spec.inputs), outputs=list(spec.outputs),
        )
        graph.nodes.append(node)
        added.append(node)
        existing_domains.add(domain)
        y += 210
    if not added:
        return graph

    # Best-effort visual connection into the standard MD5 miss chain.  The
    # compiler also reads unconnected site blocks, so even custom hand-made graphs
    # keep working if this cannot be done safely.
    try:
        md5_nodes = [n for n in graph.nodes if _node_kind(n, reg) == "exact_md5_site"]
        md5_nodes = sorted(md5_nodes, key=lambda n: (float(n.x), float(n.y), n.id))
        reverse_nodes = [n for n in graph.nodes if _node_kind(n, reg).startswith("reverse_")]
        reverse_start = sorted(reverse_nodes, key=lambda n: (float(n.x), float(n.y), n.id))[0] if reverse_nodes else None
        # Remove old edge from previous final MD5 miss to reverse start so custom
        # sites become visible in the chain instead of just floating nearby.
        if reverse_start and len(md5_nodes) >= len(added) + 1:
            old_last = md5_nodes[-len(added)-1]
            graph.edges = [e for e in graph.edges if not (e.source_node == old_last.id and e.source_port == "miss" and e.target_node == reverse_start.id)]
            prev = old_last
            for node in added:
                graph.edges.append(BlueprintEdge.from_dict(_edge(prev.id, "miss", node.id, "hash")))
                prev = node
            graph.edges.append(BlueprintEdge.from_dict(_edge(prev.id, "miss", reverse_start.id, "miss")))
    except Exception:
        pass
    return graph

def _reachable_order(graph: BlueprintGraph) -> list[BlueprintNode]:
    node_map = graph.node_map()
    outgoing: dict[str, list[BlueprintEdge]] = {}
    for edge in graph.edges:
        outgoing.setdefault(edge.source_node, []).append(edge)
    for edges in outgoing.values():
        edges.sort(key=lambda e: (node_map.get(e.target_node).x if node_map.get(e.target_node) else 0, node_map.get(e.target_node).y if node_map.get(e.target_node) else 0, e.target_node))
    starts = [n for n in graph.nodes if n.type_id == "file_input"] or sorted_nodes_for_display(graph.nodes)[:1]
    seen: set[str] = set()
    ordered: list[BlueprintNode] = []
    q = deque(starts)
    while q:
        node = q.popleft()
        if node.id in seen:
            continue
        seen.add(node.id)
        ordered.append(node)
        for edge in outgoing.get(node.id, []):
            target = node_map.get(edge.target_node)
            if target is not None and target.id not in seen:
                q.append(target)
    for node in sorted_nodes_for_display(graph.nodes):
        if node.id not in seen:
            ordered.append(node)
    return ordered


def _node_runtime(node: BlueprintNode, spec: Any | None = None) -> dict[str, Any]:
    cfg = dict(node.config or {})
    workers = max(1, int(getattr(node, "workers", cfg.get("workers", getattr(spec, "default_workers", 1))) or 1))
    delay = max(0, int(getattr(node, "min_delay_ms", cfg.get("min_delay_ms", getattr(spec, "default_min_delay_ms", 0))) or 0))
    timeout = max(1, int(getattr(node, "timeout_ms", cfg.get("timeout_ms", getattr(spec, "default_timeout_ms", 30000))) or 30000))
    retries = max(0, int(getattr(node, "retry_count", cfg.get("retry_count", getattr(spec, "default_retry_count", 0))) or 0))
    rate_group = str(getattr(node, "rate_group", cfg.get("rate_group", getattr(spec, "rate_group", ""))) or "")
    return {
        "node_id": node.id,
        "title": node.title,
        "type_id": node.type_id,
        "workers": workers,
        "min_delay_ms": delay,
        "timeout_ms": timeout,
        "retry_count": retries,
        "rate_group": rate_group,
        "enabled": bool(getattr(node, "enabled", cfg.get("enabled", True))),
        "on_disabled": str(getattr(node, "on_disabled", cfg.get("on_disabled", "skip")) or "skip"),
        "config": cfg,
    }


def compile_blueprint(graph: BlueprintGraph, registry: dict[str, Any] | None = None, *, full_access: bool = False, settings: dict | None = None) -> dict[str, Any]:
    reg = registry or registry_with_custom(None)
    analysis = analyze_graph(graph, reg, full_access=full_access)
    errors = list(analysis.get("errors") or [])
    warnings = list(analysis.get("warnings") or [])
    ordered = _reachable_order(graph)
    site_order: list[str] = []
    reverse_order: list[str] = []
    site_runtime: dict[str, dict[str, Any]] = {}
    reverse_runtime: dict[str, dict[str, Any]] = {}
    node_runtime: dict[str, dict[str, Any]] = {}
    has_local_preflight = False
    rule34_side = False
    atf_pixel_hash = False
    atf_pixel_runtime: dict[str, Any] = {}
    has_source_url_relay = False
    has_save_found = False
    has_save_nomatch = False
    for node in ordered:
        spec = reg.get(node.type_id)
        kind = spec.kind if spec else str(node.config.get("kind", "custom"))
        cfg = copy.deepcopy(node.config or {})
        runtime = _node_runtime(node, spec)
        node_runtime[node.id] = runtime
        if kind == "exact_md5_site":
            domain = str(cfg.get("domain", "") or "").strip().lower().replace("www.", "")
            active = bool(runtime.get("enabled", True)) and _site_enabled_by_settings(domain, node, settings)
            runtime["effective_enabled"] = bool(active)
            if domain and active and domain not in site_order:
                site_order.append(domain)
                site_runtime[domain] = runtime
        elif kind.startswith("reverse_"):
            active = bool(runtime.get("enabled", True)) and _kind_enabled_by_settings(kind, node, settings)
            runtime["effective_enabled"] = bool(active)
            if active and kind not in reverse_order:
                reverse_order.append(kind)
                reverse_runtime[kind] = runtime
        elif kind == "local_preflight":
            has_local_preflight = bool(runtime.get("enabled", True)) and _kind_enabled_by_settings(kind, node, settings)
            runtime["effective_enabled"] = bool(has_local_preflight)
        elif kind == "rule34_image_key":
            rule34_side = bool(runtime.get("enabled", True)) and _kind_enabled_by_settings(kind, node, settings)
            runtime["effective_enabled"] = bool(rule34_side)
            if rule34_side:
                reverse_runtime[kind] = runtime
        elif kind == "atf_pixel_hash":
            atf_pixel_hash = bool(runtime.get("enabled", True)) and _kind_enabled_by_settings(kind, node, settings)
            runtime["effective_enabled"] = bool(atf_pixel_hash)
            atf_pixel_runtime = runtime
            if atf_pixel_hash:
                reverse_runtime[kind] = runtime
        elif kind == "source_url_md5_relay":
            has_source_url_relay = bool(runtime.get("enabled", True))
            runtime["effective_enabled"] = bool(has_source_url_relay)
            if has_source_url_relay:
                reverse_runtime[kind] = runtime
        elif kind == "save_found":
            has_save_found = True
        elif kind == "save_no_match":
            has_save_nomatch = True
    # v372: ATF has two independent runtime branches.  Older/custom blueprint
    # files may still contain only the pixel_hash node even though the user enabled
    # booru.allthefallen.moe in the site table.  The parser must advertise and run
    # ATF exact-MD5 as an ordinary MD5 site, while keeping pixel_hash as fallback.
    if (settings or {}).get("tagger_force_atf_md5_lane", True):
        try:
            atf_dummy = BlueprintNode.from_dict({
                "id": "atf_forced_summary",
                "type_id": "md5_site_atf",
                "title": "ATF",
                "config": {"domain": "booru.allthefallen.moe"},
                "enabled": True,
            })
            if _site_enabled_by_settings("booru.allthefallen.moe", atf_dummy, settings) and "booru.allthefallen.moe" not in site_order:
                site_order.append("booru.allthefallen.moe")
                site_runtime.setdefault("booru.allthefallen.moe", {
                    "node_id": "atf_forced_summary",
                    "title": "ATF",
                    "type_id": "md5_site_atf",
                    "workers": 1,
                    "min_delay_ms": int((settings or {}).get("atf_pixel_hash_delay_ms", 1100) or 1100),
                    "timeout_ms": 30000,
                    "retry_count": 0,
                    "rate_group": "booru.allthefallen.moe",
                    "enabled": True,
                    "effective_enabled": True,
                    "on_disabled": "skip",
                    "config": {"domain": "booru.allthefallen.moe"},
                })
        except Exception:
            pass

    summary_bits = []
    if site_order:
        summary_bits.append("MD5: " + " → ".join(site_order))
    if reverse_order:
        names = {
            "reverse_iqdb": "IQDB",
            "reverse_danbooru_iqdb": "Danbooru IQDB",
            "reverse_e621_iqdb": "e621 IQDB",
            "reverse_saucenao": "SauceNAO",
            "reverse_tineye": "TinEye",
        }
        summary_bits.append("Reverse: " + " → ".join(names.get(x, x) for x in reverse_order))
    if rule34_side:
        summary_bits.append("rule34 image-key side queue")
    if atf_pixel_hash:
        summary_bits.append("ATF pixel_hash locator")
    if has_source_url_relay:
        summary_bits.append("source/post URL → MD5 relay")
    if has_local_preflight:
        summary_bits.append("local preflight")
    if full_access:
        summary_bits.append("full-access: warnings only unless graph is unroutable")
    max_reverse_workers = 1
    for rt in reverse_runtime.values():
        try:
            max_reverse_workers = max(max_reverse_workers, int(rt.get("workers", 1) or 1))
        except Exception:
            pass
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "full_access": bool(full_access),
        "site_order": site_order,
        "sites_only": bool(site_order),
        "site_runtime": site_runtime,
        "reverse_order": reverse_order,
        "reverse_runtime": reverse_runtime,
        "node_runtime": node_runtime,
        "reverse_workers": max_reverse_workers,
        "local_preflight": has_local_preflight,
        "rule34_side_queue": rule34_side,
        "atf_pixel_hash_locator": atf_pixel_hash,
        "atf_pixel_hash_runtime": atf_pixel_runtime,
        "source_url_md5_relay": has_source_url_relay,
        "save_found": has_save_found,
        "save_nomatch": has_save_nomatch,
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "summary": "; ".join(summary_bits) if summary_bits else "пустой граф",
    }

def apply_blueprint_runtime_settings(settings: dict) -> dict:
    out = dict(settings or {})
    # v321: the normal parser is backed by the standard blueprint by default.
    # Turning this off is a compatibility escape hatch, not the main workflow.
    if "parser_blueprint_enabled" not in out:
        out["parser_blueprint_enabled"] = True
    if not bool(out.get("parser_blueprint_enabled", True)):
        return out
    full_access = bool(out.get("parser_blueprint_full_access", True))
    graph = sync_graph_with_site_blocks(load_active_blueprint(out), out)
    reg = registry_with_custom(modules_dir(out), out)
    plan = compile_blueprint(graph, reg, full_access=full_access, settings=out)
    out["_parser_blueprint_plan"] = plan
    out["_parser_blueprint_active_name"] = graph.name
    out["_parser_blueprint_compiled_summary"] = plan.get("summary", "")
    out["_parser_blueprint_warnings"] = list(plan.get("warnings") or [])
    if not plan.get("ok"):
        out["_parser_blueprint_invalid"] = True
        return out
    site_order = list(plan.get("site_order") or [])
    if site_order:
        out["_parser_blueprint_site_order"] = site_order
        out["_parser_blueprint_sites_only"] = True
        out["_parser_blueprint_site_runtime"] = dict(plan.get("site_runtime") or {})
    reverse_order = list(plan.get("reverse_order") or [])
    wanted = set(reverse_order)
    # v327: normal parser checkboxes are master switches for built-in reverse
    # modules.  The blueprint may keep disabled blocks visible/connected, but a
    # disabled module must not be resurrected just because the block exists in
    # the graph.  compile_blueprint(..., settings=out) already filters disabled
    # blocks unless the node explicitly sets ignore_parser_toggle/force_enabled.
    out["enable_iqdb"] = "reverse_iqdb" in wanted
    out["enable_danbooru_iqdb"] = "reverse_danbooru_iqdb" in wanted
    out["enable_e621_iqdb"] = "reverse_e621_iqdb" in wanted
    out["enable_saucenao"] = "reverse_saucenao" in wanted
    out["enable_tineye"] = "reverse_tineye" in wanted
    out["_parser_blueprint_reverse_runtime"] = dict(plan.get("reverse_runtime") or {})
    out["_parser_blueprint_reverse_workers"] = max(1, min(16, int(plan.get("reverse_workers", 1) or 1)))
    out["_parser_blueprint_node_runtime"] = dict(plan.get("node_runtime") or {})
    out["local_preflight_enabled"] = bool(plan.get("local_preflight", out.get("local_preflight_enabled", True)))
    out["rule34_variant_locator_side_queue_enabled"] = bool(plan.get("rule34_side_queue", out.get("rule34_variant_locator_side_queue_enabled", True)))
    out["atf_pixel_hash_locator_enabled"] = bool(plan.get("atf_pixel_hash_locator", out.get("atf_pixel_hash_locator_enabled", True)))
    _atf_rt = dict(plan.get("atf_pixel_hash_runtime") or {})
    if _atf_rt:
        out["atf_pixel_hash_workers"] = max(1, min(16, int(_atf_rt.get("workers", out.get("atf_pixel_hash_workers", 2)) or 2)))
        out["atf_pixel_hash_delay_ms"] = max(0, int(_atf_rt.get("min_delay_ms", out.get("atf_pixel_hash_delay_ms", 1100)) or 0))
    return out
