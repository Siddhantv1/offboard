"""`document_status`'s per-document branch logic.

test_document_persistence.py exercises the full upload -> verdict -> status
pipeline against real case rows. These tests go straight at `document_status`
with constructed `case_meta` dicts (same style as test_new_agents.py's
CLEAN_CASE_META) to hit the two branches that pipeline never reaches on its
own: a collectable document whose stage has not opened yet ("not_yet_due"),
and a company-issued document before/after sign-off ("to_be_issued"/"issued").
"""
from __future__ import annotations

from backend.agents.document_collection import document_status

BASE_META = {
    "case_id": "case-test-status",
    "documents_submitted": [],
    "stages": [
        {"name": "IT Clearance", "status": "pending", "owner": "", "days_open": 0},
        {"name": "Finance Clearance", "status": "pending", "owner": "", "days_open": 0},
        {"name": "HR Clearance", "status": "pending", "owner": "", "days_open": 0},
        {"name": "Knowledge Transfer", "status": "pending", "owner": "", "days_open": 0},
        {"name": "Manager Sign-off", "status": "pending", "owner": "", "days_open": 0},
    ],
}


def _row(result: dict, name: str) -> dict:
    return next(r for r in result["documents"] if r["document"] == name)


def test_resignation_paperwork_is_always_due():
    """Resignation-stage docs are due from day one, regardless of case stage."""
    result = document_status(BASE_META)
    assert _row(result, "Notice acknowledgement")["status"] == "missing"


def test_nothing_is_asked_for_twice():
    """A resignation letter and contact details were requested from somebody
    who resigned through this product and whose record already holds both. The
    exit interview was requested as a scan as well as being collected on its
    own page, producing two records of one conversation."""
    names = {r["document"] for r in document_status(BASE_META)["documents"]}
    for gone in ("Resignation letter", "Personal contact details",
                 "Exit interview form"):
        assert gone not in names, f"{gone} is still being asked for"


def test_it_clearance_asks_for_no_documents_at_all():
    """It is a two-sided mark, not paperwork.

    The three documents it used to ask for -- an asset return checklist, a
    device handover acknowledgement, a VPN/MFA deactivation confirmation --
    were not things anybody holds, and a leaver could satisfy all three with a
    blank PDF. The employee confirms they handed everything back and IT
    verifies it against their own asset system instead.
    """
    names = [r["document"] for r in document_status(BASE_META)["documents"]]
    assert not any("Asset Return" in n or "Device handover" in n
                   or "VPN/MFA" in n for n in names)


def test_a_collectable_document_is_not_yet_due_before_its_stage_opens():
    """Finance Clearance is still 'pending', so its paperwork is not due -- and
    must not be counted as missing (which would make the case look overdue for
    documents nobody could have submitted yet)."""
    result = document_status(BASE_META)
    row = _row(result, "No-dues declaration")

    assert row["status"] == "not_yet_due"
    assert row not in result["missing"]


def test_a_collectable_document_becomes_missing_once_its_stage_opens():
    meta = {**BASE_META, "stages": [
        {**s, "status": "in_progress"} if s["name"] == "Finance Clearance" else s
        for s in BASE_META["stages"]
    ]}
    row = _row(document_status(meta), "No-dues declaration")

    assert row["status"] == "missing"


def test_a_submitted_document_is_submitted_regardless_of_stage():
    meta = {**BASE_META, "documents_submitted": ["No-dues declaration"]}
    row = _row(document_status(meta), "No-dues declaration")

    assert row["status"] == "submitted"


def test_an_issued_document_is_to_be_issued_before_sign_off():
    """Relieving letter etc. are produced by the company, not collected -- so
    before it exists it is 'to_be_issued', never 'missing'."""
    result = document_status(BASE_META)
    row = _row(result, "Relieving letter")

    assert row["status"] == "to_be_issued"
    assert row not in result["missing"]


def test_an_issued_document_flips_to_issued_once_recorded():
    meta = {**BASE_META, "documents_submitted": ["Relieving letter"]}
    row = _row(document_status(meta), "Relieving letter")

    assert row["status"] == "issued"


def test_final_sign_off_documents_are_never_missing():
    """Final Sign-off paperwork is only ever produced at closeout, so it must
    never appear in `missing` no matter how far along the case is."""
    result = document_status(BASE_META)

    assert all(r["stage"] != "Final Sign-off" for r in result["missing"])


def test_issued_documents_are_excluded_from_the_collectable_total():
    result = document_status(BASE_META)
    issued_names = {"Relieving letter", "Experience letter",
                    "Full-and-final settlement statement"}
    assert result["collectable_total"] == len(
        [d for d in result["documents"] if d["document"] not in issued_names]
    )
