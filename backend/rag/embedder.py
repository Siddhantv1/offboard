"""Embeddings via Portkey's Azure OpenAI integration.

The AWS Bedrock integration used for chat generation can't do embeddings
("embed is not supported by bedrock"), but Portkey also has an Azure OpenAI
integration registered, which can. So embeddings go through the same gateway
as generation now, just routed to a different Portkey provider by prefixing
the model id with its integration slug (`@azure-openai/...`) -- no local
embedding model, no separate client configuration needed.
"""
from __future__ import annotations

from functools import lru_cache

from portkey_ai import Portkey

from ..config import PORTKEY, RAG


@lru_cache(maxsize=1)
def _client() -> Portkey:
    # No `provider=` here (unlike the chat client in llm.py) -- the model id's
    # `@azure-openai/...` prefix already tells Portkey which integration to use.
    return Portkey(base_url=PORTKEY.base_url, api_key=PORTKEY.api_key)


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts into unit-comparable float vectors."""
    if not texts:
        return []
    resp = _client().embeddings.create(
        input=texts, model=RAG.embed_model, encoding_format="float"
    )
    return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


def embed_one(text: str) -> list[float]:
    return embed([text])[0]
