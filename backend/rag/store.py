"""Persistent vector store backed by ChromaDB.

A thin wrapper over a Chroma PersistentClient collection. We always supply our own
Model2Vec embeddings (add/query with explicit vectors), so Chroma's built-in ONNX
embedder is never loaded. Cosine space; persisted on disk under CHROMA_DIR.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import chromadb

from ..config import RAG
from .chunker import Chunk
from . import embedder


@dataclass
class Hit:
    id: str
    text: str
    source: str
    heading: str
    distance: float


class VectorStore:
    def __init__(self) -> None:
        RAG.chroma_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(RAG.chroma_dir))

    def _collection(self):
        # embedding_function=None: we pass vectors explicitly, never let Chroma embed.
        return self._client.get_or_create_collection(
            name=RAG.collection,
            embedding_function=None,
            metadata={"hnsw:space": "cosine"},
        )

    def count(self) -> int:
        try:
            return self._collection().count()
        except Exception:  # noqa: BLE001 - treat any access issue as "empty"
            return 0

    def reset(self) -> None:
        try:
            self._client.delete_collection(RAG.collection)
        except Exception:  # noqa: BLE001 - fine if it doesn't exist yet
            pass

    def add_chunks(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        vectors = embedder.embed([c.text for c in chunks])
        self._collection().add(
            ids=[c.id for c in chunks],
            documents=[c.text for c in chunks],
            embeddings=vectors,
            metadatas=[{"source": c.source, "heading": c.heading} for c in chunks],
        )
        return len(chunks)

    def all_documents(self) -> list[Hit]:
        """Every stored chunk (used to build the in-memory BM25 index)."""
        res = self._collection().get(include=["documents", "metadatas"])
        return [
            Hit(id=i, text=d, source=(m or {}).get("source", ""),
                heading=(m or {}).get("heading", ""), distance=0.0)
            for i, d, m in zip(res["ids"], res["documents"], res["metadatas"])
        ]

    def query(self, vector: list[float], n: int) -> list[Hit]:
        res = self._collection().query(
            query_embeddings=[vector],
            n_results=n,
            include=["documents", "metadatas", "distances"],
        )
        hits: list[Hit] = []
        for i, d, m, dist in zip(
            res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]
        ):
            meta: dict[str, Any] = m or {}
            hits.append(Hit(id=i, text=d, source=meta.get("source", ""),
                            heading=meta.get("heading", ""), distance=float(dist)))
        return hits
