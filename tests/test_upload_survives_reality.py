"""Uploads that were silently lost, hidden, or shadowed.

Four separate defects, all in the path between "the leaver sends a document"
and "the right person is shown the right one".
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from backend import auth, ocr
from backend.api.manager_routes import _kt_doc_for
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import CaseDocument, Employee, ExitCase, KTDoc, User


@pytest.fixture
def case():
    suffix = uuid.uuid4().hex[:8]
    emp, exit_case, _ = store.add_employee({
        "name": "Pytest Upload Reality", "role": "Engineer",
        "department": "Engineering", "email": f"pytest-ur-{suffix}@example.com",
        "status": "exiting",
    })
    ids = {"case_id": str(exit_case.meta["case_id"]),
           "employee_id": str(emp.meta["id"])}
    yield ids
    with session_scope() as s:
        s.execute(delete(KTDoc).where(KTDoc.case_id == ids["case_id"]))
        s.execute(delete(CaseDocument).where(CaseDocument.case_id == ids["case_id"]))
        s.execute(delete(User).where(User.employee_id == ids["employee_id"]))
        s.execute(delete(ExitCase).where(ExitCase.case_id == ids["case_id"]))
        s.execute(delete(Employee).where(Employee.id == ids["employee_id"]))


def _upload(case_id, text, name="KT document", filename="h.pdf"):
    with session_scope() as s:
        row = CaseDocument(case_id=case_id, document_name=name, filename=filename,
                           content_type="application/pdf", ocr_text=text,
                           verdict="needs_review")
        s.add(row)
        s.flush()
        return row.id


# -- a blank re-upload must not hide a real handover --------------------------
def test_a_blank_upload_does_not_erase_the_handover(case):
    """`latest per case` was computed before empty rows were dropped, so a
    newer blank scan hid an older real document instead of falling back to it.
    Re-seeding did the same, because the seeded marker rows carry no text."""
    _upload(case["case_id"], "SYSTEM OVERVIEW\nThe billing service.")
    _upload(case["case_id"], "   ")  # a blank scan, or a seeded marker row

    found = [d for d in store.submitted_kt_docs()
             if d.meta.get("case_id") == case["case_id"]]
    assert len(found) == 1
    assert "billing service" in found[0].body


def test_the_newest_real_upload_still_wins(case):
    _upload(case["case_id"], "first attempt")
    _upload(case["case_id"], "second attempt, complete")
    doc = store.kt_doc(f"kt-up-{case['case_id']}")
    assert "second attempt" in doc.body


# -- a corpus handover must not shadow the uploaded one -----------------------
def test_an_uploaded_handover_beats_a_corpus_one_for_the_same_case(case):
    """The loop returned on the first document matching *either* criterion, and
    the corpus is iterated first -- so a pre-product stand-in always won."""
    with session_scope() as s:
        s.add(KTDoc(id=f"kt-old-{case['case_id']}", slug=f"kt-old-{case['case_id']}",
                    case_id=case["case_id"], employee_id=case["employee_id"],
                    employee_name="Pytest Upload Reality",
                    body="STALE corpus handover"))
    _upload(case["case_id"], "FRESH uploaded handover")

    found = _kt_doc_for(case["case_id"], case["employee_id"])
    assert found.meta.get("source") == "upload"
    assert "FRESH" in found.body


def test_another_cases_document_is_not_returned_by_the_employee_fallback(case):
    """The employee-id arm could match an earlier doc from a different exit and
    beat a case match later in the list."""
    other = f"case-other-{uuid.uuid4().hex[:6]}"
    with session_scope() as s:
        s.add(KTDoc(id=f"kt-other-{uuid.uuid4().hex[:6]}",
                    slug=f"kt-other-{uuid.uuid4().hex[:6]}",
                    case_id=other, employee_id=case["employee_id"],
                    employee_name="Pytest Upload Reality",
                    body="a DIFFERENT exit's handover"))
    _upload(case["case_id"], "this exit's handover")
    try:
        found = _kt_doc_for(case["case_id"], case["employee_id"])
        assert str(found.meta.get("case_id")) == case["case_id"]
    finally:
        with session_scope() as s:
            s.execute(delete(KTDoc).where(KTDoc.case_id == other))


# -- text the database cannot hold --------------------------------------------
def test_a_utf16_html_file_does_not_produce_text_postgres_rejects():
    """NUL is valid UTF-8 and invalid in a `text` column. `record_upload`
    swallowed the DataError and returned None, so the route answered 200 with a
    verdict for a document that was never stored."""
    html = "<html><body><p>No dues owing.</p></body></html>"
    data = b"\xff\xfe" + html.encode("utf-16-le")

    out = ocr.extract_document(data, "text/html", "nodues.html")

    assert "\x00" not in out["text"]
    # And the markup is actually parsed now, rather than arriving as "text".
    assert "No dues owing." in out["text"]
    assert "<p>" not in out["text"]


def test_nul_bytes_are_stripped_whatever_the_source():
    assert "\x00" not in ocr._finish("a\x00b", engine="t", seconds=0)["text"]


def test_text_with_nul_can_actually_be_persisted(case):
    """The end of the chain: what the extractor returns has to survive the
    insert, or the upload is lost with a 200."""
    from backend.agents.document_collection import record_upload

    extracted = ocr._finish("Signed\x00 and dated", engine="html-text", seconds=0.1)
    row_id = record_upload(
        case_id=case["case_id"], document_name="No-dues declaration",
        filename="x.html", content_type="text/html", image_bytes=b"<p>x</p>",
        extracted=extracted, judgement={"verdict": "needs_review", "reason": "ok"})
    assert row_id is not None, "the insert must not be swallowed"

    with session_scope() as s:
        stored = s.scalars(select(CaseDocument).where(
            CaseDocument.id == row_id)).one()
        assert "\x00" not in stored.ocr_text


# -- an address you can actually sign in with ---------------------------------
def test_a_mixed_case_address_produces_a_usable_login():
    """`_issue_login` wrote the address verbatim while `authenticate`
    lowercases what it looks up, so the credential HR was handed never worked
    and re-issuing returned None."""
    suffix = uuid.uuid4().hex[:8]
    mixed = f"Pytest.Mixed.{suffix}@Example.com"
    emp, _, credential = store.add_employee({
        "name": "Pytest Mixed Case", "role": "Engineer",
        "department": "Engineering", "email": mixed, "status": "exiting",
    })
    try:
        assert credential is not None
        assert credential["email"] == mixed.lower()
        identity = auth.authenticate(credential["email"], credential["password"])
        assert identity is not None, "the issued credential has to work"
    finally:
        with session_scope() as s:
            s.execute(delete(User).where(User.employee_id == str(emp.meta["id"])))
            s.execute(delete(ExitCase).where(
                ExitCase.employee_id == str(emp.meta["id"])))
            s.execute(delete(Employee).where(Employee.id == str(emp.meta["id"])))
