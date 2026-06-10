from __future__ import annotations

from config import CHUNK_SIZE, CHUNK_OVERLAP


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks by character count, respecting sentence boundaries."""
    if not text or not text.strip():
        return []

    sentences = _split_sentences(text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        slen = len(sentence)
        if current_len + slen > chunk_size and current:
            chunks.append(" ".join(current))
            # Keep overlap worth of text
            overlap_text = " ".join(current)
            keep: list[str] = []
            keep_len = 0
            for s in reversed(current):
                if keep_len + len(s) > overlap:
                    break
                keep.insert(0, s)
                keep_len += len(s)
            current = keep
            current_len = keep_len

        current.append(sentence)
        current_len += slen

    if current:
        chunks.append(" ".join(current))

    return [c.strip() for c in chunks if c.strip()]


def _split_sentences(text: str) -> list[str]:
    import re
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [p for p in parts if p.strip()]
