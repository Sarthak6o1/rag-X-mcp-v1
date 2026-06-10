"""
OAuth: cache + integration so Google callback can load OAuth state without relying
on session cookies. Also persists state on disk: uvicorn --reload (or a worker
restart) was clearing the in-memory cache and caused mismatching_state.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from authlib.integrations.starlette_client import OAuth, StarletteIntegration

_RUNTIME_DIR = Path(__file__).resolve().parent.parent / ".runtime"
# Single JSON file; thread lock for sync IO from async (uvicorn is single process default).
DISK_OAUTH_CACHE_PATH = _RUNTIME_DIR / "oauth_state_cache.json"


class InMemoryAsyncOAuthCache:
    """Satisfies Authlib Starlette: async get/set/delete with TTL on set."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> str | None:
        now = time.time()
        async with self._lock:
            row = self._data.get(key)
            if not row:
                return None
            value, exp = row
            if now > exp:
                del self._data[key]
                return None
            return value

    async def set(self, key: str, value: str, expires_in: int = 3600) -> None:
        exp = time.time() + float(expires_in)
        async with self._lock:
            self._data[key] = (value, exp)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._data.pop(key, None)


class DiskAsyncOAuthCache:
    """
    File-backed store so OAuth state survives uvicorn --reload and matches callback.
    JSON: { "key": { "v": "<json string>", "exp": <unix_ts> } }
    """

    def __init__(self, path: Path = DISK_OAUTH_CACHE_PATH) -> None:
        self._path = path
        self._io_lock = threading.Lock()

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            raw = self._path.read_text(encoding="utf-8")
            return json.loads(raw) if raw.strip() else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")

    def _get_sync(self, key: str) -> str | None:
        with self._io_lock:
            data = self._load()
            row = data.get(key)
            if not row:
                return None
            exp = float(row["exp"])
            if time.time() > exp:
                data.pop(key, None)
                self._save(data)
                return None
            return str(row["v"])

    def _set_sync(self, key: str, value: str, expires_in: int) -> None:
        with self._io_lock:
            data = self._load()
            data[key] = {"v": value, "exp": time.time() + float(expires_in)}
            self._save(data)

    def _delete_sync(self, key: str) -> None:
        with self._io_lock:
            data = self._load()
            if data.pop(key, None) is not None:
                self._save(data)

    async def get(self, key: str) -> str | None:
        return await asyncio.to_thread(self._get_sync, key)

    async def set(self, key: str, value: str, expires_in: int = 3600) -> None:
        await asyncio.to_thread(self._set_sync, key, value, expires_in)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._delete_sync, key)


class StarletteIntegrationStateFromCache(StarletteIntegration):
    """
    If session cookies fail on the redirect back from Google, the default
    Starlette integration still requires a session key before reading the cache.
    We accept OAuth state from the in-memory cache when `state` matches.
    """

    async def get_state_data(self, session, state):
        if self.cache:
            key = f"_state_{self.name}_{state}"
            value = await self._get_cache_data(key)
            if value is not None and "data" in value and value.get("data") is not None:
                return value.get("data")
        return await super().get_state_data(session, state)


class RAGAdminOAuth(OAuth):
    framework_integration_cls = StarletteIntegrationStateFromCache
