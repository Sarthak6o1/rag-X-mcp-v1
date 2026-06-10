from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

import chromadb

from config import COLLECTION_NAME, VECTOR_DB_DIR
from services.embeddings import embed_query, embed_texts

_client: chromadb.PersistentClient | None = None
_collection = None
_documents_cache_count: int | None = None
_documents_cache: list[dict[str, Any]] | None = None
_file_ids_cache: dict[tuple[int, str], list[str]] = {}


def _normalize_metadata(file_id: str, chunk_index: int, metadata: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "file_id": file_id,
        "chunk_index": int(chunk_index),
        "metadata_json": json.dumps(metadata or {}, ensure_ascii=False),
    }


def _parse_metadata(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    raw = meta.get("metadata_json", "{}")
    try:
        payload = json.loads(raw) if isinstance(raw, str) else {}
    except ValueError:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        VECTOR_DB_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(VECTOR_DB_DIR))
    return _client


def get_collection():
    global _collection
    if _collection is None:
        _collection = get_client().get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _clear_read_caches() -> None:
    global _documents_cache_count, _documents_cache
    _documents_cache_count = None
    _documents_cache = None
    _file_ids_cache.clear()


def ensure_store() -> None:
    get_collection()


def ping() -> bool:
    try:
        ensure_store()
        return True
    except Exception:
        return False


def upsert_document(file_id: str, chunks: list[str], metadata: dict[str, Any] | None = None) -> tuple[int, list[list[float]]]:
    collection = get_collection()
    delete_document(file_id)
    if not chunks:
        _clear_read_caches()
        return 0, []

    embeddings = embed_texts(chunks)
    ids = [f"{file_id}::{idx}" for idx in range(len(chunks))]
    metadatas = [_normalize_metadata(file_id, idx, metadata) for idx in range(len(chunks))]
    collection.upsert(
        ids=ids,
        documents=chunks,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    _clear_read_caches()
    return len(chunks), embeddings


def delete_document(file_id: str) -> int:
    collection = get_collection()
    existing = collection.get(where={"file_id": file_id}, include=["metadatas"])
    ids = existing.get("ids", [])
    if ids:
        collection.delete(ids=ids)
        _clear_read_caches()
    return len(ids)


def _all_records(include: list[str]) -> dict[str, Any]:
    collection = get_collection()
    count = collection.count()
    if count <= 0:
        return {}
    return collection.get(limit=count, include=include)


def list_documents() -> list[dict[str, Any]]:
    global _documents_cache_count, _documents_cache
    collection = get_collection()
    count = collection.count()
    if _documents_cache is not None and _documents_cache_count == count:
        return [dict(row) for row in _documents_cache]

    counts = Counter()
    if count > 0:
        for meta in collection.get(limit=count, include=["metadatas"]).get("metadatas", []):
            if isinstance(meta, dict):
                fid = meta.get("file_id")
                if isinstance(fid, str):
                    counts[fid] += 1

    rows = [
        {"file_id": file_id, "chunk_count": chunk_count}
        for file_id, chunk_count in sorted(counts.items())
    ]
    _documents_cache_count = count
    _documents_cache = [dict(row) for row in rows]
    return rows


def list_file_ids(prefix: str = "") -> list[str]:
    count = get_collection().count()
    cache_key = (count, prefix)
    cached = _file_ids_cache.get(cache_key)
    if cached is not None:
        return list(cached)

    file_ids = [row["file_id"] for row in list_documents()]
    if prefix:
        file_ids = [fid for fid in file_ids if fid.startswith(prefix)]
    _file_ids_cache[cache_key] = list(file_ids)
    return file_ids


def get_document_chunks(file_id: str) -> list[dict[str, Any]]:
    collection = get_collection()
    result = collection.get(where={"file_id": file_id}, include=["documents", "metadatas"])
    rows: list[dict[str, Any]] = []
    for doc, meta in zip(result.get("documents", []), result.get("metadatas", [])):
        if not isinstance(doc, str):
            continue
        meta_dict = meta if isinstance(meta, dict) else {}
        rows.append(
            {
                "chunk_index": int(meta_dict.get("chunk_index", 0)),
                "content": doc,
                "metadata": _parse_metadata(meta_dict),
            }
        )
    rows.sort(key=lambda row: row["chunk_index"])
    return rows


def query_documents(query: str, k: int, file_ids: list[str] | None = None) -> list[dict[str, Any]]:
    collection = get_collection()
    query_embedding = embed_query(query)
    where: dict[str, Any] | None = None
    if file_ids:
        where = {"file_id": {"$in": file_ids}}

    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    rows: list[dict[str, Any]] = []
    for doc, meta, distance in zip(documents, metadatas, distances):
        meta_dict = meta if isinstance(meta, dict) else {}
        rows.append(
            {
                "file_id": str(meta_dict.get("file_id", "unknown")),
                "chunk_index": int(meta_dict.get("chunk_index", 0)),
                "content": doc if isinstance(doc, str) else "",
                "metadata": _parse_metadata(meta_dict),
                "score": float(1.0 - float(distance)),
            }
        )
    return rows


def get_all_embeddings_by_file() -> dict[str, list[list[float]]]:
    records = _all_records(["embeddings", "metadatas"])
    groups: dict[str, list[list[float]]] = defaultdict(list)
    for embedding, meta in zip(records.get("embeddings", []), records.get("metadatas", [])):
        if not isinstance(meta, dict):
            continue
        file_id = meta.get("file_id")
        if not isinstance(file_id, str):
            continue
        if embedding is None:
            continue
        try:
            values = [float(x) for x in embedding]
        except TypeError:
            continue
        if values:
            groups[file_id].append(values)
    return dict(groups)
