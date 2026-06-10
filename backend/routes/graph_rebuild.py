from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import (
    GRAPH_PATH,
    MANIFEST_PATH,
    MAX_SEMANTIC_EDGES_PER_DOC,
    SEMANTIC_EDGE_THRESHOLD,
    TREE_OVERLAP_THRESHOLD,
)
from rag_pipeline.form_graph import load_form_graph
from rag_pipeline.graph_builder import (
    build_folder_edges,
    build_semantic_edges,
    build_tree_overlap_edges,
    compute_doc_centroids_from_store,
    load_graph_nodes_from_store,
    merge_all_edges,
    replace_graph_edges,
    upsert_graph_nodes,
)
from rag_pipeline.manifest_io import load_manifest_documents

router = APIRouter()


class RebuildGraphBody(BaseModel):
    merge_manual: bool = Field(
        default=True,
        description="Keep existing non-duplicate manually-added edges.",
    )
    semantic_threshold: float | None = Field(
        default=None,
        description="Override SEMANTIC_EDGE_THRESHOLD for this rebuild.",
    )
    tree_threshold: float | None = Field(
        default=None,
        description="Override TREE_OVERLAP_THRESHOLD for this rebuild.",
    )


@router.post("/api/graph/rebuild")
def rebuild_knowledge_graph(body: RebuildGraphBody = RebuildGraphBody()):
    """
    Full knowledge-graph rebuild:
    1. Compute document-level centroid embeddings from chunk vectors
    2. Upsert local graph nodes (file_id → centroid + tree summary)
    3. Build folder edges (same parent directory)
    4. Build semantic-similarity edges (cosine ≥ threshold)
    5. Build tree-overlap edges (Jaccard of section titles ≥ threshold)
    6. Store edges in the local graph file + export form_graph.json
    """
    docs = load_manifest_documents(MANIFEST_PATH)
    if not docs:
        raise HTTPException(400, "Manifest is empty — ingest documents first.")

    sem_thresh = body.semantic_threshold if body.semantic_threshold is not None else SEMANTIC_EDGE_THRESHOLD
    tree_thresh = body.tree_threshold if body.tree_threshold is not None else TREE_OVERLAP_THRESHOLD

    centroids = compute_doc_centroids_from_store()
    node_count = upsert_graph_nodes(centroids, docs, replace_existing=True)

    folder_edges = build_folder_edges(docs)
    semantic_edges = build_semantic_edges(
        centroids, threshold=sem_thresh, max_per_doc=MAX_SEMANTIC_EDGES_PER_DOC,
    )
    tree_edges = build_tree_overlap_edges(docs, threshold=tree_thresh)

    manual: list[dict] = []
    if body.merge_manual and GRAPH_PATH.is_file():
        existing = load_form_graph(GRAPH_PATH)
        manual = [e for e in existing if e.get("relation") not in ("same_folder", "semantic_similar", "shared_sections")]

    merged = merge_all_edges(manual, folder_edges, semantic_edges, tree_edges)
    edge_count = replace_graph_edges(merged)

    nodes_list = load_graph_nodes_from_store()

    edge_type_counts = {}
    for e in merged:
        r = e.get("relation", "other")
        edge_type_counts[r] = edge_type_counts.get(r, 0) + 1

    payload = {
        "version": 2,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "knowledge_graph_builder",
        "stats": {
            "node_count": node_count,
            "edge_count": edge_count,
            "edge_types": edge_type_counts,
        },
        "nodes": nodes_list,
        "edges": merged,
    }
    GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    GRAPH_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "ok": True,
        "node_count": node_count,
        "edge_count": edge_count,
        "edge_types": edge_type_counts,
        "semantic_threshold": sem_thresh,
        "tree_threshold": tree_thresh,
        "graph_path": str(GRAPH_PATH),
    }
