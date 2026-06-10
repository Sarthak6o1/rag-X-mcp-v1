from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import (
    FILE_PREFIX,
    GRAPH_EXPANSION_HOPS,
    GRAPH_PATH,
    MANIFEST_PATH,
    RAG_MAX_FILTER_DOCS,
    USE_TREE_QUERY_HINTS,
)
from graph_store import load_graph_edges, load_graph_nodes
from rag_pipeline.form_graph import expand_file_ids, load_form_graph
from rag_pipeline.manifest_io import load_manifest_documents
from rag_pipeline.prompt_filter import rank_file_ids_for_query
from rag_pipeline.tree_text import build_tree_augmented_query
from vector_store import get_document_chunks as fetch_document_chunks
from vector_store import list_documents as list_vector_documents3
from vector_store import list_file_ids as list_vector_file_ids
from vector_store import query_documents as query_vector_documents

router = APIRouter()

_MANIFEST_MTIME: float = 0.0
_MANIFEST_ROWS: dict[str, dict[str, Any]] = {}
_GRAPH_MTIME: float = 0.0
_GRAPH_EDGES: list[dict[str, Any]] = []
_CATALOG_CACHE_KEY: tuple[float, tuple[str, ...]] | None = None
_CATALOG_CACHE: list[tuple[str, str]] = []


def _refresh_manifest() -> dict[str, dict[str, Any]]:
    global _MANIFEST_MTIME, _MANIFEST_ROWS
    if not MANIFEST_PATH.is_file():
        _MANIFEST_ROWS = {}
        return _MANIFEST_ROWS
    mtime = MANIFEST_PATH.stat().st_mtime
    if mtime != _MANIFEST_MTIME:
        _MANIFEST_MTIME = mtime
        _MANIFEST_ROWS = load_manifest_documents(MANIFEST_PATH)
    return _MANIFEST_ROWS


def _refresh_graph() -> list[dict[str, Any]]:
    """Load graph edges from local graph storage, caching by file mtime."""
    global _GRAPH_MTIME, _GRAPH_EDGES
    if not GRAPH_PATH.is_file():
        _GRAPH_EDGES = []
        return _GRAPH_EDGES
    mtime = GRAPH_PATH.stat().st_mtime
    if mtime != _GRAPH_MTIME:
        _GRAPH_MTIME = mtime
        edges = load_graph_edges()
        _GRAPH_EDGES = edges if edges else load_form_graph(GRAPH_PATH)
    return _GRAPH_EDGES


def _get_all_file_ids(prefix: str = FILE_PREFIX) -> list[str]:
    return list_vector_file_ids(prefix=prefix)


def _build_search_catalog(file_ids: list[str]) -> list[tuple[str, str]]:
    global _CATALOG_CACHE_KEY, _CATALOG_CACHE
    manifest = _refresh_manifest()
    cache_key = (_MANIFEST_MTIME, tuple(file_ids))
    if cache_key == _CATALOG_CACHE_KEY:
        return list(_CATALOG_CACHE)

    catalog: list[tuple[str, str]] = []
    for fid in file_ids:
        row = manifest.get(fid)
        if row:
            blob = f"{fid} {row.get('display_title', '')} {row.get('relative_path', '')}"
        else:
            blob = fid
        catalog.append((fid, blob))
    _CATALOG_CACHE_KEY = cache_key
    _CATALOG_CACHE = list(catalog)
    return catalog


def _narrow_file_filter(file_ids: list[str], all_id_set: set[str]) -> list[str] | None:
    if not file_ids:
        return None
    unique_ids = list(dict.fromkeys(file_ids))
    if len(unique_ids) >= len(all_id_set) and set(unique_ids) == all_id_set:
        return None
    return unique_ids


class QueryRequest(BaseModel):
    query: str
    k: int = Field(default=6, ge=1, le=50)
    file_ids: list[str] | None = None


class HybridQueryRequest(BaseModel):
    query: str
    k: int = Field(default=8, ge=1, le=50)
    auto_filter: bool = True
    max_documents: int = RAG_MAX_FILTER_DOCS
    file_ids_csv: str = ""
    use_tree_hints: bool = True


class ChunkResult(BaseModel):
    file_id: str
    chunk_index: int
    content: str
    score: float
    metadata: dict


class QueryResponse(BaseModel):
    query: str
    results: list[ChunkResult]
    filter_note: str = ""
    tree_note: str = ""


