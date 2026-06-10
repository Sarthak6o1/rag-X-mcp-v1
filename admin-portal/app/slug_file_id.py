"""Mirror of ``backend/rag_pipeline/slug_file_id.py`` — keep in sync manually (admin-only copy)."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def slugify_segment(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "x"


def build_semantic_file_id(
    prefix: str,
    root: Path,
    path: Path,
    *,
    max_total_length: int = 200,
    used_ids: set[str] | None = None,
) -> str:
    """
    Human-readable file_id from folder + filename (PageIndex-friendly routing).
    Collisions append a short hash; overlong paths truncate with stable digest suffix.
    """
    rel = path.resolve().relative_to(root.resolve()).as_posix()
    parts = rel.split("/")
    segments: list[str] = []
    for i, part in enumerate(parts):
        if i == len(parts) - 1:
            stem = Path(part).stem
            segments.append(slugify_segment(stem))
            ext = Path(part).suffix.lower().lstrip(".")
            if ext:
                segments.append(slugify_segment(ext))
        else:
            segments.append(slugify_segment(part))

    body = "--".join(s for s in segments if s)
    if not body:
        body = "document"

    digest8 = hashlib.sha256(rel.encode("utf-8")).hexdigest()[:8]
    full = f"{prefix}{body}"
    if len(full) > max_total_length:
        keep = max_total_length - len(prefix) - 3 - 8
        if keep < 8:
            keep = 8
        body = body[-keep:] if len(body) > keep else body
        full = f"{prefix}{body}--h{digest8}"

    if used_ids is not None:
        candidate = full
        suffix_n = 0
        while candidate in used_ids:
            suffix_n += 1
            suf = hashlib.sha256(f"{rel}:{suffix_n}".encode("utf-8")).hexdigest()[:6]
            candidate = f"{full}--x{suf}"[:max_total_length]
        used_ids.add(candidate)
        return candidate

    return full
