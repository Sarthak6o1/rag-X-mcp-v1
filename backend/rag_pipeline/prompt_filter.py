from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable

from rapidfuzz import fuzz


@lru_cache(maxsize=8192)
def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 2}


def rank_file_ids_for_query(
    query: str,
    entries: Iterable[tuple[str, str]],
    *,
    max_documents: int,
    min_fuzzy_score: float = 48.0,
    min_token_overlap: int = 1,
) -> list[str]:
    """
    Stage-0 routing: score each (file_id, search_blob) against the user query.
    search_blob should include file_id, display title, and relative path text.
    """
    query_l = query.strip().lower()
    if not query_l:
        return []

    q_tokens = _tokens(query_l)
    scored: list[tuple[float, str]] = []

    for file_id, blob in entries:
        blob_l = blob.lower()
        partial = float(fuzz.partial_ratio(query_l, blob_l))
        token_sort = float(fuzz.token_set_ratio(query_l, blob_l))
        fuzzy_score = max(partial, token_sort)

        b_tokens = _tokens(blob_l)
        overlap = len(q_tokens & b_tokens) if q_tokens else 0
        hybrid = fuzzy_score + min(30.0, overlap * 6.0)

        if fuzzy_score >= min_fuzzy_score or overlap >= min_token_overlap:
            scored.append((hybrid, file_id))

    scored.sort(key=lambda x: x[0], reverse=True)
    ordered: list[str] = []
    seen: set[str] = set()
    for _s, fid in scored:
        if fid not in seen:
            seen.add(fid)
            ordered.append(fid)
        if len(ordered) >= max_documents:
            break

    if ordered:
        return ordered

    fallback: list[tuple[float, str]] = []
    for file_id, blob in entries:
        blob_l = blob.lower()
        fallback.append((float(fuzz.token_sort_ratio(query_l, blob_l)), file_id))
    fallback.sort(key=lambda x: x[0], reverse=True)
    return [fid for _s, fid in fallback[:max_documents] if fid]
