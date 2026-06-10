# Admin Portal (Ingest UI)

Web UI for **admins** to upload documents into the RAG knowledge base.

Users sign in with **Google OAuth** (or skip auth in local dev), upload files, and the portal **extracts text**, builds a **PageIndex section tree** and a **semantic `file_id`**, then calls the backend **`POST /api/ingest`** to add the document to the vector store.

**LibreChat and other MCP clients are unchanged.** They continue using the MCP server + backend URLs you already configured. New documents become searchable as soon as backend ingest completes (and optional graph rebuild runs).

> **Location in repo:** `admin-portal/`

Part of [RAG MCP Services](../README.md).

---

## Table of contents

1. [Role in the stack](#role-in-the-stack)
2. [Features](#features)
3. [Project structure](#project-structure)
4. [Environment variables](#environment-variables)
5. [Run locally](#run-locally)
6. [Upload pipeline](#upload-pipeline)
7. [Supported upload types](#supported-upload-types)
8. [Backend requirements](#backend-requirements)
9. [Google Cloud: OAuth + Cloud Run](#google-cloud-oauth--cloud-run)
10. [HTTP routes](#http-routes)
11. [Docker](#docker)
12. [Keep in sync with backend](#keep-in-sync-with-backend)
13. [Troubleshooting](#troubleshooting)
14. [Related docs](#related-docs)

---

## Role in the stack

```
Admin browser
     │
     ▼
admin-portal (:8088)     Google OAuth + email allowlist
     │
     │  POST /api/ingest  (server-side HTTP)
     ▼
backend-full/app (:8000)   ChromaDB + manifest + graph
     ▲
     │  search / list tools
rag-full/app (:4010)       MCP for AI clients
```

This portal **only writes** into the knowledge base. It never serves search to end-users — that is the job of the MCP server and the backend's `/api/query/hybrid` endpoint.

---

## Features

- **Google OAuth login** (Authlib) with a comma-separated email allowlist
- **Multi-format upload** — `.txt`, `.md`, `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.csv`, `.json`, and media files
- **Optional media transcription** — `faster-whisper` for audio/video when no sidecar `.txt` exists
- **PageIndex trees** — section outline built at upload time (same logic as backend pipeline)
- **Semantic file IDs** — slugs aligned with backend `FILE_PREFIX` (default `kb-`)
- **Dev-skip OAuth** — `DEV_SKIP_OAUTH=true` for local development without Google credentials
- **Cloud Run deploy** — PowerShell script + Cloud Build config included
- **Health endpoint** — `GET /health` for uptime checks and configured backend URL

---

## Project structure

```
admin-portal/
├── app/
│   ├── main.py              # FastAPI routes: OAuth, upload, health
│   ├── config.py            # Environment variables
│   ├── oauth_setup.py       # Authlib Google OAuth
│   ├── extract_text.py      # PDF/DOCX/XLSX/PPTX/txt + media
│   ├── transcribe_media.py  # faster-whisper (optional)
│   ├── page_index.py        # ⚠ keep in sync with backend rag_pipeline/page_index.py
│   ├── slug_file_id.py      # ⚠ keep in sync with backend rag_pipeline/slug_file_id.py
│   └── templates/
│       └── index.html       # Upload UI
├── requirements.txt
├── Dockerfile
├── .env.example
├── .gitignore
├── deploy-cloud-run.ps1     # Windows-friendly Cloud Run deploy
├── cloudbuild.yaml
└── README.md
```

---

## Environment variables

Copy `.env.example` to `.env` for local development. **Never commit `.env`.**

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `RAG_BACKEND_URL` | Yes | `http://127.0.0.1:8000` | Backend base URL, **no trailing slash** |
| `PUBLIC_BASE_URL` | Yes | `http://127.0.0.1:8088` | This portal's public URL (OAuth) |
| `OAUTH_REDIRECT_URI` | Yes* | — | Must match Google Console exactly, e.g. `http://127.0.0.1:8088/auth/callback` |
| `GOOGLE_CLIENT_ID` | Prod | — | Google OAuth web client ID |
| `GOOGLE_CLIENT_SECRET` | Prod | — | Google OAuth secret (use Secret Manager in Cloud Run) |
| `ALLOWED_ADMIN_EMAILS` | Yes | — | Comma-separated emails allowed to upload |
| `SESSION_SECRET` | Yes | — | Long random string for signed session cookies |
| `FILE_PREFIX` | No | `kb-` | Must match backend `FILE_PREFIX` |
| `DEV_SKIP_OAUTH` | No | `false` | `true` = skip Google login (**local only**) |
| `DEV_MOCK_EMAIL` | No | `dev@local.test` | Mock user email when `DEV_SKIP_OAUTH=true` |
| `REQUEST_TIMEOUT_SECONDS` | No | `600` | Timeout for backend ingest calls |
| `TRANSCRIBE_MEDIA` | No | `true` | Transcribe audio/video with faster-whisper |
| `WHISPER_MODEL` | No | `base` | Whisper model size (`tiny`, `base`, `small`, `medium`, `large`) |
| `SESSION_COOKIE_SECURE` | No | auto | `true` when `PUBLIC_BASE_URL` is HTTPS |
| `SESSION_SAME_SITE` | No | auto | `none` on HTTPS (OAuth), `lax` on HTTP |
| `ALLOW_OAUTH_STATE_BYPASS` | No | `false` | Dev-only fallback for OAuth state mismatch (do not use in production) |

\* Required when Google OAuth is enabled (`DEV_SKIP_OAUTH=false`).

Example local `.env` (dev, no Google OAuth):

```env
RAG_BACKEND_URL=http://127.0.0.1:8000
PUBLIC_BASE_URL=http://127.0.0.1:8088
OAUTH_REDIRECT_URI=http://127.0.0.1:8088/auth/callback
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
ALLOWED_ADMIN_EMAILS=user@example.com
SESSION_SECRET=change-me-to-a-long-random-string
DEV_SKIP_OAUTH=true
DEV_MOCK_EMAIL=user@example.com
```

Example production `.env` (Google OAuth enabled):

```env
RAG_BACKEND_URL=https://rag-backend-XXXXXXX.a.run.app
PUBLIC_BASE_URL=https://rag-admin-portal-XXXXXXX.a.run.app
OAUTH_REDIRECT_URI=https://rag-admin-portal-XXXXXXX.a.run.app/auth/callback
GOOGLE_CLIENT_ID=YOUR-CLIENT-ID.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=YOUR-GOOGLE-SECRET
ALLOWED_ADMIN_EMAILS=admin@example.com,ops@example.com
SESSION_SECRET=YOUR-LONG-RANDOM-STRING
DEV_SKIP_OAUTH=false
```

Always store `GOOGLE_CLIENT_SECRET` and `SESSION_SECRET` in **Secret Manager** in production, not in plain env files.

---

## Run locally

**Prerequisites:** RAG backend running at `RAG_BACKEND_URL`.

```bash
cd admin-portal
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
# Edit .env (see env reference above)
uvicorn app.main:app --reload --host 127.0.0.1 --port 8088
```

Open `http://127.0.0.1:8088`

Verify health:

```bash
curl http://127.0.0.1:8088/health
```

### Local dev without Google OAuth

Set in `.env`:

```env
DEV_SKIP_OAUTH=true
DEV_MOCK_EMAIL=user@example.com
ALLOWED_ADMIN_EMAILS=user@example.com
```

Upload now works without Google login. **Never use `DEV_SKIP_OAUTH=true` in production.**

---

## Upload pipeline

When an admin uploads a file via the UI:

1. **Auth check** — Session email must be in `ALLOWED_ADMIN_EMAILS` (unless dev-skip mode).
2. **Save to temp dir** — Preserves relative path structure under `admin-upload/`.
3. **Build `file_id`** — `slug_file_id.build_semantic_file_id()` with `FILE_PREFIX`. Example: `guides/Onboarding Intro.pdf` → `kb-guides-onboarding-intro`.
4. **Build section tree** — `page_index.build_document_tree()` parses headings and builds a hierarchical PageIndex outline.
5. **Extract text** — PDF, Office formats, plain text/CSV/JSON; optional Whisper for media.
6. **Ingest** — `POST {RAG_BACKEND_URL}/api/ingest` with:
   ```json
   {
     "file_id": "kb-guides-onboarding-intro",
     "content": "<extracted text>",
     "display_title": "Onboarding Intro",
     "relative_path": "guides/onboarding-intro.pdf",
     "tree": { "nodes": [/* PageIndex outline */] },
     "metadata": { "source": "admin-portal", "uploaded_by": "admin@example.com" }
   }
   ```
7. **Result** — The portal shows the backend response (chunk count, any errors).

After bulk uploads, run **`POST /api/graph/rebuild`** on the backend to refresh knowledge graph edges.

Full knowledge-base setup (ingest, `vector_db/`, manifest, graph): [backend-full/app/data/README.md](../backend-full/app/data/README.md).

---

## Bulk ingest script (CLI) — primary way to load documents

Same extraction and `POST /api/ingest` flow as the UI, for an entire folder (recommended for full KB builds):

```bash
cd admin-portal
pip install -r requirements.txt

python scripts/ingest_folder.py --root "C:/path/to/documents" --dry-run
python scripts/ingest_folder.py --root "C:/path/to/documents"
```

Options: `--backend-url`, `--path-prefix`, `--source`, `--dry-run`. See [backend-full/app/data/README.md](../backend-full/app/data/README.md) for supported file types and the graph rebuild step after bulk load.

---

## Supported upload types

| Type | Extensions | How text is obtained |
|------|------------|----------------------|
| Plain text | `.txt`, `.md` | Direct read |
| Office docs | `.pdf`, `.docx`, `.xlsx`, `.pptx` | `pypdf`, `python-docx`, `openpyxl`, `python-pptx` |
| Data | `.csv`, `.json` | Converted to plain text representation |
| Media | audio/video (see `page_index.MEDIA_EXTENSIONS`) | Sidecar `.txt` transcript if present, else `faster-whisper` when `TRANSCRIBE_MEDIA=true` |

Requires **ffmpeg on PATH** for media transcription. On Cloud Run, the Dockerfile must install ffmpeg if you enable media uploads in production.

---

## Backend requirements

- Backend must be reachable from this service at `RAG_BACKEND_URL`.
- **CORS:** If admin UI and backend run on different browser origins, configure backend CORS or use a reverse proxy. Note that **server-side ingest calls** from this portal are not subject to browser CORS.
- **No public delete API** is exposed in the portal by design — ingestion adds/updates by `file_id` only. Removing production vectors is an ops task (volume reset, `DELETE /api/documents/{file_id}` from a trusted shell), not a UI button.
- **JWT:** The admin portal does **not** send JWT. In production, restrict backend network access (VPC, internal Cloud Run URLs, IAM) so only trusted callers (admin portal, MCP server) reach it.

---

## Google Cloud: OAuth + Cloud Run

Use the **same GCP project** as your RAG backend when possible.

### 1) Google OAuth client

1. [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services** → **OAuth consent screen**
   - User type: Internal (for an org-only portal) or External
   - Scopes: `openid`, `email`, `profile`
2. **Credentials** → **OAuth client ID** → **Web application**
3. **Authorized JavaScript origins**
   - Local: `http://127.0.0.1:8088`
   - Prod: `https://YOUR-ADMIN-SERVICE.run.app`
4. **Authorized redirect URIs** (must match `OAUTH_REDIRECT_URI` exactly)
   - Local: `http://127.0.0.1:8088/auth/callback`
   - Prod: `https://YOUR-ADMIN-SERVICE.run.app/auth/callback`
5. Store **Client ID** and **Client secret** in Secret Manager for Cloud Run.

Set on the Cloud Run service:

- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
- `OAUTH_REDIRECT_URI`, `PUBLIC_BASE_URL`
- `ALLOWED_ADMIN_EMAILS`, `SESSION_SECRET`
- `RAG_BACKEND_URL` = your backend Cloud Run URL (no trailing slash)
- `DEV_SKIP_OAUTH=false`

### 2) Deploy to Cloud Run

**Windows (recommended):**

```powershell
cd admin-portal
.\deploy-cloud-run.ps1
```

Optional parameters: `-ProjectId`, `-Region us-central1`, `-Service rag-admin-portal`, `-ArRepo docker`

**Manual Docker:**

```bash
cd admin-portal
docker build -t rag-admin .
# push to Artifact Registry, then:
gcloud run deploy rag-admin-portal --image IMAGE --region REGION --allow-unauthenticated
```

**Cloud Build:**

```bash
cd admin-portal
gcloud builds submit --config=cloudbuild.yaml --region=REGION .
```

`--allow-unauthenticated` on Cloud Run is normal here — **Google OAuth + email allowlist** restrict who can actually use the UI. The Cloud Run ingress is public, but `/upload` and `/` require an authenticated, allowlisted session.

Use Secret Manager for `GOOGLE_CLIENT_SECRET` and `SESSION_SECRET`:

```bash
gcloud secrets create google-oauth-secret --data-file=- < secret.txt
gcloud run services update rag-admin-portal \
  --update-secrets=GOOGLE_CLIENT_SECRET=google-oauth-secret:latest
```

### 3) After deploy

1. Add the new admin URL to OAuth **origins** and **redirect URIs** in Google Console.
2. Sign in with an allowlisted email.
3. Upload a test file.
4. Confirm the backend has the document (`GET /api/documents`).
5. Confirm MCP/LibreChat search still works (unchanged).

---

## HTTP routes

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Upload UI (login required unless dev-skip) |
| `GET` | `/login` | Redirect to Google OAuth |
| `GET` | `/auth/callback` | OAuth callback handler |
| `GET` | `/logout` | Clear session and redirect home |
| `POST` | `/upload` | Multipart upload → backend `/api/ingest` |
| `GET` | `/health` | Portal health + configured backend URL |

---

## Docker

```bash
cd admin-portal
docker build -t rag-admin-portal .
docker run -p 8088:8088 \
  -e RAG_BACKEND_URL=http://host.docker.internal:8000 \
  -e DEV_SKIP_OAUTH=true \
  -e ALLOWED_ADMIN_EMAILS=dev@local.test \
  -e DEV_MOCK_EMAIL=dev@local.test \
  -e SESSION_SECRET=local-dev-secret \
  -e PUBLIC_BASE_URL=http://127.0.0.1:8088 \
  rag-admin-portal
```

On Linux Docker, replace `host.docker.internal` with the backend container name or host IP.

---

## Keep in sync with backend

When changing RAG ingest behaviour, update **both** copies in the same commit:

- `admin-portal/app/page_index.py` ↔ `backend-full/app/rag_pipeline/page_index.py`
- `admin-portal/app/slug_file_id.py` ↔ `backend-full/app/rag_pipeline/slug_file_id.py`

Also ensure:

- `FILE_PREFIX` matches on admin portal and backend.
- Any new metadata keys passed by the portal are accepted by `POST /api/ingest`.

If these go out of sync, the admin portal can produce `file_id`s or trees that don't match what the backend's stage-0 filter and graph builder expect.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| OAuth redirect mismatch | `OAUTH_REDIRECT_URI` must exactly match the value configured in Google Cloud Console |
| 403 after Google login | Email is not in `ALLOWED_ADMIN_EMAILS` (case-sensitive, no spaces) |
| 502 on upload | Backend unreachable — check `RAG_BACKEND_URL`, verify backend `/health` |
| OAuth state lost on HTTPS | Set `SESSION_SAME_SITE=none` and ensure `SESSION_COOKIE_SECURE=true` |
| Empty search after upload | Run `POST /api/graph/rebuild` on backend; wait for embedding |
| `Could not extract text` for media | Install ffmpeg, set `TRANSCRIBE_MEDIA=true`, or include a `.txt` sidecar |
| Slow first upload of media | Whisper model downloads on first transcription; subsequent uploads are faster |
| Different `file_id` than expected | Confirm `FILE_PREFIX` matches backend; review `slug_file_id.py` logic in both copies |

---

## Related docs

- [Root README](../README.md) — full platform overview
- [Backend README](../backend-full/app/README.md) — ingest API, graph, search
- [MCP server README](../rag-full/app/README.md) — AI client tools
