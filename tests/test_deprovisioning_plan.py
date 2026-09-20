"""`it_deprovisioning`'s plan lifecycle: draft -> approve -> execute.

test_deprovisioning_audit.py covers `systems.execute_deprovisioning_action` and
the `deprovisioning_actions` audit table directly. This file covers the layer
above it: taking a persisted plan through its states exactly once, no matter
how many times it is called, and refusing the combinations that would make the
human approval gate decorative. No LLM is involved -- plans are inserted
directly, the way `plan_deprovisioning` would have left them.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from backend import auth
from backend.agents.it_deprovisioning import approve_plan, execute_plan
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import DeprovisioningAction, DeprovisioningPlan, User

EMP_ID = "emp-test-deprov-plan"
CASE_ID = "case-test-deprov-plan"

ACTIONS = [
    {"step": 1, "type": "disable_account", "target": "Okta/SSO", "detail": "disable", "privileged": False},
    {"step": 2, "type": "revoke_access", "target": "AWS IAM", "detail": "revoke", "privileged": True},
]


def _new_plan_id() -> str:
    return f"dep-test-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def actors():
    """An HR approver and an IT executor -- two different people, which is the
    whole point of the split."""
    suffix = uuid.uuid4().hex[:8]
    emails = {
        "hr": f"pytest-deprov-hr-{suffix}@example.com",
        "it": f"pytest-deprov-it-{suffix}@example.com",
    }
    ids = {}
    with session_scope() as s:
        for role, email in emails.items():
            u = User(email=email, password_hash=auth.hash_password("x" * 12),
                     role=role, full_name=f"Pytest {role.upper()}")
            s.add(u)
            s.flush()
            ids[role] = u.id
    yield ids
    with session_scope() as s:
        s.execute(delete(User).where(User.email.in_(list(emails.values()))))


def _insert(pid: str, *, status: str, actions=None, approver_id=None,
            employee_id: str = EMP_ID) -> None:
    with session_scope() as s:
        s.add(DeprovisioningPlan(
            id=pid,
            employee_id=employee_id,
            case_id=CASE_ID,
            plan={"employee_id": employee_id, "case_id": CASE_ID,
                  "actions": list(ACTIONS if actions is None else actions)},
            status=status,
            approved_by="hr-tester" if status == "approved" else None,
            approved_by_user_id=approver_id if status == "approved" else None,
            note="go ahead" if status == "approved" else "",
        ))


def _cleanup(pid: str, employee_id: str = EMP_ID) -> None:
    with session_scope() as s:
        row = s.get(DeprovisioningPlan, pid)
        if row is not None:
            s.delete(row)
        s.execute(delete(DeprovisioningAction).where(
            DeprovisioningAction.employee_id == employee_id))


@pytest.fixture
def approved_plan(actors):
    """A plan HR has already signed off, ready for IT to run."""
    pid = _new_plan_id()
    _insert(pid, status="approved", approver_id=actors["hr"])
    yield pid
    _cleanup(pid)


def _row(pid: str) -> DeprovisioningPlan:
    with session_scope() as s:
        row = s.get(DeprovisioningPlan, pid)
        s.expunge(row)
        return row


# -- execution ----------------------------------------------------------------
def test_executing_an_approved_plan_flips_it_to_executed(approved_plan, actors):
    execution = execute_plan(approved_plan, executed_by="it-tester",
                             executor_user_id=actors["it"])

    assert execution["status"] == "executed"
    assert execution["executed"] == len(ACTIONS)
    assert execution["employee_id"] == EMP_ID
    assert execution["approved_by"] == "hr-tester"
    assert execution["executed_by"] == "it-tester"
    assert len(execution["audit_log"]) == len(ACTIONS)

    row = _row(approved_plan)
    assert row.status == "executed"
    assert row.approved_by == "hr-tester"
    assert row.executed_by == "it-tester"
    assert row.execution == execution


def test_executing_creates_one_audit_row_per_action(approved_plan, actors):
    execute_plan(approved_plan, executed_by="it-tester", executor_user_id=actors["it"])

    with session_scope() as s:
        rows = s.scalars(select(DeprovisioningAction).where(
            DeprovisioningAction.employee_id == EMP_ID)).all()
    assert len(rows) == len(ACTIONS)
    assert {r.target for r in rows} == {a["target"] for a in ACTIONS}
    # The audit row names the APPROVER, not whoever pressed the button.
    assert all(r.approved_by == "hr-tester" for r in rows)


def test_double_execution_is_idempotent(approved_plan, actors):
    first = execute_plan(approved_plan, executed_by="it-tester",
                         executor_user_id=actors["it"])
    second = execute_plan(approved_plan, executed_by="someone-else",
                          executor_user_id=actors["it"])

    assert second == first

    with session_scope() as s:
        count = len(s.scalars(select(DeprovisioningAction).where(
            DeprovisioningAction.employee_id == EMP_ID)).all())
    assert count == len(ACTIONS), "actions must not be re-executed on a replayed call"


def test_execute_plan_rejects_an_unknown_plan_id():
    with pytest.raises(LookupError, match="no-such-plan-id"):
        execute_plan("no-such-plan-id", executed_by="it-tester")


def test_a_plan_with_no_actions_reports_nothing_to_execute(actors):
    pid = _new_plan_id()
    _insert(pid, status="approved", actions=[], approver_id=actors["hr"])
    try:
        execution = execute_plan(pid, executed_by="it-tester",
                                 executor_user_id=actors["it"])
        assert execution["status"] == "nothing_to_execute"
        assert execution["executed"] == 0
        assert execution["audit_log"] == []
    finally:
        _cleanup(pid)


# -- the separation of duties -------------------------------------------------
def test_an_unapproved_plan_cannot_be_executed(actors):
    """The gate, made real.

    Opening the execute route to IT is only safe because this refuses. Without
    it, an IT account could draft a revocation plan against anyone, run it, and
    have the compliance record name themselves as the approver -- the human
    approval gate would still exist but carry no weight.
    """
    pid = _new_plan_id()
    _insert(pid, status="pending")
    try:
        with pytest.raises(PermissionError, match="has not been approved"):
            execute_plan(pid, executed_by="it-tester", executor_user_id=actors["it"])
        # ...and nothing ran.
        with session_scope() as s:
            assert not s.scalars(select(DeprovisioningAction).where(
                DeprovisioningAction.employee_id == EMP_ID)).all()
        assert _row(pid).status == "pending"
    finally:
        _cleanup(pid)


def test_the_approver_cannot_also_execute(actors):
    """Four eyes. One person holding both halves is the thing the split exists
    to prevent, so it is refused even though that person is HR."""
    pid = _new_plan_id()
    _insert(pid, status="approved", approver_id=actors["hr"])
    try:
        with pytest.raises(PermissionError, match="cannot also"):
            execute_plan(pid, executed_by="hr-tester", executor_user_id=actors["hr"])
        assert _row(pid).status == "approved", "the plan must stay runnable by someone else"
    finally:
        _cleanup(pid)


def test_a_plan_for_someone_who_is_not_leaving_cannot_be_approved(actors):
    """`plan_deprovisioning` only checks the employee exists, so this is where a
    revocation plan against an active employee gets caught. Executing one would
    not be a deprovisioning -- it would be an account takeover with paperwork.
    """
    pid = _new_plan_id()
    _insert(pid, status="pending", employee_id=EMP_ID)  # no exit case exists
    try:
        with pytest.raises(PermissionError, match="no exit case"):
            approve_plan(pid, approved_by="hr-tester",
                         approver_user_id=actors["hr"])
        assert _row(pid).status == "pending"
    finally:
        _cleanup(pid)


def test_approving_a_real_leaver_works_and_records_the_approver(actors):
    cases = store.exit_cases()
    if not cases:
        pytest.skip("no seeded exit case")
    employee_id = str(cases[0].meta["employee_id"])

    pid = _new_plan_id()
    _insert(pid, status="pending", employee_id=employee_id)
    try:
        result = approve_plan(pid, approved_by="hr-tester",
                              approver_user_id=actors["hr"], note="checked")
        assert result["status"] == "approved"

        row = _row(pid)
        assert row.status == "approved"
        assert row.approved_by == "hr-tester"
        assert row.approved_by_user_id == actors["hr"]
        assert row.approved_at is not None

        # ...and now a different person can run it.
        execution = execute_plan(pid, executed_by="it-tester",
                                 executor_user_id=actors["it"])
        assert execution["status"] == "executed"
    finally:
        _cleanup(pid, employee_id)
