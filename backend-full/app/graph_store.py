from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from config import GRAPH_PATH


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_payload() -> dict[str, Any]:
    return {
        "version": 2,
        "updated_at": _utc_now_iso(),
        "source": "local_graph_store",
        "stats": {"node_count": 0, "edge_count": 0, "edge_types": {}},
        "nodes": [],
        "edges": [],
    }


def _edge_type_counts(edges: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for edge in edges:
        relation = str(edge.get("relation", "other"))
        counts[relation] = counts.get(relation, 0) + 1
    return counts


def ensure_graph_store() -> None:
    GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not GRAPH_PATH.exists():
        _write_payload(_base_payload())


def _read_payload() -> dict[str, Any]:
    ensure_graph_store()
    try:
        raw = GRAPH_PATH.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, ValueError):
        payload = _base_payload()
    if not isinstance(payload, dict):
        payload = _base_payload()
    payload.setdefault("nodes", [])
    payload.setdefault("edges", [])
    payload.setdefault("stats", {})
    return payload


def _write_payload(payload: dict[str, Any]) -> None:
    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    payload["updated_at"] = _utc_now_iso()
    payload["stats"] = {
        "node_count": len(nodes) if isinstance(nodes, list) else 0,
        "edge_count": len(edges) if isinstance(edges, list) else 0,
        "edge_types": _edge_type_counts(edges if isinstance(edges, list) else []),
    }
    GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    GRAPH_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load_graph_edges() -> list[dict[str, Any]]:
    payload = _read_payload()
    edges = payload.get("edges", [])
    return [edge for edge in edges if isinstance(edge, dict)]


def load_graph_nodes() -> list[dict[str, Any]]:
    payload = _read_payload()
    rows: list[dict[str, Any]] = []
    for node in payload.get("nodes", []):
        if not isinstance(node, dict):
            continue
        rows.append(
            {
                "file_id": node.get("file_id", ""),
                "display_title": node.get("display_title", ""),
                "tree_summary": node.get("tree_summary", ""),
                "metadata": node.get("metadata", {}) if isinstance(node.get("metadata"), dict) else {},
                "updated_at": node.get("updated_at"),
            }
        )
    rows.sort(key=lambda row: row["file_id"])
    return rows


def _upsert_nodes(raw_nodes: list[dict[str, Any]]) -> int:
    payload = _read_payload()
    existing = {
        node.get("file_id"): node
        for node in payload.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("file_id"), str)
    }
    for node in raw_nodes:
        file_id = node.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            continue
        merged = dict(existing.get(file_id, {}))
        merged.update(node)
        merged["updated_at"] = _utc_now_iso()
        existing[file_id] = merged
    payload["nodes"] = [existing[file_id] for file_id in sorted(existing)]
    _write_payload(payload)
    return len(raw_nodes)


def replace_graph_nodes(raw_nodes: list[dict[str, Any]]) -> int:
    payload = _read_payload()
    payload["nodes"] = []
    _write_payload(payload)
    return _upsert_nodes(raw_nodes)


def replace_graph_edges(edges: list[dict[str, Any]]) -> int:
    payload = _read_payload()
    payload["edges"] = [dict(edge) for edge in edges]
    _write_payload(payload)
    return len(edges)


def upsert_graph_nodes(nodes: list[dict[str, Any]]) -> int:
    return _upsert_nodes(nodes)


def upsert_single_graph_node(node: dict[str, Any]) -> None:
    _upsert_nodes([node])


def delete_document_graph(file_id: str) -> None:
    payload = _read_payload()
    payload["nodes"] = [
        node
        for node in payload.get("nodes", [])
        if isinstance(node, dict) and node.get("file_id") != file_id
    ]
    payload["edges"] = [
        edge
        for edge in payload.get("edges", [])
        if isinstance(edge, dict) and edge.get("source") != file_id and edge.get("target") != file_id
    ]
    _write_payload(payload)
