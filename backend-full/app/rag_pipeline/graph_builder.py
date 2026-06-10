"""
Knowledge-graph builder: folder edges, semantic-similarity edges, tree-overlap edges.

Edge types stored in ``graph_edges``:
- **same_folder** – documents sharing a parent directory (from manifest ``relative_path``)
- **semantic_similar** – cosine similarity between document-level centroids ≥ threshold
- **shared_sections** – Jaccard overlap of normalised tree section titles ≥ threshold
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from graph_store import load_graph_edges, load_graph_nodes, replace_graph_edges as replace_graph_edges_in_store
from graph_store import replace_graph_nodes as replace_graph_nodes_in_store
from graph_store import upsert_graph_nodes as upsert_graph_nodes_in_store
from graph_store import upsert_single_graph_node as upsert_single_graph_node_in_store
from vector_store import get_all_embeddings_by_file


# ---------------------------------------------------------------------------
# Folder (directory) edges
# ---------------------------------------------------------------------------

def build_folder_edges(docs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Chain-link documents that share a parent directory."""
    groups: dict[str, list[str]] = defaultdict(list)
    for fid, row in docs.items():
        rp = row.get("relative_path")
        if not isinstance(rp, str) or not rp.strip():
            continue
        parent = str(Path(rp.replace("\\", "/")).parent)
        if parent in (".", ""):
            parent = "(root)"
        groups[parent].append(fid)

    edges: list[dict[str, Any]] = []
    for _parent, ids in sorted(groups.items()):
        ids = sorted(set(ids))
        if len(ids) < 2:
            continue
        for i in range(len(ids) - 1):
            edges.append({
                "source": ids[i],
                "target": ids[i + 1],
                "relation": "same_folder",
                "weight": 1.0,
            })
    return edges


# ---------------------------------------------------------------------------
# Document-level centroid embeddings
# ---------------------------------------------------------------------------

def compute_doc_centroids_from_store() -> dict[str, list[float]]:
    """Compute normalised centroid per file_id from local vector-store embeddings."""
    groups = get_all_embeddings_by_file()
    centroids: dict[str, list[float]] = {}
    for file_id, vecs in groups.items():
        arr = np.array(vecs, dtype=np.float32)
        centroid = arr.mean(axis=0)
        norm = float(np.linalg.norm(centroid))
        if norm > 1e-9:
            centroid = centroid / norm
        centroids[file_id] = centroid.tolist()
    return centroids


def compute_centroid_from_vectors(vectors: list[list[float]]) -> list[float]:
    """Normalised centroid from a list of embedding vectors (in-memory, no DB)."""
    if not vectors:
        return []
    arr = np.array(vectors, dtype=np.float32)
    centroid = arr.mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm > 1e-9:
        centroid = centroid / norm
    return centroid.tolist()


# ---------------------------------------------------------------------------
# Semantic-similarity edges
# ---------------------------------------------------------------------------

