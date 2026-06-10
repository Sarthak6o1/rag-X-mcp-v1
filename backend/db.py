from __future__ import annotations

from graph_store import ensure_graph_store
from vector_store import ensure_store, ping


def ensure_tables() -> None:
    """Compatibility shim for local storage startup."""
    ensure_graph_store()
    ensure_store()


def get_connection():
    raise RuntimeError("Postgres connections are no longer used; use local storage helpers instead.")