@router.post("/api/query", response_model=QueryResponse)
def query_documents(req: QueryRequest):
    """Simple vector search (no pipeline filters)."""
    if not req.query.strip():
        raise HTTPException(400, "query cannot be empty")

    rows = query_vector_documents(req.query, req.k, req.file_ids)

    results = [
        ChunkResult(
            file_id=row["file_id"],
            chunk_index=row["chunk_index"],
            content=row["content"],
            score=float(row["score"]),
            metadata=row["metadata"] if isinstance(row["metadata"], dict) else {},
        )
        for row in rows
    ]
    return QueryResponse(query=req.query, results=results)


@router.post("/api/query/hybrid", response_model=QueryResponse)
def query_hybrid(req: HybridQueryRequest):
    """
    Full hybrid retrieval pipeline:
    1) Stage-0 prompt filter (rapidfuzz on file_id slugs + manifest titles)
    2) Knowledge graph expansion (N-hop: folder + semantic + tree edges)
    3) PageIndex tree hints (outline-augmented query)
    4) Vector search (local Chroma cosine similarity)
    """
    if not req.query.strip():

    all_ids = _get_all_file_ids()
    if not all_ids:
        return QueryResponse(query=req.query, results=[], filter_note="No documents indexed.")
    all_id_set = set(all_ids)

    filter_note = ""
    if req.file_ids_csv.strip():
        chosen = [x.strip() for x in req.file_ids_csv.split(",") if x.strip()]
        file_ids = [fid for fid in chosen if fid in all_id_set]
        if not file_ids:
            raise HTTPException(400, "No valid file_ids in file_ids_csv")
        filter_note = f"Using {len(file_ids)} file_id(s) from file_ids_csv."
    elif req.auto_filter:
        catalog = _build_search_catalog(all_ids)
        ranked = rank_file_ids_for_query(req.query, catalog, max_documents=req.max_documents)
        if ranked:
            edges = _refresh_graph()
            expanded = expand_file_ids(ranked, edges, hops=GRAPH_EXPANSION_HOPS)
            file_ids = [fid for fid in expanded if fid in all_id_set]
            if not file_ids:
                file_ids = all_ids
                filter_note = "Graph expansion produced no valid ids; falling back to all documents."
            else:
                edge_types = set(e.get("relation", "") for e in edges)
                filter_note = (
                    f"Stage-0 filter: {len(ranked)} ranked + graph expansion -> "
                    f"{len(file_ids)} file_id(s) (hops={GRAPH_EXPANSION_HOPS}, "
                    f"edge_types={sorted(edge_types)})."
                )
        else:
            file_ids = all_ids
            filter_note = "No strong file_id match; falling back to all documents."
    else:
        file_ids = all_ids
        filter_note = "auto_filter disabled; searching all documents."

    tree_note = ""
    vector_query = req.query
    if req.use_tree_hints and USE_TREE_QUERY_HINTS and file_ids:
        manifest = _refresh_manifest()
        augmented = build_tree_augmented_query(req.query, file_ids, manifest)
        if augmented != req.query:
            vector_query = augmented
            tree_note = "PageIndex outline hints appended to the vector query."

    rows = query_vector_documents(vector_query, req.k, _narrow_file_filter(file_ids, all_id_set))

    results = [
        ChunkResult(
            file_id=row["file_id"],
            chunk_index=row["chunk_index"],
            content=row["content"],
            score=float(row["score"]),
            metadata=row["metadata"] if isinstance(row["metadata"], dict) else {},
        )
        for row in rows
    ]

    return QueryResponse(query=req.query, results=results, filter_note=filter_note, tree_note=tree_note)


# ---------------------------------------------------------------------------
# Document listing / detail endpoints
# ---------------------------------------------------------------------------

@router.get("/api/documents")
def list_documents():
    return [
        {
            "file_id": row["file_id"],
            "chunk_count": row["chunk_count"],
            "ingested_at": None,
        }
        for row in list_vector_documents()
    ]


@router.get("/api/documents/{file_id}/chunks")
def get_document_chunks(file_id: str):
    rows = fetch_document_chunks(file_id)

    if not rows:
        raise HTTPException(404, f"No chunks found for file_id: {file_id}")

    return {
        "file_id": file_id,
        "chunk_count": len(rows),
        "chunks": [
            {
                "chunk_index": row["chunk_index"],
                "content": row["content"],
                "metadata": row["metadata"] if isinstance(row["metadata"], dict) else {},
            }
            for row in rows
        ],
    }


