"""Stage transitions: legality, scoping, the audit trail, and recomputation.

Every test that changes data builds its own throwaway case via
`store.add_employee` and deletes it in `finally`. That is not politeness -- the
seeded corpus is what `test_store_contract.py` compares against the golden
snapshot, so a test that transitions `case-001` would leave the contract suite
red for every subsequent run.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from backend import auth
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import (CaseStage, CaseStageEvent, Employee, ExitCase,
                               User)
from backend.policy import SLA_DAYS, effective_days_open, is_breaching
from backend.stages import (LEGAL, TransitionError, recompute_case, timeline,
                            transition)


#: Real user rows, one per role, created once for the module.
#:
#: They have to be real: `case_stage_events.actor_user_id` is a foreign key, so
#: a synthetic Identity with a made-up id fails on insert -- which is the FK
#: doing its job. Building them here also means the SET NULL behaviour on
#: delete is exercised against the real constraint rather than mocked.
_ACTORS: dict[str, int] = {}


@pytest.fixture(scope="module", autouse=True)
def _actor_accounts():
    suffix = uuid.uuid4().hex[:8]
    emails = {role: f"pytest-actor-{role}-{suffix}@example.com"
              for role in ("hr", "it", "manager")}
    with session_scope() as s:
        for role, email in emails.items():
            user = User(email=email, password_hash=auth.hash_password("x" * 12),
                        role=role, full_name=f"Pytest {role.title()}")
            s.add(user)
            s.flush()
            _ACTORS[role] = user.id
    yield
    with session_scope() as s:
        s.execute(delete(User).where(User.email.in_(list(emails.values()))))
    _ACTORS.clear()


def _identity(role: str, *, employee_id: str | None = None,
              email: str | None = None, user_id: int | None = None,
              full_name: str | None = None) -> auth.Identity:
    return auth.Identity(
        user_id=user_id if user_id is not None else _ACTORS[role],
        email=email or f"pytest-{role}@example.com",
        role=role,
        employee_id=employee_id,
        full_name=full_name or f"Pytest {role.title()}",
        must_change_password=False,
    )


@pytest.fixture
def scratch_case():
    """A throwaway exiting employee and their auto-created exit case."""
    email = f"pytest-stage-{uuid.uuid4().hex[:8]}@example.com"
    emp_doc, case_doc, _ = store.add_employee({
        "name": "Pytest Stage Probe",
        "role": "Engineer",
        "department": "Engineering",
        "email": email,
        "manager_email": "pytest-stage-manager@example.com",
        "status": "exiting",
    })
    assert case_doc is not None
    yield emp_doc.meta["id"], case_doc.meta["case_id"]
    with session_scope() as s:
        s.execute(delete(User).where(User.email == email))
        s.execute(delete(ExitCase).where(ExitCase.case_id == case_doc.meta["case_id"]))
        s.execute(delete(Employee).where(Employee.id == emp_doc.meta["id"]))


# -- the characterisation test this whole design turns on ---------------------
def test_recompute_is_a_no_op_on_the_seeded_corpus():
    """`current_stage` is authored, not derived.

    No rule reproduces it: case-001 has IT (8d) and KT (9d) both in progress and
    names KT, so it is not "lowest ordinal open"; case-003 has four stages open
    and names Finance, so it is not "longest open" either. It is a human's call
    about where the case is.

    If this fails, the recompute rule has started rewriting hand-authored
    values -- which silently changes what five agents and the dashboard report,
    and no other test in the suite would notice.
    """
    with session_scope() as s:
        cases = s.scalars(select(ExitCase).order_by(ExitCase.case_id)).all()
        assert cases, "seeded corpus is missing"
        drift = []
        for case in cases:
            before = (case.current_stage, case.overall_status)
            recompute_case(case)
            if (case.current_stage, case.overall_status) != before:
                drift.append(
                    f"{case.case_id}: {before} -> "
                    f"{(case.current_stage, case.overall_status)}"
                )
        s.rollback()
    assert not drift, "recompute rewrote authored values:\n  " + "\n  ".join(drift)


# -- legality -----------------------------------------------------------------
def test_a_stage_advances_and_records_an_event(scratch_case):
    _, case_id = scratch_case
    result = transition(
        case_id=case_id, stage_key="it", to_status="in_progress",
        actor=_identity("it"), note="picked this up",
    )
    assert result["from"] == "pending"
    assert result["to"] == "in_progress"

    events = timeline(case_id)
    assert len(events) == 1
    assert events[0]["stage"] == "IT Clearance"
    assert events[0]["from"] == "pending"
    assert events[0]["to"] == "in_progress"
    assert events[0]["note"] == "picked this up"
    assert events[0]["role"] == "it"
    assert events[0]["escalated"] is False


def test_an_illegal_move_is_refused(scratch_case):
    _, case_id = scratch_case
    # pending -> pending is a no-op, not a transition
    with pytest.raises(TransitionError, match="already pending"):
        transition(case_id=case_id, stage_key="it", to_status="pending",
                   actor=_identity("it"))


def test_an_unknown_status_is_refused(scratch_case):
    _, case_id = scratch_case
    # `blocked` is deliberately NOT a status -- it is a separate column,
    # because four consumers read `status in ("in_progress","completed")` as
    # "started" and a fourth value would silently regress derived facts.
    with pytest.raises(TransitionError, match="not a stage status"):
        transition(case_id=case_id, stage_key="it", to_status="blocked",
                   actor=_identity("it"))


def test_legal_table_has_no_dead_ends():
    """Every status must be reachable and escapable, or a case can strand."""
    for status, targets in LEGAL.items():
        assert targets, f"{status} is a dead end"
        for target in targets:
            assert target in LEGAL, f"{status} -> {target} leads nowhere"


# -- authorisation ------------------------------------------------------------
def test_a_role_cannot_action_another_roles_stage(scratch_case):
    _, case_id = scratch_case
    with pytest.raises(TransitionError, match="does not own"):
        transition(case_id=case_id, stage_key="it", to_status="in_progress",
                   actor=_identity("manager"))
    with pytest.raises(TransitionError, match="does not own"):
        transition(case_id=case_id, stage_key="manager", to_status="in_progress",
                   actor=_identity("it"))


def test_hr_may_act_on_any_stage_but_it_is_recorded_as_an_escalation(scratch_case):
    _, case_id = scratch_case
    result = transition(case_id=case_id, stage_key="it", to_status="in_progress",
                        actor=_identity("hr"))
    assert result["escalated"] is True, (
        "HR clearing an IT stage must be distinguishable from IT clearing it, "
        "or the audit trail cannot answer who actually signed off"
    )
    assert timeline(case_id)[0]["escalated"] is True

    # ...and acting on a stage HR *does* own is not an escalation.
    result = transition(case_id=case_id, stage_key="hr", to_status="in_progress",
                        actor=_identity("hr"))
    assert result["escalated"] is False


def test_only_hr_can_reopen_a_completed_stage(scratch_case):
    _, case_id = scratch_case
    transition(case_id=case_id, stage_key="it", to_status="completed",
               actor=_identity("it"))

    with pytest.raises(TransitionError, match="Only HR can reopen"):
        transition(case_id=case_id, stage_key="it", to_status="in_progress",
                   actor=_identity("it"))

    transition(case_id=case_id, stage_key="it", to_status="in_progress",
               actor=_identity("hr"))


def test_nobody_can_action_their_own_exit_case(scratch_case):
    """Segregation of duties.

    A manager who is themselves leaving must not sign off their own departure --
    "the person leaving approved their own clearance" is exactly what an exit
    audit exists to catch. Nothing else in the stack prevents it.
    """
    employee_id, case_id = scratch_case
    with pytest.raises(TransitionError, match="your own exit case"):
        transition(case_id=case_id, stage_key="manager", to_status="completed",
                   actor=_identity("manager", employee_id=employee_id))

    # Even HR, whose override covers everything else.
    with pytest.raises(TransitionError, match="your own exit case"):
        transition(case_id=case_id, stage_key="hr", to_status="completed",
                   actor=_identity("hr", employee_id=employee_id))


# -- recomputation ------------------------------------------------------------
def test_current_stage_advances_only_when_the_named_stage_completes(scratch_case):
    _, case_id = scratch_case

    # A new case names its first stage.
    with session_scope() as s:
        assert s.get(ExitCase, case_id).current_stage == "IT Clearance"

    # Starting a LATER stage must not move the pointer -- that is the case-001
    # shape (IT and KT both open, the author names KT) and deriving it from
    # ordinals is what would have rewritten four of five seeded cases.
    transition(case_id=case_id, stage_key="kt", to_status="in_progress",
               actor=_identity("manager"))
    with session_scope() as s:
        assert s.get(ExitCase, case_id).current_stage == "IT Clearance"

    # Completing the named stage advances it to the next unfinished one.
    transition(case_id=case_id, stage_key="it", to_status="completed",
               actor=_identity("it"))
    with session_scope() as s:
        assert s.get(ExitCase, case_id).current_stage == "Finance Clearance"


def test_overall_status_flips_on_the_sla_boundary(scratch_case):
    """Strict `>`, matching all five agents. Exactly SLA_DAYS is not a breach."""
    _, case_id = scratch_case

    with session_scope() as s:
        case = s.get(ExitCase, case_id)
        stage = next(x for x in case.stages if x.stage_key == "it")
        stage.status = "in_progress"
        stage.days_open = SLA_DAYS          # on the line
        stage.status_changed_at = None      # stored value is authoritative
        recompute_case(case)
        assert case.overall_status == "on_track", f"{SLA_DAYS} days is not a breach"

        stage.days_open = SLA_DAYS + 1      # over it
        recompute_case(case)
        assert case.overall_status == "at_risk"

        # A completed stage never breaches, however long it took.
        stage.status = "completed"
        recompute_case(case)
        assert case.overall_status == "on_track"
        s.rollback()


def test_a_stage_ages_even_when_days_open_was_never_written():
    """`days_open` has exactly two writers and neither increments it, so every
    case created through the product would sit at 0 forever and could never
    breach. `status_changed_at` is what makes a real case age."""
    from datetime import datetime, timedelta, timezone

    long_ago = datetime.now(timezone.utc) - timedelta(days=SLA_DAYS + 3)
    stage = {"days_open": 0, "status_changed_at": long_ago}
    assert effective_days_open(stage) == SLA_DAYS + 3
    assert is_breaching("in_progress", effective_days_open(stage))

    # A seeded value always wins if it is larger, so the corpus keeps its
    # hand-authored numbers and the golden snapshot is unaffected.
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    assert effective_days_open({"days_open": 9, "status_changed_at": recent}) == 9


# -- blocking -----------------------------------------------------------------
def test_blocking_keeps_the_stage_in_progress_so_sla_keeps_counting(scratch_case):
    """`blocked` is a reason, not a status.

    Four consumers (`systems.asset_status`, `systems.hrms_status`,
    `systems.finance_status`, `document_collection.document_status`) test
    `status in ("in_progress","completed")` to mean "started". A `blocked`
    status would read as earlier than in_progress and regress derived facts --
    including making the stage's documents stop counting as due, which erases
    policy_auditor's missing-document findings.
    """
    _, case_id = scratch_case
    transition(case_id=case_id, stage_key="it", to_status="in_progress",
               actor=_identity("it"), blocked_reason="waiting on KT completion")

    with session_scope() as s:
        stage = s.scalar(select(CaseStage).where(
            CaseStage.case_id == case_id, CaseStage.stage_key == "it"))
        assert stage.status == "in_progress", "a blocked stage is still started"
        assert stage.blocked_reason == "waiting on KT completion"
        assert stage.blocked_at is not None

    # Moving on clears the flag rather than leaving it stuck on a done stage.
    transition(case_id=case_id, stage_key="it", to_status="completed",
               actor=_identity("it"))
    with session_scope() as s:
        stage = s.scalar(select(CaseStage).where(
            CaseStage.case_id == case_id, CaseStage.stage_key == "it"))
        assert stage.blocked_reason is None
        assert stage.blocked_at is None


# -- audit trail --------------------------------------------------------------
def test_the_trail_survives_the_actor_being_deleted(scratch_case):
    """`actor_label` is denormalised beside the FK on purpose: "the system did
    it" is not an answer an auditor accepts."""
    _, case_id = scratch_case
    email = f"pytest-actor-{uuid.uuid4().hex[:8]}@example.com"
    with session_scope() as s:
        user = User(email=email, password_hash=auth.hash_password("x" * 12),
                    role="it", full_name="Departing Engineer")
        s.add(user)
        s.flush()
        user_id = user.id

    transition(case_id=case_id, stage_key="it", to_status="in_progress",
               actor=_identity("it", email=email, user_id=user_id,
                               full_name="Departing Engineer"))

    with session_scope() as s:
        s.execute(delete(User).where(User.id == user_id))

    events = timeline(case_id)
    assert len(events) == 1
    assert events[0]["by"] == "Departing Engineer", "the name must outlive the account"

    with session_scope() as s:
        row = s.scalar(select(CaseStageEvent).where(CaseStageEvent.case_id == case_id))
        assert row.actor_user_id is None, "FK is SET NULL, not CASCADE"
