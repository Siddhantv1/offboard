"""`data_store.DataStore.add_employee` / `add_exit_case`.

test_employee_case_lookup.py covers the read side (`exit_case_for_employee`)
against the seeded dataset. These are the writes: every row they create is
deleted again in a `finally`, since (unlike the read tests) they mutate the
shared database.
"""
from __future__ import annotations

import uuid

import pytest

from backend.data_store import Doc, store
from backend.db import session_scope
from backend.db.models import Employee, ExitCase, User


def _unique_email() -> str:
    return f"pytest-datastore-{uuid.uuid4().hex[:8]}@example.com"


def _cleanup(emp_id: str | None, email: str | None = None) -> None:
    with session_scope() as s:
        if email:
            s.query(User).filter(User.email == email).delete()
        if emp_id:
            s.query(ExitCase).filter(ExitCase.employee_id == emp_id).delete()
            s.query(Employee).filter(Employee.id == emp_id).delete()


def test_add_employee_requires_name_role_and_department():
    with pytest.raises(ValueError, match="required"):
        store.add_employee({"name": "", "role": "Engineer", "department": "IT"})
    with pytest.raises(ValueError, match="required"):
        store.add_employee({"name": "No Role", "role": "", "department": "IT"})
    with pytest.raises(ValueError, match="required"):
        store.add_employee({"name": "No Dept", "role": "Engineer", "department": ""})


def test_add_employee_active_creates_no_case_and_no_credential():
    email = _unique_email()
    emp_doc, case_doc, credential = store.add_employee({
        "name": "Pytest Active Employee", "role": "Engineer", "department": "Platform",
        "email": email, "status": "active",
    })
    try:
        assert emp_doc.meta["name"] == "Pytest Active Employee"
        assert emp_doc.meta["status"] == "active"
        assert case_doc is None
        assert credential is None

        with session_scope() as s:
            assert s.get(Employee, emp_doc.meta["id"]) is not None
    finally:
        _cleanup(emp_doc.meta["id"], email)


def test_add_employee_exiting_creates_a_starter_case_and_a_credential():
    email = _unique_email()
    emp_doc, case_doc, credential = store.add_employee({
        "name": "Pytest Exiting Employee", "role": "Engineer", "department": "Platform",
        "email": email, "status": "exiting",
    })
    try:
        assert case_doc is not None
        assert case_doc.meta["employee_id"] == emp_doc.meta["id"]
        stages = case_doc.meta["stages"]
        assert len(stages) == 5
        assert all(s["status"] == "pending" for s in stages)

        assert credential is not None
        assert credential["email"] == email
        assert credential["password"]

        with session_scope() as s:
            user = s.query(User).filter(User.email == email).one()
            assert user.role == "employee"
            assert user.employee_id == emp_doc.meta["id"]
            assert user.must_change_password is True
    finally:
        _cleanup(emp_doc.meta["id"], email)


def test_add_employee_exiting_with_an_existing_account_creates_no_duplicate_credential():
    """add_employee only checks whether the email already has *any* account,
    so an HR account (employee_id is legitimately null) is enough to exercise
    the branch without needing a second real employee row."""
    email = _unique_email()
    with session_scope() as s:
        s.add(User(email=email, password_hash="x", role="hr",
                   employee_id=None, full_name="Existing Account",
                   must_change_password=True))

    emp_doc = None
    try:
        emp_doc, case_doc, credential = store.add_employee({
            "name": "Pytest Preexisting Account", "role": "Engineer", "department": "Platform",
            "email": email, "status": "exiting",
        })
        assert case_doc is not None
        assert credential is None

        with session_scope() as s:
            count = s.query(User).filter(User.email == email).count()
        assert count == 1
    finally:
        _cleanup(emp_doc.meta["id"] if emp_doc else None, email)


def test_add_exit_case_rejects_an_unknown_employee():
    with pytest.raises(ValueError, match="Unknown employee"):
        store.add_exit_case(Doc(meta={"id": "no-such-employee"}))


def test_add_exit_case_creates_a_starter_case_for_a_real_employee():
    email = _unique_email()
    emp_doc, case_doc, _credential = store.add_employee({
        "name": "Pytest Case Target", "role": "Engineer", "department": "Platform",
        "email": email, "status": "active",
    })
    assert case_doc is None  # active, so add_employee itself created none

    try:
        new_case = store.add_exit_case(emp_doc, last_working_day="2026-12-31")

        assert new_case.meta["employee_id"] == emp_doc.meta["id"]
        assert str(new_case.meta["last_working_day"]) == "2026-12-31"
        stages = new_case.meta["stages"]
        assert len(stages) == 5
        assert all(s["status"] == "pending" for s in stages)
        assert store.exit_case(new_case.meta["case_id"]) is not None
    finally:
        _cleanup(emp_doc.meta["id"], email)
