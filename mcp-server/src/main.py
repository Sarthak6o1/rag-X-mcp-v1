from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

MCP_NAME = "RAG Knowledge Base"
API_BASE = os.environ.get("BACKEND_API_URL", "http://backend:8000").rstrip("/")
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "120"))
PORT = int(os.environ.get("MCP_PORT", "4010"))
JWT_SECRET = os.environ.get("JWT_SECRET", "").strip()
JWT_USER_ID = os.environ.get("JWT_USER_ID", "rag-mcp-server").strip()

mcp = FastMCP(MCP_NAME)


# ---------------------------------------------------------------------------
# Auth: optional JWT for backend calls when JWT_SECRET is set
# ---------------------------------------------------------------------------

def _build_auth_headers() -> dict[str, str]:
    if not JWT_SECRET:
        return {}
    now = datetime.now(timezone.utc)
    ttl = max(5, int(os.environ.get("JWT_TTL_MINUTES", "60")))
    token = jwt.encode(
        {
            "id": JWT_USER_ID,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=ttl)).timestamp()),
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _url(path: str) -> str:
    return f"{API_BASE}{path}"


def _handle_response(response: requests.Response) -> Any:
    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": response.text}
    if not response.ok:
        detail = payload.get("detail") if isinstance(payload, dict) else str(payload)
        raise RuntimeError(f"Backend API error ({response.status_code}): {detail}")
    return payload


def _get(path: str, params: dict | None = None) -> Any:
    response = requests.get(_url(path), params=params, headers=_build_auth_headers(), timeout=REQUEST_TIMEOUT)
    return _handle_response(response)


def _post(path: str, payload: dict) -> Any:
    response = requests.post(_url(path), json=payload, headers=_build_auth_headers(), timeout=REQUEST_TIMEOUT)
    return _handle_response(response)


def _format_results(results: list[dict]) -> str:
    if not results:
        return "No results found."
    parts: list[str] = []
    for r in results:
        file_id = r.get("file_id", "unknown")
        score = r.get("score", 0.0)
        content = r.get("content", "")
        parts.append(f"[{file_id}] (score={score:.3f})\n{content}")
    return "\n\n---\n\n".join(parts)


def _format_tree(tree: dict[str, Any]) -> str:
    nodes = tree.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return "(no outline)"
    root_id = tree.get("root_id", "n0")
    by_id = {n["id"]: n for n in nodes if isinstance(n, dict) and "id" in n}
    lines: list[str] = []

    def emit(node_id: str, depth: int) -> None:
        node = by_id.get(node_id)
        if not node:
            return
        title = str(node.get("title", ""))
        indent = "  " * depth
        lines.append(f"{indent}- {title} ({node_id})")
        children = [n["id"] for n in nodes if isinstance(n, dict) and n.get("parent_id") == node_id]
        for cid in children:
            emit(cid, depth + 1)

    emit(str(root_id), 0)
    return "\n".join(lines) if lines else "(empty tree)"


# ---------------------------------------------------------------------------
# Tool 1: List all indexed documents
# ---------------------------------------------------------------------------

@mcp.tool(
    name="list_documents",
    description=(
        "List all document IDs currently indexed in the knowledge base. "
        "Returns file IDs and chunk counts. Call this to see what information "
        "is available before searching."
    ),
)
def list_documents() -> str:
    docs = _get("/api/documents")
    if not docs:
        return "No documents are currently indexed in the knowledge base."
    lines = [f"- {d['file_id']} ({d['chunk_count']} chunks)" for d in docs]
    return f"Indexed documents ({len(docs)} total):\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 2: Resolve file_ids (Stage-0 prompt filter + graph expansion)
# ---------------------------------------------------------------------------

@mcp.tool(
    name="resolve_file_ids",
    description=(
        "Stage-0: rank file_ids from the user prompt using fuzzy matching on "
        "document slugs and manifest titles, then expand via the form graph. "
        "Use this before searching to see which documents are most relevant."
    ),
)
def resolve_file_ids(query: str, max_documents: int = 12) -> str:
    result = _post("/api/resolve-file-ids", {"query": query, "max_documents": max_documents})
    ranked = result.get("ranked", [])
    expanded = result.get("expanded", [])
    if not ranked:
        return "No file_ids matched strongly; use list_documents or search with auto_filter off."
    lines = ["Ranked (top matches):"] + [f"  - {fid}" for fid in ranked]
    if expanded:
        lines.append("Graph-expanded (related documents):")
        lines.extend(f"  - {fid}" for fid in expanded)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 3: Get document outline (PageIndex tree)
# ---------------------------------------------------------------------------

@mcp.tool(
    name="get_document_outline",
    description=(
        "Return the PageIndex-style section tree (headings/TOC) for one document. "
        "This shows the document structure without reading full content."
    ),
)
def get_document_outline(file_id: str) -> str:
    if not file_id.strip():
        raise ValueError("file_id cannot be empty")
    try:
        result = _get(f"/api/documents/{file_id}/outline")
    except RuntimeError as exc:
        return str(exc)

    tree = result.get("tree", {})
    title = result.get("display_title", file_id)
    rel = result.get("relative_path", "")
    body = _format_tree(tree)
    return f"Document: {title}\nPath: {rel}\nfile_id: {file_id}\n\nOutline:\n{body}"


