"""Local embeddings via Model2Vec static embeddings.

Static embeddings = precomputed token vectors averaged per text: no PyTorch, no
transformer inference, tiny footprint (~30 MB model). The model downloads from
Hugging Face on first use and is cached; set MODEL2VEC_PATH to a local dir if HF
is unreachable. Generation still happens on the Portkey gateway — only this step
is local.
"""
from __future__ import annotations

from functools import lru_cache

from ..config import RAG


@lru_cache(maxsize=1)
def _model():
    from model2vec import StaticModel  # imported lazily so app import stays cheap

    return StaticModel.from_pretrained(RAG.embed_model)


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts into unit-comparable float vectors."""
    if not texts:
        return []
    vectors = _model().encode(texts)
    return [v.tolist() for v in vectors]


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def dimension() -> int:
    return int(_model().dim)
