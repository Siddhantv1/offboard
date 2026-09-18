"""The reported bug: an accepted upload vanished.

`validate_upload` OCR'd the scan, asked the model to judge it, returned the
verdict -- and wrote nothing. `documents_submitted` was only ever read, and
only ever written as `[]` at case creation, so the next `document_status()`
call still reported the document missing and the agent drafted a reminder
chasing a form that had just been submitted.

These tests exercise `record_upload` directly rather than `validate_upload`,
so they cover persistence without spending a gateway call on OCR and
judgement.
"""
from __future__ import annotations

import pytest

from backend.agents.document_collection import document_status, record_upload
from backend.data_store import DataStore, store
from backend.db import session_scope
from backend.db.models import CaseDocument

# A real entry in REQUIRED_DOCUMENTS, due at a stage that is under way on the
# fixture case, so document_status reports it as "missing" rather than
# "not_yet_due" before the upload lands.
DOC = "No-dues declaration"


@pytest.fixture
def case_id() -> str:
    """A case that does not already have DOC submitted, cleaned up after."""
    for case in store.exit_cases():
        if DOC not in (case.meta.get("documents_submitted") or []):
            cid = case.meta["case_id"]
            break
    else:
        pytest.skip(f"every case already has {DOC!r} submitted")

    yield cid

    with session_scope() as session:
        session.query(CaseDocument).filter(
            CaseDocument.case_id == cid,
            CaseDocument.document_name == DOC,
        ).delete(synchronize_session=False)


def _upload(case_id: str, verdict: str) -> int | None:
    return record_upload(
        case_id=case_id,
        document_name=DOC,
        filename="scan.png",
        content_type="image/png",
        image_bytes=b"\x89PNG-not-a-real-image",
        extracted={"engine": "winocr", "text": "No dues outstanding.",
                   "confidence": 0.94, "seconds": 0.41},
        judgement={"verdict": verdict, "reason": "Signed and dated.",
                   "signed": True, "dated": True},
        uploaded_by="test",
    )


def test_accepted_upload_appears_in_documents_submitted(case_id):
    assert DOC not in store.exit_case(case_id).meta["documents_submitted"]

    assert _upload(case_id, "accepted") is not None

    assert DOC in store.exit_case(case_id).meta["documents_submitted"], (
        "accepted upload did not persist against the case"
    )


def test_accepted_upload_flips_the_document_to_submitted(case_id):
    _upload(case_id, "accepted")
    rows = document_status(store.exit_case(case_id).meta)["documents"]
    row = next(r for r in rows if r["document"] == DOC)
    assert row["status"] == "submitted"


def test_accepted_upload_raises_the_completion_percentage(case_id):
    before = document_status(store.exit_case(case_id).meta)
    _upload(case_id, "accepted")
    after = document_status(store.exit_case(case_id).meta)
    assert after["submitted_count"] == before["submitted_count"] + 1
    assert after["completion_pct"] >= before["completion_pct"]


def test_rejected_upload_is_recorded_but_does_not_count(case_id):
    """A rejected scan must leave an audit trail -- you need to know someone
    tried -- without satisfying the requirement."""
    _upload(case_id, "rejected")

    assert DOC not in store.exit_case(case_id).meta["documents_submitted"]
    with session_scope() as session:
        row = session.query(CaseDocument).filter(
            CaseDocument.case_id == case_id,
            CaseDocument.document_name == DOC,
        ).one()
        assert row.verdict == "rejected"
        assert row.ocr_text == "No dues outstanding."
        assert row.ocr_engine == "winocr"
        assert row.image_bytes == len(b"\x89PNG-not-a-real-image")
        assert row.image is not None, "the scan itself should be retained"


def test_resubmitting_after_a_rejection_clears_the_document(case_id):
    """The realistic sequence: a bad scan, then a good one. The later verdict
    must win, or the employee is permanently blocked by their first attempt."""
    _upload(case_id, "rejected")
    assert DOC not in store.exit_case(case_id).meta["documents_submitted"]

    _upload(case_id, "accepted")
    assert DOC in store.exit_case(case_id).meta["documents_submitted"]