# ---------------------------------------------------------------------------
# Tool 4: Hybrid search (FULL PIPELINE — recommended)
# ---------------------------------------------------------------------------

@mcp.tool(
    name="search_knowledge_base",
    description=(
        "Full hybrid retrieval (recommended): "
        "(1) prompt-to-file_id filter (fuzzy match on slugs + titles), "
        "(2) form graph expansion (related documents), "
        "(3) PageIndex tree hints (outline-augmented query), "
        "(4) vector search on local ChromaDB (cosine similarity). "
        "Use this for any question that might be answered by indexed documents. "
        "Parameters: "
        "  query (required) - natural language search question. "
        "  k (optional, default 8) - number of results. "
        "  auto_filter (optional, default true) - enable Stage-0 prompt filter. "
        "  file_ids_csv (optional) - comma-separated file IDs to restrict scope. "
        "  use_tree_hints (optional, default true) - append outline hints to query."
    ),
)
def search_knowledge_base(
    query: str,
    k: int = 8,
    auto_filter: bool = True,
    max_documents: int = 12,
    file_ids_csv: str = "",
    use_tree_hints: bool = True,
) -> str:
    if not query.strip():
        raise ValueError("query cannot be empty")

    result = _post("/api/query/hybrid", {
        "query": query,
        "k": k,
        "auto_filter": auto_filter,
        "max_documents": max_documents,
        "file_ids_csv": file_ids_csv,
        "use_tree_hints": use_tree_hints,
    })

    results = result.get("results", [])
    filter_note = result.get("filter_note", "")
    tree_note = result.get("tree_note", "")

    if not results:
        return f'No results found for: "{query}". ({filter_note})'

    body = _format_results(results)
    notes = [n for n in [filter_note, tree_note] if n]
    header = " ".join(notes)
    return f"{header}\n\n{body}" if header else body


# ---------------------------------------------------------------------------
# Tool 5: Simple vector search (no pipeline)
# ---------------------------------------------------------------------------

@mcp.tool(
    name="search_simple",
    description=(
        "Simple vector search without pipeline filters. "
        "Use this if you want raw similarity results without prompt filtering "
        "or graph expansion. For most queries, prefer search_knowledge_base instead."
    ),
)
def search_simple(query: str, k: int = 6, file_ids_csv: str = "") -> str:
    if not query.strip():
        raise ValueError("query cannot be empty")

    payload: dict[str, Any] = {"query": query, "k": k}
    if file_ids_csv.strip():
        payload["file_ids"] = [fid.strip() for fid in file_ids_csv.split(",") if fid.strip()]

    result = _post("/api/query", payload)
    return _format_results(result.get("results", []))


# ---------------------------------------------------------------------------
# Tool 6: Get full document content
# ---------------------------------------------------------------------------

@mcp.tool(
    name="get_document_content",
    description=(
        "Load all stored text chunks for one document by file_id. "
        "Use this when the user wants to read or review a specific document."
    ),
)
def get_document_content(file_id: str) -> str:
    if not file_id.strip():
        raise ValueError("file_id cannot be empty")

    result = _get(f"/api/documents/{file_id}/chunks")
    chunks = result.get("chunks", [])
    if not chunks:
        return f"No content found for document: {file_id}"

    parts = [f"Document: {file_id} ({len(chunks)} chunks)\n"]
    for c in chunks:
        parts.append(f"--- Chunk {c['chunk_index']} ---\n{c['content']}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Tool 7: Get form graph summary
# ---------------------------------------------------------------------------

@mcp.tool(
    name="get_form_graph",
    description=(
        "List the curated document-to-document relationship edges (JSON graph) "
        "used to expand retrieval. Shows how documents are related to each other."
    ),
)
def get_form_graph() -> str:
    result = _get("/api/graph")
    edges = result.get("edges", [])
    if not edges:
        return "No graph edges loaded. Add edges to form_graph.json to enable graph-based expansion."
    return json.dumps(edges, indent=2)


# ---------------------------------------------------------------------------
# Health / root routes
# ---------------------------------------------------------------------------

@mcp.custom_route("/health", methods=["GET"])
async def health_check(_request: Request) -> JSONResponse:
    try:
        backend_health = _get("/health")
    except Exception as exc:
        backend_health = {"error": str(exc)}

    return JSONResponse({
        "status": "healthy",
        "service": MCP_NAME,
        "backend_api": API_BASE,
        "backend_health": backend_health,
        "mcp_endpoint": "/mcp",
    })


@mcp.custom_route("/", methods=["GET"])
async def root(_request: Request) -> JSONResponse:
    return JSONResponse({
        "service": MCP_NAME,
        "health_endpoint": "/health",
        "mcp_endpoint": "/mcp",
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(mcp.http_app(), host="0.0.0.0", port=PORT, log_level="info")
