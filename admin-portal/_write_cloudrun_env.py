"""Emit YAML for `gcloud run deploy --env-vars-file` from admin-portal/.env (production keys only)."""
from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> None:
    admin = Path(__file__).resolve().parent
    vals: dict[str, str] = {}
    for line in (admin / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()

    keys = [
        "RAG_BACKEND_URL",
        "PUBLIC_BASE_URL",
        "OAUTH_REDIRECT_URI",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "ALLOWED_ADMIN_EMAILS",
        "SESSION_SECRET",
        "ALLOW_OAUTH_STATE_BYPASS",
        "FILE_PREFIX",
    ]
    lines = []
    for k in keys:
        if k not in vals:
            raise KeyError(f"Missing required key in .env: {k}")
        lines.append(f"{k}: {json.dumps(vals[k])}")

    out = Path(os.environ["TEMP"]) / "rag-admin-cloudrun-env.yaml"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
