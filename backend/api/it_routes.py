"""One person's IT clearance, in one place.

IT's work was spread across three pages that each told part of the story. The
queue said a clearance was open; clicking it landed on HR's case page, which
leads with five clearance stages IT does not own and buries the access
position; and whether the revocation had been authorised lived on a separate
Deprovisioning page listing every plan in the company, where the same employee
appeared four times because each draft inserted another row.

So the one question an engineer actually has -- "what does this person still
have, and am I allowed to take it away yet?" -- could not be answered on any
single screen.

This endpoint answers it: the stage, what the leaver has confirmed, live access
and asset position, and the deprovisioning plan with its authorisation state
inline. Authorisation is HR's and stays HR's; showing it here is reporting, not
granting.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..auth import DeprovUser
from ..data_store import store
from ..db import session_scope
from ..db.models import CaseStage, DeprovisioningPlan
from ..agents.it_deprovisioning import action_states
from ..policy import may_act_on_stage
from ..systems import access_status, asset_status
from .. import stages as stage_service
from .deps import ScopedCase

router = APIRouter(prefix="/api/it", tags=["it"])


def _plan_for(employee_id: str, user) -> dict[str, Any] | None:
    """The plan that is currently in play, plus what this caller may do with it.

    The newest non-executed plan, or the newest executed one when there is
    nothing open. Drafting now supersedes an earlier pending plan, so "the
    current plan" is a single row again rather than a pile of identical drafts.
    """
    with session_scope() as s:
        rows = s.scalars(
            select(DeprovisioningPlan)
            .where(DeprovisioningPlan.employee_id == employee_id)
            .order_by(DeprovisioningPlan.created_at.desc())
        ).all()
        if not rows:
            return None
        row = next((r for r in rows if r.status != "executed"), rows[0])
        plan = row.plan or {}
        summary = {
            "plan_id": row.id,
            "status": row.status,
            "summary": plan.get("summary"),
            "urgency": plan.get("urgency"),
            "approved_by": row.approved_by,
            "approved_at": row.approved_at,
            "executed_by": row.executed_by,
            "executed_at": row.executed_at,
            # Authorising is HR's alone. IT sees the state so it knows whether
            # it is waiting or being waited on -- the thing the separate page
            # existed to tell them, said where the work is.
            "can_approve": row.status == "pending" and user.is_hr,
            # Per action, not per plan: the button lives on each row now.
            "can_execute": row.status in ("approved", "executed")
                           and user.role in ("it", "hr"),
        }

    # Each step with whether it has actually been carried out, matched against
    # the executed records rather than the plan's own status. A plan marked
    # executed with a step that never ran would otherwise read as finished.
    actions = action_states(summary["plan_id"])
    summary["actions"] = actions
    summary["action_count"] = len(actions)
    summary["done_count"] = sum(1 for a in actions if a["done"])
    summary["privileged_count"] = sum(1 for a in actions if a.get("privileged"))
    return summary


@router.get("/clearance/{case_id}")
def it_clearance(case: ScopedCase, user: DeprovUser) -> dict[str, Any]:
    """Everything needed to work one IT clearance, in a single request.

    One call rather than five because the page is useless in pieces: a revoke
    button beside a stale access list is how somebody revokes twice or not at
    all.
    """
    case_id = str(case.meta.get("case_id"))
    employee_id = str(case.meta.get("employee_id") or "")

    with session_scope() as s:
        stage = s.scalar(select(CaseStage).where(
            CaseStage.case_id == case_id, CaseStage.stage_key == "it"))
        if stage is None:
            raise HTTPException(
                status_code=404,
                detail=f"{case_id} has no IT clearance stage.")
        stage_view = {
            "name": stage.name,
            "status": stage.status,
            "owner": stage.owner or None,
            "owner_user_id": stage.owner_user_id,
            "blocked_reason": stage.blocked_reason,
            "mine": stage.owner_user_id == user.user_id,
        }

    confirmations = stage_service.employee_confirmations(case_id)

    return {
        "case": {
            "case_id": case_id,
            "employee_id": employee_id,
            "employee": case.meta.get("employee_name"),
            "exit_type": case.meta.get("exit_type"),
            "last_working_day": case.meta.get("last_working_day"),
            "overall_status": case.meta.get("overall_status"),
        },
        "stage": stage_view,
        "confirmation": confirmations.get("it"),
        "can_act": may_act_on_stage(user.role, "it"),
        # Live, not remembered: what the identity provider and asset register
        # say right now. The whole point of the page is that these and the
        # buttons beside them agree.
        "access": access_status(case_id),
        "assets": asset_status(case_id),
        "plan": _plan_for(employee_id, user),
    }


@router.post("/clearance/{case_id}/assets")
def log_asset(case: ScopedCase, user: DeprovUser,
              payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record what happened to one company asset.

    The thing that did not exist. Whether a YubiKey came back was inferred from
    the IT Clearance stage, so the only way to say "it is back" was to complete
    the clearance -- which was itself supposed to depend on the YubiKey being
    back. IT now states it directly, with their name against it.
    """
    from ..db.models import ASSET_STATES, AssetReturn

    payload = payload or {}
    asset = str(payload.get("asset") or "").strip()
    state = str(payload.get("state") or "").strip().lower()
    note = str(payload.get("note") or "").strip()

    if not asset:
        raise HTTPException(status_code=422, detail="Which asset?")
    if state not in ASSET_STATES:
        raise HTTPException(
            status_code=422,
            detail=f"State must be one of: {', '.join(ASSET_STATES)}.")
    # Writing an asset off is a charge against someone's final settlement, so
    # it needs a reason on the record rather than a bare click.
    if state == "written_off" and not note:
        raise HTTPException(
            status_code=422,
            detail="Say why it is being written off — it is charged to their "
                   "final settlement and the note is the only explanation "
                   "anyone will find later.")

    case_id = str(case.meta.get("case_id"))
    employee_id = str(case.meta.get("employee_id") or "")
    with session_scope() as s:
        row = s.scalar(select(AssetReturn).where(
            AssetReturn.employee_id == employee_id, AssetReturn.asset == asset))
        if row is None:
            row = AssetReturn(employee_id=employee_id, case_id=case_id, asset=asset)
            s.add(row)
        row.state = state
        row.case_id = case_id
        row.note = note
        row.logged_by = user.full_name or user.email
        row.logged_by_user_id = user.user_id
        s.flush()
        return {"asset": row.asset, "state": row.state,
                "logged_by": row.logged_by, "logged_at": row.logged_at}


@router.post("/clearance/{case_id}/actions")
def run_one_action(case: ScopedCase, user: DeprovUser,
                   payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Carry out a single step of the authorised plan.

    One button that ran twelve revocations at once recorded a fiction: in
    reality AWS goes the hour notice is confirmed, the mailbox converts on the
    last day, and the laptop comes back at a desk in between.
    """
    from ..agents.it_deprovisioning import execute_one_action

    payload = payload or {}
    plan_id = str(payload.get("plan_id") or "").strip()
    try:
        index = int(payload.get("index"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Which step?") from None
    if not plan_id:
        raise HTTPException(status_code=422, detail="Which plan?")

    try:
        return execute_one_action(
            plan_id, index=index,
            executed_by=user.full_name or user.email,
            executor_user_id=user.user_id,
            note=str(payload.get("note") or ""))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
