"""The orchestration has to change the case, not just describe it.

Before this node existed a full run could complete and leave the exit case
byte-for-byte identical. `run_routing` worked out the best approver for every
open stage and discarded the answer; the escalation node drafted chase messages
nobody recorded; nothing moved a stage. The run returned a large document and
changed nothing, which is precisely what "it doesn't actually do anything"
means.

`apply_decisions` is called directly here with a fabricated state. That keeps
these tests off the LLM -- the point under test is what gets written, not what
a model said.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from backend import auth
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import CaseStage, Employee, ExitCase, User
from backend.stages import timeline


@pytest.fixture
def apply_decisions():
    # Imported through backend.main: backend.orchestration and backend.agents
    # form a cycle, so importing the graph module first fails.
    import backend.main  # noqa: F401
    from backend.orchestration.graph import apply_decisions as node
    return node


@pytest.fixture
def it_person():
    email = f"pytest-orch-it-{uuid.uuid4().hex[:8]}@example.com"
    with session_scope() as s:
        u = User(email=email, password_hash=auth.hash_password("x" * 12),
                 role="it", full_name="Pytest Orch Engineer")
        s.add(u)
        s.flush()
        uid, name = u.id, u.full_name
    yield {"id": uid, "name": name}
    with session_scope() as s:
        s.execute(delete(User).where(User.email == email))


@pytest.fixture
def scratch_case():
    email = f"pytest-orch-{uuid.uuid4().hex[:8]}@example.com"
    emp, case, _ = store.add_employee({
        "name": "Pytest Orch Probe", "role": "Engineer",
        "department": "Engineering", "email": email, "status": "exiting",
    })
    yield str(case.meta["case_id"])
    with session_scope() as s:
        s.execute(delete(User).where(User.email == email))
        s.execute(delete(ExitCase).where(ExitCase.case_id == case.meta["case_id"]))
        s.execute(delete(Employee).where(Employee.id == emp.meta["id"]))


def _stage(case_id, key="it"):
    with session_scope() as s:
        st = s.scalar(select(CaseStage).where(
            CaseStage.case_id == case_id, CaseStage.stage_key == key))
        return st.status, st.owner, st.owner_user_id


def _routing_state(case_id, it_person, **over):
    return {
        "case_id": case_id,
        "routing": {
            "IT Clearance": {
                "stage": "IT Clearance",
                "stage_key": "it",
                "case_id": case_id,
                "chosen": {"name": it_person["name"], "user_id": it_person["id"]},
                "routing": {"chosen_approver": it_person["name"],
                            "reason": "Lowest open workload."},
            },
        },
        **over,
    }


def test_the_recommended_approver_is_actually_put_on_the_stage(
        apply_decisions, scratch_case, it_person):
    out = apply_decisions(_routing_state(scratch_case, it_person))

    _, owner, owner_id = _stage(scratch_case)
    assert owner_id == it_person["id"], "routing was computed and thrown away again"
    assert owner == it_person["name"]
    assert any(a["kind"] == "assigned" for a in out["applied"])


def test_it_never_reassigns_a_stage_somebody_already_holds(
        apply_decisions, scratch_case, it_person):
    """A stage with an owner was given to them by a human. A machine moving it
    on a schedule is how you lose track of who is accountable."""
    with session_scope() as s:
        st = s.scalar(select(CaseStage).where(
            CaseStage.case_id == scratch_case, CaseStage.stage_key == "it"))
        st.owner_user_id = 1
        st.owner = "Someone Already"

    out = apply_decisions(_routing_state(scratch_case, it_person))

    _, owner, owner_id = _stage(scratch_case)
    assert (owner, owner_id) == ("Someone Already", 1)
    assert not any(a["kind"] == "assigned" for a in out["applied"])


def test_a_drafted_plan_starts_it_clearance(apply_decisions, scratch_case, it_person):
    state = _routing_state(scratch_case, it_person,
                           deprov_plan={"plan_id": "dep-test-1", "actions": []})
    out = apply_decisions(state)

    status, _, _ = _stage(scratch_case)
    assert status == "in_progress"
    assert any(a["kind"] == "started" for a in out["applied"])


def test_it_never_completes_a_clearance(apply_decisions, scratch_case, it_person):
    """Marking work done is a judgement about the real world -- was the laptop
    returned, did the handover happen. No agent in this graph can see the real
    world, so none of them may assert it."""
    state = _routing_state(scratch_case, it_person,
                           deprov_plan={"plan_id": "dep-test-2", "actions": []})
    apply_decisions(state)
    apply_decisions(state)          # even run twice

    for key in ("it", "finance", "hr", "kt", "manager"):
        status, _, _ = _stage(scratch_case, key)
        assert status != "completed", f"{key} was auto-completed"


def test_a_failed_run_changes_nothing(apply_decisions, scratch_case, it_person):
    """Half-applying the conclusions of a run that errored would leave the case
    in a state no human chose."""
    state = _routing_state(scratch_case, it_person, errors=["compliance: gateway down"])
    out = apply_decisions(state)

    assert out == {}
    _, _, owner_id = _stage(scratch_case)
    assert owner_id is None


def test_what_changed_is_reported_separately_from_the_step_log(
        apply_decisions, scratch_case, it_person):
    """`log` is a trace of nodes that ran, which is equally true of a run that
    touched nothing. `applied` is the answer to "what is different now"."""
    out = apply_decisions(_routing_state(scratch_case, it_person))
    assert out["applied"], "a run that assigned somebody reported no changes"
    assert all({"kind", "stage", "detail"} <= set(a) for a in out["applied"])


def test_the_orchestrator_is_named_in_the_audit_trail_not_a_person(
        apply_decisions, scratch_case, it_person):
    """Borrowing the identity of whoever pressed the button would make the
    trail claim a person did what a machine did."""
    apply_decisions(_routing_state(
        scratch_case, it_person,
        deprov_plan={"plan_id": "dep-test-3", "actions": []}))

    events = timeline(scratch_case)
    assert events, "starting IT Clearance recorded nothing"
    assert events[-1]["by"] == "Exit Process Orchestrator"
