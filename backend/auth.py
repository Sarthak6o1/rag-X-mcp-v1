from __future__ import annotations

from datetime import datetime, timezone

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request

from config import JWT_SECRET


def verify_jwt(request: Request) -> dict | None:
    """Verify JWT Bearer token if JWT_SECRET is configured. Returns claims or None."""
    if not JWT_SECRET:
        return None

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:].strip()
    try:
        claims = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")

    return claims
