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
from backend import auth
from backend.db.models import DeprovisioningAction, DeprovisioningPlan, User

# Imported through `backend.main` on purpose: `backend.orchestration.graph` has
# a known import cycle with the agent registry and cannot be imported first.
import backend.main  # noqa: F401  (side effect: resolves the cycle)
from backend.orchestration import graph  # noqa: E402

ACTIONS = [
    {"step": 1, "type": "disable_account", "target": "Okta/SSO", "privileged": False},
    {"step": 2, "type": "revoke_access", "target": "AWS IAM", "privileged": True},
]


@pytest.fixture
def two_people():
    """An approver and an executor, as real rows.

    `deprovisioning_plans.approved_by_user_id` carries a foreign key to
    `users`, which is the constraint that makes the separation-of-duties check
    meaningful rather than a comparison of two arbitrary integers -- so the
    test has to honour it too.
    """
    suffix = uuid.uuid4().hex[:8]
    emails = {"approver": f"pytest-sod-hr-{suffix}@example.com",
              "executor": f"pytest-sod-it-{suffix}@example.com"}
    with session_scope() as s:
        rows = {}
        for role_key, email in emails.items():
            row = User(email=email, password_hash=auth.hash_password("x" * 12),
                       role="hr" if role_key == "approver" else "it",
                       full_name=f"Pytest {role_key}")
            s.add(row)
            s.flush()
            rows[role_key] = row.id
    yield rows
    with session_scope() as s:
        s.execute(delete(User).where(User.email.in_(list(emails.values()))))


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


def _state(plan_id, employee_id, case_id, approver_id, *, decision="approve"):
    return {
        "employee_id": employee_id,
        "case_id": case_id,
        "errors": [],
        "approval": {"decision": decision, "by": "pytest-gate",
                     "by_user_id": approver_id, "note": "auto-approve"},
        "deprov_plan": {"plan_id": plan_id, "actions": list(ACTIONS)},
    }


def test_the_gate_authorises_the_plan_and_leaves_it_for_it_to_run(pending_plan, two_people):
    """The gate authorises. It does not execute.

    This test used to assert the opposite, with a comment claiming "the gate's
    approver is recorded as the approver, and the orchestrator as the executor
    -- not one person doing both." That was the fiction. `execute_plan`'s
    separation-of-duties check was `executor_user_id is not None and
    approved_by_user_id == executor_user_id`, and the node passed `None` for
    both, so the guard was skipped rather than satisfied. One HR account
    posting to `/api/orchestrate/{case_id}` approved a model-authored plan and
    revoked every account in it, in one request, alone -- and the row could not
    even show that, because `approved_by_user_id` was NULL.

    Authorising and carrying out are two acts by two people. The second one
    already exists: IT works the plan step by step on that person's clearance
    page, each revocation logged against the engineer who made it.
    """
    plan_id, employee_id, case_id = pending_plan

    out = graph.deprov_execute(
        _state(plan_id, employee_id, case_id, two_people["approver"]))

    assert not out.get("errors"), out.get("errors")
    assert out["deprov_execution"]["status"] == "authorised"
    assert out["deprov_execution"]["executed"] == 0
    assert out["deprov_execution"]["awaiting"] == len(ACTIONS)

    with session_scope() as s:
        row = s.get(DeprovisioningPlan, plan_id)
        # Authorised and waiting, not executed.
        assert row.status == "approved"
        assert row.approved_by == "pytest-gate"
        assert row.approved_by_user_id == two_people["approver"]
        assert row.executed_by is None

        # Nothing was revoked by this run.
        assert not s.scalars(select(DeprovisioningAction).where(
            DeprovisioningAction.employee_id == employee_id)).all()


def test_an_approval_with_no_account_behind_it_cannot_be_executed(pending_plan):
    """An unidentified approver used to make the two-person check vanish. It is
    a refusal now: a machine actor with no human behind it has not satisfied
    anything."""
    from backend.agents.it_deprovisioning import approve_plan, execute_plan

    plan_id, employee_id, _ = pending_plan
    approve_plan(plan_id, approved_by="api (auto)", approver_user_id=None)

    with pytest.raises(PermissionError) as caught:
        execute_plan(plan_id, executed_by="orchestrator", executor_user_id=None)
    assert "nobody to check the executor against" in str(caught.value)


def test_an_unattributed_executor_is_refused(pending_plan, two_people):
    from backend.agents.it_deprovisioning import approve_plan, execute_plan

    plan_id, _, _ = pending_plan
    approve_plan(plan_id, approved_by="Pytest HR", approver_user_id=two_people["approver"])

    with pytest.raises(PermissionError) as caught:
        execute_plan(plan_id, executed_by="orchestrator", executor_user_id=None)
    assert "attributable to an account" in str(caught.value)


def test_the_approver_still_cannot_be_the_executor(pending_plan, two_people):
    from backend.agents.it_deprovisioning import approve_plan, execute_plan

    plan_id, _, _ = pending_plan
    approve_plan(plan_id, approved_by="Pytest HR", approver_user_id=two_people["approver"])

    with pytest.raises(PermissionError) as caught:
        execute_plan(plan_id, executed_by="Pytest HR", executor_user_id=two_people["approver"])
    assert "cannot also" in str(caught.value)


def test_a_second_person_can_execute_it(pending_plan, two_people):
    from backend.agents.it_deprovisioning import approve_plan, execute_plan

    plan_id, employee_id, _ = pending_plan
    approve_plan(plan_id, approved_by="Pytest HR", approver_user_id=two_people["approver"])

    result = execute_plan(plan_id, executed_by="Pytest IT",
                          executor_user_id=two_people["executor"])
    assert result["executed"] == len(ACTIONS)
    with session_scope() as s:
        assert len(s.scalars(select(DeprovisioningAction).where(
            DeprovisioningAction.employee_id == employee_id)).all()) == len(ACTIONS)


def test_a_rejected_gate_executes_nothing(pending_plan):
    plan_id, employee_id, case_id = pending_plan

    out = graph.deprov_execute(
        _state(plan_id, employee_id, case_id, None, decision="reject"))

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


def test_the_node_reports_a_refusal_instead_of_crashing(pending_plan, two_people):
    """A plan for somebody with no exit case cannot be approved. The node must
    surface that as an error on the run, not raise out of the graph."""
    plan_id, employee_id, case_id = pending_plan
    with session_scope() as s:
        s.get(DeprovisioningPlan, plan_id).employee_id = "emp-does-not-exist"

    out = graph.deprov_execute(
        _state(plan_id, "emp-does-not-exist", case_id, two_people["approver"]))

    assert out["deprov_execution"]["status"] == "error"
    assert out["errors"], "a refusal has to reach the run's error list"
    assert "no exit case" in out["errors"][0]
