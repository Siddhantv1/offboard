"""IT Clearance is a two-sided mark, not paperwork.

It used to be three document uploads: an asset return checklist, a device
handover acknowledgement and a VPN/MFA deactivation confirmation. None of those
is a document anyone actually holds -- the laptop goes back to a desk, and IT
reads the truth out of their own asset system. A leaver could satisfy all three
with a blank PDF, and the stage moved whether or not the hardware came back.

Now the employee attests that they have done their part, and IT verifies
independently. The employee's word alone never closes the clearance; that is
the property most of these tests are about.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend import auth
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import CaseStage, Employee, ExitCase, User
from backend.main import app
from backend.stages import TransitionError, timeline, transition


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def staff():
    suffix = uuid.uuid4().hex[:8]
    spec = {"hr": f"pytest-itc-hr-{suffix}@example.com",
            "it": f"pytest-itc-it-{suffix}@example.com"}
    with session_scope() as s:
        for role, email in spec.items():
            s.add(User(email=email, password_hash=auth.hash_password("x" * 12),
                       role=role, full_name=f"Pytest {role.upper()} {suffix[:4]}"))
    yield spec
    with session_scope() as s:
        s.execute(delete(User).where(User.email.in_(list(spec.values()))))


@pytest.fixture
def leaver():
    """An exiting employee, their case, and the login `add_employee` makes."""
    email = f"pytest-itc-{uuid.uuid4().hex[:8]}@example.com"
    emp, case, _credential = store.add_employee({
        "name": "Pytest Leaver", "role": "Engineer", "department": "Engineering",
        "email": email, "status": "exiting",
    })
    # `add_employee` issues a one-time password with `must_change_password`
    # set, which 403s every other route until it is changed. Settling it here
    # keeps these tests about clearance rather than about first-login flow.
    password = "x" * 12
    with session_scope() as s:
        account = s.scalar(select(User).where(User.email == email))
        account.password_hash = auth.hash_password(password)
        account.must_change_password = False
    yield {"email": email, "case_id": str(case.meta["case_id"]),
           "employee_id": str(emp.meta["id"]), "password": password}
    with session_scope() as s:
        s.execute(delete(User).where(User.email == email))
        s.execute(delete(ExitCase).where(ExitCase.case_id == case.meta["case_id"]))
        s.execute(delete(Employee).where(Employee.id == emp.meta["id"]))


def _login(client, email, password="x" * 12):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = client.cookies.get(auth.CSRF_COOKIE)


def _identity(email, role, employee_id=None):
    with session_scope() as s:
        u = s.scalar(select(User).where(User.email == email))
        return auth.Identity(user_id=u.id, email=u.email, role=role,
                             employee_id=employee_id, full_name=u.full_name,
                             must_change_password=False)


def _stage(case_id, key="it"):
    with session_scope() as s:
        st = s.scalar(select(CaseStage).where(
            CaseStage.case_id == case_id, CaseStage.stage_key == key))
        return st.status, st.employee_confirmed_at, st.employee_confirmed_note


# -- IT cannot close it alone -------------------------------------------------
def test_it_cannot_verify_before_the_employee_has_confirmed(staff, leaver):
    with pytest.raises(TransitionError, match="confirmed their side"):
        transition(case_id=leaver["case_id"], stage_key="it",
                   to_status="completed", actor=_identity(staff["it"], "it"))


def test_it_can_verify_once_the_employee_has_confirmed(staff, leaver):
    from backend.stages import confirm_employee_side
    confirm_employee_side(
        case_id=leaver["case_id"], stage_key="it",
        actor=_identity(leaver["email"], "employee", leaver["employee_id"]),
        note="Laptop and badge handed to reception.")

    transition(case_id=leaver["case_id"], stage_key="it", to_status="completed",
               actor=_identity(staff["it"], "it"))
    status, confirmed_at, note = _stage(leaver["case_id"])
    assert status == "completed"
    assert confirmed_at is not None
    assert "reception" in note


# -- the employee cannot close it alone either --------------------------------
def test_confirming_does_not_complete_the_stage(client, leaver):
    """The whole point of two-sided. If the employee's own click closed their
    IT clearance, this would be the document-upload hole again: a box ticked by
    the one person with a reason to tick it."""
    _login(client, leaver["email"], leaver["password"])
    r = client.post("/api/me/case/stages/it/confirm",
                    json={"note": "Everything returned"})
    assert r.status_code == 200, r.text
    assert r.json()["confirmed"] is True

    status, confirmed_at, _ = _stage(leaver["case_id"])
    assert status != "completed", "the employee closed their own clearance"
    assert confirmed_at is not None


def test_hr_can_clear_it_without_the_employee_but_it_is_recorded_as_an_override(
        staff, leaver):
    """A leaver who has already gone cannot be made to click a button, so HR
    keeps a way through -- but it is an override, not a normal completion, and
    the audit trail has to say so."""
    transition(case_id=leaver["case_id"], stage_key="it", to_status="completed",
               actor=_identity(staff["hr"], "hr"), note="Left without handover.")

    events = timeline(leaver["case_id"])
    assert events[-1]["to"] == "completed"
    assert events[-1]["escalated"] is True, "an unconfirmed clearance was waved through"


# -- shape of the thing -------------------------------------------------------
def test_confirming_is_idempotent_and_keeps_the_first_timestamp(client, leaver):
    _login(client, leaver["email"], leaver["password"])
    first = client.post("/api/me/case/stages/it/confirm", json={"note": "one"})
    assert first.status_code == 200
    again = client.post("/api/me/case/stages/it/confirm", json={"note": "two"})
    assert again.status_code == 200
    # Compared as instants, not strings: the first response serialises the
    # value it just set and the second reads it back through the driver, so the
    # offsets are written differently for the same moment.
    assert (datetime.fromisoformat(again.json()["at"])
            == datetime.fromisoformat(first.json()["at"])),         "a second click reset the clock IT is measured against"


def test_a_stage_with_no_employee_side_cannot_be_confirmed(client, leaver):
    """Finance, HR, KT and sign-off are cleared from records the company
    already holds. There is nothing for the leaver to attest to."""
    _login(client, leaver["email"], leaver["password"])
    r = client.post("/api/me/case/stages/finance/confirm", json={})
    assert r.status_code == 422
    assert "nothing for you to confirm" in r.json()["detail"]


def test_the_confirmation_is_on_the_case_timeline_for_it_to_see(client, staff, leaver):
    _login(client, leaver["email"], leaver["password"])
    client.post("/api/me/case/stages/it/confirm", json={"note": "Left at reception"})

    events = timeline(leaver["case_id"])
    assert any(e["role"] == "employee" and "Confirmed their side" in e["note"]
               for e in events)


def test_it_sees_the_confirmation_on_the_case(client, staff, leaver):
    _login(client, leaver["email"], leaver["password"])
    client.post("/api/me/case/stages/it/confirm", json={"note": "Done"})

    _login(client, staff["it"])
    body = client.get(f"/api/cases/{leaver['case_id']}").json()
    assert body["employee_confirmations"]["it"]["confirmed"] is True
    # Only two-sided stages appear at all.
    assert set(body["employee_confirmations"]) == {"it"}


def test_an_unconfirmed_stage_shows_as_waiting_rather_than_absent(client, staff, leaver):
    _login(client, staff["it"])
    body = client.get(f"/api/cases/{leaver['case_id']}").json()
    assert body["employee_confirmations"]["it"]["confirmed"] is False


def test_a_signed_off_stage_offers_the_employee_nothing_to_do(client, staff, leaver):
    """No dead controls.

    IT can clear the stage from their own records without the employee ever
    confirming -- Aarav Mehta's case-001 is exactly that shape. The page used
    to render the confirm button anyway, and pressing it could only ever
    produce "already signed off". `actionable` is what lets the UI say there is
    nothing to do instead of offering an action that cannot succeed.
    """
    from backend.stages import confirm_employee_side, employee_confirmations

    before = employee_confirmations(leaver["case_id"])["it"]
    assert before["actionable"] is True

    transition(case_id=leaver["case_id"], stage_key="it", to_status="completed",
               actor=_identity(staff["hr"], "hr"), note="Cleared without them.")

    after = employee_confirmations(leaver["case_id"])["it"]
    assert after["actionable"] is False
    assert after["status"] == "completed"

    # And the route agrees, rather than the flag being cosmetic.
    _login(client, leaver["email"], leaver["password"])
    r = client.post("/api/me/case/stages/it/confirm", json={})
    assert r.status_code == 422
    assert "already signed off" in r.json()["detail"]


def test_confirming_stays_available_while_the_stage_is_open(staff, leaver):
    from backend.stages import employee_confirmations

    transition(case_id=leaver["case_id"], stage_key="it", to_status="in_progress",
               actor=_identity(staff["it"], "it"))
    assert employee_confirmations(leaver["case_id"])["it"]["actionable"] is True
