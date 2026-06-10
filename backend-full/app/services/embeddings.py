from __future__ import annotations

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from config import EMBEDDINGS_MODEL

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDINGS_MODEL)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = get_model()
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


@lru_cache(maxsize=256)
def _embed_query_cached(query: str) -> tuple[float, ...]:
    return tuple(embed_texts([query])[0])


def embed_query(query: str) -> list[float]:
    return list(_embed_query_cached(query))
