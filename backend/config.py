"""Central configuration.

The LLM layer talks to the Portkey AI gateway. Persistence is PostgreSQL,
reached via `DATABASE_URL`. Values are read from the environment (.env) so no
secret is hard-coded.
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
    # The Bedrock integration this defaulted to now returns
    # "400 bedrock error: Operation not allowed" -- it is no longer permitted
    # on this gateway, so the default points at a provider that works.
    provider: str = os.getenv("PORTKEY_PROVIDER", "@dsvertex")
    model: str = os.getenv("PORTKEY_MODEL", "gemini-2.5-flash")


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


# -- persistence --------------------------------------------------------------
# PostgreSQL is the system of record. There is deliberately no SQLite fallback:
# a silent downgrade to a single-writer engine would hide concurrency bugs
# until production, and the schema uses JSONB and TEXT[] that SQLite cannot
# index anyway.
#
# The URL carries the "+psycopg" dialect tag for SQLAlchemy. Libraries that
# want a plain libpq URI (LangGraph's PostgresSaver) should use PG_URI below
# rather than re-deriving it.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://offboard:offboard@localhost:5432/offboard",
)

# Separate database for the test suite, so a run can truncate freely without
# touching development data. Overridden by TEST_DATABASE_URL in .env.
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    DATABASE_URL.rsplit("/", 1)[0] + "/offboard_test",
)


def libpq_uri(url: str) -> str:
    """SQLAlchemy dialect URL -> plain libpq URI.

    psycopg and LangGraph's PostgresSaver connect with libpq directly and
    reject the "+psycopg" dialect suffix SQLAlchemy requires.
    """
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


PG_URI = libpq_uri(DATABASE_URL)

# Echo every statement to stderr. Off by default: the analysis agents issue
# hundreds of queries and the noise buries everything else.
DB_ECHO = os.getenv("DB_ECHO", "").lower() in ("1", "true", "yes")
