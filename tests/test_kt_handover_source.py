"""The handover a leaver actually uploaded, and who gets to accept it.

Two unconnected notions of "KT document" existed side by side.

`kt_docs` is a read-only corpus of three handovers written before this product
existed. `case_documents` is what people upload through the portal. Every
screen about handovers -- the KT reviewer's dropdown, the case page's "review
the handover" action, the manager's workspace -- read only the first. So Rahul
Verma could upload a 2,700-word handover PDF, the product could accept it, and
his manager's page would say "no handover document has been filed for this exit
yet" and then make him force the stage complete over a warning saying the same.

And the verdict on it was written by the model that read the scan. Accepting a
handover is the manager's judgement -- `ROLE_AGENTS_EXCEPT` takes the KT
reviewer off HR for exactly that reason -- but there was no route for a manager
to say so, so the model's read stood as the decision and the manager arrived to
a settled question.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from backend import auth
from backend.agents.document_collection import who_accepts
from backend.api.options import kt_options
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import CaseDocument, Employee, ExitCase, User
from backend.scoping import identity_for
from backend.main import app

PASSWORD = "x" * 12
HANDOVER = ("KNOWLEDGE TRANSFER AND HANDOVER\n"
            "System overview: the billing service and its two workers.\n"
            "Pending work: BILL-201, BILL-208.\n"
            "Successor: Tara Menon.\n")


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def team():
    suffix = uuid.uuid4().hex[:8]
    spec = {"manager": f"pytest-kt-mgr-{suffix}@example.com",
            "hr": f"pytest-kt-hr-{suffix}@example.com",
            "it": f"pytest-kt-it-{suffix}@example.com"}
    roles = {"manager": "manager", "hr": "hr", "it": "it"}
    with session_scope() as s:
        for key, email in spec.items():
            s.add(User(email=email, password_hash=auth.hash_password(PASSWORD),
                       role=roles[key], full_name=f"Pytest {key}"))

    leaver = f"pytest-kt-leaver-{suffix}@example.com"
    emp, case, _ = store.add_employee({
        "name": "Pytest Handover Source", "role": "Engineer",
        "department": "Engineering", "email": leaver,
        "manager_email": spec["manager"], "status": "exiting",
    })
    ids = {**spec, "case_id": str(case.meta["case_id"]),
           "employee_id": str(emp.meta["id"])}
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


def _upload(case_id, *, text=HANDOVER, verdict="needs_review",
            name="KT document", filename="handover.pdf"):
    with session_scope() as s:
        row = CaseDocument(case_id=case_id, document_name=name, filename=filename,
                           content_type="application/pdf", ocr_text=text,
                           verdict=verdict, reason="")
        s.add(row)
        s.flush()
        return row.id


def _manager_identity(email):
    from sqlalchemy import select

    with session_scope() as s:
        return identity_for(s.scalar(select(User).where(User.email == email)))


# -- the two sources, joined --------------------------------------------------
def test_an_uploaded_handover_is_a_handover(team):
    """The corpus table is not the only place one can come from."""
    doc_id = _upload(team["case_id"])
    case_id = team["case_id"]

    found = [d for d in store.all_kt_docs() if d.meta.get("case_id") == case_id]
    assert len(found) == 1
    doc = found[0]
    assert doc.meta["source"] == "upload"
    assert doc.meta["document_id"] == doc_id
    assert "BILL-201" in doc.body

    # And `kt_docs()` is untouched, because the golden snapshot pins it.
    assert case_id not in {d.meta.get("case_id") for d in store.kt_docs()}


def test_the_id_the_dropdown_offers_can_actually_be_opened(team):
    """The failure mode `interview()` already carries a comment about: offer a
    record in a select and then refuse to resolve it."""
    _upload(team["case_id"])
    options = kt_options(_manager_identity(team["manager"]))
    values = [o["value"] for o in options]
    assert values, "the manager's own report's handover is missing from the dropdown"
    for value in values:
        assert store.kt_doc(value) is not None, f"{value} is offered and cannot be opened"


def test_the_dropdown_shows_it_to_the_manager_who_owns_the_exit(team):
    _upload(team["case_id"])
    mine = kt_options(_manager_identity(team["manager"]))
    assert any(o["value"] == f"kt-up-{team['case_id']}" for o in mine)
    assert any("Pytest Handover Source" in o["label"] for o in mine)


def test_a_seeded_marker_row_with_no_text_is_not_offered_as_a_document(team):
    """The corpus's `documents_submitted` is materialised as accepted rows that
    hold no file and no text. Offering one would put an empty document in the
    reviewer's dropdown."""
    _upload(team["case_id"], text="", verdict="accepted", filename="")
    assert not [d for d in store.submitted_kt_docs()
                if d.meta.get("case_id") == team["case_id"]]


def test_the_newest_upload_is_the_handover(team):
    """Somebody re-sending a refused scan owes one document, not a series of
    attempts. Same latest-wins rule as the employee's checklist."""
    _upload(team["case_id"], text="first attempt", verdict="rejected")
    _upload(team["case_id"], text="second attempt with everything in it")
    doc = store.kt_doc(f"kt-up-{team['case_id']}")
    assert "second attempt" in doc.body


# -- the manager's page finds it ---------------------------------------------
def test_the_managers_page_stops_claiming_nothing_was_filed(client, team):
    """The reported bug, end to end: document uploaded, page says none exists,
    and completing Knowledge Transfer then demands an override for a reason
    that is not true."""
    _upload(team["case_id"])
    _login(client, team["manager"])
    body = client.get(f"/api/manager/signoff/{team['case_id']}").json()

    assert body["kt"] is not None
    assert body["kt"]["source"] == "upload"
    assert "BILL-201" in body["kt"]["body"]
    assert body["can_review"] is True


