"""The documents the company owes a leaver.

Three of them sat in `REQUIRED_DOCUMENTS` marked `issued: True` from the start
and nothing ever produced one, so the checklist carried three lines nobody
could ever action.

Two properties matter more than the formatting, and both are about a PDF on
company letterhead being believed:

- nothing is issued before every clearance is done, because a relieving letter
  asserts the person has been released;
- no figures are invented, because a settlement statement with numbers this
  system has never seen is a fabricated financial record.
"""
from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend import auth, issued_documents
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import (AssetReturn, CaseStage, Employee, ExitCase, User)
from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def leaver():
    email = f"pytest-iss-{uuid.uuid4().hex[:8]}@example.com"
    emp, case, _ = store.add_employee({
        "name": "Pytest Departing", "role": "Staff Engineer",
        "department": "Engineering", "email": email, "status": "exiting",
        "location": "Bengaluru, IN", "join_date": "2021-04-01",
        "assets_assigned": ["MacBook Pro", "YubiKey"],
    })
    ids = {"case_id": str(case.meta["case_id"]),
           "employee_id": str(emp.meta["id"]), "email": email}
    password = "x" * 12
    with session_scope() as s:
        account = s.scalar(select(User).where(User.email == email))
        account.password_hash = auth.hash_password(password)
        account.must_change_password = False
    ids["password"] = password
    yield ids
    with session_scope() as s:
        s.execute(delete(AssetReturn).where(
            AssetReturn.employee_id == ids["employee_id"]))
        s.execute(delete(User).where(User.email == email))
        s.execute(delete(ExitCase).where(ExitCase.case_id == ids["case_id"]))
        s.execute(delete(Employee).where(Employee.id == ids["employee_id"]))


def _finish_every_stage(case_id):
    with session_scope() as s:
        for st in s.scalars(select(CaseStage).where(
                CaseStage.case_id == case_id)).all():
            st.status = "completed"


def _login(client, email, password):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = client.cookies.get(auth.CSRF_COOKIE)


# -- the gate -----------------------------------------------------------------
def test_nothing_is_issued_while_a_clearance_is_open(leaver):
    """The letter is the thing the clearance exists to gate."""
    state = issued_documents.readiness(leaver["case_id"])
    assert state["ready"] is False
    assert state["outstanding"]

    with pytest.raises(PermissionError, match="every clearance"):
        issued_documents.generate(leaver["case_id"], "relieving-letter")


def test_every_stage_means_every_stage(leaver):
    """Four of five is not released."""
    with session_scope() as s:
        rows = s.scalars(select(CaseStage).where(
            CaseStage.case_id == leaver["case_id"]).order_by(CaseStage.ordinal)).all()
        for st in rows[:-1]:
            st.status = "completed"

    assert issued_documents.readiness(leaver["case_id"])["ready"] is False


def test_they_are_issued_once_the_exit_is_finished(leaver):
    _finish_every_stage(leaver["case_id"])
    state = issued_documents.readiness(leaver["case_id"])
    assert state["ready"] is True
    assert {d["id"] for d in state["documents"]} == set(issued_documents.ISSUED)


# -- what is in them ----------------------------------------------------------
def _text(case_id, doc_id) -> str:
    """The rendered text, not the raw bytes.

    Searching the bytes is worse than useless here: reportlab compresses the
    content stream, so every `in pdf` assertion passes whatever the document
    says. The first version of the "invents no figures" test below passed for
    exactly that reason and would have gone on passing over a page of invented
    money.
    """
    import io as _io

    from pypdf import PdfReader

    pdf, _ = issued_documents.generate(case_id, doc_id)
    assert pdf.startswith(b"%PDF-")
    return "".join(page.extract_text() or "" for page in
                   PdfReader(_io.BytesIO(pdf)).pages)


def test_each_document_is_a_real_pdf_named_for_the_person(leaver):
    _finish_every_stage(leaver["case_id"])
    for doc_id in issued_documents.ISSUED:
        pdf, filename = issued_documents.generate(leaver["case_id"], doc_id)
        assert pdf.startswith(b"%PDF-")
        assert len(pdf) > 800, f"{doc_id} is suspiciously small"
        assert "Pytest-Departing" in filename
        assert filename.endswith(".pdf")


def test_the_settlement_statement_invents_no_figures(leaver):
    """A number on company letterhead is believed. This system has never seen
    a salary, a notice-period adjustment or a leave balance, so it states the
    clearance position and says amounts come from payroll."""
    _finish_every_stage(leaver["case_id"])
    body = _text(leaver["case_id"], "settlement-statement")
    # A currency symbol or an amount-shaped run would mean somebody started
    # generating money.
    assert "₹" not in body and "INR" not in body and "$" not in body
    assert not re.search(r"\b\d{1,3}(,\d{3})+(\.\d{2})?\b", body), (
        "the statement contains something shaped like a money amount")
    assert "payroll" in body.lower(), "it should say where amounts come from"


def test_a_written_off_asset_is_reported_as_recoverable(leaver):
    """It is the one financial fact the system genuinely holds."""
    _finish_every_stage(leaver["case_id"])
    with session_scope() as s:
        s.add(AssetReturn(employee_id=leaver["employee_id"],
                          case_id=leaver["case_id"], asset="YubiKey",
                          state="written_off", logged_by="Pytest IT",
                          note="Never returned."))
    body = _text(leaver["case_id"], "settlement-statement")
    assert "YubiKey" in body
    assert "recoverable" in body.lower()


def test_the_experience_letter_says_nothing_about_performance(leaver):
    """The record holds a rating. Turning an internal number into a sentence on
    a reference is a judgement the company has not made."""
    _finish_every_stage(leaver["case_id"])
    body = _text(leaver["case_id"], "experience-letter").lower()
    for word in ("performance", "rating", "excellent", "satisfactory"):
        assert word not in body, f"the reference letter editorialises: {word!r}"


def test_an_unknown_document_is_refused(leaver):
    _finish_every_stage(leaver["case_id"])
    with pytest.raises(LookupError):
        issued_documents.generate(leaver["case_id"], "pay-rise-letter")


# -- over the wire ------------------------------------------------------------
def test_the_leaver_downloads_their_own(client, leaver):
    _finish_every_stage(leaver["case_id"])
    _login(client, leaver["email"], leaver["password"])

    state = client.get("/api/me/case/issued").json()
    assert state["ready"] is True

    r = client.get("/api/me/case/issued/relieving-letter")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "attachment" in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF-")


def test_downloading_early_is_refused_over_the_wire_too(client, leaver):
    _login(client, leaver["email"], leaver["password"])
    r = client.get("/api/me/case/issued/relieving-letter")
    assert r.status_code == 409
    assert "clearance" in r.json()["detail"]
