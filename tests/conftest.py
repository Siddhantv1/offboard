"""Shared test fixtures.

Two things happen here, and the order matters.

**First**, before anything imports `backend`, `DATABASE_URL` is redirected at
`TEST_DATABASE_URL`. `backend.db` builds its engine at import time from
`DATABASE_URL`, so this is the only moment the redirect can take effect —
which is why it sits above the `backend` imports rather than in a fixture.
Until this existed the suite ran against whatever database the developer
happened to be pointing at, and a crashed test left rows in it.

**Second**, the golden-snapshot fixture supports `test_store_contract.py`,
which was the PostgreSQL migration's safety net and still pins the store's
public shape.

The test database must be migrated *and seeded* — most of the suite reads
real records (`test_store_contract.py` compares six seeded exit cases against
the snapshot, and `test_api.py` skips when no seeded employee has a case). An
empty-but-migrated database turns the suite green-by-skipping, which is worse
than failing, so `_require_seeded_database()` refuses to run against one.

    python -m scripts.init_database          # once, creates offboard_test
    DATABASE_URL=$TEST_DATABASE_URL python -m alembic upgrade head
    DATABASE_URL=$TEST_DATABASE_URL python -m backend.db.seed
"""
from __future__ import annotations

import os
from pathlib import Path

# -- redirect the engine BEFORE `backend` is imported anywhere ----------------
# load_dotenv does not override variables already present in the environment,
# so an explicit DATABASE_URL in the shell still wins over .env -- and the
# assignment below wins over both, for this process only.
from dotenv import load_dotenv  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

_DEV_URL = os.environ.get("DATABASE_URL", "")
_TEST_URL = os.environ.get("TEST_DATABASE_URL") or (
    _DEV_URL.rsplit("/", 1)[0] + "/offboard_test" if _DEV_URL else ""
)
if not _TEST_URL:
    raise RuntimeError(
        "Neither TEST_DATABASE_URL nor DATABASE_URL is set, so the suite has no "
        "database to run against. Set them in .env."
    )
os.environ["DATABASE_URL"] = _TEST_URL

import json  # noqa: E402
from datetime import date, datetime  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from backend.db import session_scope  # noqa: E402
from backend.db.models import Employee, ExitCase  # noqa: E402

GOLDEN = Path(__file__).parent / "golden" / "store_snapshot.json"


def _require_seeded_database() -> None:
    """Refuse to run against an empty database.

    Without this the suite does not fail — it *skips*, because the fixtures
    that need a real case call `pytest.skip` when they cannot find one. A run
    that reports "208 passed" against a database with no rows in it is the
    worst possible outcome, so this turns that silence into one clear error.
    """
    with session_scope() as s:
        employees = s.scalar(select(func.count()).select_from(Employee)) or 0
        cases = s.scalar(select(func.count()).select_from(ExitCase)) or 0
    if employees and cases:
        return
    raise RuntimeError(
        f"The test database ({_TEST_URL.rsplit('@', 1)[-1]}) has "
        f"{employees} employees and {cases} exit cases — it has not been seeded.\n"
        "Run:\n"
        "  DATABASE_URL=$TEST_DATABASE_URL python -m alembic upgrade head\n"
        "  DATABASE_URL=$TEST_DATABASE_URL python -m backend.db.seed"
    )


def pytest_configure(config: pytest.Config) -> None:
    # A session fixture would run too late for tests that touch the database
    # during collection, and would be skippable. This is neither.
    _require_seeded_database()


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
