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
from .policy import (STAGE_STATUSES, effective_days_open, is_breaching,
                     may_act_on_stage, needs_employee_confirmation,
                     EMPLOYEE_CONFIRMABLE)


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


def set_stage_owner(stage: CaseStage, user_id: int | None, label: str) -> None:
    """Point both owner columns at one person. Never write just one.

    `owner` is the name people read on the case page and in every agent's
    context; `owner_user_id` is the foreign key the work queues route on. They
    are two halves of a single fact, and they used to be written separately --
    the id unconditionally, the name only when it was blank. So moving a stage
    that already named somebody took the routing and left their name behind,
    and the stage then displayed one person while queueing for another. Seven
    rows in the live database drifted apart that way, including a live manager
    sign-off that sat in HR's queue.

    Writing both together is also what makes a drift *detectable*: any row
    whose name and id disagree can only have come from code that bypassed this,
    so `scripts/link_stage_owners.py --repair` can safely correct it.
    """
    stage.owner_user_id = user_id
    stage.owner = label


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

        # Two-sided clearance. The employee attests that they have done their
        # part; the owning role verifies it independently. Completing before
        # the employee has said anything means nobody asked whether the laptop
        # actually came back -- which is the failure the old document uploads
        # only pretended to catch.
        #
        # HR can still force it through, because a leaver who has already gone
        # cannot be made to click a button. That is an override, so it is
        # recorded as one rather than waved past.
        if (to_status == "completed"
                and needs_employee_confirmation(stage.stage_key)
                and stage.employee_confirmed_at is None):
            if actor.role != "hr":
                raise TransitionError(
                    f"{stage.name} cannot be verified until "
                    f"{case.employee_name or 'the employee'} has confirmed their "
                    "side. Chase them, or ask HR to clear it without them."
                )
            escalated = True

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

        # Claiming here is a *side effect* of moving the stage -- the case page
        # sends it on every transition -- so it only ever picks up work nobody
        # holds. Taking a stage that already names someone is a handover, and a
        # handover is a deliberate act with its own route (`assign`), not
        # something that happens because HR clicked "complete" on a colleague's
        # behalf. That distinction is the whole of the case-003 bug.
        unowned = stage.owner_user_id is None and not (stage.owner or "").strip()
        if claim and actor.user_id and unowned:
            set_stage_owner(stage, actor.user_id, actor.full_name or actor.email)

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


def employee_confirmations(case_id: str) -> dict[str, dict[str, Any]]:
    """stage_key -> what the employee has attested, for the stages that have a
    two-sided mark. Stages they have not confirmed appear with `at` as None, so
    the UI can distinguish "waiting on them" from "not their concern"."""
    with session_scope() as s:
        rows = s.scalars(
            select(CaseStage).where(CaseStage.case_id == case_id)).all()
        return {
            st.stage_key: {
                "at": st.employee_confirmed_at,
                "note": st.employee_confirmed_note,
                "confirmed": st.employee_confirmed_at is not None,
                "asks": EMPLOYEE_CONFIRMABLE[st.stage_key],
            }
            for st in rows
            if needs_employee_confirmation(st.stage_key)
        }


def confirm_employee_side(*, case_id: str, stage_key: str, actor: Any,
                          note: str = "") -> dict[str, Any]:
    """Record that the employee says they have done their part of a stage.

    Idempotent: confirming twice keeps the first timestamp. The point of the
    field is "when did they say it", and letting a second click move that
    forward would quietly reset the clock IT is being measured against.
    """
    with session_scope() as s:
        case = s.get(ExitCase, case_id)
        if case is None:
            raise TransitionError(f"Unknown exit case '{case_id}'.")

        stage = next((x for x in case.stages if x.stage_key == stage_key), None)
        if stage is None:
            raise TransitionError(f"This case has no '{stage_key}' clearance stage.")
        if not needs_employee_confirmation(stage_key):
            raise TransitionError(
                f"{stage.name} is cleared from records the company already holds. "
                "There is nothing for you to confirm on it."
            )
        if stage.status == "completed":
            raise TransitionError(f"{stage.name} is already signed off.")

        first_time = stage.employee_confirmed_at is None
        if first_time:
            stage.employee_confirmed_at = datetime.now(timezone.utc)
        stage.employee_confirmed_note = (note or "").strip() or None

        if first_time:
            # Visible to IT on the case timeline, so "the employee says they
            # handed everything back" is part of the record rather than a flag
            # only the stage row knows about.
            s.add(CaseStageEvent(
                case_id=case.case_id,
                stage_key=stage.stage_key,
                stage_name=stage.name,
                from_status=stage.status,
                to_status=stage.status,
                actor_user_id=getattr(actor, "user_id", None),
                actor_label=getattr(actor, "full_name", None)
                            or getattr(actor, "email", ""),
                actor_role="employee",
                escalated=False,
                note=(f"Confirmed their side: {EMPLOYEE_CONFIRMABLE[stage_key]}."
                      + (f" {note.strip()}" if note and note.strip() else "")),
            ))
        s.flush()
        return {
            "case_id": case.case_id,
            "stage": stage.name,
            "stage_key": stage.stage_key,
            "confirmed": True,
            "at": stage.employee_confirmed_at,
            "note": stage.employee_confirmed_note,
        }


def record_escalation(*, case_id: str, stage_key: str, actor: Any,
                      note: str = "") -> dict[str, Any]:
    """Record that a stage was chased, without moving it.

    The SLA Escalation agent drafts a chase message and, until now, that was
    the end of it: you copied the text somewhere and the system never learned
    that anyone had chased. So the same breach was re-escalated on the next
    sweep, and the case's own record could not answer "has anyone actually
    pushed on this?".

    An escalation is an event, not a transition -- `from_status` equals
    `to_status` deliberately, because the stage genuinely has not moved. It is
    flagged `escalated`, which the case timeline already renders distinctly.
    """
    with session_scope() as s:
        case = s.get(ExitCase, case_id)
        if case is None:
            raise TransitionError(f"Unknown exit case '{case_id}'.")

        stage = next((x for x in case.stages if x.stage_key == stage_key), None)
        if stage is None:
            raise TransitionError(f"This case has no '{stage_key}' clearance stage.")
        if stage.status == "completed":
            raise TransitionError(
                f"{stage.name} is already completed. There is nothing to chase."
            )

        event = CaseStageEvent(
            case_id=case.case_id,
            stage_key=stage.stage_key,
            stage_name=stage.name,
            from_status=stage.status,
            to_status=stage.status,
            actor_user_id=getattr(actor, "user_id", None),
            actor_label=getattr(actor, "full_name", None) or getattr(actor, "email", ""),
            actor_role=getattr(actor, "role", None),
            escalated=True,
            note=(note or "").strip(),
        )
        s.add(event)
        s.flush()
        return {
            "case_id": case.case_id,
            "stage": stage.name,
            "stage_key": stage.stage_key,
            "status": stage.status,
            "owner": stage.owner or None,
            "escalated": True,
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
            # Two-sided stages only: whether the leaver has said they are done.
            # None means the stage has no employee side at all, which reads
            # differently from "waiting on them".
            "employee_confirmed": (stage.employee_confirmed_at is not None
                                   if needs_employee_confirmation(stage.stage_key)
                                   else None),
        })
    # Worst first: breaching before not, then longest open. A queue sorted by
    # id would bury the case that is about to miss its last working day.
    out.sort(key=lambda r: (not r["breaching"], -r["days_open"], r["case_id"]))
    return out
