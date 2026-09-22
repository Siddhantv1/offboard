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


class StageClosedError(TransitionError):
    """The stage is finished, so there is no work to hand to anyone.

    A subclass so existing `except TransitionError` handlers keep catching it,
    but the routes can map it to 409 (a conflict with the stage's state) rather
    than 422 (a malformed request), which is what the UI switches on.
    """


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


def _it_clearance_blockers(case_id: str) -> list[str]:
    """What is genuinely unfinished on an IT clearance, in words.

    Read from the two records that actually know: `asset_returns` and the
    executed deprovisioning actions. Neither is derived from the stage, which
    is the whole point -- a check computed from the thing being checked cannot
    contradict it.
    """
    from .systems import access_status, asset_status

    blockers = []
    try:
        assets = asset_status(case_id)
        access = access_status(case_id)
    except LookupError:
        return []

    outstanding = assets.get("outstanding") or []
    if outstanding:
        blockers.append(
            f"{len(outstanding)} asset{'s' if len(outstanding) != 1 else ''} "
            f"unaccounted for ({', '.join(outstanding)})")
    live = access.get("still_active") or []
    if live:
        blockers.append(
            f"{len(live)} account{'s' if len(live) != 1 else ''} still live "
            f"({', '.join(live)})")
    return blockers


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

        # What blocks an IT sign-off is unfinished work, not an unclicked
        # button.
        #
        # This used to refuse until the leaver confirmed their side. That was
        # backwards twice over. It let someone who had already left -- or who
        # simply never logged in -- freeze their own clearance indefinitely,
        # with IT unable to close a case whose hardware was sitting on a desk
        # in front of them. And it dressed up as assurance something that is
        # not: the leaver's click evidences nothing IT cannot see better, and
        # a click by the person with every reason to click is the weakest
        # signal in the room.
        #
        # IT holds the authoritative record because IT physically takes the
        # laptop and clicks revoke in Okta. So the gate is that record: assets
        # unaccounted for, or access still live. Both are overridable with a
        # reason, because writing a lost YubiKey off to the final settlement is
        # a real outcome -- but the reason is recorded against a named person,
        # which is what makes a sign-off mean something.
        if to_status == "completed" and stage.stage_key == "it":
            blockers = _it_clearance_blockers(case.case_id)
            if blockers and not (note or "").strip():
                raise TransitionError(
                    f"{stage.name} still has {'; '.join(blockers)}. Deal with "
                    "those first, or add a note saying why you are signing off "
                    "anyway -- it goes on the record with your name."
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

        # Claiming here is a *side effect* of moving the stage -- the case page
        # sends it on every transition -- so it only ever picks up work nobody
        # holds. Taking a stage that already names someone is a handover, and a
        # handover is a deliberate act with its own route (`assign`), not
        # something that happens because HR clicked "complete" on a colleague's
        # behalf. That distinction is the whole of the case-003 bug.
        unowned = stage.owner_user_id is None and not (stage.owner or "").strip()
        took_ownership = bool(claim and actor.user_id and unowned)
        if took_ownership:
            set_stage_owner(stage, actor.user_id, actor.full_name or actor.email)

        s.add(CaseStageEvent(
            case_id=case.case_id,
            stage_key=stage.stage_key,
            stage_name=stage.name,
            kind="transition",
            from_status=from_status,
            to_status=to_status,
            actor_user_id=actor.user_id,
            actor_label=actor.full_name or actor.email,
            actor_role=actor.role,
            escalated=escalated,
            # One user action, one event. Picking up an unowned stage while
            # moving it is part of the same act, so it is said on the
            # transition rather than filed as a separate assignment the reader
            # has to correlate by timestamp.
            note=" ".join(filter(None, [
                (note or "").strip(),
                "Took ownership of the stage." if took_ownership else "",
            ])),
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


def assign_owner(*, case_id: str, stage_key: str, assignee_user_id: int | None,
                 assignee_label: str, actor: Any, note: str = "",
                 self_claim: bool = False) -> dict[str, Any]:
    """Put a person on a stage, and record who put them there.

    Assignment decides who is accountable for a clearance, and until now it was
    the only such decision the audit trail did not capture: `assign` and
    `claim` wrote `owner`/`owner_user_id` straight onto the row and left no
    event behind. The case could say Farhan Khan owns IT Clearance and could
    not say who decided that, when, or who held it before -- on a record whose
    entire purpose is answering exactly that.

    Both routes come through here so they cannot drift. `claim` is the special
    case where the assignee is the caller, which reads differently in the
    trail ("took it") and is the only difference between them.
    """
    with session_scope() as s:
        case = s.get(ExitCase, case_id)
        if case is None:
            raise TransitionError(f"Unknown exit case '{case_id}'.")

        stage = next((x for x in case.stages if x.stage_key == stage_key), None)
        if stage is None:
            raise TransitionError(f"This case has no '{stage_key}' clearance stage.")

        # Assigning finished work is a no-op that looks like an action: the
        # write succeeds and the queue, which shows open work only, stays
        # empty. Refused here rather than in each route.
        if stage.status == "completed":
            raise StageClosedError(
                f"{stage.name} on {case_id} is already completed, so assigning it "
                "would not put any work on anyone's queue. Reopen the stage first "
                "if it needs redoing."
            )

        # Defence in depth. The assign route checks this too, with a better
        # message; this is what stops a caller that never goes through a route
        # -- the orchestrator -- creating work that shows on nobody's queue.
        if assignee_user_id is not None:
            from .db.models import User
            from .scoping import can_see_case, identity_for

            holder = s.get(User, assignee_user_id)
            if holder is not None and not can_see_case(identity_for(holder),
                                                       case.to_meta()):
                raise TransitionError(
                    f"{assignee_label} cannot see {case_id}, so {stage.name} "
                    "would sit on nobody's queue."
                )

        previous = (stage.owner or "").strip()
        previous_id = stage.owner_user_id
        set_stage_owner(stage, assignee_user_id, assignee_label)

        # Nothing changed, so nothing is worth a line in the trail. Recording
        # it anyway would pad the timeline with re-clicks and make the entries
        # that do mean something harder to find.
        unchanged = previous_id == assignee_user_id and previous == assignee_label
        if not unchanged:
            if self_claim:
                what = f"Took {stage.name}."
            elif previous:
                what = f"Reassigned {stage.name} from {previous} to {assignee_label}."
            else:
                what = f"Assigned {stage.name} to {assignee_label}."
            s.add(CaseStageEvent(
                case_id=case.case_id,
                stage_key=stage.stage_key,
                stage_name=stage.name,
                kind="assignment",
                # An assignment does not move the stage. Equal statuses say so
                # honestly; `kind` is what stops the UI rendering it as a move.
                from_status=stage.status,
                to_status=stage.status,
                actor_user_id=getattr(actor, "user_id", None),
                actor_label=getattr(actor, "full_name", None)
                            or getattr(actor, "email", ""),
                actor_role=getattr(actor, "role", None),
                escalated=False,
                note=(what + (f" {note.strip()}" if note and note.strip() else "")),
            ))
        s.flush()
        return {
            "case_id": case.case_id,
            "stage": stage.name,
            "stage_key": stage.stage_key,
            "owner": stage.owner,
            "owner_user_id": stage.owner_user_id,
            "previous_owner": previous or None,
            "recorded": not unchanged,
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
                "status": st.status,
                # Whether confirming would still do anything. A stage that is
                # already signed off refuses the confirm call, so without this
                # the page shows a button whose only outcome is an error --
                # which is the same dead control that made assigning a
                # completed stage look like it worked.
                "actionable": (st.status != "completed"
                               and st.employee_confirmed_at is None),
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
                kind="confirmation",
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
            kind="escalation",
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
