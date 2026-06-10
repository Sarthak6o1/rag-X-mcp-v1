import os
from pathlib import Path

from dotenv import load_dotenv

# Local dev: load admin-portal/.env. In Cloud Run, use only injected env/Secret Manager (no .env in image).
_BASE = Path(__file__).resolve().parent.parent
_env_file = _BASE / ".env"
if _env_file.is_file():
    load_dotenv(_env_file, override=True)
else:
    load_dotenv(override=False)

RAG_BACKEND_URL = os.getenv("RAG_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8088").rstrip("/")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "").strip()

SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-change-me-use-long-random-in-production")

# When PUBLIC_BASE_URL is https (e.g. ngrok), use Secure session cookies so the OAuth
# state survives the Google redirect. Set SESSION_COOKIE_SECURE=false for plain http:// local dev.
_raw_cookie_secure = os.getenv("SESSION_COOKIE_SECURE", "").strip().lower()
if _raw_cookie_secure in ("1", "true", "yes"):
    SESSION_COOKIE_SECURE = True
elif _raw_cookie_secure in ("0", "false", "no"):
    SESSION_COOKIE_SECURE = False
else:
    SESSION_COOKIE_SECURE = PUBLIC_BASE_URL.lower().startswith("https://")

# Lax is fine for same-origin dev. For ngrok/HTTPS, "none" + Secure often fixes OAuth
# state (CSRF) after the Google redirect. Override with SESSION_SAME_SITE=lax if needed.
_raw_same_site = os.getenv("SESSION_SAME_SITE", "").strip().lower()
if _raw_same_site in ("lax", "strict", "none"):
    SESSION_SAME_SITE = _raw_same_site
else:
    SESSION_SAME_SITE = "none" if SESSION_COOKIE_SECURE else "lax"

DEV_MOCK_EMAIL = os.getenv("DEV_MOCK_EMAIL", "dev@local.test").strip().lower()

_raw_emails = os.getenv("ALLOWED_ADMIN_EMAILS", "")
ALLOWED_ADMIN_EMAILS = {
    e.strip().lower() for e in _raw_emails.split(",") if e.strip()
}

DEV_SKIP_OAUTH = os.getenv("DEV_SKIP_OAUTH", "false").lower() in ("1", "true", "yes")
# Set false in production (e.g. Cloud Run) once OAuth is stable.
ALLOW_OAUTH_STATE_BYPASS = os.getenv("ALLOW_OAUTH_STATE_BYPASS", "false").lower() in (
    "1",
    "true",
    "yes",
)

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "600"))

# Must match the RAG backend’s FILE_PREFIX (default `kb-`) so file_id slugs align.
FILE_PREFIX = os.getenv("FILE_PREFIX", "kb-")

# When no sidecar .txt exists next to video/audio, transcribe with faster-whisper (needs ffmpeg on PATH).
TRANSCRIBE_MEDIA = os.getenv("TRANSCRIBE_MEDIA", "true").lower() in ("1", "true", "yes")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base").strip() or "base"
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu").strip() or "cpu"
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8").strip() or "int8"