@router.get("/api/documents/{file_id}/outline")
def get_document_outline(file_id: str):
    """Return the PageIndex-style section tree from the manifest."""
    manifest = _refresh_manifest()
    row = manifest.get(file_id)
    if not row:
        raise HTTPException(404, f"No manifest entry for {file_id}")
    tree = row.get("tree")
    if not isinstance(tree, dict):
        raise HTTPException(404, f"No tree stored for {file_id}")
    return {
        "file_id": file_id,
        "display_title": row.get("display_title", file_id),
        "relative_path": row.get("relative_path", ""),
        "tree": tree,
    }


@router.get("/api/file-ids")
def list_file_ids():
    return _get_all_file_ids(prefix="")


# ---------------------------------------------------------------------------
# Knowledge graph endpoints
# ---------------------------------------------------------------------------

@router.get("/api/graph")
def get_graph():
    """Return the full knowledge graph: nodes + typed edges."""
    nodes = load_graph_nodes()
    edges = _refresh_graph()

    edge_type_counts: dict[str, int] = {}
    for e in edges:
        r = e.get("relation", "other")
        edge_type_counts[r] = edge_type_counts.get(r, 0) + 1

    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "edge_types": edge_type_counts,
        "nodes": nodes,
        "edges": edges,
    }


@router.get("/api/graph/tree")
def get_kb_tree():
    """
    KB-wide hierarchical tree built from manifest relative_path values.
    Structure: root → folders → documents (each with its PageIndex section tree).
    """
    manifest = _refresh_manifest()
    if not manifest:
        return {"root": "Knowledge Base", "children": []}

    folder_map: dict[str, list[dict]] = defaultdict(list)
    for fid, row in sorted(manifest.items()):
        rp = row.get("relative_path", "")
        if not rp:
            rp = fid
        parts = Path(rp.replace("\\", "/")).parts
        if len(parts) <= 1:
            folder_key = "(root)"
        else:
            folder_key = "/".join(parts[:-1])

        doc_node: dict[str, Any] = {
            "file_id": fid,
            "name": row.get("display_title", parts[-1] if parts else fid),
            "type": "document",
            "relative_path": rp,    
        }
        tree = row.get("tree")
        if isinstance(tree, dict):
            doc_node["sections"] = tree.get("nodes", [])
        folder_map[folder_key].append(doc_node)

    def _build_nested(folder_map: dict[str, list[dict]]) -> list[dict]:
        tree: dict[str, Any] = {}
        for folder_path, docs in sorted(folder_map.items()):
            parts = folder_path.split("/") if folder_path != "(root)" else ["(root)"]
            node = tree
            for part in parts:
                if "children_map" not in node:
                    node["children_map"] = {}
                if part not in node["children_map"]:
                    node["children_map"][part] = {"name": part, "type": "folder"}
                node = node["children_map"][part]
            node.setdefault("documents", []).extend(docs)

        def _flatten(node: dict) -> dict:
            result: dict[str, Any] = {"name": node.get("name", ""), "type": node.get("type", "folder")}
            children: list[dict] = [] 
            for child_node in (node.get("children_map") or {}).values():
                children.append(_flatten(child_node))
            children.extend(node.get("documents", []))
            if children:
                result["children"] = children
            return result

        top_children: list[dict] = []
        for child_node in (tree.get("children_map") or {}).values():
            top_children.append(_flatten(child_node))
        top_children.extend(tree.get("documents", []))
        return top_children

    return {
        "root": "Knowledge Base",
        "document_count": len(manifest),
        "children": _build_nested(folder_map),
    }


@router.post("/api/resolve-file-ids")
def resolve_file_ids(payload: dict):
    """Stage-0: rank file_ids from a query using prompt filter + graph expansion."""
    query = payload.get("query", "")
    max_documents = payload.get("max_documents", RAG_MAX_FILTER_DOCS)

    all_ids = _get_all_file_ids()
    if not all_ids:
        return {"ranked": [], "expanded": [], "note": "No documents indexed."}

    catalog = _build_search_catalog(all_ids)
    ranked = rank_file_ids_for_query(query, catalog, max_documents=max_documents)
    if not ranked:
        return {"ranked": [], "expanded": [], "note": "No strong file_id match."}

    edges = _refresh_graph()
    expanded = expand_file_ids(ranked, edges, hops=GRAPH_EXPANSION_HOPS)
    all_id_set = set(all_ids)
    valid = [fid for fid in expanded if fid in all_id_set]
    
    return {
        "ranked": ranked,
        "expanded": sorted(set(valid) - set(ranked)),
        "all_valid": valid,
        "note": f"{len(ranked)} ranked + graph -> {len(valid)} total",
    }
