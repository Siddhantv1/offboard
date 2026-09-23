"""The manager's handover workspace.

Reported the same way IT's was: clicking a row in "Awaiting sign-off" landed a
manager on HR's case page. That page opens with five clearance stages, three of
which a manager cannot move, devotes two panels to a document review queue that
is HR's alone to action, and does not show the handover document anywhere --
the one artefact the manager's entire decision rests on. Finding it meant the
agent catalogue and a dropdown of `kt-00N` ids.

What is worth pinning here is the scope, not the layout: a manager sees their
own reports and nobody else's, and the page hands them the two stages they own
rather than all five.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from backend import auth
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import (CaseDocument, Employee, ExitCase, KTDoc, User)
from backend.main import app

PASSWORD = "x" * 12


@pytest.fixture
def client():
    # Not the context-manager form: entering it runs the startup event, which
    # makes a real embedding call against the gateway.
    return TestClient(app)


@pytest.fixture
def team():
    """Two managers, an HR account, an IT account, and one leaver who reports
    to the first manager. The second manager exists to prove the scope holds:
    "a manager" is not the same permission as "this person's manager"."""
    suffix = uuid.uuid4().hex[:8]
    spec = {
        "manager": f"pytest-mgr-{suffix}@example.com",
        "other_manager": f"pytest-mgr2-{suffix}@example.com",
        "hr": f"pytest-mgr-hr-{suffix}@example.com",
        "it": f"pytest-mgr-it-{suffix}@example.com",
    }
    roles = {"manager": "manager", "other_manager": "manager",
             "hr": "hr", "it": "it"}
    with session_scope() as s:
        for key, email in spec.items():
            s.add(User(email=email, password_hash=auth.hash_password(PASSWORD),
                       role=roles[key], full_name=f"Pytest {key}"))

    leaver_email = f"pytest-leaver-{suffix}@example.com"
    emp, exit_case, _ = store.add_employee({
        "name": "Pytest Handover Probe",
        "role": "Engineer",
        "department": "Engineering",
        "email": leaver_email,
        # This, and only this, is what makes the first manager their manager.
        "manager_email": spec["manager"],
        "status": "exiting",
    })
    ids = {**spec,
           "case_id": str(exit_case.meta["case_id"]),
           "employee_id": str(emp.meta["id"])}

    yield ids

    with session_scope() as s:
        s.execute(delete(KTDoc).where(KTDoc.case_id == ids["case_id"]))
        s.execute(delete(CaseDocument).where(CaseDocument.case_id == ids["case_id"]))
        s.execute(delete(User).where(User.email.in_([*spec.values(), leaver_email])))
        s.execute(delete(ExitCase).where(ExitCase.case_id == ids["case_id"]))
        s.execute(delete(Employee).where(Employee.id == ids["employee_id"]))


def _login(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = client.cookies.get(auth.CSRF_COOKIE)


def _add_kt(case_id, employee_id):
    kt_id = f"kt-mgr-{uuid.uuid4().hex[:8]}"
    with session_scope() as s:
        s.add(KTDoc(id=kt_id, slug=kt_id, case_id=case_id, employee_id=employee_id,
                    employee_name="Pytest Handover Probe", role="Engineer",
                    successor="Tara Menon", reviewed=False,
                    body="# Handover\n\n## System Overview\nOne service.\n"))
    return kt_id


def _add_document(case_id, name, verdict="accepted"):
    with session_scope() as s:
        s.add(CaseDocument(case_id=case_id, document_name=name,
                           filename=f"{name}.png", content_type="image/png",
                           verdict=verdict))


# -- what the page is for -----------------------------------------------------
def test_it_hands_the_manager_the_handover_and_their_own_two_stages(client, team):
    """The page exists because none of this was on one screen, and the document
    was on none of them."""
    kt_id = _add_kt(team["case_id"], team["employee_id"])
    _login(client, team["manager"])

    body = client.get(f"/api/manager/signoff/{team['case_id']}").json()

    assert [s["stage_key"] for s in body["stages"]] == ["kt", "manager"]
    assert body["kt"]["kt_id"] == kt_id
    assert body["kt"]["successor"] == "Tara Menon"
    # The document itself, not a reference to it. Without the body the page can
    # ask for a judgement it has given the reader no way to make.
    assert "System Overview" in body["kt"]["body"]
    assert body["can_act"] is True


def test_the_three_stages_a_manager_cannot_move_are_not_on_it(client, team):
    """IT, Finance and HR clearance belong to other people. Listing them was
    most of what made the case page the wrong page."""
    _login(client, team["manager"])
    body = client.get(f"/api/manager/signoff/{team['case_id']}").json()
    assert {s["stage_key"] for s in body["stages"]} == {"kt", "manager"}


def test_it_carries_only_the_handover_paperwork(client, team):
    """Finance and HR forms are on the same case and are not this reader's to
    accept. Including them would put back the review queue this page removes."""
    _add_document(team["case_id"], "KT document")
    _add_document(team["case_id"], "No-dues declaration")
    _add_document(team["case_id"], "PF transfer/withdrawal form")

    _login(client, team["manager"])
    body = client.get(f"/api/manager/signoff/{team['case_id']}").json()

    assert [d["document_name"] for d in body["documents"]] == ["KT document"]


def test_no_handover_filed_is_a_stated_absence_not_an_error(client, team):
    """A case with no KT document is the normal early state, and the manager
    still has stages to move on it."""
    _login(client, team["manager"])
    body = client.get(f"/api/manager/signoff/{team['case_id']}")
    assert body.status_code == 200
    assert body.json()["kt"] is None
    assert body.json()["review"] is None


# -- scope --------------------------------------------------------------------
def test_a_manager_cannot_open_somebody_elses_report(client, team):
    """"A manager" is not the same permission as "this person's manager". The
    role guard lets them through; `scoped_case` is what stops them."""
    _login(client, team["other_manager"])
    r = client.get(f"/api/manager/signoff/{team['case_id']}")
    assert r.status_code == 403


def test_hr_may_read_it_as_the_escalation_path(client, team):
    """Same grant HR has over IT's clearance workspace, and for the same
    reason: somebody has to pick the work up when the manager is on leave."""
    _login(client, team["hr"])
    assert client.get(f"/api/manager/signoff/{team['case_id']}").status_code == 200


def test_it_is_not_its_page(client, team):
    _login(client, team["it"])
    assert client.get(f"/api/manager/signoff/{team['case_id']}").status_code == 403


def test_the_leaver_cannot_read_their_own_review(client, team):
    """They see their own case at /api/me/case. This one carries a manager's
    judgement of their handover, which is not theirs to read."""
    leaver = f"pytest-leaver-{uuid.uuid4().hex[:8]}@example.com"
    with session_scope() as s:
        s.add(User(email=leaver, password_hash=auth.hash_password(PASSWORD),
                   role="employee", employee_id=team["employee_id"],
                   full_name="Pytest Handover Probe"))
    try:
        _login(client, leaver)
        assert client.get(
            f"/api/manager/signoff/{team['case_id']}").status_code == 403
    finally:
        with session_scope() as s:
            s.execute(delete(User).where(User.email == leaver))
