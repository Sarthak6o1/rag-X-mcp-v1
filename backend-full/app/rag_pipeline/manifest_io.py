from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_manifest_documents(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    docs = data.get("documents")
    if not isinstance(docs, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in docs:
        if isinstance(item, dict) and isinstance(item.get("file_id"), str):
            out[item["file_id"]] = item
    return out


def merge_manifest_entries(path: Path, new_entries: list[dict[str, Any]], *, version: int = 1) -> None:
    existing = load_manifest_documents(path)
    for entry in new_entries:
        fid = entry.get("file_id")
        if isinstance(fid, str):
            existing[fid] = entry
    payload = {
        "version": version,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "documents": sorted(existing.values(), key=lambda x: str(x.get("file_id", ""))),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
