"""The employee-facing document upload.

Every test here stops *before* `validate_upload` runs, because that call does
local OCR and then hits the gateway, and this repo's rule is that tests never
touch the network. That is fine: the value of this endpoint is in its guards,
and each one refuses before any file is read.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from backend import auth
from backend.api.cases_routes import _ISSUED, _UPLOADABLE, _upload_checklist
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import CaseDocument, User
from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def employee():
    """A throwaway account bound to a seeded employee who has an exit case."""
    cases = store.exit_cases()
    if not cases:
        pytest.skip("no seeded exit case")
    emp_id = str(cases[0].meta["employee_id"])
    email = f"pytest-upload-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-upload"
    with session_scope() as s:
        s.add(User(email=email, password_hash=auth.hash_password(password),
                   role="employee", employee_id=emp_id, full_name="Pytest Uploader"))
    yield email, password, str(cases[0].meta["case_id"])
    with session_scope() as s:
        s.execute(delete(User).where(User.email == email))


def _login(client: TestClient, email: str, password: str) -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    client.headers["X-CSRF-Token"] = client.cookies.get(auth.CSRF_COOKIE)


# -- the checklist ------------------------------------------------------------
def test_the_checklist_never_offers_a_document_the_company_issues():
    """A self-issued relieving letter is a forged record, so those documents
    must not appear as something an employee can send."""
    assert _ISSUED, "expected some issued documents in the corpus"
    uploadable = {d["name"] for d in _UPLOADABLE}
    assert not (uploadable & _ISSUED)

    case = store.exit_cases()[0]
    offered = {r["document"] for r in _upload_checklist(case.meta)}
    assert not (offered & _ISSUED)


def test_the_checklist_marks_what_is_already_in(employee, client):
    email, password, _ = employee
    _login(client, email, password)

    body = client.get("/api/me/case/documents").json()
    assert body["checklist"], "an exiting employee should have a checklist"
    assert set(body["summary"]) == {"submitted", "required_now", "completion_pct", "all_in"}

    # A document already accepted reads as done and is not re-requested.
    accepted = [r for r in body["checklist"] if r["state"] == "accepted"]
    assert accepted, "the seeded case has documents in already"
    assert all(r["can_upload"] is False for r in accepted), (
        "an accepted document should not be presented as outstanding work"
    )


def test_the_checklist_has_exactly_one_row_per_document(employee, client):
    """A person owes a document, not a series of attempts.

    The page used to show the requirement list and a separate list of every
    upload, so re-sending a rejected form produced two entries for the same
    thing -- and the two lists could disagree about its state at the same
    moment. The attempt is folded into the requirement now.
    """
    email, password, _ = employee
    _login(client, email, password)
    body = client.get("/api/me/case/documents").json()

    names = [r["document"] for r in body["checklist"]]
    assert len(names) == len(set(names)), f"duplicate rows: {names}"

    # The old separate list is gone; nothing should re-introduce it.
    assert "uploads" not in body, (
        "a second list of raw uploads is what produced the duplicates"
    )

    for row in body["checklist"]:
        assert row["state"] in {
            "accepted", "rejected", "in_review", "needed", "not_yet_due",
        }, row
        # Only an accepted document has nothing left to do.
        assert row["can_upload"] is (row["state"] != "accepted")
        # A row that reports attempts must carry the latest one, and vice
        # versa -- otherwise the UI cannot show why something was refused.
        assert (row["attempts"] > 0) is (row["latest"] is not None)


def test_a_rejected_document_still_invites_a_new_copy(employee, client):
    """The state that matters most: refused, and obvious what to do next."""
    email, password, case_id = employee
    _login(client, email, password)

    with session_scope() as s:
        s.add(CaseDocument(
            case_id=case_id, document_name="No-dues declaration",
            filename="blurry.png", verdict="rejected",
            reason="Too blurry to read the signature.",
        ))
    try:
        body = client.get("/api/me/case/documents").json()
        row = next(r for r in body["checklist"]
                   if r["document"] == "No-dues declaration")
        assert row["state"] == "rejected"
        assert row["can_upload"] is True, "a refusal must be fixable"
        assert row["latest"]["reason"] == "Too blurry to read the signature."
    finally:
        with session_scope() as s:
            s.execute(delete(CaseDocument).where(
                CaseDocument.case_id == case_id,
                CaseDocument.filename == "blurry.png"))


# -- the guards ---------------------------------------------------------------
def test_an_issued_document_is_refused(employee, client):
    email, password, _ = employee
    _login(client, email, password)
    resp = client.post(
        "/api/me/case/documents",
        data={"document_name": "Relieving letter"},
        files={"file": ("x.png", b"not-really-an-image", "image/png")},
    )
    assert resp.status_code == 403
    assert "issued to you" in resp.json()["detail"]


def test_a_document_not_on_the_checklist_is_refused(employee, client):
    email, password, _ = employee
    _login(client, email, password)
    resp = client.post(
        "/api/me/case/documents",
        data={"document_name": "My holiday photos"},
        files={"file": ("x.png", b"bytes", "image/png")},
    )
    assert resp.status_code == 422
    assert "not a document on your checklist" in resp.json()["detail"]


def test_a_missing_file_is_refused(employee, client):
    email, password, _ = employee
    _login(client, email, password)
    resp = client.post("/api/me/case/documents",
                       data={"document_name": "No-dues declaration"})
    assert resp.status_code == 422
    assert "Attach" in resp.json()["detail"]


def test_an_empty_file_is_refused(employee, client):
    email, password, _ = employee
    _login(client, email, password)
    resp = client.post(
        "/api/me/case/documents",
        data={"document_name": "No-dues declaration"},
        files={"file": ("empty.png", b"", "image/png")},
    )
    assert resp.status_code == 422
    assert "empty" in resp.json()["detail"].lower()


def test_an_oversized_upload_is_refused_before_it_is_buffered(employee, client):
    """Checked against Content-Length, so a 20 MB body is rejected without
    being spooled to disk first."""
    email, password, _ = employee
    _login(client, email, password)
    resp = client.post(
        "/api/me/case/documents",
        data={"document_name": "No-dues declaration"},
        files={"file": ("big.png", b"\x00" * (11 * 1024 * 1024), "image/png")},
    )
    assert resp.status_code == 413


# -- scoping ------------------------------------------------------------------
def test_upload_requires_a_session(client):
    resp = client.post("/api/me/case/documents",
                       data={"document_name": "No-dues declaration"})
    assert resp.status_code == 401


def test_upload_requires_the_csrf_header(employee, client):
    email, password, _ = employee
    _login(client, email, password)
    client.headers.pop("X-CSRF-Token", None)
    resp = client.post("/api/me/case/documents",
                       data={"document_name": "No-dues declaration"})
    assert resp.status_code == 403
    assert "CSRF" in resp.json()["detail"]


def test_an_account_with_no_case_gets_a_clear_404(client):
    """An HR account has no `employee_id`, so there is no case to resolve.
    It should say so rather than 500."""
    email = f"pytest-upload-hr-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-nocase"
    with session_scope() as s:
        s.add(User(email=email, password_hash=auth.hash_password(password),
                   role="hr", full_name="Pytest No Case"))
    try:
        _login(client, email, password)
        resp = client.get("/api/me/case/documents")
        assert resp.status_code == 404
        assert "No exit case" in resp.json()["detail"]
    finally:
        with session_scope() as s:
            s.execute(delete(User).where(User.email == email))


def test_nobody_can_upload_against_someone_elses_case(employee, client):
    """There is deliberately no case id in this API.

    The case is resolved from `user.employee_id` on the session, so there is
    nothing for a caller to substitute -- which is why this endpoint is safe
    without the `scoped_case` dependency the staff routes use.
    """
    import inspect

    from backend.api import cases_routes

    sig = inspect.signature(cases_routes.upload_my_document)
    assert "case_id" not in sig.parameters, (
        "the employee upload route must not accept a case id from the client"
    )
    src = inspect.getsource(cases_routes.upload_my_document)
    assert "_my_case(user)" in src
