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
        actions = [a for a in (plan.get("actions") or []) if isinstance(a, dict)]
        return {
            "plan_id": row.id,
            "status": row.status,
            "summary": plan.get("summary"),
            "urgency": plan.get("urgency"),
            "actions": actions,
            "action_count": len(actions),
            "privileged_count": sum(1 for a in actions if a.get("privileged")),
            "approved_by": row.approved_by,
            "approved_at": row.approved_at,
            "executed_by": row.executed_by,
            "executed_at": row.executed_at,
            # Authorising is HR's alone. IT sees the state so it knows whether
            # it is waiting or being waited on -- the thing the separate page
            # existed to tell them, said where the work is.
            "can_approve": row.status == "pending" and user.is_hr,
            "can_execute": row.status == "approved" and user.role in ("it", "hr"),
            "executed_count": sum(1 for r in rows if r.status == "executed"),
        }


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