def build_semantic_edges(
    centroids: dict[str, list[float]],
    *,
    threshold: float = 0.55,
    max_per_doc: int = 5,
) -> list[dict[str, Any]]:
    """Edges between documents whose centroid cosine similarity ≥ *threshold*."""
    file_ids = sorted(centroids.keys())
    n = len(file_ids)
    if n < 2:
        return []

    mat = np.array([centroids[fid] for fid in file_ids], dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    normed = mat / (norms + 1e-9)
    sim = normed @ normed.T

    seen: set[tuple[str, str]] = set()
    edges: list[dict[str, Any]] = []

    for i in range(n):
        candidates = [
            (float(sim[i, j]), j)
            for j in range(n)
            if j != i and float(sim[i, j]) >= threshold
        ]
        candidates.sort(reverse=True)
        for score, j in candidates[:max_per_doc]:
            key = tuple(sorted((file_ids[i], file_ids[j])))
            if key in seen:
                continue
            seen.add(key)
            edges.append({
                "source": key[0],
                "target": key[1],
                "relation": "semantic_similar",
                "weight": round(score, 4),
            })
    return edges


# ---------------------------------------------------------------------------
# Tree-overlap edges (Jaccard on section titles)
# ---------------------------------------------------------------------------

_TITLE_RE = re.compile(r"[^a-z0-9 ]+")                          # Remove non-alphanumeric characters


def _normalise_title(t: str) -> str:
    return _TITLE_RE.sub(" ", t.lower()).strip()


def _extract_tree_titles(tree: dict[str, Any]) -> set[str]:
    nodes = tree.get("nodes", [])
    root_id = tree.get("root_id"    , "n0")
    titles: set[str] = set()
    for n in nodes:
        if not isinstance(n, dict) or n.get("id") == root_id:
            continue
        raw = str(n.get("title", "")).strip()
        if len(raw) > 2:
            normed = _normalise_title(raw)
            if normed:
                titles.add(normed)
    return titles


def build_tree_overlap_edges(
    docs: dict[str, dict[str, Any]],
    *,
    threshold: float = 0.25,
) -> list[dict[str, Any]]:
    """Edges between documents whose tree section titles have Jaccard ≥ *threshold*."""
    doc_titles: dict[str, set[str]] = {}
    for fid, row in docs.items():
        tree = row.get("tree")
        if not isinstance(tree, dict):
            continue
        titles = _extract_tree_titles(tree)
        if titles:
            doc_titles[fid] = titles

    file_ids = sorted(doc_titles.keys())
    edges: list[dict[str, Any]] = []
    for i in range(len(file_ids)):
        for j in range(i + 1, len(file_ids)):
            a, b = doc_titles[file_ids[i]], doc_titles[file_ids[j]]
            intersection = len(a & b)
            if intersection == 0:
                continue
            jaccard = intersection / len(a | b)
            if jaccard >= threshold:
                edges.append({
                    "source": file_ids[i],
                    "target": file_ids[j],
                    "relation": "shared_sections",
                    "weight": round(jaccard, 4),
                })
    return edges


# ---------------------------------------------------------------------------
# Merge / deduplicate
# ---------------------------------------------------------------------------

def merge_all_edges(*edge_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge N edge lists; first occurrence wins for each (source, target, relation) triple."""
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for edges in edge_lists:
        for e in edges:
            s = e.get("source", "")
            t = e.get("target", "")
            r = e.get("relation", "related")
            key = (*sorted((s, t)), r)
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(e))
    return out


# ---------------------------------------------------------------------------
# DB persistence helpers
# ---------------------------------------------------------------------------

def upsert_graph_nodes(
    centroids: dict[str, list[float]],
    manifest: dict[str, dict[str, Any]],
    *,
    replace_existing: bool = False,
) -> int:
    """Insert or update file-backed graph nodes with centroid embeddings + tree summaries."""
    if not centroids:
        return 0
    nodes: list[dict[str, Any]] = []
    for fid, centroid in centroids.items():
        row = manifest.get(fid, {})
        tree = row.get("tree")
        tree_summary = ""
        if isinstance(tree, dict):
            titles = _extract_tree_titles(tree)
            tree_summary = " | ".join(sorted(titles)[:50])
        nodes.append(
            {
                "file_id": fid,
                "display_title": row.get("display_title", ""),
                "doc_embedding": centroid,
                "tree_summary": tree_summary,
                "metadata": row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {},
            }
        )
    if replace_existing:
        return replace_graph_nodes_in_store(nodes)
    return upsert_graph_nodes_in_store(nodes)


def upsert_single_graph_node(
    file_id: str, centroid: list[float],
    display_title: str = "", tree: dict | None = None,
) -> None:
    """Upsert a single graph node (called from ingest)."""
    tree_summary = ""
    if isinstance(tree, dict):
        titles = _extract_tree_titles(tree)
        tree_summary = " | ".join(sorted(titles)[:50])
    upsert_single_graph_node_in_store(
        {
            "file_id": file_id,
            "display_title": display_title,
            "doc_embedding": centroid,
            "tree_summary": tree_summary,
            "metadata": {},
        }
    )


def replace_graph_edges(edges: list[dict[str, Any]]) -> int:
    """Replace all file-backed graph edges with the given list."""
    return replace_graph_edges_in_store(edges)


def load_graph_edges_from_store() -> list[dict[str, Any]]:
    """Read all edges from local graph storage."""
    return load_graph_edges()


def load_graph_nodes_from_store() -> list[dict[str, Any]]:
    """Read all nodes from local graph storage without raw embedding vectors."""
    return load_graph_nodes()