def test_a_later_rejection_un_clears_an_accepted_document(case_id):
    """The inverse. If a document is re-submitted and rejected, it must stop
    counting -- otherwise a bad replacement silently inherits the old pass."""
    _upload(case_id, "accepted")
    _upload(case_id, "rejected")
    assert DOC not in store.exit_case(case_id).meta["documents_submitted"]


def test_repeated_acceptance_lists_the_document_once(case_id):
    _upload(case_id, "accepted")
    _upload(case_id, "accepted")
    submitted = store.exit_case(case_id).meta["documents_submitted"]
    assert submitted.count(DOC) == 1


def test_upload_is_visible_to_a_separate_store_instance(case_id):
    """The old `_EXECUTED`-style failure mode: state that lives only in the
    process that wrote it. Another worker must see this."""
    _upload(case_id, "accepted")
    assert DOC in DataStore().exit_case(case_id).meta["documents_submitted"]


def test_review_document_overrides_the_verdict(case_id):
    """The point of the human override: a model verdict that flows into
    `documents_submitted` (and everything downstream of it) must be exactly
    what a reviewer's override changes -- not a note bolted on beside an
    unchanged status."""
    doc_id = _upload(case_id, "needs_review")
    assert DOC not in store.exit_case(case_id).meta["documents_submitted"]

    reviewed = store.review_document(
        case_id, doc_id, verdict="accepted",
        reviewed_by="hr@perficient.com", note="Checked manually, it's fine.",
    )

    assert reviewed["verdict"] == "accepted"
    assert reviewed["reviewed_by"] == "hr@perficient.com"
    assert reviewed["review_note"] == "Checked manually, it's fine."
    assert reviewed["reviewed_at"] is not None
    assert DOC in store.exit_case(case_id).meta["documents_submitted"], (
        "the override did not flow through to documents_submitted"
    )


def test_review_document_can_also_revoke_an_acceptance(case_id):
    doc_id = _upload(case_id, "accepted")
    assert DOC in store.exit_case(case_id).meta["documents_submitted"]

    store.review_document(case_id, doc_id, verdict="rejected", reviewed_by="hr@perficient.com")

    assert DOC not in store.exit_case(case_id).meta["documents_submitted"]


def test_review_document_rejects_an_unknown_verdict(case_id):
    doc_id = _upload(case_id, "needs_review")
    with pytest.raises(ValueError):
        store.review_document(case_id, doc_id, verdict="maybe", reviewed_by="hr@perficient.com")


def test_review_document_rejects_a_doc_id_from_another_case(case_id):
    """A doc_id is a global primary key; the case_id in the URL must still be
    checked, or reviewing case-001's document #7 could silently overwrite a
    document that actually belongs to case-002."""
    other = next(c for c in store.exit_cases() if c.meta["case_id"] != case_id)
    doc_id = _upload(case_id, "needs_review")
    with pytest.raises(LookupError):
        store.review_document(
            other.meta["case_id"], doc_id, verdict="accepted", reviewed_by="hr@perficient.com"
        )


def test_case_documents_lists_every_verdict_not_just_accepted(case_id):
    """Unlike `documents_submitted`, this is the raw audit trail a reviewer
    needs: rejected and needs_review rows must show up too, not just passes."""
    _upload(case_id, "rejected")
    rows = store.case_documents(case_id)
    assert any(r["document_name"] == DOC and r["verdict"] == "rejected" for r in rows)


def test_unknown_case_is_rejected_by_the_foreign_key(case_id):
    """A typo in the case id must not silently create an orphaned document."""
    assert record_upload(
        case_id="case-does-not-exist",
        document_name=DOC, filename="x.png", content_type="image/png",
        image_bytes=b"x",
        extracted={"engine": "winocr", "text": "hello", "confidence": 0.9,
                   "seconds": 0.1},
        judgement={"verdict": "accepted", "reason": "ok"},
    ) is None
