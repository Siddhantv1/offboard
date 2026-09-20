"""Gateway / config sanity check."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..agents import REGISTRY
from ..auth import StaffUser
from ..config import PORTKEY
from ..data_store import store
from ..rag import retriever as rag

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health(user: StaffUser) -> dict[str, Any]:
    # Staff, not HR-only: the sidebar shows a gateway indicator to everyone who
    # works cases, and "is the model reachable" is operational info an IT
    # engineer legitimately needs. Still closed to employees, who were
    # deliberately scoped out of the platform's internals.
    return {
        "status": "ok",
        "gateway": PORTKEY.base_url,
        "provider": PORTKEY.provider,
        "model": PORTKEY.model,
        "agents": len(REGISTRY),
        "employees": len(store.employees()),
        "exit_cases": len(store.exit_cases()),
        "rag": rag.stats(),
    }
