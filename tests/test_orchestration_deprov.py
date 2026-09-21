"""The orchestrator's deprovisioning step.

This exists because that step silently broke and nothing noticed. Splitting
approval from execution changed `execute_plan`'s signature, and the
orchestrator still called it the old way -- so every end-to-end run finished
with `deprov_execute: execute_plan() got an unexpected keyword argument
'approved_by'` in its errors while still reporting a result. The unit tests
covered `execute_plan` directly and the HTTP route, but nothing covered the
graph node that calls it, which is the one place the two had to agree.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from backend.agents.it_deprovisioning import execute_plan
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import DeprovisioningAction, DeprovisioningPlan

# Imported through `backend.main` on purpose: `backend.orchestration.graph` has
# a known import cycle with the agent registry and cannot be imported first.
import backend.main  # noqa: F401  (side effect: resolves the cycle)
from backend.orchestration import graph  # noqa: E402

ACTIONS = [
    {"step": 1, "type": "disable_account", "target": "Okta/SSO", "privileged": False},
    {"step": 2, "type": "revoke_access", "target": "AWS IAM", "privileged": True},
]


@pytest.fixture
def pending_plan():
    """A drafted plan for a real leaver, in the state the gate leaves it."""
    cases = store.exit_cases()
    if not cases:
        pytest.skip("no seeded exit case")
    case = cases[0]
    employee_id = str(case.meta["employee_id"])
    plan_id = f"dep-orch-{uuid.uuid4().hex[:8]}"

    with session_scope() as s:
        s.add(DeprovisioningPlan(
            id=plan_id,
            employee_id=employee_id,
            case_id=str(case.meta["case_id"]),
            plan={"employee_id": employee_id, "case_id": str(case.meta["case_id"]),
                  "plan_id": plan_id, "actions": list(ACTIONS)},
            status="pending",
        ))
    yield plan_id, employee_id, str(case.meta["case_id"])
    with session_scope() as s:
        row = s.get(DeprovisioningPlan, plan_id)
        if row is not None:
            s.delete(row)
        s.execute(delete(DeprovisioningAction).where(
            DeprovisioningAction.employee_id == employee_id))


def _state(plan_id, employee_id, case_id, *, decision="approve"):
    return {
        "employee_id": employee_id,
        "case_id": case_id,
        "errors": [],
        "approval": {"decision": decision, "by": "pytest-gate", "note": "auto-approve"},
        "deprov_plan": {"plan_id": plan_id, "actions": list(ACTIONS)},
    }


def test_the_gate_approving_actually_executes_the_plan(pending_plan):
    """The regression, pinned.

    A signature mismatch here does not raise out of the node -- it is caught
    and folded into `errors`, so the run still "succeeds" with a broken step.
    Asserting on `errors` is the only thing that catches it.
    """
    plan_id, employee_id, case_id = pending_plan

    out = graph.deprov_execute(_state(plan_id, employee_id, case_id))

    assert not out.get("errors"), out.get("errors")
    assert out["deprov_execution"]["status"] == "executed"
    assert out["deprov_execution"]["executed"] == len(ACTIONS)

    with session_scope() as s:
        row = s.get(DeprovisioningPlan, plan_id)
        assert row.status == "executed"
        # The gate's approver is recorded as the approver, and the orchestrator
        # as the executor -- not one person doing both.
        assert row.approved_by == "pytest-gate"
        assert row.executed_by == "orchestrator"

        actions = s.scalars(select(DeprovisioningAction).where(
            DeprovisioningAction.employee_id == employee_id)).all()
        assert len(actions) == len(ACTIONS)


def test_a_rejected_gate_executes_nothing(pending_plan):
    plan_id, employee_id, case_id = pending_plan

    out = graph.deprov_execute(_state(plan_id, employee_id, case_id, decision="reject"))

    assert out["deprov_execution"]["status"] == "skipped"
    assert out["deprov_execution"]["executed"] == 0
    with session_scope() as s:
        assert s.get(DeprovisioningPlan, plan_id).status == "pending"
        assert not s.scalars(select(DeprovisioningAction).where(
            DeprovisioningAction.employee_id == employee_id)).all()


def test_the_orchestrator_cannot_bypass_the_approval_requirement(pending_plan):
    """`execute_plan` refuses an unapproved plan. That refusal is what makes it
    safe for IT to hold the execute button, so the orchestrator has to satisfy
    it rather than route around it."""
    plan_id, _, _ = pending_plan
    with pytest.raises(PermissionError, match="has not been approved"):
        execute_plan(plan_id, executed_by="orchestrator")


def test_the_node_reports_a_refusal_instead_of_crashing(pending_plan):
    """A plan for somebody with no exit case cannot be approved. The node must
    surface that as an error on the run, not raise out of the graph."""
    plan_id, employee_id, case_id = pending_plan
    with session_scope() as s:
        s.get(DeprovisioningPlan, plan_id).employee_id = "emp-does-not-exist"

    out = graph.deprov_execute(_state(plan_id, "emp-does-not-exist", case_id))

    assert out["deprov_execution"]["status"] == "error"
    assert out["errors"], "a refusal has to reach the run's error list"
    assert "no exit case" in out["errors"][0]
