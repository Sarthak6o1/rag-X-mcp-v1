# RAG MCP Backend (FastAPI)

FastAPI service that powers retrieval-augmented generation (RAG) for MCP-style knowledge bases.

Documents are **chunked**, **embedded** with Sentence Transformers, and stored in a local **ChromaDB** vector index. A companion **knowledge graph** links related documents by folder structure, semantic similarity, and shared section outlines. The **hybrid query** endpoint combines fuzzy document filtering, graph expansion, PageIndex tree hints, and vector search into a single retrieval call.

> **Location in repo:** `backend/` — run all commands below from that directory.

This service is the **source of truth** for the knowledge base. The [MCP server](../mcp-server/README.md) calls it for search; the [admin portal](../admin-portal/README.md) calls it for ingest. Together they form the [RAG X MCP platform](../README.md).

---

## Table of contents

1. [Features](#features)
2. [Architecture](#architecture)
3. [How hybrid search works](#hybrid-query-pipeline-post-apiqueryhybrid)
4. [Knowledge graph edges](#knowledge-graph-edge-types)
5. [Project structure](#project-structure)
6. [Prerequisites](#prerequisites)
7. [Environment variables](#environment-variables)
8. [Run locally](#run-locally)
9. [Docker](#docker)
10. [API reference](#api-reference)
11. [Typical workflow](#typical-workflow)
12. [Authentication](#authentication)
13. [Data persistence](#data-persistence)
14. [Tuning retrieval](#tuning-retrieval)
15. [Troubleshooting](#troubleshooting)
16. [Production (Cloud Run)](#production-cloud-run)
17. [Related docs](#related-docs)

---

## Features

- **Document ingest** — Accepts raw text, chunks it, embeds with Sentence Transformers, and upserts into ChromaDB.
- **Hybrid retrieval** — Multi-stage pipeline: fuzzy prompt filter → graph expansion → tree-augmented query → cosine similarity vector search.
- **Knowledge graph** — Auto-builds typed edges from folder paths, embedding centroids, and section-tree overlap; exports to `data/form_graph.json`.
- **PageIndex outlines** — Stores and queries hierarchical section trees per document via the manifest.
- **Optional JWT auth** — When `JWT_SECRET` is set, all `/api/*` routes require a valid HS256 Bearer token.
- **Persistent local storage** — ChromaDB on disk, JSON manifest, and graph files. No external database required for development.
- **Configurable thresholds** — Tune semantic / tree similarity, graph hop count, stage-0 fanout, and chunking via env vars.
- **OpenAPI / Swagger UI** — Interactive docs at `/docs` and `/redoc`.

---

## Architecture

```
Client (MCP / admin portal / curl)
        │
        ▼
   FastAPI (main.py)
        │
        ├── routes/ingest.py        → chunk + embed + manifest + graph node
        ├── routes/query.py         → vector + hybrid search, catalog, graph views
        └── routes/graph_rebuild.py → rebuild folder / semantic / tree edges
        │
        ├── vector_store.py         → ChromaDB persistence layer
        ├── graph_store.py          → graph nodes & edges on disk
        ├── services/embeddings.py  → Sentence Transformers wrapper
        ├── services/chunker.py     → text splitting (CHUNK_SIZE / OVERLAP)
        └── rag_pipeline/           → fuzzy filter, graph builder, tree hints, manifest I/O
```

### Hybrid query pipeline (`POST /api/query/hybrid`)

The pipeline narrows the candidate set before vector search to improve both quality and latency.

1. **Stage-0 filter** — Ranks candidate `file_id`s using `rapidfuzz` against a search catalog made from slugs + manifest titles. Returns up to `RAG_MAX_FILTER_DOCS` results.
2. **Graph expansion** — Walks the knowledge graph from the ranked set for `GRAPH_EXPANSION_HOPS` hops across typed edges, widening the candidate set with related documents.
3. **Tree hints** — When `USE_TREE_QUERY_HINTS=true`, appends top section titles from the candidate documents' PageIndex outlines to the embedding query.
4. **Vector search** — Queries ChromaDB with cosine similarity, scoped to the narrowed `file_id` set. Returns top `k` chunks with metadata.

If stage-0 returns nothing strong, the pipeline transparently **falls back to searching all documents** and records a `filter_note` in the response.

### Retrieval design notes

- The stage-0 filter is intentionally cheap and metadata-driven, so broad searches avoid scanning every document unless needed.
- Graph expansion runs before vector search to pull nearby documents into scope when filenames or section titles are related.
- Tree hints preserve document structure by adding relevant section titles to the embedding query.
- The response includes filter and graph notes so retrieval behavior can be inspected during tuning.

### Knowledge graph edge types

Built by `POST /api/graph/rebuild` (see `rag_pipeline/graph_builder.py`):

| Edge type | How it is created |
|-----------|-------------------|
| `same_folder` | Chains documents that share a parent directory derived from `relative_path` |
| `semantic_similar` | Cosine similarity between document embedding centroids ≥ `SEMANTIC_EDGE_THRESHOLD`, capped at `MAX_SEMANTIC_EDGES_PER_DOC` neighbors per document |
| `shared_sections` | Jaccard overlap of normalised PageIndex section titles ≥ `TREE_OVERLAP_THRESHOLD` |

Graph expansion during search walks all three edge types up to `GRAPH_EXPANSION_HOPS` from the stage-0 result.

---

## Project structure

```
.
├── main.py                 # FastAPI app, CORS, startup, /health
├── config.py               # Environment-driven settings
├── auth.py                 # Optional JWT verification
├── vector_store.py         # ChromaDB read/write
├── graph_store.py          # Graph node/edge persistence
├── db.py                   # Shared DB helpers
├── Dockerfile              # CPU-only PyTorch + app image
├── requirements.txt
├── data/
│   ├── README.md           # How to rebuild KB (runtime JSON is gitignored)
│   ├── kb_manifest.json    # Runtime — not in Git (ingest)
│   └── form_graph.json     # Runtime — not in Git (graph/rebuild)
├── routes/
│   ├── ingest.py           # Ingest & delete documents
│   ├── query.py            # Search, catalog, graph API
│   └── graph_rebuild.py    # Full graph rebuild
├── services/
│   ├── chunker.py
│   └── embeddings.py
└── rag_pipeline/
    ├── prompt_filter.py    # Stage-0 file_id ranking
    ├── form_graph.py       # Graph load & expansion
    ├── graph_builder.py    # Edge construction
    ├── tree_text.py        # Outline-augmented queries
    ├── page_index.py       # Section tree extraction (mirrored in admin-portal)
    ├── manifest_io.py
    └── slug_file_id.py     # Semantic file_id builder (mirrored in admin-portal)
```

`vector_db/` is created at runtime and gitignored.

---

## Prerequisites

- **Python 3.11+**
- **~2 GB free disk** for the embeddings model cache (the default `all-MiniLM-L6-v2` is ~400 MB; first run downloads it)
- **CPU is fine** — the Dockerfile installs CPU-only PyTorch on purpose to keep the image small

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDINGS_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Hugging Face model name |
| `COLLECTION_NAME` | `rag_mcp_docs` | ChromaDB collection name |
| `VECTOR_DB_DIR` | `./vector_db` | ChromaDB persistence directory |
| `MANIFEST_PATH` | `./data/kb_manifest.json` | Document manifest path |
| `GRAPH_PATH` | `./data/form_graph.json` | Knowledge graph export path |
| `CHUNK_SIZE` | `1200` | Characters per chunk |
| `CHUNK_OVERLAP` | `150` | Overlap between consecutive chunks |
| `FILE_PREFIX` | `kb-` | Required prefix for indexed `file_id`s |
| `BACKEND_PORT` | `8000` | Uvicorn port when running `python main.py` directly |
| `JWT_SECRET` | *(empty)* | HS256 secret; when set, all `/api/*` routes require Bearer auth |
| `RAG_MAX_FILTER_DOCS` | `12` | Max docs returned from stage-0 filter |
| `GRAPH_EXPANSION_HOPS` | `1` | Graph hops after stage-0 filtering |
| `USE_TREE_QUERY_HINTS` | `true` | Append PageIndex section titles to the vector query |
| `SEMANTIC_EDGE_THRESHOLD` | `0.55` | Min cosine similarity for `semantic_similar` edges (0–1) |
| `TREE_OVERLAP_THRESHOLD` | `0.25` | Min Jaccard overlap for `shared_sections` edges (0–1) |
| `MAX_SEMANTIC_EDGES_PER_DOC` | `5` | Cap on `semantic_similar` neighbors per document |

Create a `.env` file in this directory for local development. **Never commit `.env`.**

```env
JWT_SECRET=your-secret-here
VECTOR_DB_DIR=./vector_db
GRAPH_EXPANSION_HOPS=1
```

---

## Run locally

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Verify:

```bash
curl http://localhost:8000/health
```

Interactive API docs: `http://localhost:8000/docs`
Alternative docs (ReDoc): `http://localhost:8000/redoc`

---

## Docker

The Dockerfile installs **CPU-only PyTorch** first to avoid pulling multi-GB CUDA wheels.

```bash
cd backend
docker build -t rag-mcp-backend .

docker run -p 8000:8000 \
  -v "$(pwd)/vector_db:/app/vector_db" \
  -v "$(pwd)/data:/app/data" \
  -e JWT_SECRET=your-secret \
  rag-mcp-backend
```

Mount `vector_db/` and `data/` so embeddings and the knowledge graph survive container restarts. Without these mounts the KB is rebuilt from scratch on each `docker run`.

---

## API reference

All endpoints are JSON unless noted. When `JWT_SECRET` is set, include `Authorization: Bearer <jwt>` on every request.

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service status, vector store ping, configured paths |

**Example response:**

```json
{
  "status": "healthy",
  "vector_store": "ready",
  "embeddings_model": "sentence-transformers/all-MiniLM-L6-v2",
  "collection": "rag_mcp_docs",
  "vector_db_dir": "/app/vector_db",
  "manifest": "/app/data/kb_manifest.json",
  "graph": "/app/data/form_graph.json"
}
```

### Ingest

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/ingest` | Ingest document text (chunks, embeds, updates manifest + graph node) |
| `DELETE` | `/api/documents/{file_id}` | Remove all chunks and graph data for a document |

**Ingest request:**

```bash
curl -X POST http://localhost:8000/api/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "file_id": "kb-my-doc",
    "content": "Full document text...",
    "display_title": "My Document",
    "relative_path": "guides/my-doc.md",
    "metadata": { "source": "upload" },
    "tree": {
      "root_id": "n0",
      "nodes": [
        { "id": "n0", "title": "My Document",  "parent_id": null },
        { "id": "n1", "title": "Introduction", "parent_id": "n0" }
      ]
    }
  }'
```

**Ingest response:**

```json
{ "file_id": "kb-my-doc", "chunks_stored": 7 }
```

**Delete:**

```bash
curl -X DELETE http://localhost:8000/api/documents/kb-my-doc
```

```json
{ "file_id": "kb-my-doc", "chunks_deleted": 7 }
```

### Query

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/query` | Plain vector search (optional `file_ids` filter) |
| `POST` | `/api/query/hybrid` | Full hybrid pipeline (auto-filter + tree hints) |
| `POST` | `/api/resolve-file-ids` | Stage-0 rank + graph expansion only, no vector search |

**Hybrid query request:**

```bash
curl -X POST http://localhost:8000/api/query/hybrid \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How do I configure authentication?",
    "k": 8,
    "auto_filter": true,
    "max_documents": 12,
    "use_tree_hints": true
  }'
```

**Hybrid query response:**

```json
{
  "query": "How do I configure authentication?",
  "results": [
    {
      "file_id": "kb-auth-setup",
      "chunk_index": 2,
      "content": "Set JWT_SECRET to enable token verification...",
      "score": 0.87,
      "metadata": { "source": "upload" }
    }
  ],
  "filter_note": "Stage-0 filter: 3 ranked + graph expansion -> 5 file_id(s) (hops=1, edge_types=['same_folder', 'semantic_similar', 'shared_sections']).",
  "tree_note": "PageIndex outline hints appended to the vector query."
}
```

**Resolve file IDs (debug):**

```bash
curl -X POST http://localhost:8000/api/resolve-file-ids \
  -H "Content-Type: application/json" \
  -d '{ "query": "authentication", "max_documents": 6 }'
```

### Documents & catalog

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/documents` | List indexed documents with chunk counts |
| `GET` | `/api/documents/{file_id}/chunks` | All stored chunks for one document |
| `GET` | `/api/documents/{file_id}/outline` | PageIndex section tree from manifest |
| `GET` | `/api/file-ids` | All known `file_id`s (no prefix filter) |

### Knowledge graph

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/graph` | Full graph: nodes, edges, edge-type counts |
| `GET` | `/api/graph/tree` | Folder hierarchy derived from manifest `relative_path` values |
| `POST` | `/api/graph/rebuild` | Recompute folder, semantic, and tree-overlap edges |

**Graph rebuild request (all fields optional):**

```bash
curl -X POST http://localhost:8000/api/graph/rebuild \
  -H "Content-Type: application/json" \
  -d '{
    "merge_manual": true,
    "semantic_threshold": 0.55,
    "tree_threshold": 0.25
  }'
```

**Graph rebuild response:**

```json
{
  "ok": true,
  "node_count": 42,
  "edge_count": 87,
  "edge_types": { "same_folder": 24, "semantic_similar": 41, "shared_sections": 22 },
  "semantic_threshold": 0.55,
  "tree_threshold": 0.25,
  "graph_path": "/app/data/form_graph.json"
}
```

---

## Typical workflow

1. **Ingest** documents via `POST /api/ingest` or the [admin portal](../admin-portal/README.md) upload UI. Include `display_title`, `relative_path`, and `tree` when available — they make the stage-0 filter and tree-overlap edges far more useful.
2. **Rebuild graph** with `POST /api/graph/rebuild` after a bulk ingest (or once a day in production).
3. **Query** with `POST /api/query/hybrid` for production retrieval, or via [MCP tools](../mcp-server/README.md) from LibreChat or another MCP host.
4. **Inspect** the KB with `GET /api/graph/tree`, `GET /api/documents`, or `POST /api/resolve-file-ids`.

---

## Authentication

When `JWT_SECRET` is empty, the backend runs **open** (development only). When set, every `/api/*` route requires:

```
Authorization: Bearer <jwt>
```

Tokens must be **HS256-signed** with the configured secret. The MCP server generates these automatically when both services share `JWT_SECRET`. The admin portal does **not** send JWT — restrict backend network access in production so only trusted callers can reach it.

A failing token returns `401 Unauthorized` with a descriptive `detail`.

---

## Data persistence

| Path | Purpose |
|------|---------|
| `vector_db/` | ChromaDB files (gitignored, created on first run) |
| `data/kb_manifest.json` | Per-`file_id` titles, paths, section trees |
| `data/form_graph.json` | Serialized graph (nodes + typed edges) |

See [data/README.md](data/README.md) for ingest and graph rebuild steps. In production, mount these paths to persistent storage so they survive container restarts.

---

## Tuning retrieval

| Goal | Try |
|------|-----|
| Broader recall | Increase `GRAPH_EXPANSION_HOPS` from `1` to `2`, or raise `RAG_MAX_FILTER_DOCS` |
| Tighter precision | Lower `RAG_MAX_FILTER_DOCS` or raise `SEMANTIC_EDGE_THRESHOLD` |
| Fewer noisy graph edges | Raise `SEMANTIC_EDGE_THRESHOLD` (e.g. `0.65`) and/or `TREE_OVERLAP_THRESHOLD` (e.g. `0.40`) |
| Shorter chunks | Lower `CHUNK_SIZE` (e.g. `800`) — better for FAQ-style content |
| Longer chunks | Raise `CHUNK_SIZE` (e.g. `1800`) — better for narrative docs |
| Disable tree hints | `USE_TREE_QUERY_HINTS=false` (faster but loses outline context) |
| Different embedding model | Set `EMBEDDINGS_MODEL`; **re-ingest everything** since vectors are model-specific |

Always test changes against a held-out set of queries before deploying.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Empty hybrid search results | KB empty or graph not rebuilt | Check `GET /api/documents`; run `POST /api/graph/rebuild` |
| Slow first query | Embedding model downloads on startup | Wait for download to finish; subsequent queries are fast |
| `filter_note` falls back to all docs | Stage-0 filter found no strong match | Use more specific query terms, or set `auto_filter=false` |
| Ingest succeeds but doc missing from filter | `file_id` does not start with `FILE_PREFIX` | Re-ingest with a proper prefix, or change `FILE_PREFIX` |
| `401 Unauthorized` | JWT missing or mismatched | Ensure `JWT_SECRET` is shared with the caller (MCP server) |
| `400 chunks produced from content` | Content empty after stripping | Provide non-empty text; check encoding |
| Container restart loses data | `vector_db/` and `data/` not mounted | Add Docker volumes or persistent storage |

---

## Production (Cloud Run)

When deploying to Cloud Run:

- **Storage:** Cloud Run instances are ephemeral. Persist `VECTOR_DB_DIR`, `MANIFEST_PATH`, and `GRAPH_PATH` using one of:
  - Cloud Storage FUSE mount
  - Filestore (NFS) mount
  - Rebuild on deploy if the KB is small enough
- **Secrets:** Store `JWT_SECRET` in Secret Manager and reference it from the Cloud Run service.
- **Networking:** Restrict access so only the MCP server and admin portal can reach the backend. Options: internal Cloud Run ingress, VPC connector, IAM-based invocation.
- **Image build:** Cloud Build or `gcloud run deploy --source .` from this directory.
- **Cold start:** Embedding model loads on first request. Use Cloud Run min-instances to avoid cold-start latency.

---

## Related docs

- [Root README](../README.md) — platform overview (backend + MCP + admin)
- [MCP server](../mcp-server/README.md) — AI client tools
- [Admin portal](../admin-portal/README.md) — document upload UI

## License

Apache-2.0. See [../LICENSE](../LICENSE).
