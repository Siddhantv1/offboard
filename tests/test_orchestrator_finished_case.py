"""Re-running the orchestrator on an exit that is already done.

Reported against the live app: run the orchestrator for a fully cleared
employee and it still offers to authorise an IT deprovisioning plan for them.

The cause was that `run_deprov_plan` was the only one of the six parallel nodes
that neither looked at the state of the case nor could be skipped.
`run_routing`, `apply_decisions` and `escalate` all pass over completed stages;
this one drafted a plan unconditionally and persisted it, and `approval_gate`
paused because its only skip condition was "no plan" -- and there was always a
plan.

It is not a stray button. Authorising it runs `approve_plan` + `execute_plan`
against dead accounts and files another `executed` row. emp-001 accumulated
five of those, each with its own timestamp, so the record gives five different
answers to when their access was revoked.

Nothing here calls the gateway: the skip returns before `plan_deprovisioning`.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from backend.db import session_scope
from backend.db.models import (AssetReturn, DeprovisioningAction,
                               DeprovisioningPlan, Employee, ExitCase, User)
from backend.data_store import store


@pytest.fixture
def graph():
    # Imported through backend.main: backend.orchestration and backend.agents
    # form a cycle, so importing the graph module first fails.
    import backend.main  # noqa: F401
    from backend.orchestration import graph as module
    return module


@pytest.fixture
def case():
    """An exiting employee holding one asset and one live account."""
    suffix = uuid.uuid4().hex[:8]
    emp, exit_case, _ = store.add_employee({
        "name": "Pytest Orchestrator Rerun", "role": "Engineer",
        "department": "Engineering", "email": f"pytest-orr-{suffix}@example.com",
        "status": "exiting",
        "access_systems": ["Okta/SSO"],
        "assets_assigned": ["MacBook Pro"],
    })
    ids = {"case_id": str(exit_case.meta["case_id"]),
           "employee_id": str(emp.meta["id"])}
    yield ids
    with session_scope() as s:
        s.execute(delete(AssetReturn).where(
            AssetReturn.employee_id == ids["employee_id"]))
        s.execute(delete(DeprovisioningAction).where(
            DeprovisioningAction.employee_id == ids["employee_id"]))
        s.execute(delete(DeprovisioningPlan).where(
            DeprovisioningPlan.employee_id == ids["employee_id"]))
        s.execute(delete(User).where(User.employee_id == ids["employee_id"]))
        s.execute(delete(ExitCase).where(ExitCase.case_id == ids["case_id"]))
        s.execute(delete(Employee).where(Employee.id == ids["employee_id"]))


def _hand_everything_back(ids: dict[str, str]) -> None:
    """Log the asset returned and the account revoked, the way IT would."""
    from backend.systems import execute_deprovisioning_action

    with session_scope() as s:
        s.add(AssetReturn(employee_id=ids["employee_id"], case_id=ids["case_id"],
                          asset="MacBook Pro", state="returned",
                          logged_by="Pytest IT"))
    execute_deprovisioning_action(
        {"step": 1, "type": "disable_account", "target": "Okta/SSO",
         "privileged": False},
        employee_id=ids["employee_id"], case_id=ids["case_id"],
        approved_by="Pytest HR")


def _state(ids: dict[str, str]) -> dict:
    case = store.exit_case(ids["case_id"])
    return {"case_id": ids["case_id"], "employee_id": ids["employee_id"],
            "case_meta": case.meta, "case_notes": ""}


# -- the predicate ------------------------------------------------------------
def test_outstanding_work_is_read_from_the_records_it_maintains(case, graph):
    outstanding = graph.nothing_left_to_revoke(case["case_id"])
    assert "MacBook Pro" in outstanding
    assert "Okta/SSO" in outstanding

    _hand_everything_back(case)
    assert graph.nothing_left_to_revoke(case["case_id"]) == []


def test_an_unknown_case_is_not_treated_as_finished(graph):
    """An absence of records is not evidence the work is done — plan as normal
    rather than skipping on a missing row."""
    assert graph.nothing_left_to_revoke("case-does-not-exist") == ["unknown"]


# -- the node -----------------------------------------------------------------
def test_no_plan_is_drafted_when_there_is_nothing_to_revoke(case, graph):
    _hand_everything_back(case)

    out = graph.run_deprov_plan(_state(case))

    assert out["deprov_plan"] == {}
    assert "skipped" in out["log"][0]
    # And nothing was persisted, so there is no row for anyone to authorise.
    with session_scope() as s:
        assert s.query(DeprovisioningPlan).filter_by(
            employee_id=case["employee_id"]).count() == 0


def test_the_approval_gate_does_not_pause_on_a_finished_exit(case, graph):
    """The reported symptom: the panel offering to authorise a revocation for
    somebody with nothing left to revoke."""
    _hand_everything_back(case)
    state = {**_state(case), **graph.run_deprov_plan(_state(case))}

    # `interrupt()` would raise out of this call if the gate paused.
    out = graph.approval_gate(state)

    assert out["approval"]["decision"] == "skipped"
    assert out["approval"]["reason"] == "nothing left to revoke"


def test_a_real_exit_still_reaches_the_gate(case, graph):
    """The skip must not swallow the case the gate exists for. Stops before the
    gateway call, so this asserts the node did not take the skip branch."""
    state = _state(case)
    assert graph.nothing_left_to_revoke(state["case_id"]), "fixture should owe something"

    # `approval_gate` on a state that still carries a plan pauses; here we only
    # check the node does not short-circuit, without calling the LLM.
    out = graph.approval_gate({**state, "deprov_plan": {}, "errors": ["upstream"]})
    assert out == {}
