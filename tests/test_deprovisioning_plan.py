"""`it_deprovisioning.execute_plan`'s `DeprovisioningPlan` CAS lifecycle.

test_deprovisioning_audit.py covers `systems.execute_deprovisioning_action`
and the `deprovisioning_actions` audit table directly. This file covers the
layer above it: taking a persisted plan from "pending" to "executed" exactly
once, no matter how many times it is called. No LLM is involved -- plans are
inserted directly, the way `plan_deprovisioning` would have left them.
"""
from __future__ import annotations

import uuid

import pytest

from backend.agents.it_deprovisioning import execute_plan
from backend.db import session_scope
from backend.db.models import DeprovisioningAction, DeprovisioningPlan

EMP_ID = "emp-test-deprov-plan"
CASE_ID = "case-test-deprov-plan"

ACTIONS = [
    {"step": 1, "type": "disable_account", "target": "Okta/SSO", "detail": "disable", "privileged": False},
    {"step": 2, "type": "revoke_access", "target": "AWS IAM", "detail": "revoke", "privileged": True},
]


def _new_plan_id() -> str:
    return f"dep-test-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def plan_id():
    """Insert a pending plan with two actions, clean up plan + audit rows after."""
    pid = _new_plan_id()
    with session_scope() as s:
        s.add(DeprovisioningPlan(
            id=pid,
            employee_id=EMP_ID,
            case_id=CASE_ID,
            plan={"employee_id": EMP_ID, "case_id": CASE_ID, "actions": list(ACTIONS)},
            status="pending",
        ))
    yield pid
    with session_scope() as s:
        row = s.get(DeprovisioningPlan, pid)
        if row is not None:
            s.delete(row)
        s.query(DeprovisioningAction).filter(
            DeprovisioningAction.employee_id == EMP_ID
        ).delete()


def _row(pid: str) -> DeprovisioningPlan:
    with session_scope() as s:
        row = s.get(DeprovisioningPlan, pid)
        s.expunge(row)
        return row


def test_executing_a_pending_plan_flips_it_to_executed(plan_id):
    execution = execute_plan(plan_id, approved_by="hr-tester", note="go ahead")

    assert execution["status"] == "executed"
    assert execution["executed"] == len(ACTIONS)
    assert execution["employee_id"] == EMP_ID
    assert execution["approved_by"] == "hr-tester"
    assert execution["approval_note"] == "go ahead"
    assert len(execution["audit_log"]) == len(ACTIONS)

    row = _row(plan_id)
    assert row.status == "executed"
    assert row.approved_by == "hr-tester"
    assert row.execution == execution


def test_executing_creates_one_audit_row_per_action(plan_id):
    execute_plan(plan_id, approved_by="hr-tester")

    with session_scope() as s:
        rows = s.query(DeprovisioningAction).filter(
            DeprovisioningAction.employee_id == EMP_ID
        ).all()
    assert len(rows) == len(ACTIONS)
    targets = {r.target for r in rows}
    assert targets == {a["target"] for a in ACTIONS}
    assert all(r.approved_by == "hr-tester" for r in rows)


def test_double_execution_is_idempotent(plan_id):
    first = execute_plan(plan_id, approved_by="hr-tester", note="first")
    second = execute_plan(plan_id, approved_by="someone-else", note="second call")

    assert second == first

    with session_scope() as s:
        count = s.query(DeprovisioningAction).filter(
            DeprovisioningAction.employee_id == EMP_ID
        ).count()
    assert count == len(ACTIONS), "actions must not be re-executed on a replayed call"


def test_execute_plan_rejects_an_unknown_plan_id():
    with pytest.raises(LookupError, match="no-such-plan-id"):
        execute_plan("no-such-plan-id")


def test_a_plan_with_no_actions_reports_nothing_to_execute():
    pid = _new_plan_id()
    with session_scope() as s:
        s.add(DeprovisioningPlan(
            id=pid,
            employee_id=EMP_ID,
            case_id=CASE_ID,
            plan={"employee_id": EMP_ID, "case_id": CASE_ID, "actions": []},
            status="pending",
        ))
    try:
        execution = execute_plan(pid, approved_by="hr-tester")
        assert execution["status"] == "nothing_to_execute"
        assert execution["executed"] == 0
        assert execution["audit_log"] == []
    finally:
        with session_scope() as s:
            row = s.get(DeprovisioningPlan, pid)
            if row is not None:
                s.delete(row)
