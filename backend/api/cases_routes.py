"""Exit cases: the caller's own case, the case list, documents, stage
transitions, the audit timeline, and the per-role work queue.

Every route that names a case takes `case: ScopedCase` rather than a
`case_id: str`. That is deliberate and structural -- see `deps.scoped_case`.
`tests/test_permissions.py` walks the route table and fails if a new
`{case_id}` route is added without it.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool
# Form parts arrive as this class, not fastapi.UploadFile.
from starlette.datastructures import UploadFile

from ..agents.document_collection import (REQUIRED_DOCUMENTS, document_status,
                                          validate_upload)
from ..llm import LLMError
from ..auth import CurrentUser, HRUser, Identity, StaffUser
from ..data_store import store
from ..db import session_scope
from ..policy import STAGE_OWNERS, may_run_agent
from .. import stages as stage_service
from .deps import ScopedCase, visible_cases

router = APIRouter(prefix="/api", tags=["cases"])


def _stage_keys_for(user: Identity) -> set[str]:
    """Which stage types belong in this caller's queue."""
    if user.is_hr:
        return set(STAGE_OWNERS)
    return {key for key, owners in STAGE_OWNERS.items() if user.role in owners}


@router.get("/me/case")
def my_case(user: CurrentUser) -> dict[str, Any]:
    """The signed-in person's own exit case: stages, status, and exactly which
    documents are still owed. Scoped to `user.employee_id` from the session --
    never a client-supplied id, so nobody can request another person's case by
    guessing a case_id.

    Not restricted by role: an IT engineer or a manager who is themselves
    leaving has an exit case too, and should be able to see it.
    """
    case = store.exit_case_for_employee(user.employee_id) if user.employee_id else None
    if not case:
        raise HTTPException(
            status_code=404, detail="No exit case is associated with this account."
        )
    return {"case": case.meta, "documents": document_status(case.meta)}


#: Documents the company issues at sign-off. An employee must never be able to
#: upload one of these -- a self-issued relieving letter is a forged record.
_ISSUED = {d["name"] for d in REQUIRED_DOCUMENTS if d.get("issued")}

#: What an employee may send us, in the order the process asks for it.
_UPLOADABLE = [d for d in REQUIRED_DOCUMENTS if not d.get("issued")]

#: Same cap as the agent upload route. Checked against Content-Length before
#: request.form() runs, because that call spools the whole body first.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _my_case(user: Identity):
    """The caller's own exit case, or 404.

    Resolved from `user.employee_id` on the session -- never from anything the
    client sent, so there is no id to tamper with.
    """
    case = store.exit_case_for_employee(user.employee_id) if user.employee_id else None
    if not case:
        raise HTTPException(
            status_code=404, detail="No exit case is associated with this account."
        )
    return case


