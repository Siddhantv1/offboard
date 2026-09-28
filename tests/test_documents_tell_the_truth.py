"""What the leaving documents claim, against what the records say.

The relieving letter stated "Company assets issued to them have been accounted
for and system access has been revoked" unconditionally, and `readiness` gated
issuance on the five stage statuses alone. `stages.transition` deliberately
lets IT sign off with items outstanding as long as they give a reason ("YubiKey
lost, writing it off"), so the two together produced a signed statement on
company letterhead that the company's own `access_status` contradicted.

The settlement statement had the mirror problem: it listed `returned` and
`written_off` and nothing else, so an asset in the default `outstanding` state
-- no `asset_returns` row at all -- appeared nowhere, under a sentence saying
there was nothing to recover.

And `days_open`, which decides whether anything is late, was published raw.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pypdf import PdfReader
from sqlalchemy import delete

from backend import issued_documents
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import (AssetReturn, DeprovisioningAction, Employee,
                               ExitCase, User, effective_days_open)


def _text(pdf: bytes) -> str:
    """Page text with the layout's line breaks collapsed.

    reportlab justifies these paragraphs, so a phrase is as likely to arrive as
    "have been accounted\\nfor" as on one line -- and an assertion that a
    sentence is *absent* would then pass for the wrong reason.
    """
    import re
    from io import BytesIO

    raw = " ".join((p.extract_text() or "") for p in PdfReader(BytesIO(pdf)).pages)
    return re.sub(r"\s+", " ", raw)


@pytest.fixture
def cleared_case():
    """Every stage completed, one laptop never logged, no accounts revoked."""
    suffix = uuid.uuid4().hex[:8]
    emp, case, _ = store.add_employee({
        "name": "Pytest Truthful Docs", "role": "Engineer",
        "department": "Engineering", "email": f"pytest-td-{suffix}@example.com",
        "status": "exiting", "assets_assigned": ["MacBook Pro"],
        "access_systems": ["Okta/SSO"],
    })
    ids = {"case_id": str(case.meta["case_id"]),
           "employee_id": str(emp.meta["id"])}
    with session_scope() as s:
        for row in s.get(ExitCase, ids["case_id"]).stages:
            row.status = "completed"
    yield ids
    with session_scope() as s:
        s.execute(delete(AssetReturn).where(
            AssetReturn.employee_id == ids["employee_id"]))
        s.execute(delete(DeprovisioningAction).where(
            DeprovisioningAction.employee_id == ids["employee_id"]))
        s.execute(delete(User).where(User.employee_id == ids["employee_id"]))
        s.execute(delete(ExitCase).where(ExitCase.case_id == ids["case_id"]))
        s.execute(delete(Employee).where(Employee.id == ids["employee_id"]))


def test_the_relieving_letter_does_not_claim_an_unreturned_laptop_is_back(
        cleared_case):
    body = _text(issued_documents.relieving_letter(cleared_case["case_id"]))

    assert "have been accounted for" not in body
    assert "signed off with items outstanding" in body
    # The release itself is still asserted -- `readiness` gates that on every
    # stage being complete, which it is.
    assert "has been relieved from the services" in body


def test_the_letter_says_so_plainly_when_everything_really_is_back(
        cleared_case):
    from backend.systems import execute_deprovisioning_action

    with session_scope() as s:
        s.add(AssetReturn(employee_id=cleared_case["employee_id"],
                          case_id=cleared_case["case_id"], asset="MacBook Pro",
                          state="returned", logged_by="Pytest IT"))
    execute_deprovisioning_action(
        {"step": 1, "type": "disable_account", "target": "Okta/SSO"},
        employee_id=cleared_case["employee_id"],
        case_id=cleared_case["case_id"], approved_by="Pytest HR")

    body = _text(issued_documents.relieving_letter(cleared_case["case_id"]))
    assert "have been accounted for" in body
    assert "items outstanding" not in body


def test_the_settlement_statement_names_what_is_unaccounted_for(cleared_case):
    body = _text(issued_documents.settlement_statement(cleared_case["case_id"]))

    assert "MacBook Pro" in body
    assert "Not accounted for" in body
    # And it stops claiming there is nothing to recover.
    assert "no dues were outstanding" not in body


def test_the_documents_are_still_issued_at_all(cleared_case):
    """Gating issuance on the asset position was tried and reverted: a person
    can be released owing a laptop, and the settlement statement exists to
    record exactly that -- withholding it until the laptop comes back withholds
    the document whose job is to say it has not."""
    state = issued_documents.readiness(cleared_case["case_id"])
    assert state["ready"] is True
    for doc_id in issued_documents.ISSUED:
        pdf, _ = issued_documents.generate(cleared_case["case_id"], doc_id)
        assert pdf.startswith(b"%PDF")


# -- days_open ----------------------------------------------------------------
def test_a_stage_that_has_been_open_for_weeks_says_so(cleared_case):
    """`CaseStage.to_meta` published the stored column, which nothing
    increments. Every agent reading it -- dashboard_insights, policy_auditor,
    sla_escalation, workflow_optimizer, systems, and the orchestrator's
    escalate node -- saw 0 on every case the product created, while
    `recompute_case` marked the same case at risk off `status_changed_at`."""
    with session_scope() as s:
        row = next(r for r in s.get(ExitCase, cleared_case["case_id"]).stages
                   if r.stage_key == "it")
        row.status = "in_progress"
        row.days_open = 0
        row.status_changed_at = datetime.now(timezone.utc) - timedelta(days=30)

    case = store.exit_case(cleared_case["case_id"])
    stage = next(s for s in case.meta["stages"] if s["name"] == "IT Clearance")
    assert stage["days_open"] >= 30, "the agents read this number"


def test_a_seeded_value_still_wins_over_a_shorter_elapsed_time():
    """The corpus carries hand-authored numbers and the golden snapshot pins
    them, so the max() is load-bearing in both directions."""
    assert effective_days_open({"days_open": 9, "status_changed_at": None}) == 9
    assert effective_days_open({
        "days_open": 9,
        "status_changed_at": datetime.now(timezone.utc) - timedelta(days=2),
    }) == 9