# -- whose decision it is -----------------------------------------------------
def test_the_handover_is_the_managers_to_accept_and_the_rest_are_not():
    assert who_accepts("KT document") == "manager"
    assert who_accepts("Successor acknowledgement") == "manager"
    assert who_accepts("No-dues declaration") is None
    assert who_accepts("PF transfer/withdrawal form") is None


def test_a_manager_can_accept_the_handover(client, team):
    """The control that did not exist. Until now the only route to a verdict
    was HR's, so the model's read was the decision."""
    doc_id = _upload(team["case_id"])
    _login(client, team["manager"])

    r = client.post(
        f"/api/cases/{team['case_id']}/documents/{doc_id}/review",
        json={"verdict": "accepted", "note": "Tara has picked the work up."})
    assert r.status_code == 200, r.text
    assert r.json()["document"]["verdict"] == "accepted"

    # And it counts -- this is what clears the requirement everywhere else.
    case = store.exit_case(team["case_id"])
    assert "KT document" in (case.meta.get("documents_submitted") or [])


def test_a_manager_cannot_accept_hrs_paperwork(client, team):
    """Widening this route from HR to staff is only safe because of this: a
    manager gets the handover, not the no-dues declaration."""
    doc_id = _upload(team["case_id"], name="No-dues declaration")
    _login(client, team["manager"])
    r = client.post(f"/api/cases/{team['case_id']}/documents/{doc_id}/review",
                    json={"verdict": "accepted"})
    assert r.status_code == 403
    assert "HR's decision" in r.json()["detail"]


def test_it_cannot_review_documents_at_all(client, team):
    doc_id = _upload(team["case_id"])
    _login(client, team["it"])
    # IT cannot see the case in the first place, which is the outer guard.
    r = client.post(f"/api/cases/{team['case_id']}/documents/{doc_id}/review",
                    json={"verdict": "accepted"})
    assert r.status_code == 403


def test_hr_keeps_every_document(client, team):
    doc_id = _upload(team["case_id"], name="No-dues declaration")
    _login(client, team["hr"])
    r = client.post(f"/api/cases/{team['case_id']}/documents/{doc_id}/review",
                    json={"verdict": "accepted"})
    assert r.status_code == 200, r.text


# -- the validator stops closing the question ---------------------------------
def _pdf(text: str) -> bytes:
    from io import BytesIO

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in text.splitlines():
        c.drawString(72, y, line)
        y -= 18
    c.save()
    return buf.getvalue()


@pytest.fixture
def no_llm(monkeypatch):
    """The model always says "accepted". Nothing here reaches the gateway; the
    question under test is what the code does with that answer."""
    from backend.agents import document_collection as dc

    monkeypatch.setattr(dc, "_policy_requirements", lambda *a, **k: ("", []))
    monkeypatch.setattr(dc.llm, "complete_json", lambda *a, **k: {
        "verdict": "accepted",
        "reason": "Right document, right person, signed and dated.",
    })


def test_a_model_may_not_accept_the_handover(team, no_llm):
    """The reported bug. The OCR read is evidence; accepting a handover is a
    judgement about whether the team can carry on without this person, and the
    model has never met the successor."""
    from backend.agents.document_collection import validate_upload

    case = store.exit_case(team["case_id"])
    out = validate_upload(case.meta, "KT document", _pdf(HANDOVER),
                          "handover.pdf", "application/pdf", "Pytest Leaver")

    assert out["validation"]["verdict"] == "needs_review"
    # The read itself is kept -- it is useful advice, it is just not a decision.
    assert "Right document" in out["validation"]["reason"]
    assert "manager's decision" in out["validation"]["reason"]

    # And it stays outstanding, which is the truth: nobody with the authority
    # has looked at it yet.
    refreshed = store.exit_case(team["case_id"])
    assert "KT document" not in (refreshed.meta.get("documents_submitted") or [])


def test_the_documents_that_are_hrs_still_settle_on_the_read(team, no_llm):
    """Only the handover changes. HR has a review queue and a route to overturn
    a verdict on everything else, so holding those back would add a step and
    remove nothing."""
    from backend.agents.document_collection import validate_upload

    case = store.exit_case(team["case_id"])
    out = validate_upload(case.meta, "No-dues declaration",
                          _pdf("NO-DUES DECLARATION\nSigned."),
                          "nodues.pdf", "application/pdf", "Pytest Leaver")
    assert out["validation"]["verdict"] == "accepted"


def test_a_refusal_is_still_a_refusal(team, monkeypatch):
    """Downgrading only ever touches `accepted`. A model that rejects a scan is
    not making the manager's decision for them -- it is saying the scan is
    unreadable or the wrong document, which is a fact about the file."""
    from backend.agents import document_collection as dc

    monkeypatch.setattr(dc, "_policy_requirements", lambda *a, **k: ("", []))
    monkeypatch.setattr(dc.llm, "complete_json", lambda *a, **k: {
        "verdict": "rejected", "reason": "This is a payslip."})

    case = store.exit_case(team["case_id"])
    out = dc.validate_upload(case.meta, "KT document", _pdf("PAYSLIP"),
                             "oops.pdf", "application/pdf", "Pytest Leaver")
    assert out["validation"]["verdict"] == "rejected"
