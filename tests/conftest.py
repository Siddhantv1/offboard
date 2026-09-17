"""Shared test fixtures.

The golden-snapshot fixtures here support `test_store_contract.py`, which is
the PostgreSQL migration's safety net. Database fixtures are added in
`Task 2`; until then this file is deliberately dependency-free so the contract
suite can run against the original file-backed store with nothing installed.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

GOLDEN = Path(__file__).parent / "golden" / "store_snapshot.json"


def _decode(obj: dict[str, Any]) -> Any:
    """Inverse of `capture_golden._encode`.

    Only a single-key dict carrying the exact marker is converted, so a real
    metadata field that happens to be called `__date__` is left alone.
    """
    if len(obj) == 1:
        if "__date__" in obj:
            return date.fromisoformat(obj["__date__"])
        if "__datetime__" in obj:
            return datetime.fromisoformat(obj["__datetime__"])
    return obj


@pytest.fixture(scope="session")
def golden() -> dict[str, list[dict[str, Any]]]:
    if not GOLDEN.exists():
        pytest.fail(
            f"Golden snapshot missing at {GOLDEN}. Run `python -m tests.capture_golden` "
            "against the file-backed store BEFORE migrating — it cannot be "
            "regenerated afterwards."
        )
    return json.loads(GOLDEN.read_text(encoding="utf-8"), object_hook=_decode)
