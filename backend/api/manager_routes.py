"""One person's handover and sign-off, in one place.

A manager's whole part in an exit is two stages -- Knowledge Transfer and
Manager Sign-off -- and one judgement: is the handover good enough that the
team can carry on without this person. Until now, clicking a row in their queue
landed them on HR's case page.

That page is wrong for them in the same way it was wrong for IT, and for the
same reasons. It opens with five clearance stages, three of which a manager
cannot move; it devotes two panels to a document review queue that is HR's
alone to action; it carries the full audit trail and the agent menu. The
handover document itself -- the one artefact the decision actually turns on --
is not on it anywhere. So the manager read a stage list, went to the agent
catalogue to find the KT reviewer, picked their own report out of a dropdown of
record ids, and came back to move a stage.

This endpoint answers their question instead: here is the handover, here is
what the review made of it, here is what the leaver has sent, and here are the
two stages you own.

Sign-off stays a judgement, not a calculation. Nothing here completes a stage;
the ordinary transition route does that, permission-checked and audited like
every other move.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from ..auth import ManagerUser
from ..data_store import store
from ..db import session_scope
from ..db.models import AgentRun, CaseStage
from ..policy import may_act_on_stage
from .deps import ScopedCase

router = APIRouter(prefix="/api/manager", tags=["manager"])

#: The stages a manager owns, in the order the work happens: you review the
#: handover, then you release the person. Mirrors `STAGE_OWNERS`, where both
#: belong to `manager`.
OWNED = ("kt", "manager")


def _kt_doc_for(case_id: str, employee_id: str) -> Any:
    """The handover document for this exit.

    Matched on case first and employee second, the same order
    `cases_routes._related_ids` uses: a KT document carries both, and the
    employee is the fallback for one filed before the case existed.
    """
    for doc in store.kt_docs():
        meta = doc.meta
        if str(meta.get("case_id") or "") == case_id:
            return doc
        if employee_id and str(meta.get("employee_id") or "") == employee_id:
            return doc
    return None


def _latest_review(kt_id: str) -> dict[str, Any] | None:
    """The most recent successful `kt_reviewer` run against this document.

    Read back rather than re-run. The review costs an LLM call and says the
    same thing every time the document has not changed, so opening the page
    should show the last one and leave running another as a decision.

    Matched on the input rather than on `AgentRun.employee_id`, because the
    review is a judgement about a *document*: re-reading it under a second
    employee id would attach one person's verdict to another's handover.
    """
    with session_scope() as s:
        row = s.scalar(
            select(AgentRun)
            .where(AgentRun.agent_id == "kt_reviewer",
                   AgentRun.error.is_(None),
                   AgentRun.result.is_not(None),
                   AgentRun.inputs["kt_id"].astext == kt_id)
            # started_at then id: two runs in the same second would otherwise
            # come back in whatever order the planner chose.
            .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
            .limit(1)
        )
        if row is None:
            return None
        return {"result": row.result, "reviewed_at": row.finished_at}


def _stage_view(stage: CaseStage, user) -> dict[str, Any]:
    return {
        "stage_key": stage.stage_key,
        "name": stage.name,
        "status": stage.status,
        "owner": stage.owner or None,
        "owner_user_id": stage.owner_user_id,
        "blocked_reason": stage.blocked_reason,
        "days_open": stage.days_open,
        "mine": stage.owner_user_id == user.user_id,
    }


@router.get("/signoff/{case_id}")
def manager_signoff(case: ScopedCase, user: ManagerUser) -> dict[str, Any]:
    """Everything needed to review a handover and sign somebody off.

    One call, because the page is useless in pieces: an "accept the handover"
    button beside a document the reader has not been shown is the dead control
    this replaces.
    """
    case_id = str(case.meta.get("case_id"))
    employee_id = str(case.meta.get("employee_id") or "")
    employee = store.employee(employee_id) if employee_id else None

    with session_scope() as s:
        rows = s.scalars(
            select(CaseStage)
            .where(CaseStage.case_id == case_id, CaseStage.stage_key.in_(OWNED))
        ).all()
        by_key = {r.stage_key: _stage_view(r, user) for r in rows}
    stages = [by_key[key] for key in OWNED if key in by_key]

    kt = _kt_doc_for(case_id, employee_id)
    kt_meta = None
    if kt is not None:
        kt_meta = {**kt.meta, "body": kt.body, "title": kt.title}

    # Only the handover's own paperwork. The rest of the case's uploads are
    # finance and HR forms, which are not this reader's to judge and would put
    # a review queue back on a page built to remove one.
    documents = [d for d in store.case_documents(case_id)
                 if str(d.get("document_name") or "").lower()
                 in {"kt document", "successor acknowledgement"}]

    return {
        "case": {
            "case_id": case_id,
            "employee_id": employee_id,
            "employee": case.meta.get("employee_name"),
            "role": (employee.meta.get("role") if employee else None),
            "department": (employee.meta.get("department") if employee else None),
            "exit_type": case.meta.get("exit_type"),
            "last_working_day": case.meta.get("last_working_day"),
            "overall_status": case.meta.get("overall_status"),
        },
        "stages": stages,
        "kt": kt_meta,
        "review": _latest_review(str(kt.meta.get("kt_id") or kt.slug)) if kt else None,
        "documents": documents,
        # One flag for both stages: `STAGE_OWNERS` gives Knowledge Transfer and
        # Manager Sign-off to the same role, so they can never differ.
        "can_act": may_act_on_stage(user.role, "kt"),
    }
