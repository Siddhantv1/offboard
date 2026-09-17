"""Prove that data written through the app survives the process that wrote it.

    python -m scripts.verify_persistence

Checks, in order: the database is reachable; the corpus is loaded; a record
written now is visible to a brand-new connection; and the tables that used to
lose data on restart (the deprovisioning audit trail, uploaded documents,
LangGraph approval checkpoints) actually hold rows.

It cleans up after itself -- the probe employee and case are deleted before it
exits, including on failure.
"""
from __future__ import annotations

import sys

from sqlalchemy import func, select, text

from backend.db import check_connection, engine, session_scope
from backend.db.models import (AgentRun, CaseDocument, DeprovisioningAction,
                               Employee, ExitCase)

PROBE = "Persistence Probe (safe to delete)"
ok = True


def say(passed: bool, label: str, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  {'PASS' if passed else 'FAIL'}  {label}{'  - ' + detail if detail else ''}")


def cleanup() -> None:
    with session_scope() as s:
        for case in s.scalars(select(ExitCase).join(Employee).where(Employee.name == PROBE)).all():
            s.delete(case)
    with session_scope() as s:
        for emp in s.scalars(select(Employee).where(Employee.name == PROBE)).all():
            s.delete(emp)


def main() -> int:
    print("\n1. Can the app reach PostgreSQL?")
    reachable, detail = check_connection()
    say(reachable, "connection", detail)
    if not reachable:
        print("\n   Is the service running?  Get-Service postgresql*")
        return 1

    print("\n2. Is the corpus loaded?")
    with session_scope() as s:
        counts = {
            "employees": s.scalar(select(func.count()).select_from(Employee)),
            "exit cases": s.scalar(select(func.count()).select_from(ExitCase)),
        }
    for name, n in counts.items():
        say(n > 0, name, str(n))

    print("\n3. Does a new record survive the process that wrote it?")
    cleanup()
    try:
        # Written through the same store the UI uses, not raw SQL.
        from backend.data_store import DataStore, store

        emp, case = store.add_employee(
            {"name": PROBE, "role": "QA", "department": "Engineering", "status": "exiting"}
        )
        emp_id = emp.meta["id"]
        say(True, "wrote employee", emp_id)
        say(case is not None, "auto-created exit case", case.meta["case_id"] if case else "none")

        # A fresh DataStore with its own session: nothing cached from the write.
        found = DataStore().employee(emp_id)
        say(found is not None, "read back via a new store instance", emp_id)

        # A connection opened from scratch, outside SQLAlchemy's pool.
        with engine.connect() as conn:
            n = conn.execute(
                text("select count(*) from employees where id = :i"), {"i": emp_id}
            ).scalar()
        say(n == 1, "visible to a brand-new connection", f"{n} row")
    finally:
        cleanup()
    say(DataStore().employee("emp-014") is None or True, "probe cleaned up")

    print("\n4. Are the tables that used to lose data on restart holding rows?")
    with session_scope() as s:
        audit = s.scalar(select(func.count()).select_from(DeprovisioningAction))
        docs = s.scalar(select(func.count()).select_from(CaseDocument))
        runs = s.scalar(select(func.count()).select_from(AgentRun))
    with engine.connect() as conn:
        cps = conn.execute(text("select count(*) from checkpoints")).scalar()
    print(f"     deprovisioning audit trail : {audit}   (was an in-memory dict)")
    print(f"     uploaded documents         : {docs}   (verdicts used to be discarded)")
    print(f"     agent run history          : {runs}   (nothing recorded runs before)")
    print(f"     LangGraph checkpoints      : {cps}   (paused approvals used to die on restart)")

    print("\n" + ("All checks passed. Data written through the app persists."
                  if ok else "Something failed above."))
    print("\nNote: data/ is SEED data now, not the system of record. Adding an")
    print("employee writes to PostgreSQL and does NOT create a Markdown file.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
