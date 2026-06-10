from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import config
from app.extract_text import SUPPORTED_LABEL, UPLOAD_ACCEPT, extract_text
from app.oauth_setup import DiskAsyncOAuthCache, RAGAdminOAuth
from app.page_index import build_document_tree
from app.slug_file_id import build_semantic_file_id

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="RAG Admin Portal")
# SameSite=None requires Secure; needed so OAuth state survives Google → ngrok callback.
_session_cookie_secure = config.SESSION_COOKIE_SECURE or (
    config.SESSION_SAME_SITE == "none"
)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    same_site=config.SESSION_SAME_SITE,
    https_only=_session_cookie_secure,
)

oauth = RAGAdminOAuth(cache=DiskAsyncOAuthCache())
if config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET:
    oauth.register(
        name="google",
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def _strip_nul(value: str) -> str:
    return value.replace("\x00", "")


def _normalize_relative_path(relative_path: str, filename: str) -> str:
    """
    Build a single relative POSIX path under a synthetic ingest root, matching how
    the backend’s bulk pipeline names documents. Drops ``.`` and ``..`` segments.
    """
    name = Path(filename or "file").name
    raw = (relative_path or "").strip().replace("\\", "/")
    if not raw:
        return f"admin-upload/{name}"
    parts = [p for p in raw.split("/") if p and p not in (".", "..")]
    if not parts:
        return f"admin-upload/{name}"
    rel = "/".join(parts)
    if rel.endswith("/") or (Path(rel).suffix == "" and not rel.endswith(name)):
        rel = f"{rel.rstrip('/')}/{name}"
    return rel


def _session_email(request: Request) -> str | None:
    return request.session.get("email")


def _is_allowed_email(email: str | None) -> bool:
    if not email:
        return False
    return email.lower() in config.ALLOWED_ADMIN_EMAILS


def _index_ctx() -> dict:
    return {
        "backend_url": config.RAG_BACKEND_URL,
        "supported_label": SUPPORTED_LABEL,
        "file_prefix": config.FILE_PREFIX,
        "upload_accept": UPLOAD_ACCEPT,
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if config.DEV_SKIP_OAUTH:
        request.session["email"] = config.DEV_MOCK_EMAIL
        dev_email = request.session["email"]
        allowed = _is_allowed_email(dev_email)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "email": dev_email,
                "allowed": allowed,
                "dev_mode": True,
                "login_url": None,
                **_index_ctx(),
            },
        )

    email = _session_email(request)
    allowed = _is_allowed_email(email)
    login_url = "/login" if config.GOOGLE_CLIENT_ID else None
    if email and not allowed:
        request.session.clear()
        raise HTTPException(403, "Your account is not allowed for this admin portal.")
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "email": email,
            "allowed": allowed,
            "dev_mode": False,
            "login_url": login_url,
            **_index_ctx(),
        },
    )


@app.get("/login")
async def login(request: Request):
    if config.DEV_SKIP_OAUTH:
        return RedirectResponse("/", status_code=302)
    if not config.GOOGLE_CLIENT_ID:
        raise HTTPException(503, "Google OAuth not configured (set GOOGLE_CLIENT_ID).")
    redirect_uri = config.OAUTH_REDIRECT_URI or f"{config.PUBLIC_BASE_URL}/auth/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    if config.DEV_SKIP_OAUTH:
        return RedirectResponse("/", status_code=302)
    token = None
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as exc:  # noqa: BLE001
        # In ngrok/local dev, state storage may be lost due to reload/browser policies.
        # Allow direct code exchange as a controlled fallback.
        code = request.query_params.get("code")
        redirect_uri = config.OAUTH_REDIRECT_URI or f"{config.PUBLIC_BASE_URL}/auth/callback"
        should_bypass = (
            config.ALLOW_OAUTH_STATE_BYPASS
            and code
            and "mismatching_state" in str(exc)
        )
        if should_bypass:
            token = await oauth.google.fetch_access_token(
                code=code,
                redirect_uri=redirect_uri,
                grant_type="authorization_code",
            )
        else:
            raise HTTPException(400, f"OAuth failed: {exc}") from exc

    email = None
    if isinstance(token.get("userinfo"), dict):
        email = token["userinfo"].get("email")
    if not email and token.get("id_token"):
        try:
            userinfo = await oauth.google.parse_id_token(request, token)
            if isinstance(userinfo, dict):
                email = userinfo.get("email")
        except Exception:
            pass
    if not email:
        access = token.get("access_token")
        if access:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {access}"},
                    timeout=30.0,
                )
                if r.is_success:
                    email = r.json().get("email")

    if not email:
        raise HTTPException(400, "Could not read email from Google account.")

    email = email.lower()
    if not _is_allowed_email(email):
        request.session.clear()
        raise HTTPException(403, "Your account is not allowed for this admin portal.")

    request.session["email"] = email
    return RedirectResponse("/", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)


@app.post("/upload")
async def upload(
    request: Request,
    file: UploadFile = File(...),
    display_title: str = Form(""),
    relative_path: str = Form(""),
):
    if not config.DEV_SKIP_OAUTH:
        email = _session_email(request)
        if not email:
            raise HTTPException(401, "Not logged in")
        if not _is_allowed_email(email):
            raise HTTPException(403, "Your email is not allowed to upload.")
    else:
        email = request.session.get("email") or config.DEV_MOCK_EMAIL
        if not _is_allowed_email(email):
            raise HTTPException(
                403,
                "Email not in ALLOWED_ADMIN_EMAILS. For dev, set DEV_MOCK_EMAIL to match an allowlisted email or expand the list.",
            )

    filename = file.filename or "upload"
    raw = await file.read()
    rel = _normalize_relative_path(relative_path, filename)

    with tempfile.TemporaryDirectory(prefix="rag_admin_") as tmp_root_str:
        tmp_root = Path(tmp_root_str)
        target = tmp_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        file_id = build_semantic_file_id(config.FILE_PREFIX, tmp_root, target)
        tree = build_document_tree(target)
        text, err = extract_text(raw, filename, work_path=target)
    if err:
        raise HTTPException(400, err)
    text = _strip_nul(text)
    if not text.strip():
        raise HTTPException(400, "No usable text after extraction.")

    display = display_title.strip() or Path(filename).stem

    payload = {
        "file_id": file_id,
        "content": text,
        "metadata": {"source": "admin-portal", "uploaded_by": email},
        "display_title": display,
        "relative_path": rel,
        "tree": tree,
    }

    try:
        async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
            r = await client.post(
                f"{config.RAG_BACKEND_URL}/api/ingest",
                json=payload,
            )
    except httpx.RequestError as exc:
        raise HTTPException(502, f"Backend unreachable: {exc}") from exc

    if not r.is_success:
        try:
            detail = r.json()
        except Exception:
            detail = r.text[:2000]
        raise HTTPException(r.status_code, f"Ingest failed: {detail}")

    data = r.json()
    return {
        "ok": True,
        "file_id": data.get("file_id", file_id),
        "chunks_stored": data.get("chunks_stored"),
        "message": "Your file was added. It may take a moment to show up in search.",
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "rag-admin-portal", "backend": config.RAG_BACKEND_URL}
