"""Hybrid retriever: semantic (Portkey embeddings + Chroma) + lexical (BM25), fused
with Reciprocal Rank Fusion.

Semantic search catches paraphrases ("when do I get my money" -> final
settlement); BM25 catches exact terms/IDs. RRF merges the two ranked lists
without needing to calibrate their incomparable score scales.

Public surface mirrors the old keyword retriever so the agent is a drop-in swap:
`retrieve(query, k) -> list[Passage]` with `.source`, `.heading`, `.text`.
"""
from __future__ import annotations

from dataclasses import dataclass

import bm25s

from ..config import RAG
from . import embedder, index
from .store import Hit, VectorStore

RRF_K = 60  # standard RRF damping constant


@dataclass
class Passage:
    source: str
    heading: str
    text: str
    score: float = 0.0


class _HybridIndex:
    """Lazily-loaded store + in-memory BM25 built from the stored chunks."""

    def __init__(self) -> None:
        self._vs: VectorStore | None = None
        self._docs: list[Hit] = []
        self._bm25: bm25s.BM25 | None = None

    def ensure(self) -> None:
        if self._vs is None:
            self._vs = VectorStore()
        if self._vs.count() == 0:
            index.build(verbose=False)
        if self._bm25 is None or not self._docs:
            self._load_lexical()

    def _load_lexical(self) -> None:
        assert self._vs is not None
        self._docs = self._vs.all_documents()
        corpus = [d.text for d in self._docs]
        if not corpus:
            self._bm25 = None
            return
        tokens = bm25s.tokenize(corpus, stopwords="en", show_progress=False)
        bm = bm25s.BM25()
        bm.index(tokens, show_progress=False)
        self._bm25 = bm

    def reload(self) -> None:
        """Force a rebuild of the in-memory lexical index (after re-indexing)."""
        self._bm25 = None
        self._docs = []
        self.ensure()

    def search(self, query: str, k: int) -> list[Passage]:
        self.ensure()
        assert self._vs is not None
        cand = RAG.candidates

        # semantic ranked list (ascending distance == descending relevance)
        semantic = self._vs.query(embedder.embed_one(query), cand)

        # lexical ranked list -> indices into self._docs
        lexical: list[Hit] = []
        if self._bm25 is not None:
            q_tokens = bm25s.tokenize(query, stopwords="en", show_progress=False)
            n = min(cand, len(self._docs))
            idxs, _scores = self._bm25.retrieve(q_tokens, k=n, show_progress=False)
            lexical = [self._docs[i] for i in idxs[0]]

        # Reciprocal Rank Fusion
        fused: dict[str, float] = {}
        by_id: dict[str, Hit] = {}
        for rank, hit in enumerate(semantic):
            fused[hit.id] = fused.get(hit.id, 0.0) + 1.0 / (RRF_K + rank)
            by_id[hit.id] = hit
        for rank, hit in enumerate(lexical):
            fused[hit.id] = fused.get(hit.id, 0.0) + 1.0 / (RRF_K + rank)
            by_id.setdefault(hit.id, hit)

        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:k]
        return [
            Passage(source=by_id[i].source, heading=by_id[i].heading,
                    text=by_id[i].text, score=round(s, 5))
            for i, s in ranked
        ]

    def stats(self) -> dict:
        vs = self._vs or VectorStore()
        return {
            "indexed_chunks": vs.count(),
            "embedder": RAG.embed_model,
            "vector_db": "chromadb",
        }


_INDEX = _HybridIndex()


def retrieve(query: str, k: int = 5) -> list[Passage]:
    if not query or not query.strip():
        return []
    return _INDEX.search(query.strip(), k)


def ensure_index() -> None:
    """Build the index if empty; called at server startup so first query is fast."""
    _INDEX.ensure()


def stats() -> dict:
    return _INDEX.stats()