def _upload_checklist(case_meta: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per document, carrying both what is required and what was sent.

    This used to return the requirement only, and the page listed every upload
    row separately underneath. That read badly: re-sending a rejected form
    produced two entries for the same document, and the requirement list and
    the sent list disagreed about the same thing at the same time. A person
    owes a *document*, not a series of attempts -- so the attempt is folded
    into the requirement, and only the latest one counts.

    Latest-wins matches `ExitCase.submitted_documents()` exactly, which is what
    actually decides whether the requirement is cleared. Same rule, one place
    to read it.
    """
    by_name = {r["document"]: r for r in document_status(case_meta)["documents"]}

    # Uploads arrive oldest-first (ordered by uploaded_at, id), so the last one
    # written per name is the one that counts.
    latest: dict[str, dict[str, Any]] = {}
    attempts: dict[str, int] = {}
    for upload in store.case_documents(str(case_meta.get("case_id") or "")):
        name = upload.get("document_name")
        latest[name] = upload
        attempts[name] = attempts.get(name, 0) + 1

    rows = []
    for d in _UPLOADABLE:
        name = d["name"]
        required = by_name.get(name, {}).get("status", "missing")
        sent = latest.get(name)
        verdict = (sent or {}).get("verdict")

        # One vocabulary the UI can switch on, rather than making it reconcile
        # "missing" against a rejected upload to work out what to show.
        if verdict == "accepted":
            state = "accepted"
        elif verdict == "rejected":
            state = "rejected"
        elif verdict:
            state = "in_review"
        elif required == "not_yet_due":
            state = "not_yet_due"
        else:
            state = "needed"

        rows.append({
            "document": name,
            "stage": d["stage"],
            "state": state,
            "conditional": bool(d.get("conditional")),
            # Accepted is the only state with nothing left to do. "not_yet_due"
            # still takes an upload: being early is not a mistake, the paperwork
            # is the same paperwork.
            "can_upload": state != "accepted",
            "attempts": attempts.get(name, 0),
            "latest": {
                "id": sent["id"],
                "filename": sent.get("filename"),
                "verdict": sent.get("verdict"),
                "reason": sent.get("reason"),
                "uploaded_at": sent.get("uploaded_at"),
                "reviewed_by": sent.get("reviewed_by"),
                "review_note": sent.get("review_note"),
                "has_image": sent.get("has_image"),
            } if sent else None,
        })
    return rows


@router.get("/me/case/documents")
def my_case_documents(user: CurrentUser) -> dict[str, Any]:
    """The caller's document checklist: what is required, what they sent, and
    what happened to it -- one row per document, not one per attempt."""
    case = _my_case(user)
    status = document_status(case.meta)
    return {
        "case_id": case.meta.get("case_id"),
        "checklist": _upload_checklist(case.meta),
        "summary": {
            "submitted": status["submitted_count"],
            "required_now": status["required_now_count"],
            "completion_pct": status["completion_pct"],
            "all_in": status["all_due_documents_in"],
        },
    }


@router.get("/me/case/documents/{doc_id}/image")
def my_document_image(doc_id: int, user: CurrentUser) -> Response:
    """The caller's own scan. Scoped by resolving their case from the session,
    so the doc_id alone grants nothing."""
    case = _my_case(user)
    found = store.document_image(str(case.meta.get("case_id")), doc_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No scan is stored for that document.")
    data, content_type, filename = found
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/me/case/documents")
async def upload_my_document(user: CurrentUser, request: Request) -> dict[str, Any]:
    """Upload one exit document against the caller's own case.

    The employee-facing half of Document Collection. Until now an employee
    could see exactly which documents they owed and had no way to send any of
    them -- the only upload route was HR-only, so the person who actually holds
    the signed NDA had to email it to somebody.

    Same pipeline as the HR route: local OCR, then a cheap text judgement, then
    persisted with its verdict. The image never leaves the machine.
    """
    case = _my_case(user)

    content_length = request.headers.get("content-length")
    if content_length is not None and int(content_length) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="That file is too large (max 10 MB).")

    form = await request.form()
    document_name = str(form.get("document_name") or "").strip()
    upload = form.get("file")

    if not document_name:
        raise HTTPException(status_code=422, detail="Say which document this is.")
    if document_name in _ISSUED:
        # Not a validation nicety: these are the records the company issues.
        raise HTTPException(
            status_code=403,
            detail=f"'{document_name}' is issued to you by HR at sign-off, "
                   "so it is not something you upload.",
        )
    if document_name not in {d["name"] for d in _UPLOADABLE}:
        raise HTTPException(
            status_code=422, detail=f"'{document_name}' is not a document on your checklist."
        )
    if not isinstance(upload, UploadFile):
        raise HTTPException(status_code=422, detail="Attach a scan or photo of the document.")

    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="That file is too large (max 10 MB).")
    if not data:
        raise HTTPException(status_code=422, detail="That file is empty.")

    try:
        result = await run_in_threadpool(
            validate_upload,
            case.meta, document_name, data,
            upload.filename or "", upload.content_type or "",
            user.full_name or user.email,
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"LLM gateway error: {exc}") from exc

    if result.get("error"):
        raise HTTPException(status_code=422, detail=result["error"])

    # Re-read the case: an accepted upload changes `documents_submitted`, so the
    # caller gets the updated progress in the same response rather than having
    # to refetch and briefly show stale numbers.
    refreshed = _my_case(user)
    status = document_status(refreshed.meta)
    verdict = str(result.get("validation", {}).get("verdict") or "needs_review")

    return {
        "document": document_name,
        "verdict": verdict,
        "reason": result.get("validation", {}).get("reason") or "",
        "persisted": result.get("persisted"),
        "checklist": _upload_checklist(refreshed.meta),
        "summary": {
            "submitted": status["submitted_count"],
            "required_now": status["required_now_count"],
            "completion_pct": status["completion_pct"],
            "all_in": status["all_due_documents_in"],
        },
    }


@router.get("/cases")
def list_cases(user: StaffUser) -> dict[str, Any]:
    """Exit cases within the caller's scope, as picker options."""
    return {
        "cases": [
            {"value": str(d.meta.get("case_id", d.slug)),
             "label": f"{d.meta.get('case_id', d.slug)} · "
                      f"{d.meta.get('employee_name', '')}".strip(" ·")}
            for d in visible_cases(user)
        ]
    }


@router.get("/queue")
def work_queue(
    user: StaffUser,
    scope: str = Query("mine", pattern="^(mine|all)$"),
) -> dict[str, Any]:
    """The caller's work: open stages of the types their role owns.

    `scope=mine` is stages assigned to them; `scope=all` is every open stage of
    those types, including unclaimed ones, so work can be picked up when the
    assignee is away. Ordered worst-first (breaching, then longest open) --
    a queue sorted by id buries the case about to miss its last working day.
    """
    stage_keys = _stage_keys_for(user)
    if not stage_keys:
        return {"scope": scope, "stages": []}

    # HR and IT see every case, so skip building the id set for them.
    case_ids = None
    if user.role not in ("hr", "it"):
        case_ids = {str(c.meta.get("case_id")) for c in visible_cases(user)}

    with session_scope() as s:
        rows = stage_service.queue(
            s,
            stage_keys=stage_keys,
            case_ids=case_ids,
            owner_user_id=user.user_id if scope == "mine" else None,
        )
    return {"scope": scope, "stage_keys": sorted(stage_keys), "stages": rows}


#: Agents worth offering from a case page, and how to point them at it.
#: `param` is the field the case's related record id goes into.
_CASE_ACTIONS = [
    {"agent_id": "kt_reviewer", "param": "kt_id",
     "label": "Review the handover document",
     "blurb": "Scores the KT document against role-critical topics and flags gaps."},
    {"agent_id": "risk_assessment", "param": "employee_id",
     "label": "Assess exit risk",
     "blurb": "Knowledge concentration, project exposure and handover risk."},
    {"agent_id": "checklist", "param": "employee_id",
     "label": "Generate an exit checklist",
     "blurb": "A stage-by-stage checklist for this person's exit."},
    {"agent_id": "compliance", "param": "case_id",
     "label": "Check clearance compliance",
     "blurb": "Whether this case meets policy for a clean release."},
    {"agent_id": "multi_system_clearance", "param": "case_id",
     "label": "Check system clearance",
     "blurb": "Live status across IT assets, IAM, HRMS and finance."},
    {"agent_id": "it_deprovisioning", "param": "employee_id",
     "label": "Draft a deprovisioning plan",
     "blurb": "An ordered plan of accounts to disable and access to revoke."},
    {"agent_id": "interview_summarizer", "param": "interview_id",
     "label": "Summarise the exit interview",
     "blurb": "Themes, sentiment and anything that needs escalating."},
]


def _related_ids(case_meta: dict[str, Any]) -> dict[str, str]:
    """The record ids that belong to this case, for prefilling an agent form."""
    case_id = str(case_meta.get("case_id") or "")
    employee_id = str(case_meta.get("employee_id") or "")
    ids = {"case_id": case_id, "employee_id": employee_id}

    for doc in store.kt_docs():
        if str(doc.meta.get("case_id") or "") == case_id or (
                employee_id and str(doc.meta.get("employee_id") or "") == employee_id):
            ids["kt_id"] = str(doc.meta.get("kt_id") or doc.slug)
            break

    for doc in store.interviews():
        if employee_id and str(doc.meta.get("employee_id") or "") == employee_id:
            ids["interview_id"] = str(doc.meta.get("interview_id") or doc.slug)
            break

    return ids


@router.get("/cases/{case_id}")
def case_detail(case: ScopedCase, user: StaffUser) -> dict[str, Any]:
    """One case: its documents, the stages this caller may move, and the agents
    they can run against it.

    `actions` is computed here rather than in the UI because the rule already
    lives here -- `may_run_agent` is the same table the catalogue and the run
    route read. It carries the related record id with it, so a manager reviewing
    a handover never has to know that Aarav Mehta is emp-001 and his KT document
    is kt-001. Picking a person out of a dropdown of employee codes was the
    single least usable thing in the app.
    """
    related = _related_ids(case.meta)
    actions = [
        {**action, "value": related[action["param"]]}
        for action in _CASE_ACTIONS
        if may_run_agent(user.role, action["agent_id"]) and related.get(action["param"])
    ]
    return {
        "case": case.meta,
        "documents": document_status(case.meta),
        "may_action": sorted(_stage_keys_for(user)),
        "related": related,
        "actions": actions,
    }


@router.get("/cases/{case_id}/timeline")
def case_timeline(case: ScopedCase, user: StaffUser) -> dict[str, Any]:
    return {
        "case_id": case.meta.get("case_id"),
        "events": stage_service.timeline(str(case.meta.get("case_id"))),
    }


@router.post("/cases/{case_id}/stages/{stage_key}/transition")
def transition_stage(
    case: ScopedCase, stage_key: str, user: StaffUser,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Move one clearance stage, and record who did it and why.

    Authorisation is two independent checks that both have to pass: `ScopedCase`
    says the caller may see this case at all, and `stage_service.transition`
    says their role owns this stage type (or that they are HR, in which case it
    is recorded as an escalation).
    """
    payload = payload or {}
    try:
        return stage_service.transition(
            case_id=str(case.meta.get("case_id")),
            stage_key=stage_key,
            to_status=str(payload.get("to") or ""),
            actor=user,
            note=str(payload.get("note") or ""),
            blocked_reason=(str(payload.get("blocked_reason")).strip()
                            if payload.get("blocked_reason") else None),
            claim=bool(payload.get("claim")),
        )
    except stage_service.TransitionError as exc:
        # 422, not 403: the caller may act here in principle, the specific move
        # is what is wrong. A genuine role refusal is raised by ScopedCase or by
        # may_act_on_stage, and those carry their own status codes.
        status = 403 if "does not own" in str(exc) or "Only HR" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.post("/cases/{case_id}/stages/{stage_key}/claim")
def claim_stage(case: ScopedCase, stage_key: str, user: StaffUser) -> dict[str, Any]:
    """Take ownership of an unclaimed stage without changing its status."""
    from sqlalchemy import select

    from ..db.models import CaseStage
    from ..policy import may_act_on_stage

    if not may_act_on_stage(user.role, stage_key):
        raise HTTPException(
            status_code=403, detail="Your role does not own this clearance stage."
        )
    with session_scope() as s:
        stage = s.scalar(select(CaseStage).where(
            CaseStage.case_id == str(case.meta.get("case_id")),
            CaseStage.stage_key == stage_key,
        ))
        if stage is None:
            raise HTTPException(
                status_code=404, detail=f"This case has no '{stage_key}' stage."
            )
        stage.owner_user_id = user.user_id
        if not (stage.owner or "").strip():
            stage.owner = user.full_name or user.email
        return {"case_id": case.meta.get("case_id"), "stage_key": stage_key,
                "owner": stage.owner, "owner_user_id": stage.owner_user_id}


@router.post("/employees")
def create_employee(user: HRUser, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add a new employee record. If marked "exiting", also creates a starter
    exit case for the orchestrator and, if the email has no account yet, a
    one-time login credential."""
    try:
        emp_doc, case_doc, credential = store.add_employee(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "employee": emp_doc.meta,
        "case": case_doc.meta if case_doc else None,
        "credential": credential,
    }


@router.get("/cases/{case_id}/documents")
def list_case_documents(case: ScopedCase, user: StaffUser) -> dict[str, Any]:
    """Every uploaded document on a case, every verdict -- the raw rows behind
    Document Collection's `documents_submitted` summary, so a reviewer can see
    (and override) exactly what the model decided and why."""
    case_id = str(case.meta.get("case_id"))
    return {"case_id": case_id, "documents": store.case_documents(case_id)}


@router.get("/cases/{case_id}/documents/{doc_id}/image")
def case_document_image(case: ScopedCase, doc_id: int, user: StaffUser) -> Response:
    """The stored scan itself.

    Behind `ScopedCase`, so a manager can only pull images from their own
    team's cases -- these are people's signed personal paperwork, and a
    guessable `/documents/287/image` with no case scope would be the easiest
    possible data leak in the app.

    `inline` rather than `attachment`: the point is to look at it next to the
    verdict, not to collect a folder of downloads.
    """
    found = store.document_image(str(case.meta.get("case_id")), doc_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No scan is stored for that document.")
    data, content_type, filename = found
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            # Personal paperwork: never let a shared cache hold it.
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/cases/{case_id}/documents/{doc_id}/review")
def review_case_document(
    case: ScopedCase, doc_id: int, user: HRUser,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A human overrides the model's verdict on one uploaded document.

    The verdict this writes is exactly what `ExitCase.submitted_documents()`
    reads back, so overriding it here is what actually clears (or reinstates)
    the requirement everywhere else in the app -- not a note bolted on beside
    an unchanged status.
    """
    case_id = str(case.meta.get("case_id"))
    payload = payload or {}
    try:
        document = store.review_document(
            case_id, doc_id,
            verdict=str(payload.get("verdict") or ""),
            reviewed_by=user.full_name or user.email,
            note=str(payload.get("note") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"case_id": case_id, "document": document}
