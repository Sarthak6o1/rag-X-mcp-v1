from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import MANIFEST_PATH
from graph_store import delete_document_graph
from rag_pipeline.graph_builder import compute_centroid_from_vectors, upsert_single_graph_node
from rag_pipeline.manifest_io import merge_manifest_entries
from services.chunker import chunk_text
from vector_store import delete_document as delete_document_chunks
from vector_store import upsert_document

router = APIRouter()


def _strip_nul(value: str) -> str:
    return value.replace("\x00", "")


class IngestRequest(BaseModel):
    file_id: str
    content: str
    metadata: dict | None = None
    display_title: str | None = None
    relative_path: str | None = None
    tree: dict | None = None


class IngestResponse(BaseModel):
    file_id: str
    chunks_stored: int


@router.post("/api/ingest", response_model=IngestResponse)
def ingest_document(req: IngestRequest):
    clean_content = _strip_nul(req.content)
    if not clean_content.strip():
        raise HTTPException(400, "content cannot be empty")

    chunks = [_strip_nul(chunk) for chunk in chunk_text(clean_content)]
    chunks = [chunk for chunk in chunks if chunk.strip()]
    if not chunks:
        raise HTTPException(400, "no chunks produced from content")

    meta = req.metadata or {}
    chunks_stored, vectors = upsert_document(req.file_id, chunks, meta)

    centroid = compute_centroid_from_vectors(vectors)
    if centroid:
        upsert_single_graph_node(
            file_id=req.file_id,
            centroid=centroid,
            display_title=req.display_title or "",
            tree=req.tree,
        )

    manifest_entry = {"file_id": req.file_id}
    if req.display_title:
        manifest_entry["display_title"] = req.display_title
    if req.relative_path:
        manifest_entry["relative_path"] = req.relative_path
    if req.tree:
        manifest_entry["tree"] = req.tree
    if len(manifest_entry) > 1:
        merge_manifest_entries(MANIFEST_PATH, [manifest_entry])

    return IngestResponse(file_id=req.file_id, chunks_stored=chunks_stored)


@router.delete("/api/documents/{file_id}")
def delete_document(file_id: str):
    deleted = delete_document_chunks(file_id)
    delete_document_graph(file_id)

    if deleted == 0:
        raise HTTPException(404, f"No chunks found for file_id: {file_id}")
    return {"file_id": file_id, "chunks_deleted": deleted}
