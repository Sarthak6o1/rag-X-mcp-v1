# RAG MCP Services

Source code for a **Retrieval-Augmented Generation (RAG)** platform with first-class [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) support.

Three independent services work together to **ingest documents**, **index them for search**, and **expose them to AI assistants** as MCP tools. Each service is deployable on its own (typically as a separate Google Cloud Run service) and communicates over plain HTTP.

| Service | Path | Default port | Role |
|---------|------|--------------|------|
| **RAG backend** | `backend-full/app/` | `8000` | Index, search, and graph-link documents (FastAPI + ChromaDB) |
| **MCP server** | `rag-full/app/` | `4010` | Expose the knowledge base as MCP tools for AI assistants |
| **Admin portal** | `admin-portal/` | `8088` | Admin upload UI → calls backend ingest API |

> **Production:** These services run on **Google Cloud Run** in GCP. This repository is the **source of truth for application code only** — live URLs, secrets, and runtime configuration are managed in Cloud Run + Secret Manager, not in this README.

---

## Table of contents

1. [What each service does](#what-each-service-does)
2. [Repository layout](#repository-layout)
3. [System architecture](#system-architecture)
4. [Glossary](#glossary)
5. [Prerequisites](#prerequisites)
6. [Quick start (local, all three services)](#quick-start-local-all-three-services)
7. [End-to-end tutorial](#end-to-end-tutorial)
8. [Service communication](#service-communication)
9. [Docker Compose (local full stack)](#docker-compose-local-full-stack)
10. [Authentication and security](#authentication-and-security)
11. [Shared code sync](#shared-code-sync-important)
12. [Component documentation](#component-documentation)
13. [Backend API summary](#backend-api-summary)
14. [MCP tools summary](#mcp-tools-summary)
15. [Admin portal summary](#admin-portal-summary)
16. [Typical workflow](#typical-workflow)
17. [Production (Cloud Run)](#production-cloud-run)
18. [Troubleshooting](#troubleshooting)
19. [Tech stack](#tech-stack)
20. [Repository conventions](#repository-conventions)
21. [FAQ](#faq)
22. [License](#license)

---

## What each service does

### Backend (`backend-full/app`)

The **core API and data store**. It is the single source of truth for the knowledge base.

Responsibilities:

- **Chunking and embedding** — Splits document text into overlapping windows and embeds them with Sentence Transformers (default `all-MiniLM-L6-v2`).
- **Vector storage** — Persists embeddings and metadata in a local ChromaDB collection on disk.
- **Manifest** — Stores per-document metadata (display title, relative path, section tree) in `data/kb_manifest.json`.
- **Knowledge graph** — Builds and serves typed edges between documents:
  - `same_folder` (shared directory)
  - `semantic_similar` (centroid cosine similarity)
  - `shared_sections` (section-title Jaccard overlap)
- **Hybrid search** — A multi-stage retrieval pipeline: fuzzy document filter → graph expansion → tree-augmented query → vector similarity.
- **Optional JWT auth** — Validates `Authorization: Bearer …` when `JWT_SECRET` is set.

**Consumers:** MCP server (read), admin portal (write), any other HTTP caller.

### MCP server (`rag-full/app`)

A thin **FastMCP** wrapper around the backend. It does no embedding or storage of its own — every tool call is forwarded to the backend.

Why it exists:

- AI clients like **LibreChat**, **Cursor**, **Claude Desktop**, etc. natively speak MCP.
- They can discover and call tools (`search_knowledge_base`, `list_documents`, …) without a custom integration.
- It centralizes JWT generation so individual clients never see the secret.

**Does not ingest documents.** Uploads go through the admin portal or `POST /api/ingest` on the backend.

### Admin portal (`admin-portal`)

Web UI for admins to **upload files** into the knowledge base.

What it does on each upload:

1. Authenticates the admin via Google OAuth (or skips OAuth in local dev).
2. Saves the file to a temp directory preserving its relative path.
3. Builds a **semantic `file_id`** slug (e.g. `kb-guides-onboarding-getting-started`).
4. Builds a **PageIndex section tree** from the document headings.
5. **Extracts text** (PDF, Office, plain text, CSV, JSON, media via Whisper).
6. Calls **`POST /api/ingest`** on the backend with the extracted text, tree, and metadata.

**Does not affect MCP clients.** LibreChat etc. keep using the MCP server URL they already have configured.

---

## Repository layout

```
rag-mcp-services/
├── README.md                     ← you are here (platform overview)
├── .gitignore                    ← repo-wide ignores (.env, .venv, vector_db, etc.)
├── backend-full/
│   └── app/                      ← FastAPI RAG API
│       ├── README.md             ← backend-specific docs (API reference)
│       ├── main.py               ← app factory, CORS, startup, /health
│       ├── config.py             ← env-driven settings
│       ├── auth.py               ← JWT verification
│       ├── vector_store.py       ← ChromaDB read/write
│       ├── graph_store.py        ← graph nodes/edges on disk
│       ├── db.py                 ← shared DB helpers
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── routes/
│       │   ├── ingest.py         ← POST /api/ingest, DELETE
│       │   ├── query.py          ← /api/query, /api/query/hybrid, catalog, graph
│       │   └── graph_rebuild.py  ← POST /api/graph/rebuild
│       ├── rag_pipeline/
│       │   ├── prompt_filter.py  ← stage-0 fuzzy file_id ranking
│       │   ├── form_graph.py     ← graph load + expansion
│       │   ├── graph_builder.py  ← folder / semantic / tree edge construction
│       │   ├── tree_text.py      ← outline-augmented query helper
│       │   ├── page_index.py     ← section tree extraction
│       │   ├── manifest_io.py
│       │   └── slug_file_id.py   ← semantic file_id builder
│       ├── services/
│       │   ├── chunker.py        ← text splitting
│       │   └── embeddings.py     ← Sentence Transformers wrapper
│       └── data/
│           └── README.md         ← KB ingestion steps (manifest, graph, vector_db)
├── rag-full/
│   └── app/                      ← FastMCP server
│       ├── README.md             ← MCP server docs (tool reference)
│       ├── Dockerfile
│       ├── requirements.txt
│       └── src/
│           └── main.py           ← FastMCP tools + health routes
└── admin-portal/                 ← Admin ingest UI
    ├── README.md                 ← admin portal docs (OAuth + Cloud Run)
    ├── app/
    │   ├── main.py               ← FastAPI routes: OAuth, upload, health
    │   ├── config.py
    │   ├── oauth_setup.py
    │   ├── extract_text.py
    │   ├── transcribe_media.py
    │   ├── page_index.py         ← ⚠ mirror of backend rag_pipeline/page_index.py
    │   ├── slug_file_id.py       ← ⚠ mirror of backend rag_pipeline/slug_file_id.py
    │   └── templates/
    │       └── index.html
    ├── requirements.txt
    ├── Dockerfile
    ├── .env.example
    ├── deploy-cloud-run.ps1      ← Windows-friendly Cloud Run deploy
    └── cloudbuild.yaml
```

Each service lives in its **own top-level folder**. There is no nesting of `rag-full` or `admin-portal` inside `backend-full`.

---

## System architecture

```
                    ┌─────────────────────┐
                    │  Admin users        │
                    │  (Google OAuth)     │
                    └──────────┬──────────┘
                               │ upload files
                               ▼
                    ┌─────────────────────┐
                    │  admin-portal       │  :8088
                    │  extract + ingest   │
                    └──────────┬──────────┘
                               │ POST /api/ingest
                               ▼
┌─────────────────────┐   REST    ┌─────────────────────┐
│  MCP client         │◄─────────►│  backend-full/app   │  :8000
│  LibreChat, Cursor  │  + JWT?   │  ChromaDB + graph   │
└──────────┬──────────┘           └──────────▲──────────┘
           │ MCP HTTP (/mcp)                 │
           ▼                                 │
┌─────────────────────┐                      │
│  rag-full/app       │──────────────────────┘
│  FastMCP · :4010    │  search / list / graph tools
└─────────────────────┘
```

### End-to-end data flow

1. **Ingest** — Admin uploads a file (or an API caller sends text directly). The receiving service chunks the text, embeds chunks, and upserts them into ChromaDB. The manifest entry is created or updated, and a graph node is added/updated for that document.
2. **Graph rebuild** — `POST /api/graph/rebuild` is called (manually or after bulk ingest). The backend recomputes:
   - `same_folder` edges from `relative_path`
   - `semantic_similar` edges from centroid cosine similarity (`SEMANTIC_EDGE_THRESHOLD`)
   - `shared_sections` edges from PageIndex section-title overlap (`TREE_OVERLAP_THRESHOLD`)
3. **Search** — A user asks LibreChat a question. LibreChat calls `search_knowledge_base` on the MCP server, which forwards to `POST /api/query/hybrid`:
   1. **Stage-0 prompt filter** ranks `file_id`s by fuzzy match against slugs + manifest titles.
   2. **Graph expansion** walks `GRAPH_EXPANSION_HOPS` hops over the typed edges to widen the candidate set.
   3. **Tree hints** appends top section titles from candidate documents to the embedding query.
   4. **Vector search** runs cosine similarity against the narrowed `file_id` set in ChromaDB.
4. **MCP response** — The MCP server formats results as readable text and returns them to the client.

---

## Glossary

| Term | Meaning |
|------|---------|
| `file_id` | Unique slug per document, e.g. `kb-guides-onboarding-intro`. Used as the primary key everywhere. |
| `FILE_PREFIX` | Required prefix for `file_id` (default `kb-`). Lets the backend filter system docs from user docs. |
| Manifest | `data/kb_manifest.json` — keyed by `file_id`, stores `display_title`, `relative_path`, and the section `tree`. |
| Tree / PageIndex | Hierarchical section outline extracted from a document (`tree.nodes`). |
| Centroid | Mean embedding vector of all chunks belonging to a document; used for graph similarity. |
| Stage-0 filter | Fast rapidfuzz pass over `file_id` slugs + manifest titles to pick a candidate document set before vector search. |
| Graph expansion | Walks N hops over typed edges to include related documents in the candidate set. |
| Hybrid search | Combination of stage-0 + graph expansion + tree hints + vector search. |
| MCP tool | An endpoint exposed by `rag-full/app` to MCP clients. Backed by backend REST. |
| JWT (HS256) | Optional shared-secret token between MCP server and backend. |

---

## Prerequisites

| Requirement | Why |
|-------------|-----|
| **Python 3.11+** | Used by all three services |
| **~2 GB free disk** | First backend run downloads `all-MiniLM-L6-v2` (~400 MB) and creates ChromaDB |
| **Docker (optional)** | For containerized local runs and Cloud Run image builds |
| **Google Cloud account (prod admin)** | OAuth client + Cloud Run deploy |
| **ffmpeg on PATH (optional)** | Media transcription in admin portal |

---

## Quick start (local, all three services)

Run from the **repository root**. Open **three terminals**, one per service.

### 1. RAG backend (terminal 1)

```bash
cd backend-full/app
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Verify:

```bash
curl http://localhost:8000/health
```

You should see something like:

```json
{
  "status": "healthy",
  "vector_store": "ready",
  "embeddings_model": "sentence-transformers/all-MiniLM-L6-v2",
  "collection": "rag_mcp_docs",
  "vector_db_dir": "/path/to/vector_db",
  "manifest": "/path/to/data/kb_manifest.json",
  "graph": "/path/to/data/form_graph.json"
}
```

Interactive API docs: `http://localhost:8000/docs`

### 2. MCP server (terminal 2)

```bash
cd rag-full/app
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Point at the backend started above
export BACKEND_API_URL=http://localhost:8000   # macOS/Linux
# Windows PowerShell:
# $env:BACKEND_API_URL = "http://localhost:8000"

python src/main.py
```

Verify:

```bash
curl http://localhost:4010/health
```

Then connect your MCP client to `http://localhost:4010/mcp` (HTTP transport).

### 3. Admin portal (terminal 3)

```bash
cd admin-portal
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
# Edit .env (see admin-portal/README.md for full env reference)
uvicorn app.main:app --reload --host 127.0.0.1 --port 8088
```

Open `http://127.0.0.1:8088`

Minimum `.env` for local dev **without Google OAuth** (NEVER use in production):

```env
RAG_BACKEND_URL=http://127.0.0.1:8000
PUBLIC_BASE_URL=http://127.0.0.1:8088
SESSION_SECRET=local-dev-secret-change-me
DEV_SKIP_OAUTH=true
DEV_MOCK_EMAIL=user@example.com
ALLOWED_ADMIN_EMAILS=user@example.com
```

---

## End-to-end tutorial

A 5-step path to verify the whole stack works locally.

### Step 1 — Start all three services

Follow the [Quick start](#quick-start-local-all-three-services) above.

### Step 2 — Ingest a test document via API (no UI required)

```bash
curl -X POST http://localhost:8000/api/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "file_id": "kb-test-onboarding",
    "content": "Welcome to the platform. This guide explains how to log in, configure your account, and run your first query.",
    "display_title": "Test Onboarding",
    "relative_path": "guides/test-onboarding.md",
    "metadata": { "source": "tutorial" },
    "tree": {
      "root_id": "n0",
      "nodes": [
        { "id": "n0", "title": "Test Onboarding", "parent_id": null },
        { "id": "n1", "title": "Logging in",     "parent_id": "n0" },
        { "id": "n2", "title": "First query",    "parent_id": "n0" }
      ]
    }
  }'
```

Expected response:

```json
{ "file_id": "kb-test-onboarding", "chunks_stored": 1 }
```

### Step 3 — Rebuild the knowledge graph

```bash
curl -X POST http://localhost:8000/api/graph/rebuild \
  -H "Content-Type: application/json" -d '{}'
```

### Step 4 — Query the backend directly

```bash
curl -X POST http://localhost:8000/api/query/hybrid \
  -H "Content-Type: application/json" \
  -d '{ "query": "How do I log in?", "k": 4 }'
```

You should see `kb-test-onboarding` in the results.

### Step 5 — Query through MCP

Open LibreChat or another MCP client and call:

```
search_knowledge_base(query="How do I log in?")
```

You should get the same result, formatted as readable text.

If all five steps succeed, the stack is wired up correctly.

---

## LibreChat integration

LibreChat can call the RAG stack through the MCP service. Keep the service address outside the repository and pass it as an environment variable at runtime.

Add an MCP server entry to `librechat.yaml`:

```yaml
mcpServers:
  knowledge-base:
    type: streamable-http
    url: ${RAG_MCP_URL}
    timeout: 300000
```

Operational flow:

1. Run the backend and MCP service.
2. Set `BACKEND_API_URL` for the MCP service so it can reach the backend.
3. Set `RAG_MCP_URL` for LibreChat so it can reach the MCP service.
4. Restart LibreChat and enable the `knowledge-base` MCP tools.

Do not commit live service addresses, OAuth credentials, JWT secrets, or deployment-specific hostnames.

---

## Service communication

| From | To | Protocol | Purpose |
|------|----|----------|---------|
| Admin portal | Backend | HTTP `POST /api/ingest` | Upload documents |
| MCP server | Backend | HTTP REST (+ optional JWT) | Search, list, graph |
| MCP client | MCP server | MCP HTTP (`/mcp`) | AI tool calls |
| MCP client | Backend | — | **Never direct** |

### Shared configuration that must match across services

| Setting | Backend | MCP server | Admin portal |
|---------|---------|------------|--------------|
| Backend URL | — | `BACKEND_API_URL` | `RAG_BACKEND_URL` |
| JWT secret | `JWT_SECRET` | `JWT_SECRET` (same value) | — |
| File ID prefix | `FILE_PREFIX` (`kb-`) | — | `FILE_PREFIX` (must match) |
| Collection | `COLLECTION_NAME` | — | — |

---

## Docker Compose (local full stack)

Save as `docker-compose.yml` at the repository root:

```yaml
services:
  backend:
    build: ./backend-full/app
    ports: ["8000:8000"]
    volumes:
      - backend-data:/app/vector_db
      - backend-manifest:/app/data
    environment:
      JWT_SECRET: ${JWT_SECRET:-}

  mcp:
    build: ./rag-full/app
    ports: ["4010:4010"]
    environment:
      BACKEND_API_URL: http://backend:8000
      JWT_SECRET: ${JWT_SECRET:-}
    depends_on: [backend]

  admin:
    build: ./admin-portal
    ports: ["8088:8088"]
    environment:
      RAG_BACKEND_URL: http://backend:8000
      PUBLIC_BASE_URL: http://127.0.0.1:8088
      OAUTH_REDIRECT_URI: http://127.0.0.1:8088/auth/callback
      DEV_SKIP_OAUTH: "true"
      ALLOWED_ADMIN_EMAILS: dev@local.test
      DEV_MOCK_EMAIL: dev@local.test
      SESSION_SECRET: local-dev-secret-change-me
    depends_on: [backend]

volumes:
  backend-data:
  backend-manifest:
```

```bash
docker compose up --build
```

Volumes are critical — without them, ChromaDB embeddings and the manifest/graph are lost on container restart.

---

## Authentication and security

| Service | Mechanism | When to enable | Notes |
|---------|-----------|----------------|-------|
| **Backend** | Optional `JWT_SECRET` (HS256) | Always in production | When empty, the API is open (dev only) |
| **MCP server** | Auto-signs short-lived JWTs | Whenever backend `JWT_SECRET` is set | Same secret on both services |
| **Admin portal** | Google OAuth + email allowlist | Always in production | `DEV_SKIP_OAUTH=true` is for local dev only |

Additional security recommendations:

- **Network**: in production, make the backend reachable only by the MCP server and admin portal (VPC, internal Cloud Run URLs, IAM-restricted ingress).
- **Secrets**: store `JWT_SECRET`, `SESSION_SECRET`, `GOOGLE_CLIENT_SECRET` in **Secret Manager** and reference them from Cloud Run.
- **CORS**: backend allows all origins by default for simplicity; lock down in production if exposed to browsers.
- **Delete endpoint**: `DELETE /api/documents/{file_id}` is intentionally **not** exposed by the MCP server or admin UI — removing production data is an ops action.

---

## Shared code sync (important)

The admin portal duplicates a few pipeline helpers from the backend so it can build `file_id` slugs and PageIndex trees **identically** to backend-side ingest.

| Admin portal | Backend equivalent |
|--------------|-------------------|
| `admin-portal/app/page_index.py` | `backend-full/app/rag_pipeline/page_index.py` |
| `admin-portal/app/slug_file_id.py` | `backend-full/app/rag_pipeline/slug_file_id.py` |

When you change document tree building or `file_id` slug logic on the backend, update **both** copies in the same commit. Also keep `FILE_PREFIX` the same on both.

---

## Component documentation

| Component | Detailed docs |
|-----------|---------------|
| RAG API (FastAPI) | [backend-full/app/README.md](backend-full/app/README.md) |
| MCP server (FastMCP) | [rag-full/app/README.md](rag-full/app/README.md) |
| Admin ingest UI | [admin-portal/README.md](admin-portal/README.md) |

---

## Backend API summary

| Area | Key endpoints |
|------|----------------|
| Health | `GET /health` |
| Ingest | `POST /api/ingest`, `DELETE /api/documents/{file_id}` |
| Search | `POST /api/query`, `POST /api/query/hybrid` |
| Catalog | `GET /api/documents`, `GET /api/file-ids`, `GET /api/documents/{id}/chunks`, `GET /api/documents/{id}/outline` |
| Graph | `GET /api/graph`, `GET /api/graph/tree`, `POST /api/graph/rebuild` |
| Debug | `POST /api/resolve-file-ids` |

Full API reference with payloads: [backend-full/app/README.md](backend-full/app/README.md).

---

## MCP tools summary

| Tool | When to use |
|------|-------------|
| `search_knowledge_base` | **Default** — full hybrid search pipeline |
| `search_simple` | Raw vector search without filters |
| `list_documents` | See what is currently indexed |
| `resolve_file_ids` | Debug which docs would be selected for a query |
| `get_document_outline` | Section tree for one document |
| `get_document_content` | Full text chunks for one document |
| `get_form_graph` | Document relationship edges (JSON) |

Tool details: [rag-full/app/README.md](rag-full/app/README.md).

---

## Admin portal summary

| Feature | Details |
|---------|---------|
| Auth | Google OAuth (Authlib) + email allowlist (`ALLOWED_ADMIN_EMAILS`) |
| Upload types | `.txt`, `.md`, `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.csv`, `.json`, audio/video |
| Media | Optional faster-whisper transcription (`TRANSCRIBE_MEDIA=true`, needs ffmpeg) |
| Pipeline | Extract text → build PageIndex tree + semantic `file_id` → `POST /api/ingest` |
| Deploy | Cloud Run via `deploy-cloud-run.ps1` or `cloudbuild.yaml` |

Setup details: [admin-portal/README.md](admin-portal/README.md).

---

## Typical workflow

### Local development

1. Start backend → MCP server → admin portal (see [Quick start](#quick-start-local-all-three-services)).
2. Upload a test document via the admin UI (or `POST /api/ingest` directly).
3. Run `POST /api/graph/rebuild` on the backend.
4. Query via MCP (`search_knowledge_base`) or directly via `POST /api/query/hybrid`.

### Production (Cloud Run)

1. Deploy the **backend** to Cloud Run with persistent storage for `vector_db/` and `data/`.
2. Deploy the **MCP server** with `BACKEND_API_URL` pointing at the backend URL. Share `JWT_SECRET` between the two via Secret Manager.
3. Deploy the **admin portal** with Google OAuth credentials and `RAG_BACKEND_URL`.
4. Wire MCP/LibreChat clients to the deployed MCP service URL + `/mcp`.
5. Use the admin portal to upload documents; run graph rebuild after bulk ingest.

---

## Production (Cloud Run)

| Concern | Recommendation |
|---------|----------------|
| Image registry | Artifact Registry in the same region as Cloud Run |
| Build | Cloud Build (`cloudbuild.yaml` provided for admin portal) or `gcloud run deploy --source` |
| Secrets | Secret Manager (`JWT_SECRET`, `SESSION_SECRET`, `GOOGLE_CLIENT_SECRET`) |
| Storage | Mount/persist `vector_db/` and `data/` (e.g. Cloud Storage FUSE, Filestore, or rebuild-on-deploy if KB is small) |
| Networking | Keep backend access restricted; only the MCP server and admin portal should call it |
| Logging | Cloud Logging captures structured FastAPI logs |
| Scaling | MCP server can scale freely; backend should be configured for stateful storage |

Specific deploy commands and OAuth configuration live in each component's README.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| MCP `/health` shows backend error | Wrong `BACKEND_API_URL` | Point to the running backend URL with **no** trailing slash |
| Upload succeeds but search empty | Graph not rebuilt or embeddings still processing | Run `POST /api/graph/rebuild`; wait a moment and retry |
| `401 Unauthorized` from backend | `JWT_SECRET` mismatch | Use the **same** secret on backend and MCP server |
| Admin OAuth fails | Redirect URI mismatch | `OAUTH_REDIRECT_URI` must match Google Console **exactly** |
| Admin 403 after login | Email not allowlisted | Add to `ALLOWED_ADMIN_EMAILS` |
| `filter_note` falls back to all docs | Stage-0 filter found no strong match | Use more specific query terms; or `auto_filter=false` |
| Different `file_id` slugs for same file | `FILE_PREFIX` or slug logic mismatch | Align `FILE_PREFIX` and `slug_file_id.py` across services |
| Slow first query | Embedding model downloads on backend startup | Wait for download to finish; subsequent queries are fast |
| Media upload says "couldn't get text" | Whisper unavailable or no sidecar transcript | Install ffmpeg, set `TRANSCRIBE_MEDIA=true`, or provide a `.txt` sidecar |

---

## Tech stack

| Layer | Technologies |
|-------|----------------|
| Backend | FastAPI, ChromaDB, Sentence Transformers, PyJWT, rapidfuzz, numpy |
| MCP server | FastMCP, requests, uvicorn, PyJWT |
| Admin portal | FastAPI, Jinja2, Authlib, httpx, faster-whisper (optional) |
| Document extraction | pypdf, python-docx, openpyxl, python-pptx |
| Storage | ChromaDB on disk + JSON manifest/graph files |
| Deploy | Docker, Google Cloud Run, Artifact Registry, Cloud Build, Secret Manager |

---

## Repository conventions

- **Service folders are siblings** at the repo root. Do not nest one service inside another.
- **No virtualenvs or build artifacts** committed (see `.gitignore`).
- **`.env` files are never committed.** Use `.env.example` to document required keys.
- **Knowledge base setup:** ingest documents, rebuild graph, verify search — [backend-full/app/data/README.md](backend-full/app/data/README.md).
- **READMEs at three levels**:
  - Root → platform overview (this file)
  - Per service → API/tool/UI specifics
  - All link to one another.
- **Cross-service code sync** is annotated with `⚠ keep in sync` comments where applicable.

---

## FAQ

**Q: Why do `mcp_rag` (local dev) and `rag-mcp-services` (this repo) both exist?**
The local `mcp_rag/` is your dev workspace and may include Docker container exports, virtualenvs, and the ChromaDB state. This repo (`rag-mcp-services`) contains only the application source code — what gets deployed to Cloud Run.

**Q: Can I run only the backend without MCP or admin?**
Yes. The backend is fully usable on its own via REST. MCP and admin portal are optional add-ons.

**Q: Can I use a different embedding model?**
Yes. Set `EMBEDDINGS_MODEL` to any Hugging Face Sentence Transformers model. Be aware that changing models on an existing vector DB requires re-embedding everything.

**Q: Where do I add custom MCP tools?**
In `rag-full/app/src/main.py`. Define a function and decorate with `@mcp.tool(...)` from FastMCP.

**Q: How do I delete a document in production?**
`DELETE /api/documents/{file_id}` is available on the backend but **not** exposed via the admin UI or MCP server. Call it manually after verifying the `file_id`.

**Q: Does the MCP server cache results?**
No. Every tool call forwards a fresh HTTP request to the backend. Caching, if needed, should be added at the backend layer.

---

## License

Private repository — all rights reserved unless otherwise specified.
