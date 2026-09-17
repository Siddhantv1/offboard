"""Capture the file-backed store's behaviour as a golden snapshot.

Run this ONCE, against the Markdown-backed DataStore, before the PostgreSQL
migration begins. `tests/test_store_contract.py` then asserts that whatever
implementation is in place reproduces this exactly.

That is what makes swapping the storage layer under 15 agents (soon 20) a safe
refactor rather than a hopeful one: the snapshot is an independent record of
what the agents were actually relying on, taken before anything moved.

    python -m tests.capture_golden
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backend.data_store import store

# Every collection the store exposes. `policies` and `process_docs` are
# included even though they stay file-backed: if a future change accidentally
# routes them through the database, the contract test catches it.
COLLECTIONS = ("employees", "exit_cases", "kt_docs", "interviews",
               "policies", "process_docs")

OUT = Path(__file__).parent / "golden" / "store_snapshot.json"


def _encode(value: Any) -> dict[str, str]:
    """YAML frontmatter parses bare ISO dates into `datetime.date` objects, so
    `meta["join_date"]` is a date, not a string. The snapshot has to preserve
    that distinction — a store returning "2021-03-15" where agents expect
    `date(2021, 3, 15)` is a real regression, and a plain `str()` here would
    hide it.

    `date` and `datetime` are tagged separately because `date.fromisoformat`
    silently accepts a full datetime string and truncates the time.
    """
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, date):
        return {"__date__": value.isoformat()}
    raise TypeError(
        f"Unserialisable {type(value).__name__} in store metadata: {value!r}. "
        "Add an encoding for it here rather than stringifying it, or the "
        "contract test will not detect a type change."
    )


def snapshot() -> dict[str, list[dict[str, Any]]]:
    return {
        name: [
            {"slug": d.slug, "title": d.title, "meta": d.meta, "body": d.body}
            for d in getattr(store, name)()
        ]
        for name in COLLECTIONS
    }


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = snapshot()
    OUT.write_text(
        json.dumps(data, indent=2, default=_encode, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {OUT}")
    total = 0
    for name, rows in data.items():
        print(f"  {name}: {len(rows)}")
        total += len(rows)
    if total == 0:
        raise SystemExit(
            "Captured nothing — DATA_DIR is resolving to the wrong place. "
            "Fix that before migrating; an empty snapshot would make the "
            "contract tests pass vacuously."
        )


if __name__ == "__main__":
    main()
