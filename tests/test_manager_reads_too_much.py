"""What a manager could read on their own report's case, and should not.

`can_see_case` gives a manager exactly the people who report to them, and three
routes were then guarded by `StaffUser` -- which includes `manager` -- with
nothing narrower behind them. So a line manager could read:

  * their report's submitted exit interview, including the answer to "how was
    working with your manager, and where did you need more from them?", on a
    form that tells the leaver their answers do not affect their evaluation;
  * every document on the case, including the bank account confirmation and
    the PF transfer form;
  * the bytes of any of them.

`manager_routes` already filtered its own page down to the handover on exactly
that reasoning ("the rest are not this reader's to judge"). The filtering was a
courtesy on one screen; the routes underneath were the other door.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from backend import auth, exit_interview
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import CaseDocument, Employee, ExitCase, User
from backend.main import app

PASSWORD = "x" * 12


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def team():
    suffix = uuid.uuid4().hex[:8]
    spec = {"manager": f"pytest-mr-mgr-{suffix}@example.com",
            "hr": f"pytest-mr-hr-{suffix}@example.com",
            "it": f"pytest-mr-it-{suffix}@example.com"}
    roles = {"manager": "manager", "hr": "hr", "it": "it"}
    with session_scope() as s:
        for key, email in spec.items():
            s.add(User(email=email, password_hash=auth.hash_password(PASSWORD),
                       role=roles[key], full_name=f"Pytest {key}"))

    leaver = f"pytest-mr-leaver-{suffix}@example.com"
    emp, case, _ = store.add_employee({
        "name": "Pytest Reads Too Much", "role": "Engineer",
        "department": "Engineering", "email": leaver,
        "manager_email": spec["manager"], "status": "exiting",
    })
    ids = {**spec, "case_id": str(case.meta["case_id"]),
           "employee_id": str(emp.meta["id"])}

    docs = {}
    with session_scope() as s:
        for name in ("KT document", "Bank account confirmation"):
            blob = b"%PDF-1.4 " + name.encode()
            row = CaseDocument(case_id=ids["case_id"], document_name=name,
                               filename=f"{name}.pdf", content_type="application/pdf",
                               ocr_text=f"contents of {name}", verdict="needs_review",
                               image=blob, image_bytes=len(blob))
            s.add(row)
            s.flush()
            docs[name] = row.id
    ids["docs"] = docs

    yield ids
    with session_scope() as s:
        s.execute(delete(CaseDocument).where(CaseDocument.case_id == ids["case_id"]))
        s.execute(delete(User).where(User.email.in_([*spec.values(), leaver])))
        s.execute(delete(ExitCase).where(ExitCase.case_id == ids["case_id"]))
        s.execute(delete(Employee).where(Employee.id == ids["employee_id"]))


def _login(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = client.cookies.get(auth.CSRF_COOKIE)


# -- the exit interview -------------------------------------------------------
def test_a_manager_cannot_read_their_reports_exit_interview(client, team):
    exit_interview.save(
        case_id=team["case_id"], employee_id=team["employee_id"],
        answers={q["id"]: (q["options"][0] if q.get("options") else "Said plainly.")
                 for q in exit_interview.QUESTIONS},
        submitted=True)

    _login(client, team["manager"])
    assert client.get(f"/api/cases/{team['case_id']}/interview").status_code == 403


def test_hr_still_reads_it(client, team):
    _login(client, team["hr"])
    assert client.get(f"/api/cases/{team['case_id']}/interview").status_code == 200


# -- the documents ------------------------------------------------------------
def test_a_manager_sees_only_the_handover_in_the_document_list(client, team):
    _login(client, team["manager"])
    body = client.get(f"/api/cases/{team['case_id']}/documents").json()
    assert [d["document_name"] for d in body["documents"]] == ["KT document"]


def test_a_manager_cannot_pull_their_reports_bank_details(client, team):
    _login(client, team["manager"])
    r = client.get(
        f"/api/cases/{team['case_id']}/documents/{team['docs']['Bank account confirmation']}/file")
    assert r.status_code == 403
    assert "not yours to read" in r.json()["detail"]


def test_a_manager_can_still_open_the_handover_their_page_shows(client, team):
    """The narrowing must not break the workspace it was modelled on."""
    _login(client, team["manager"])
    r = client.get(
        f"/api/cases/{team['case_id']}/documents/{team['docs']['KT document']}/file")
    assert r.status_code == 200


def test_hr_still_reads_every_document(client, team):
    _login(client, team["hr"])
    body = client.get(f"/api/cases/{team['case_id']}/documents").json()
    assert len(body["documents"]) == 2
    for doc_id in team["docs"].values():
        assert client.get(
            f"/api/cases/{team['case_id']}/documents/{doc_id}/file").status_code == 200


def test_a_missing_document_is_a_404_not_a_403(client, team):
    _login(client, team["hr"])
    assert client.get(
        f"/api/cases/{team['case_id']}/documents/99999999/file").status_code == 404


# -- deciding on your own exit ------------------------------------------------
def test_nobody_rules_on_paperwork_for_their_own_exit(client, team):
    """`assert_may_transition` blocks the analogous stage move -- "the person
    leaving approved their own clearance is precisely what an exit audit looks
    for" -- and the verdict written here feeds the same completion state."""
    selfie = f"pytest-mr-self-{uuid.uuid4().hex[:8]}@example.com"
    with session_scope() as s:
        s.add(User(email=selfie, password_hash=auth.hash_password(PASSWORD),
                   role="manager", employee_id=team["employee_id"],
                   full_name="Pytest Self Manager"))
        # A manager who reports to themselves: a data-entry slip, not exotic.
        s.get(Employee, team["employee_id"]).manager_email = selfie
    try:
        _login(client, selfie)
        r = client.post(
            f"/api/cases/{team['case_id']}/documents/{team['docs']['KT document']}/review",
            json={"verdict": "accepted"})
        assert r.status_code == 403
        assert "your own exit" in r.json()["detail"]
    finally:
        with session_scope() as s:
            s.execute(delete(User).where(User.email == selfie))
