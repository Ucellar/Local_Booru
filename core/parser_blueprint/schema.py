"""Typed parser blueprint graph model.

This module is intentionally independent from Qt.  The UI edits the same JSON
that the runtime compiler reads, so a user can create/replace modules manually
without touching the parser core.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable
import copy
import time

BLUEPRINT_FORMAT = "local-booru-parser-blueprint-v1"
ANY_TYPE = "Any"


@dataclass(frozen=True)
class PortSpec:
    name: str
    type: str = ANY_TYPE
    label: str = ""
    multi: bool = False

    @classmethod
    def from_dict(cls, data: dict | str) -> "PortSpec":
        if isinstance(data, str):
            return cls(name=data, type=ANY_TYPE, label=data)
        if not isinstance(data, dict):
            raise ValueError("port must be object or string")
        name = str(data.get("name", "") or "").strip()
        if not name:
            raise ValueError("port without name")
        return cls(
            name=name,
            type=str(data.get("type", ANY_TYPE) or ANY_TYPE).strip() or ANY_TYPE,
            label=str(data.get("label", name) or name),
            multi=bool(data.get("multi", False)),
        )

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "label": self.label or self.name, "multi": self.multi}


@dataclass(frozen=True)
class NodeTypeSpec:
    type_id: str
    title: str
    category: str
    inputs: tuple[PortSpec, ...] = ()
    outputs: tuple[PortSpec, ...] = ()
    kind: str = "custom"
    action: str = "custom.noop"
    default_config: dict[str, Any] = field(default_factory=dict)
    default_workers: int = 1
    default_min_delay_ms: int = 0
    default_timeout_ms: int = 30000
    default_retry_count: int = 0
    rate_group: str = ""
    color: str = "#334155"
    description: str = ""
    editable_ports: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "NodeTypeSpec":
        if not isinstance(data, dict):
            raise ValueError("node type must be object")
        type_id = str(data.get("type_id", "") or "").strip()
        if not type_id:
            raise ValueError("node type without type_id")
        return cls(
            type_id=type_id,
            title=str(data.get("title", type_id) or type_id),
            category=str(data.get("category", "Пользовательские") or "Пользовательские"),
            inputs=tuple(PortSpec.from_dict(x) for x in data.get("inputs", []) or []),
            outputs=tuple(PortSpec.from_dict(x) for x in data.get("outputs", []) or []),
            kind=str(data.get("kind", "custom") or "custom"),
            action=str(data.get("action", "custom.noop") or "custom.noop"),
            default_config=dict(data.get("default_config", {}) or {}),
            default_workers=max(1, int(data.get("default_workers", data.get("workers", 1)) or 1)),
            default_min_delay_ms=max(0, int(data.get("default_min_delay_ms", data.get("min_delay_ms", 0)) or 0)),
            default_timeout_ms=max(1, int(data.get("default_timeout_ms", data.get("timeout_ms", 30000)) or 30000)),
            default_retry_count=max(0, int(data.get("default_retry_count", data.get("retry_count", 0)) or 0)),
            rate_group=str(data.get("rate_group", "") or ""),
            color=str(data.get("color", "#334155") or "#334155"),
            description=str(data.get("description", "") or ""),
            editable_ports=bool(data.get("editable_ports", True)),
        )

    def to_dict(self) -> dict:
        return {
            "type_id": self.type_id,
            "title": self.title,
            "category": self.category,
            "inputs": [p.to_dict() for p in self.inputs],
            "outputs": [p.to_dict() for p in self.outputs],
            "kind": self.kind,
            "action": self.action,
            "default_config": copy.deepcopy(self.default_config),
            "default_workers": self.default_workers,
            "default_min_delay_ms": self.default_min_delay_ms,
            "default_timeout_ms": self.default_timeout_ms,
            "default_retry_count": self.default_retry_count,
            "rate_group": self.rate_group,
            "color": self.color,
            "description": self.description,
            "editable_ports": self.editable_ports,
        }


@dataclass
class BlueprintNode:
    id: str
    type_id: str
    title: str = ""
    x: float = 0.0
    y: float = 0.0
    config: dict[str, Any] = field(default_factory=dict)
    workers: int = 1
    min_delay_ms: int = 0
    timeout_ms: int = 30000
    retry_count: int = 0
    rate_group: str = ""
    enabled: bool = True
    on_disabled: str = "skip"
    inputs: list[PortSpec] = field(default_factory=list)
    outputs: list[PortSpec] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict, registry: dict[str, NodeTypeSpec] | None = None) -> "BlueprintNode":
        if not isinstance(data, dict):
            raise ValueError("node must be object")
        node_id = str(data.get("id", "") or "").strip()
        if not node_id:
            raise ValueError("node without id")
        type_id = str(data.get("type_id", "") or "").strip()
        spec = (registry or {}).get(type_id)
        title = str(data.get("title", "") or (spec.title if spec else type_id) or type_id)
        inputs_raw = data.get("inputs", None)
        outputs_raw = data.get("outputs", None)
        if inputs_raw is None and spec:
            inputs = list(spec.inputs)
        else:
            inputs = [PortSpec.from_dict(x) for x in (inputs_raw or [])]
        if outputs_raw is None and spec:
            outputs = list(spec.outputs)
        else:
            outputs = [PortSpec.from_dict(x) for x in (outputs_raw or [])]
        cfg = copy.deepcopy(spec.default_config) if spec else {}
        cfg.update(dict(data.get("config", {}) or {}))
        return cls(
            id=node_id,
            type_id=type_id,
            title=title,
            x=float(data.get("x", 0.0) or 0.0),
            y=float(data.get("y", 0.0) or 0.0),
            config=cfg,
            workers=max(1, int(data.get("workers", cfg.get("workers", spec.default_workers if spec else 1)) or 1)),
            min_delay_ms=max(0, int(data.get("min_delay_ms", cfg.get("min_delay_ms", spec.default_min_delay_ms if spec else 0)) or 0)),
            timeout_ms=max(1, int(data.get("timeout_ms", cfg.get("timeout_ms", spec.default_timeout_ms if spec else 30000)) or 30000)),
            retry_count=max(0, int(data.get("retry_count", cfg.get("retry_count", spec.default_retry_count if spec else 0)) or 0)),
            rate_group=str(data.get("rate_group", cfg.get("rate_group", spec.rate_group if spec else "")) or ""),
            enabled=bool(data.get("enabled", cfg.get("enabled", True))),
            on_disabled=str(data.get("on_disabled", cfg.get("on_disabled", "skip")) or "skip"),
            inputs=inputs,
            outputs=outputs,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type_id": self.type_id,
            "title": self.title,
            "x": self.x,
            "y": self.y,
            "config": copy.deepcopy(self.config),
            "workers": int(self.workers),
            "min_delay_ms": int(self.min_delay_ms),
            "timeout_ms": int(self.timeout_ms),
            "retry_count": int(self.retry_count),
            "rate_group": self.rate_group,
            "enabled": bool(self.enabled),
            "on_disabled": self.on_disabled,
            "inputs": [p.to_dict() for p in self.inputs],
            "outputs": [p.to_dict() for p in self.outputs],
        }

    def input_port(self, name: str) -> PortSpec | None:
        return next((p for p in self.inputs if p.name == name), None)

    def output_port(self, name: str) -> PortSpec | None:
        return next((p for p in self.outputs if p.name == name), None)


@dataclass
class BlueprintEdge:
    id: str
    source_node: str
    source_port: str
    target_node: str
    target_port: str

    @classmethod
    def from_dict(cls, data: dict) -> "BlueprintEdge":
        if not isinstance(data, dict):
            raise ValueError("edge must be object")
        return cls(
            id=str(data.get("id", "") or f"edge-{int(time.time()*1000)}"),
            source_node=str(data.get("source_node", "") or ""),
            source_port=str(data.get("source_port", "") or ""),
            target_node=str(data.get("target_node", "") or ""),
            target_port=str(data.get("target_port", "") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_node": self.source_node,
            "source_port": self.source_port,
            "target_node": self.target_node,
            "target_port": self.target_port,
        }


@dataclass
class BlueprintGraph:
    name: str = "Новый blueprint"
    version: int = 1
    nodes: list[BlueprintNode] = field(default_factory=list)
    edges: list[BlueprintEdge] = field(default_factory=list)
    active: bool = False
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict, registry: dict[str, NodeTypeSpec] | None = None) -> "BlueprintGraph":
        if not isinstance(data, dict):
            raise ValueError("blueprint must be object")
        fmt = str(data.get("format", BLUEPRINT_FORMAT) or BLUEPRINT_FORMAT)
        if fmt != BLUEPRINT_FORMAT:
            raise ValueError(f"unsupported blueprint format: {fmt}")
        return cls(
            name=str(data.get("name", "Новый blueprint") or "Новый blueprint"),
            version=int(data.get("version", 1) or 1),
            nodes=[BlueprintNode.from_dict(x, registry) for x in (data.get("nodes", []) or [])],
            edges=[BlueprintEdge.from_dict(x) for x in (data.get("edges", []) or [])],
            active=bool(data.get("active", False)),
            description=str(data.get("description", "") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "format": BLUEPRINT_FORMAT,
            "name": self.name,
            "version": self.version,
            "active": self.active,
            "description": self.description,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    def node_map(self) -> dict[str, BlueprintNode]:
        return {n.id: n for n in self.nodes}


def compatible_types(source_type: str, target_type: str) -> bool:
    source_type = str(source_type or ANY_TYPE)
    target_type = str(target_type or ANY_TYPE)
    if source_type == ANY_TYPE or target_type == ANY_TYPE:
        return True
    if source_type == target_type:
        return True
    # Common union-ish conveniences for parser graphs.
    if source_type == "TagBundle" and target_type in ("MatchBundle", "Any"):
        return True
    if source_type == "SiteMatch" and target_type in ("MatchBundle", "Any"):
        return True
    if source_type == "MatchBundle" and target_type in ("TagBundle", "Any"):
        return True
    return False


def validate_graph(graph: BlueprintGraph, registry: dict[str, NodeTypeSpec] | None = None) -> list[str]:
    errors: list[str] = []
    node_by_id = graph.node_map()
    if len(node_by_id) != len(graph.nodes):
        errors.append("Есть дублирующиеся ID блоков")
    for node in graph.nodes:
        if registry is not None and node.type_id not in registry:
            errors.append(f"Блок {node.id}: неизвестный тип {node.type_id}")
        in_names = [p.name for p in node.inputs]
        out_names = [p.name for p in node.outputs]
        if len(set(in_names)) != len(in_names):
            errors.append(f"Блок {node.title}: повторяющиеся входы")
        if len(set(out_names)) != len(out_names):
            errors.append(f"Блок {node.title}: повторяющиеся выходы")
    used_incoming: dict[tuple[str, str], str] = {}
    graph_adj: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    for edge in graph.edges:
        src = node_by_id.get(edge.source_node)
        dst = node_by_id.get(edge.target_node)
        if not src:
            errors.append(f"Связь {edge.id}: нет исходного блока {edge.source_node}")
            continue
        if not dst:
            errors.append(f"Связь {edge.id}: нет целевого блока {edge.target_node}")
            continue
        src_port = src.output_port(edge.source_port)
        dst_port = dst.input_port(edge.target_port)
        if not src_port:
            errors.append(f"Связь {edge.id}: у {src.title} нет выхода {edge.source_port}")
            continue
        if not dst_port:
            errors.append(f"Связь {edge.id}: у {dst.title} нет входа {edge.target_port}")
            continue
        if not compatible_types(src_port.type, dst_port.type):
            errors.append(f"Связь {src.title}.{src_port.name} → {dst.title}.{dst_port.name}: несовместимые типы {src_port.type} → {dst_port.type}")
        key = (dst.id, dst_port.name)
        if not dst_port.multi and key in used_incoming:
            errors.append(f"Вход {dst.title}.{dst_port.name} уже подключён; включи multi=true или удали лишнюю связь")
        used_incoming[key] = edge.id
        graph_adj.setdefault(src.id, []).append(dst.id)

    # Cycle detection.  Parser execution graphs must be DAG; feedback loops may
    # be added later only for local non-network nodes with explicit safeguards.
    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node_id: str, stack: list[str]):
        if node_id in visiting:
            errors.append("Цикл в графе: " + " -> ".join(stack + [node_id]))
            return
        if node_id in visited:
            return
        visiting.add(node_id)
        for nxt in graph_adj.get(node_id, []):
            dfs(nxt, stack + [node_id])
        visiting.discard(node_id)
        visited.add(node_id)

    for node_id in list(graph_adj):
        dfs(node_id, [])
    return errors



def analyze_graph(graph: BlueprintGraph, registry: dict[str, NodeTypeSpec] | None = None, *, full_access: bool = False) -> dict[str, list[str]]:
    """Return {errors,warnings}.

    In full-access mode the editor behaves like a real power-user blueprint
    system: incompatible port types, duplicate non-multi inputs and cycles are
    warnings, not blockers. Missing nodes/ports and duplicate IDs remain hard
    errors because the runtime cannot even route the graph.
    """
    errors: list[str] = []
    warnings: list[str] = []
    node_by_id = graph.node_map()
    if len(node_by_id) != len(graph.nodes):
        errors.append("Есть дублирующиеся ID блоков")
    for node in graph.nodes:
        if registry is not None and node.type_id not in registry:
            warnings.append(f"Блок {node.id}: неизвестный тип {node.type_id}; будет трактоваться как custom/noop, если нет Python-плагина")
        in_names = [p.name for p in node.inputs]
        out_names = [p.name for p in node.outputs]
        if len(set(in_names)) != len(in_names):
            errors.append(f"Блок {node.title}: повторяющиеся входы")
        if len(set(out_names)) != len(out_names):
            errors.append(f"Блок {node.title}: повторяющиеся выходы")
        if int(getattr(node, "workers", 1) or 1) > 1 and not str(getattr(node, "rate_group", "") or node.config.get("rate_group", "")).strip():
            warnings.append(f"Блок {node.title}: workers={node.workers}, но rate_group пустой; пользователь отвечает за лимиты")
    used_incoming: dict[tuple[str, str], str] = {}
    graph_adj: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    for edge in graph.edges:
        src = node_by_id.get(edge.source_node)
        dst = node_by_id.get(edge.target_node)
        if not src:
            errors.append(f"Связь {edge.id}: нет исходного блока {edge.source_node}")
            continue
        if not dst:
            errors.append(f"Связь {edge.id}: нет целевого блока {edge.target_node}")
            continue
        src_port = src.output_port(edge.source_port)
        dst_port = dst.input_port(edge.target_port)
        if not src_port:
            errors.append(f"Связь {edge.id}: у {src.title} нет выхода {edge.source_port}")
            continue
        if not dst_port:
            errors.append(f"Связь {edge.id}: у {dst.title} нет входа {edge.target_port}")
            continue
        if not compatible_types(src_port.type, dst_port.type):
            msg = f"Связь {src.title}.{src_port.name} → {dst.title}.{dst_port.name}: несовместимые типы {src_port.type} → {dst_port.type}"
            (warnings if full_access else errors).append(msg)
        key = (dst.id, dst_port.name)
        if not dst_port.multi and key in used_incoming:
            msg = f"Вход {dst.title}.{dst_port.name} уже подключён; full-access разрешит, runtime возьмёт последний/пользовательский обработчик"
            (warnings if full_access else errors).append(msg)
        used_incoming[key] = edge.id
        graph_adj.setdefault(src.id, []).append(dst.id)

    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node_id: str, stack: list[str]):
        if node_id in visiting:
            msg = "Цикл в графе: " + " -> ".join(stack + [node_id])
            (warnings if full_access else errors).append(msg)
            return
        if node_id in visited:
            return
        visiting.add(node_id)
        for nxt in graph_adj.get(node_id, []):
            dfs(nxt, stack + [node_id])
        visiting.discard(node_id)
        visited.add(node_id)

    for node_id in list(graph_adj):
        dfs(node_id, [])
    return {"errors": errors, "warnings": warnings}

def sorted_nodes_for_display(nodes: Iterable[BlueprintNode]) -> list[BlueprintNode]:
    return sorted(nodes, key=lambda n: (float(n.x), float(n.y), n.title.lower(), n.id))
