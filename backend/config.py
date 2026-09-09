"""Central configuration.

The LLM layer talks to the Portkey AI gateway (Bedrock provider), mirroring the
Portkey access configured in Claude's settings.json. Values are read from the
environment (.env) so no secret is hard-coded, but sensible defaults are provided
so the app runs out of the box in this demo.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (parent of backend/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
FRONTEND_DIR = PROJECT_ROOT / "frontend"


class PortkeySettings:
    """Portkey gateway settings for the LLM layer."""

    base_url: str = os.getenv(
        "PORTKEY_BASE_URL", "https://portkeygateway.perficient.com/v1"
    )
    api_key: str = os.getenv("PORTKEY_API_KEY")
    provider: str = os.getenv("PORTKEY_PROVIDER", "@aws-bedrock-use2")
    model: str = os.getenv("PORTKEY_MODEL", "us.anthropic.claude-sonnet-5")


PORTKEY = PortkeySettings()


class RAGSettings:
    """FAQ RAG pipeline: Portkey-hosted embeddings + persistent Chroma vector DB.

    The AWS Bedrock integration used for chat can't do embeddings, but Portkey's
    Azure OpenAI integration can -- so embeddings go through the same gateway as
    generation, just via a different Portkey-registered provider.
    """

    chroma_dir: Path = Path(os.getenv("CHROMA_DIR", str(DATA_DIR / ".chroma")))
    collection: str = os.getenv("RAG_COLLECTION", "exit_docs")
    embed_model: str = os.getenv("EMBED_MODEL", "@azure-openai/text-embedding-3-small")
    chunk_chars: int = int(os.getenv("RAG_CHUNK_CHARS", "750"))
    chunk_overlap: int = int(os.getenv("RAG_CHUNK_OVERLAP", "110"))
    top_k: int = int(os.getenv("RAG_TOP_K", "5"))
    # candidates pulled from each retriever before RRF fusion
    candidates: int = int(os.getenv("RAG_CANDIDATES", "12"))


RAG = RAGSettings()

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
