from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

_ADJ_CACHE_KEY: tuple[int, int, float] | None = None
_ADJ_CACHE: dict[str, list[tuple[str, float]]] = {}


def load_form_graph(path: Path) -> list[dict[str, Any]]:
    """Load edges from the JSON file (fallback when DB is unavailable)."""
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict) and "edges" in data:
        edges = data["edges"]
    elif isinstance(data, list):
        edges = data
    else:
        return []
    out: list[dict[str, Any]] = []
    for item in edges:
        if not isinstance(item, dict):
            continue
        src = item.get("source") or item.get("src")
        dst = item.get("target") or item.get("dst")
        if not isinstance(src, str) or not isinstance(dst, str):
            continue
        rel = item.get("relation", "related")
        weight = float(item.get("weight", 1.0))
        out.append({"source": src, "target": dst, "relation": str(rel), "weight": weight})
    return out


def load_graph_edges(conn=None, json_path: Path | None = None) -> list[dict[str, Any]]:
    """Load edges from local graph storage with JSON fallback."""
    if conn is not None:
        try:
            from rag_pipeline.graph_builder import load_graph_edges_from_store
            edges = load_graph_edges_from_store()
            if edges:
                return edges
        except Exception:
            pass
    if json_path is not None:
        return load_form_graph(json_path)
    return []


def expand_file_ids(
    seed_ids: list[str],
    edges: list[dict[str, Any]],
    *,
    hops: int = 1,
    min_weight: float = 0.25,
) -> list[str]:
    """Undirected BFS expansion through graph edges (multi-type aware)."""
    global _ADJ_CACHE_KEY, _ADJ_CACHE
    cache_key = (id(edges), len(edges), float(min_weight))
    if cache_key != _ADJ_CACHE_KEY:
        adj: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for e in edges:
            w = float(e.get("weight", 1.0))
            if w < min_weight:
                continue
            s, t = e["source"], e["target"]
            adj[s].append((t, w))
            adj[t].append((s, w))
        _ADJ_CACHE_KEY = cache_key
        _ADJ_CACHE = dict(adj)
    adj = _ADJ_CACHE

    current = set(seed_ids)
    for _ in range(max(0, hops)):
        nxt = set(current)
        for fid in current:
            for neighbor, _w in adj.get(fid, []):
                nxt.add(neighbor)
        current = nxt
    return sorted(current)
