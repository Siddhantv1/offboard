"""Stage transitions: the write path for a clearance stage.

This is the first thing in the codebase that *moves* a case forward. Everything
before it either read the corpus or wrote whole records; a stage's `status` was
whatever the Markdown said.

Three rules are load-bearing and each exists because of something measured
against the seeded data -- see the notes on `_next_current_stage` and
`recompute_case` before changing either.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import session_scope
from .db.models import CaseStage, CaseStageEvent, ExitCase
from .policy import STAGE_STATUSES, effective_days_open, is_breaching, may_act_on_stage


class TransitionError(Exception):
    """The move was refused. The message is safe to show the caller."""


#: Which moves are legal, as {from: {to}}.
#:
#: `completed -> in_progress` exists because reopening happens (a signature
#: turns out to be missing after sign-off) and pretending otherwise just means
#: someone edits the database by hand. It is restricted to HR in
#: `assert_may_transition`.
LEGAL: dict[str, frozenset[str]] = {
    "pending": frozenset({"in_progress", "completed"}),
    "in_progress": frozenset({"pending", "completed"}),
    "completed": frozenset({"in_progress"}),
}

#: Moving *out* of these requires HR. Reopening a completed stage rewrites a
#: clearance decision someone already signed off.
HR_ONLY_FROM = frozenset({"completed"})


def assert_may_transition(
    *, role: str, stage_key: str | None, from_status: str, to_status: str,
    actor_employee_id: str | None = None, case_employee_id: str | None = None,
) -> bool:
    """Raise `TransitionError` unless this move is allowed. Returns True when
    the actor is acting outside the stage's owning role (an HR escalation),
    which the caller records on the event."""
    if to_status not in STAGE_STATUSES:
        raise TransitionError(
            f"'{to_status}' is not a stage status. Use one of: "
            + ", ".join(STAGE_STATUSES) + "."
        )
    if from_status == to_status:
        raise TransitionError(f"This stage is already {to_status.replace('_', ' ')}.")
    if to_status not in LEGAL.get(from_status, frozenset()):
        raise TransitionError(
            f"Cannot move a stage from {from_status.replace('_', ' ')} to "
            f"{to_status.replace('_', ' ')}."
        )

    # Segregation of duties. Nothing else stops a manager who is themselves
    # exiting from signing off their own departure, and "the person leaving
    # approved their own clearance" is precisely what an exit audit looks for.
    if (actor_employee_id and case_employee_id
            and actor_employee_id == case_employee_id):
        raise TransitionError(
            "You cannot action a stage on your own exit case. Ask HR to do it."
        )

    if from_status in HR_ONLY_FROM and role != "hr":
        raise TransitionError(
            "Only HR can reopen a completed stage."
        )
    if not may_act_on_stage(role, stage_key):
        raise TransitionError("Your role does not own this clearance stage.")

    # True when HR is acting on a stage HR does not own -- a legitimate
    # override, but one the audit trail has to distinguish.
    from .policy import STAGE_OWNERS
    return role not in STAGE_OWNERS.get(stage_key or "", frozenset())


def _next_current_stage(case: ExitCase) -> str | None:
    """What `current_stage` should become, or None to leave it alone.

    **`current_stage` is authored, not derived.** Measured against the six
    seeded cases, no rule reproduces it: case-001 has IT (8d) and KT (9d) both
    in progress and names KT; case-003 has four stages in progress and names
    Finance; case-004 names HR while Finance is equally in progress. It is a
    human's judgement of where the case actually is, and deriving it from
    ordinals would rewrite four of the five multi-stage cases the first time
    anyone moved a stage -- silently changing the dashboard and what every
    agent reading `case["current_stage"]` is told.

    So this only advances the pointer when the stage it currently names is
    finished, and otherwise returns None. On the corpus as it stands, that is a
    no-op for all six cases; `test_stage_transitions.py` pins that.
    """
    by_name = {s.name: s for s in case.stages}
    current = by_name.get(case.current_stage or "")
    if current is not None and current.status != "completed":
        return None  # still where the author said it was
    remaining = [s for s in sorted(case.stages, key=lambda s: s.ordinal)
                 if s.status != "completed"]
    if not remaining:
        return case.current_stage  # everything done; leave the last name in place
    return remaining[0].name


def recompute_case(case: ExitCase, *, now: datetime | None = None) -> None:
    """Refresh the case-level fields a transition can change.

    `overall_status` genuinely is derived, and the rule here reproduces every
    hand-authored value in the corpus exactly: at risk when any non-completed
    stage has been open strictly longer than the SLA. That matches all five
    agents that infer the same thing (`dashboard_insights`, `sla_escalation`,
    `workflow_optimizer`, `policy_auditor`, `end_to_end_automation`), so the
    stored field and the agents cannot disagree.
    """
    at_risk = any(
        is_breaching(
            s.status,
            effective_days_open(
                {"days_open": s.days_open, "status_changed_at": s.status_changed_at},
                now=now,
            ),
        )
        for s in case.stages
    )
    case.overall_status = "at_risk" if at_risk else "on_track"

    nxt = _next_current_stage(case)
    if nxt is not None:
        case.current_stage = nxt


def transition(
    *, case_id: str, stage_key: str, to_status: str, actor, note: str = "",
    blocked_reason: str | None = None, claim: bool = False,
) -> dict[str, Any]:
    """Move one stage, record it, and refresh the case. One transaction.

    `actor` is an `auth.Identity`. Returns the resulting stage and case state.
    """
    to_status = (to_status or "").strip().lower()
    with session_scope() as s:
        case = s.get(ExitCase, case_id)
        if case is None:
            raise TransitionError(f"Unknown exit case '{case_id}'.")

        stage = next((x for x in case.stages if x.stage_key == stage_key), None)
        if stage is None:
            raise TransitionError(
                f"This case has no '{stage_key}' clearance stage."
            )

        from_status = stage.status
        escalated = assert_may_transition(
            role=actor.role,
            stage_key=stage.stage_key,
            from_status=from_status,
            to_status=to_status,
            actor_employee_id=actor.employee_id,
            case_employee_id=case.employee_id,
        )

        now = datetime.now(timezone.utc)
        stage.status = to_status
        stage.status_changed_at = now

        # A stage that is moving is no longer stalled; a caller re-blocking it
        # passes the reason again. Keeping the flag sticky across a transition
        # would leave "blocked" showing on a completed stage.
        if blocked_reason:
            stage.blocked_reason = blocked_reason.strip() or None
            stage.blocked_at = now
        else:
            stage.blocked_reason = None
            stage.blocked_at = None

        if claim and actor.user_id:
            stage.owner_user_id = actor.user_id
            if not (stage.owner or "").strip():
                stage.owner = actor.full_name or actor.email

        s.add(CaseStageEvent(
            case_id=case.case_id,
            stage_key=stage.stage_key,
            stage_name=stage.name,
            from_status=from_status,
            to_status=to_status,
            actor_user_id=actor.user_id,
            actor_label=actor.full_name or actor.email,
            actor_role=actor.role,
            escalated=escalated,
            note=(note or "").strip(),
        ))

        recompute_case(case, now=now)
        s.flush()

        return {
            "case_id": case.case_id,
            "stage": stage.name,
            "stage_key": stage.stage_key,
            "from": from_status,
            "to": to_status,
            "escalated": escalated,
            "current_stage": case.current_stage,
            "overall_status": case.overall_status,
        }


def timeline(case_id: str) -> list[dict[str, Any]]:
    """Every recorded transition on a case, oldest first."""
    with session_scope() as s:
        rows = s.scalars(
            select(CaseStageEvent)
            .where(CaseStageEvent.case_id == case_id)
            .order_by(CaseStageEvent.created_at, CaseStageEvent.id)
        ).all()
        return [r.to_meta() for r in rows]


def queue(session: Session, *, stage_keys: set[str], case_ids: set[str] | None = None,
          owner_user_id: int | None = None) -> list[dict[str, Any]]:
    """Open stages of the given types, newest breach first.

    `case_ids` is the caller's visible set (None means unrestricted, i.e. HR
    and IT). `owner_user_id` narrows to one person's own queue.
    """
    stmt = (
        select(CaseStage, ExitCase)
        .join(ExitCase, ExitCase.case_id == CaseStage.case_id)
        .where(CaseStage.stage_key.in_(stage_keys), CaseStage.status != "completed")
    )
    if case_ids is not None:
        if not case_ids:
            return []
        stmt = stmt.where(CaseStage.case_id.in_(case_ids))
    if owner_user_id is not None:
        stmt = stmt.where(CaseStage.owner_user_id == owner_user_id)

    out = []
    for stage, case in session.execute(stmt).all():
        days = effective_days_open(
            {"days_open": stage.days_open, "status_changed_at": stage.status_changed_at}
        )
        out.append({
            "case_id": case.case_id,
            "employee_id": case.employee_id,
            "employee_name": case.employee_name,
            "last_working_day": case.last_working_day,
            "stage": stage.name,
            "stage_key": stage.stage_key,
            "status": stage.status,
            "owner": stage.owner or None,
            "owner_user_id": stage.owner_user_id,
            "claimed": stage.owner_user_id is not None,
            "days_open": days,
            "breaching": is_breaching(stage.status, days),
            "blocked_reason": stage.blocked_reason,
        })
    # Worst first: breaching before not, then longest open. A queue sorted by
    # id would bury the case that is about to miss its last working day.
    out.sort(key=lambda r: (not r["breaching"], -r["days_open"], r["case_id"]))
    return out
