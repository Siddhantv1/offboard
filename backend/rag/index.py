"""Ingestion pipeline: (re)build the vector index from the doc corpus.

Sources = process docs + policies (which include the FAQ source and the
documents-required list). Idempotent: resets the collection and rebuilds.

Run:  python -m backend.rag.index
"""
from __future__ import annotations

from ..data_store import store as data_store
from .chunker import chunk_doc
from .store import VectorStore


def build(verbose: bool = True) -> int:
    docs = data_store.process_docs() + data_store.policies()
    chunks = [c for doc in docs for c in chunk_doc(doc)]

    vs = VectorStore()
    vs.reset()
    added = vs.add_chunks(chunks)

    if verbose:
        print(f"Indexed {added} chunks from {len(docs)} documents "
              f"into collection '{vs._collection().name}'.")
        by_source: dict[str, int] = {}
        for c in chunks:
            by_source[c.source] = by_source.get(c.source, 0) + 1
        for src, n in sorted(by_source.items()):
            print(f"  {n:3d}  {src}")
    return added


if __name__ == "__main__":
    build()
