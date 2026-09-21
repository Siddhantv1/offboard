"""The LangGraph exit-clearance orchestrator, and the deprovisioning approvals
queue it pauses on.

Running the orchestrator stays HR-only. IT gets read access to the approvals
list (it is their work that comes out of the gate) but cannot start a run or
answer the gate.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .. import auth
from ..agents.it_deprovisioning import approve_plan
from ..auth import DeprovUser, HRUser
from ..llm import LLMError
from sqlalchemy import select

from ..data_store import store
from ..db import session_scope
from ..db.models import DeprovisioningPlan
from ..orchestration import (pending_approval, pending_approvals,
                             run_orchestration, stream_orchestration)
from .deps import ScopedCase

router = APIRouter(prefix="/api", tags=["orchestration"])
logger = logging.getLogger(__name__)


@router.get("/approvals")
def list_pending_approvals(user: DeprovUser) -> dict[str, Any]:
    """Orchestration runs paused at the human approval gate, across all
    sessions and surviving restarts.

    Before the checkpoints were durable, a paused run was only reachable via
    the thread_id held in the browser tab that started it.
    """
    return {"approvals": pending_approvals()}


@router.get("/deprovisioning/plans")
def list_deprovisioning_plans(user: DeprovUser, status: str | None = None) -> dict[str, Any]:
    """Every drafted deprovisioning plan and where it has got to.

    This did not exist, which made the whole Deprovisioning page a lie: it was
    reading `/api/approvals`, which lists LangGraph runs paused at the
    orchestrator's interrupt -- a different thing entirely. Plans drafted by
    running the IT Deprovisioning agent directly were written to the database
    and then unreachable by anyone, so neither HR could approve one nor IT run
    one. There were seven sitting in `deprovisioning_plans` with nothing in the
    product able to show them.

    Open to `hr` and `it` because both halves of the workflow live here: HR
    authorises, IT executes, and each needs to see the other's queue to know
    whether they are waiting or being waited on.
    """
    wanted = (status or "").strip().lower()
    if wanted and wanted not in ("pending", "approved", "executed"):
        raise HTTPException(
            status_code=422,
            detail="status must be pending, approved or executed.",
        )

    with session_scope() as s:
        stmt = select(DeprovisioningPlan).order_by(
            DeprovisioningPlan.created_at.desc())
        if wanted:
            stmt = stmt.where(DeprovisioningPlan.status == wanted)
        rows = s.scalars(stmt).all()

        out = []
        for row in rows:
            plan = row.plan or {}
            actions = [a for a in (plan.get("actions") or []) if isinstance(a, dict)]
            employee = store.employee(row.employee_id)
            out.append({
                "plan_id": row.id,
                "status": row.status,
                "employee_id": row.employee_id,
                "employee": plan.get("employee") or (employee.meta.get("name")
                                                     if employee else row.employee_id),
                "case_id": row.case_id,
                "summary": plan.get("summary"),
                "urgency": plan.get("urgency"),
                "actions": actions,
                "action_count": len(actions),
                "privileged_count": sum(1 for a in actions if a.get("privileged")),
                "risks_if_delayed": plan.get("risks_if_delayed") or [],
                "dependencies": plan.get("dependencies") or [],
                "approved_by": row.approved_by,
                "approved_at": row.approved_at,
                "executed_by": row.executed_by,
                "executed_at": row.executed_at,
                "note": row.note,
                "execution": row.execution,
                # What this caller can do with it right now, decided here
                # rather than re-derived in the UI from role plus status.
                "can_approve": row.status == "pending" and user.is_hr,
                "can_execute": row.status == "approved" and user.role in ("it", "hr"),
            })
    return {"plans": out}


@router.post("/deprovisioning/{plan_id}/approve")
def approve_deprovisioning_plan(
    plan_id: str, user: HRUser, payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """HR authorises a drafted deprovisioning plan so IT can run it.

    HR-only, and separate from executing, because those must be two people --
    see `it_deprovisioning.execute_plan`.
    """
    payload = payload or {}
    try:
        return approve_plan(
            plan_id,
            approved_by=user.full_name or user.email,
            approver_user_id=user.user_id,
            note=str(payload.get("note") or ""),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/orchestrate/{case_id}")
def orchestrate_case(case: ScopedCase, user: HRUser,
                     payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the multi-agent LangGraph orchestrator for one exit case end to end.
    Non-interactive: the human approval gate is answered with `decision`
    from the body (default "approve"). Use the /stream route for a real pause.

    Takes `ScopedCase` as well as `HRUser` -- redundant today, since HR sees
    every case, but it keeps every `{case_id}` route resolving its case the one
    supported way. `tests/test_permissions.py` enforces that uniformity, and an
    exception here would be the hole a future role widening falls through.
    """
    case_id = str(case.meta.get("case_id"))
    decision = str((payload or {}).get("decision") or "approve").lower()
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="decision must be 'approve' or 'reject'.")
    try:
        result = run_orchestration(
            case_id, decision=decision, note=str((payload or {}).get("note") or ""),
            by=user.full_name or user.email,
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"LLM gateway error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("orchestration failed for case '%s'", case_id)
        raise HTTPException(
            status_code=500, detail="Something went wrong running the orchestrator."
        ) from exc
    return {"case_id": case_id, "orchestration": result}


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/orchestrate/{case_id}/stream")
def orchestrate_case_stream(
    request: Request, user: HRUser, case: ScopedCase,
    thread_id: str | None = None, decision: str | None = None,
    note: str = "", csrf_token: str | None = None,
) -> StreamingResponse:
    """The orchestrator as Server-Sent Events so the UI can light up each node
    the moment it starts and finishes:
      run_start {thread_id}          node_start {node, step}
      node_end  {node, step, error, log, retries}
      awaiting_approval {thread_id, case_id, employee, plan}  -- run is paused
      done      {result}             error {message}

    A fresh call starts a run. When it pauses at the human approval gate, call
    the same route again with `thread_id` and `decision=approve|reject` (plus an
    optional `note`) to resume that run and stream the rest of it. GET rather
    than POST because the browser's EventSource can only GET -- which also
    means `SameSite=Lax` alone will not stop a cross-site top-level navigation
    from carrying the session cookie here, so a resume additionally requires
    the double-submit `csrf_token`.
    """
    case_id = str(case.meta.get("case_id"))

    resume: dict[str, Any] | None = None
    if thread_id or decision:
        if not (thread_id and decision):
            raise HTTPException(status_code=422, detail="Resuming needs both thread_id and decision.")
        auth.verify_csrf(request, csrf_token)
        decision = decision.lower()
        if decision not in ("approve", "reject"):
            raise HTTPException(status_code=422, detail="decision must be 'approve' or 'reject'.")
        pending = pending_approval(thread_id)
        if not pending:
            raise HTTPException(
                status_code=409,
                detail="No approval is pending for that run (it may have finished "
                       "or been restarted).")
        if str(pending.get("case_id")) != case_id:
            raise HTTPException(status_code=409, detail="That run belongs to a different exit case.")
        resume = {"decision": decision, "note": note, "by": user.full_name or user.email}

    def events() -> Iterator[str]:
        try:
            for ev in stream_orchestration(case_id, thread_id=thread_id, resume=resume):
                yield _sse(ev.pop("event"), ev)
        except LLMError as exc:
            yield _sse("error", {"message": f"LLM gateway error: {exc}"})
        except Exception:  # noqa: BLE001
            logger.exception("orchestration stream failed for case '%s'", case_id)
            yield _sse("error", {"message": "Something went wrong running the orchestrator."})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
