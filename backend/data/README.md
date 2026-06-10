# Knowledge base ingestion

Runtime files in this folder:

| File | How it is created |
|------|-------------------|
| `kb_manifest.json` | Updated on each `POST /api/ingest` (title, path, section tree) |
| `form_graph.json` | Written by `POST /api/graph/rebuild` |
| `../vector_db/` | Chroma chunk embeddings (created on ingest) |

---

## Supported document types

Same set for the bulk ingest script and [admin portal](../../admin-portal/README.md):

| Type | Extensions | How text is obtained |
|------|------------|----------------------|
| Plain text | `.txt`, `.md` | Read directly |
| PDF | `.pdf` | `pypdf` |
| Word | `.docx` | `python-docx` |
| Excel | `.xlsx` | `openpyxl` |
| PowerPoint | `.pptx` | `python-pptx` |
| CSV | `.csv` | Converted to text rows |
| JSON | `.json` | Pretty-printed text |
| Video | `.mp4`, `.mov`, `.mkv`, `.avi`, `.wmv`, `.webm` | Sidecar `filename.ext.txt` or `_transcripts/filename.ext.txt`, else Whisper if `TRANSCRIBE_MEDIA=true` (needs **ffmpeg**) |

---

## Way 1 — Bulk ingest script (recommended)

Best for loading a whole folder (policies, training docs, etc.) — local backend or Cloud Run.

**Prerequisites:** Backend running. Python env with admin-portal dependencies (`pip install -r admin-portal/requirements.txt`).

```bash
cd admin-portal

# Optional: copy and edit .env (RAG_BACKEND_URL, FILE_PREFIX, TRANSCRIBE_MEDIA)
# cp .env.example .env

# Dry-run: validate extraction without calling the backend
python scripts/ingest_folder.py --root "C:/path/to/your/documents" --dry-run

# Ingest everything under the folder (recursive)
python scripts/ingest_folder.py --root "C:/path/to/your/documents"

# Cloud Run or custom path prefix
python scripts/ingest_folder.py \
  --root "./docs" \
  --backend-url https://YOUR-BACKEND-URL.run.app \
  --path-prefix "policies-and-procedures"
```

**Video:** Sidecar `video.mp4.txt` or `_transcripts/video.mp4.txt`, or `TRANSCRIBE_MEDIA=true` in `admin-portal/.env` (needs **ffmpeg**).

Pipeline: `extract_text` → `build_semantic_file_id` → `build_document_tree` → `POST /api/ingest`.

---

## Way 2 — Admin portal (UI upload)

Best for one-off uploads without using the CLI.

1. Start the **backend** (`uvicorn` or Cloud Run).
2. Start the **admin portal** — [admin-portal/README.md](../../admin-portal/README.md).
3. Set `RAG_BACKEND_URL` to your backend URL.
4. Sign in (Google OAuth, or `DEV_SKIP_OAUTH=true` locally).
5. Upload each file (same extraction and ingest pipeline as Way 1).

---

## Way 3 — Direct API (text or custom pipelines)

Use when you already have plain text or your own extractor.

```bash
export BACKEND_URL=http://localhost:8000

curl -X POST "$BACKEND_URL/api/ingest" \
  -H "Content-Type: application/json" \
  -d '{
    "file_id": "kb-example-policy",
    "content": "Full document text here...",
    "display_title": "Example Policy",
    "relative_path": "policies/example-policy.pdf",
    "tree": {
      "root_id": "n0",
      "nodes": [
        { "id": "n0", "title": "Example Policy", "parent_id": null }
      ]
    }
  }'
```

`file_id` must start with `FILE_PREFIX` (default `kb-`). Include `display_title`, `relative_path`, and `tree` when possible — they improve search and graph edges.

---

## After ingest — check and rebuild graph

### 1. Verify documents

```bash
curl "$BACKEND_URL/health"
curl "$BACKEND_URL/api/documents"
```

Expect `vector_store: ready` and each `file_id` with `chunk_count` > 0.

### 2. Rebuild graph (required after bulk ingest)

```bash
curl -X POST "$BACKEND_URL/api/graph/rebuild" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Builds `form_graph.json` from the manifest and Chroma centroids. Hybrid search uses these edges for related-document expansion.

### 3. Test search

```bash
curl -X POST "$BACKEND_URL/api/query/hybrid" \
  -H "Content-Type: application/json" \
  -d '{ "query": "your question", "k": 4 }'
```

Or MCP `search_knowledge_base` — [mcp-server](../../mcp-server/README.md).

---

## Rebuild from scratch

1. Stop the backend.
2. Delete `../vector_db/`, `kb_manifest.json`, and `form_graph.json`.
3. Start the backend.
4. Ingest again (script, portal, or API).
5. Run **graph rebuild** (step 2 above).

On Cloud Run, persist `/app/vector_db` and `/app/data` on a volume so data survives redeploys.
