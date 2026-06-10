# RAG MCP Server

**FastMCP** server that exposes the RAG knowledge base as **MCP tools** for AI clients (LibreChat, Cursor, Claude Desktop, ChatGPT custom connectors, etc.). Every tool forwards to the FastAPI backend in `backend-full/app` — this service does no embedding or storage of its own.

Documents enter the knowledge base via the [admin portal](../../admin-portal/README.md) or direct `POST /api/ingest` on the backend — this service only **reads** the indexed content.

> **Location in repo:** `rag-full/app/`

Part of [RAG MCP Services](../../README.md).

---

## Table of contents

1. [Role in the stack](#role-in-the-stack)
2. [MCP tools](#mcp-tools)
3. [Tool details](#tool-details)
4. [Environment variables](#environment-variables)
5. [Run locally](#run-locally)
6. [Connect an MCP client](#connect-an-mcp-client)
7. [Authentication (JWT)](#authentication-jwt)
8. [Docker](#docker)
9. [Docker Compose (full stack)](#docker-compose-full-stack)
10. [Project structure](#project-structure)
11. [Troubleshooting](#troubleshooting)
12. [Production (Cloud Run)](#production-cloud-run)
13. [Related docs](#related-docs)

---

## Role in the stack

```
AI client (LibreChat / Cursor / Claude Desktop / MCP host)
        │  MCP HTTP transport (/mcp)
        ▼
  rag-full/app    ← this service (FastMCP, port 4010)
        │         ← signs short-lived JWT if JWT_SECRET set
        │  HTTP REST
        ▼
  backend-full/app   ← RAG API (ChromaDB, graph, ingest)
        ▲
        │  POST /api/ingest
  admin-portal       ← admin uploads (separate from MCP)
```

The MCP server is **stateless**. It can scale horizontally; only the backend stores data.

---

## MCP tools

| Tool | Description |
|------|-------------|
| `list_documents` | List indexed document IDs and chunk counts |
| `resolve_file_ids` | Stage-0 fuzzy filter + graph expansion (no vector search) |
| `get_document_outline` | PageIndex section tree for one document |
| `search_knowledge_base` | **Recommended** — full hybrid pipeline |
| `search_simple` | Raw vector search without filters |
| `get_document_content` | All text chunks for one `file_id` |
| `get_form_graph` | Document relationship edges (JSON) |

All tools return formatted text suitable for an LLM to read. JSON-style responses are included inline where useful.

---

## Tool details

### `search_knowledge_base` (recommended for most queries)

Calls `POST /api/query/hybrid` on the backend.

Pipeline applied by the backend:

1. **Stage-0 prompt filter** — `rapidfuzz` over slugs + manifest titles.
2. **Form graph expansion** — Related documents via `same_folder`, `semantic_similar`, `shared_sections` edges.
3. **PageIndex tree hints** — Top section titles appended to the embedding query.
4. **Vector search** — ChromaDB cosine similarity, scoped to the narrowed `file_id` set.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | required | User question or search phrase |
| `k` | int | `8` | Number of top chunks to return |
| `auto_filter` | bool | `true` | Whether to run stage-0 + graph expansion |
| `max_documents` | int | `12` | Cap on candidate documents after filter |
| `file_ids_csv` | string | `""` | Comma-separated list to force a specific `file_id` scope |
| `use_tree_hints` | bool | `true` | Append section titles to vector query |

### `search_simple`

Calls `POST /api/query` on the backend. Raw cosine similarity without stage-0 filter, graph expansion, or tree hints. Useful when you already know the relevant `file_ids` or want to skip filtering.

### `resolve_file_ids`

Preview which documents the stage-0 filter + graph expansion would select for a given query, **without running the vector search**. Useful for debugging retrieval scope.

| Parameter | Type | Default |
|-----------|------|---------|
| `query` | string | required |
| `max_documents` | int | `12` |

### `get_document_outline`

Returns the PageIndex section tree for one `file_id` — headings and structure without full content. Useful for orienting before reading.

| Parameter | Type |
|-----------|------|
| `file_id` | string |

### `get_document_content`

Returns all stored text chunks for one document, in order. Use when the user wants to read or summarize a full document.

| Parameter | Type |
|-----------|------|
| `file_id` | string |

### `get_form_graph`

Returns the knowledge graph (nodes + typed edges) as JSON. Useful for understanding how documents relate.

### `list_documents`

Lists all indexed `file_id`s with chunk counts. Call this first if the user asks "what's in the knowledge base?"

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_API_URL` | `http://backend:8000` | Base URL of the RAG backend (**no trailing slash**) |
| `MCP_PORT` | `4010` | HTTP port for MCP + health routes |
| `REQUEST_TIMEOUT_SECONDS` | `120` | Timeout for backend HTTP calls |
| `JWT_SECRET` | *(empty)* | When set, signs Bearer tokens for backend requests |
| `JWT_USER_ID` | `rag-mcp-server` | Subject claim for generated JWTs |
| `JWT_TTL_MINUTES` | `60` | Token lifetime when `JWT_SECRET` is set |

Example local `.env`:

```env
BACKEND_API_URL=http://localhost:8000
MCP_PORT=4010
JWT_SECRET=your-shared-secret-with-backend
JWT_TTL_MINUTES=60
```

Use the **same `JWT_SECRET`** as the backend when auth is enabled. The MCP server signs a fresh HS256 token for every backend call; the backend validates it.

---

## Run locally

**Prerequisites:** Backend running (see [backend-full/app/README.md](../../backend-full/app/README.md)).

```bash
cd rag-full/app
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python src/main.py
```

Endpoints:

| Path | Description |
|------|-------------|
| `GET /` | Service info |
| `GET /health` | MCP health + proxied backend `/health` |
| `/mcp` | MCP protocol endpoint (FastMCP HTTP transport) |

Verify:

```bash
curl http://localhost:4010/health
```

Expected response shape:

```json
{
  "status": "healthy",
  "service": "rag-mcp-server",
  "backend_url": "http://localhost:8000",
  "backend_health": {
    "status": "healthy",
    "vector_store": "ready"
  }
}
```

---

## Connect an MCP client

Point your MCP host at the deployed MCP endpoint for this service:

```
${RAG_MCP_URL}
```

Ensure `BACKEND_API_URL` resolves from the MCP process/container to the running backend. Keep deployment-specific addresses in environment variables or platform configuration, not in committed docs.

### LibreChat example

In `librechat.yaml`, add this under the top-level `mcpServers` key:

```yaml
mcpServers:
  knowledge-base:
    type: streamable-http
    url: ${RAG_MCP_URL}
    timeout: 300000
```

Set `RAG_MCP_URL` in the runtime environment where LibreChat starts. This keeps local, staging, and production addresses out of source control while still making the integration portable.

No changes are required when admins upload via the admin portal — new documents appear in search after backend ingest completes.

### MCP client configuration checklist

1. **Backend healthy** — the backend health endpoint returns `healthy`.
2. **MCP server can reach backend** — `BACKEND_API_URL` is correct, no trailing slash.
3. **MCP server healthy** — the MCP health endpoint returns `healthy` with `backend_health` populated.
4. **MCP client URL** — Set LibreChat's `mcpServers.<name>.url` from `RAG_MCP_URL`.
5. **JWT (if enabled)** — Same `JWT_SECRET` on backend and MCP server.
6. **After uploads** — Optionally run `POST /api/graph/rebuild` on the backend for best retrieval.

---

## Authentication (JWT)

When `JWT_SECRET` is set on both this server and the backend:

1. Before every backend HTTP call, the MCP server generates a short-lived HS256 JWT with claims:
   - `sub` = `JWT_USER_ID` (default `rag-mcp-server`)
   - `iat` = now, `exp` = now + `JWT_TTL_MINUTES`
2. The token is sent as `Authorization: Bearer <jwt>`.
3. The backend validates the signature and expiration; mismatched secrets cause `401`.

Clients of the MCP server **never** see the JWT — it lives entirely between MCP server and backend.

---

## Docker

```bash
cd rag-full/app
docker build -t rag-mcp-server .
docker run -p 4010:4010 \
  -e BACKEND_API_URL=http://host.docker.internal:8000 \
  -e JWT_SECRET=your-secret \
  rag-mcp-server
```

On Linux Docker, replace `host.docker.internal` with the backend container name or host IP.

---

## Docker Compose (full stack)

See [root README](../../README.md) for a compose file covering backend + MCP + admin portal.

Minimal backend + MCP:

```yaml
services:
  backend:
    build: ./backend-full/app
    ports: ["8000:8000"]
    volumes:
      - backend-data:/app/vector_db
      - backend-manifest:/app/data

  mcp:
    build: ./rag-full/app
    ports: ["4010:4010"]
    environment:
      BACKEND_API_URL: http://backend:8000
      JWT_SECRET: ${JWT_SECRET:-}
    depends_on: [backend]

volumes:
  backend-data:
  backend-manifest:
```

---

## Project structure

```
rag-full/app/
├── Dockerfile
├── requirements.txt
└── src/
    └── main.py    # FastMCP tools, JWT signing, /health, MCP /mcp endpoint
```

Dependencies: `fastmcp`, `requests`, `uvicorn`, `PyJWT`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `backend_health` shows error in `/health` | Wrong `BACKEND_API_URL` or backend down | Set correct URL (no trailing slash); verify backend `/health` |
| Tools return timeout | Backend slow on first request (model load) | Increase `REQUEST_TIMEOUT_SECONDS` (default 120) |
| `401 Unauthorized` from backend | `JWT_SECRET` mismatch | Set matching secret on both services |
| Empty search after admin upload | Backend ingest may still be processing, or graph not rebuilt | Wait, then run `POST /api/graph/rebuild` |
| MCP client cannot connect | Wrong transport or URL | Use HTTP transport with full `/mcp` path |
| Tool not visible in client | MCP client cache | Reload tool list / restart MCP host |

---

## Production (Cloud Run)

Deploy as a separate Cloud Run service. Required settings:

| Env var | Value |
|---------|-------|
| `BACKEND_API_URL` | Your backend Cloud Run URL (no trailing slash) |
| `JWT_SECRET` | Same as backend (from Secret Manager) |
| `MCP_PORT` | Cloud Run sets `PORT`; this app reads `MCP_PORT` env. Set them to the same value (e.g. `8080`) |

Recommendations:

- Allow unauthenticated invocations only if your MCP clients are public; otherwise restrict via IAM.
- Use min-instances ≥ 1 to avoid cold starts for active MCP clients.
- Logs go to Cloud Logging automatically.

Point LibreChat/MCP clients at the deployed service URL + `/mcp`.

---

## Related docs

- [Root README](../../README.md) — platform overview
- [Backend README](../../backend-full/app/README.md) — API, hybrid pipeline, graph
- [Admin portal](../../admin-portal/README.md) — document upload (feeds the backend this server reads)
