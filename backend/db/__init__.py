"""Database engine and session management.

Sync SQLAlchemy, deliberately. Every agent and the `store` facade
are synchronous, and FastAPI already wraps the blocking routes in
`run_in_threadpool`. Going async here would force rewriting every agent for no
benefit at this concurrency.

Import `session_scope` for a transaction; do not create sessions by hand, or
you will eventually leak a connection from the pool on an error path.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from ..config import DATABASE_URL, DB_ECHO

# The orchestrator fans out to six nodes in parallel, each of which reads
# through the store, and uvicorn may run several workers. The default pool of
# 5 would serialise that; 10 + 20 overflow leaves headroom without letting a
# runaway loop exhaust the server's max_connections (100 by default).
engine = create_engine(
    DATABASE_URL,
    echo=DB_ECHO,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,   # a connection the server dropped is replaced, not raised
    pool_recycle=1800,
    future=True,
)

# expire_on_commit=False: the store returns detached `Doc` objects built from
# ORM rows, and re-fetching every attribute after commit would issue a query
# per field on an already-closed session.
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commits on success, rolls back on any exception.

    Always closes, so the connection returns to the pool even when the caller
    raises.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_connection() -> tuple[bool, str]:
    """Is the database reachable? Returns (ok, detail).

    Used by the health endpoint and the CLI scripts so a misconfigured
    DATABASE_URL produces one clear message instead of a stack trace from
    whichever query happened to run first.
    """
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SHOW server_version")).scalar()
        return True, f"PostgreSQL {version}"
    except Exception as exc:  # noqa: BLE001 - surfaced as a message, not raised
        return False, f"{type(exc).__name__}: {exc}"
