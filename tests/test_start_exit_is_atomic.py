"""Marking somebody as leaving, twice at once.

`PATCH /api/employees/{id}` did this as four transactions with a check-then-act
across them: read "does a case exist", create the case, issue the login, set
the status. Two HR tabs both read None and both created a case -- `_next_id`'s
advisory lock only guarantees they get *different* ids, and there is no unique
constraint on `exit_cases.employee_id` to catch it. One person then has two
live exit cases, both with five pending stages, both in the queues, and
`exit_case_for_employee` picking between them arbitrarily.

`add_employee` shares one transaction for exactly this reason and says so; this
is the same situation reached by the other door.
"""
from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import delete, select

from backend.data_store import store
from backend.db import session_scope
from backend.db.models import Employee, ExitCase, User


@pytest.fixture
def active_employee():
    suffix = uuid.uuid4().hex[:8]
    emp, case, _ = store.add_employee({
        "name": "Pytest Not Leaving Yet", "role": "Engineer",
        "department": "Engineering", "email": f"pytest-se-{suffix}@example.com",
        "status": "active",
    })
    assert case is None, "an active employee starts with no exit case"
    emp_id = str(emp.meta["id"])
    yield emp_id
    with session_scope() as s:
        s.execute(delete(User).where(User.employee_id == emp_id))
        s.execute(delete(ExitCase).where(ExitCase.employee_id == emp_id))
        s.execute(delete(Employee).where(Employee.id == emp_id))


def _cases_for(emp_id: str) -> list[str]:
    with session_scope() as s:
        return [c.case_id for c in s.scalars(
            select(ExitCase).where(ExitCase.employee_id == emp_id)).all()]


def test_one_person_gets_one_exit_case(active_employee):
    results: list = [None, None]
    start = threading.Barrier(2)

    def go(index):
        start.wait(timeout=5)
        try:
            results[index] = store.start_exit(active_employee)
        except Exception as exc:  # noqa: BLE001
            results[index] = exc

    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    cases = _cases_for(active_employee)
    assert len(cases) == 1, f"two tabs produced {cases}"

    created = [r for r in results if isinstance(r, tuple) and r[0] is not None]
    assert len(created) == 1, "exactly one caller should report creating it"


def test_the_status_and_the_case_land_together(active_employee):
    """A crash between the second and fourth transaction used to leave
    somebody `active` with a live exit case -- the half-created record
    `add_employee` refuses to produce."""
    created, credential = store.start_exit(active_employee)

    assert created is not None
    assert credential is not None, "a leaver needs a way in"
    assert store.employee(active_employee).meta["status"] == "exiting"
    assert len(_cases_for(active_employee)) == 1


def test_marking_an_already_exiting_person_creates_nothing_new(active_employee):
    store.start_exit(active_employee)
    created, credential = store.start_exit(active_employee)

    assert created is None, "the case already existed"
    assert credential is None, "and so did the login"
    assert len(_cases_for(active_employee)) == 1


def test_an_unknown_employee_is_refused():
    with pytest.raises(ValueError):
        store.start_exit("emp-does-not-exist")
