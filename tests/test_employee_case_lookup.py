"""`exit_case_for_employee` is what `/api/me/case` resolves against: the
employee id in that lookup comes from the caller's own session, never from a
client-supplied case_id, so an employee can only ever land on their own case.
"""
from __future__ import annotations

from backend.data_store import store


def test_returns_the_case_for_a_matching_employee_id():
    case = next(c for c in store.exit_cases())
    emp_id = case.meta["employee_id"]

    found = store.exit_case_for_employee(emp_id)

    assert found is not None
    assert found.meta["employee_id"] == emp_id


def test_returns_none_for_an_employee_with_no_case():
    exiting_ids = {c.meta["employee_id"] for c in store.exit_cases()}
    employee = next(e for e in store.employees() if e.meta["id"] not in exiting_ids)

    assert store.exit_case_for_employee(employee.meta["id"]) is None


def test_returns_none_for_an_unknown_employee_id():
    assert store.exit_case_for_employee("emp-does-not-exist") is None


def test_returns_none_for_an_empty_employee_id():
    assert store.exit_case_for_employee("") is None
    assert store.exit_case_for_employee("   ") is None
