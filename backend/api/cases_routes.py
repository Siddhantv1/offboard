"""Exit cases: the caller's own case, the case list, documents, stage
transitions, the audit timeline, and the per-role work queue.

Every route that names a case takes `case: ScopedCase` rather than a
`case_id: str`. That is deliberate and structural -- see `deps.scoped_case`.
`tests/test_permissions.py` walks the route table and fails if a new
`{case_id}` route is added without it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool
# Form parts arrive as this class, not fastapi.UploadFile.
from starlette.datastructures import UploadFile

from ..agents.document_collection import (REQUIRED_DOCUMENTS, document_status,
                                          validate_upload, who_accepts)
from ..llm import LLMError
from ..auth import CurrentUser, HRUser, Identity, StaffUser
from .. import ocr
from ..data_store import store
from ..db import session_scope
from ..policy import STAGE_OWNERS, may_run_agent
from .. import checklist_progress
from .. import exit_interview
from .. import issued_documents
from .. import stages as stage_service
from .deps import ScopedCase, can_see_case, visible_cases

router = APIRouter(prefix="/api", tags=["cases"])
logger = logging.getLogger(__name__)


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
    return {
        "case": case.meta,
        "documents": document_status(case.meta),
        # The stages with a side the employee has to mark themselves. IT
        # Clearance is the only one: it turns on a physical handover no
        # document ever evidenced.
        "confirmations": stage_service.employee_confirmations(
            str(case.meta.get("case_id"))),
    }


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
            # Who the leaver is actually waiting on. Nothing is auto-accepted
            # any more, so "under review" is now every document's normal state
            # and the row told all of them it was with HR -- wrong for the two
            # the manager decides.
            "decided_by": who_accepts(name),
            "attempts": attempts.get(name, 0),
            "latest": {
                "id": sent["id"],
                "filename": sent.get("filename"),
                "verdict": sent.get("verdict"),
                "reason": sent.get("reason"),
                "uploaded_at": sent.get("uploaded_at"),
                "reviewed_by": sent.get("reviewed_by"),
                "review_note": sent.get("review_note"),
                "has_file": sent.get("has_file"),
                "content_type": sent.get("content_type"),
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


#: Types a browser may render on our own origin.
#:
#: An uploaded HTML file served inline would execute as a page of this
#: application, at this application's origin, with the signed-in person's
#: session attached -- stored XSS, handed to us by the very feature that
#: accepts HTML. So anything outside this set is sent as an attachment, with a
#: sandbox policy in case something renders it anyway. PDFs stay inline: the
#: browser's PDF viewer is not a DOM and the point of accepting them is that
#: you can read one without downloading it.
_INLINE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif",
                 "application/pdf"}


def _stored_file(found: tuple[bytes, str, str] | None) -> Response:
    """Hand back one stored upload, with the disposition its type has earned."""
    if found is None:
        raise HTTPException(status_code=404,
                            detail="No file is stored for that document.")
    data, content_type, filename = found
    inline = content_type.split(";")[0].strip().lower() in _INLINE_TYPES
    headers = {
        "Content-Disposition":
            f'{"inline" if inline else "attachment"}; filename="{filename}"',
        # Personal paperwork: never let a shared cache hold it.
        "Cache-Control": "private, no-store",
        # Without this a browser may sniff past the type we declared and
        # render as HTML something we deliberately sent as a download.
        "X-Content-Type-Options": "nosniff",
    }
    if not inline:
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return Response(content=data, media_type=content_type, headers=headers)


@router.get("/me/case/documents/{doc_id}/file")
def my_document_file(doc_id: int, user: CurrentUser) -> Response:
    """The caller's own upload. Scoped by resolving their case from the
    session, so the doc_id alone grants nothing."""
    case = _my_case(user)
    return _stored_file(store.document_file(str(case.meta.get("case_id")), doc_id))


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
        raise HTTPException(
            status_code=422,
            detail=f"Attach the document — {ocr.ACCEPTED_LABEL}.")

    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="That file is too large (max 10 MB).")
    if not data:
        raise HTTPException(status_code=422, detail="That file is empty.")

    # The type gate, and until now there wasn't one: the route took whatever
    # bytes arrived and only failed further in, when PIL could not open them,
    # as "Could not read the image" -- so an .exe was stored in the database
    # before anything objected. Sniffed rather than trusted, because the
    # declared type is a claim by the uploader and this decision also governs
    # how the file is handed back out again.
    kind = ocr.sniff_kind(data, upload.content_type or "", upload.filename or "")
    if kind is None:
        raise HTTPException(
            status_code=415,
            detail=f"That is not a file we can read. Send a {ocr.ACCEPTED_LABEL} "
                   "file — a PDF of the signed form is ideal.")

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


# -- the employee's exit interview -------------------------------------------
# Same shape as the document routes above and for the same reason: the case is
# resolved from `user.employee_id` on the session, so these carry no case id
# and there is nothing for a caller to substitute.


@router.get("/me/case/interview")
def my_exit_interview(user: CurrentUser) -> dict[str, Any]:
    """The exit interview questions, whatever this person has answered so far,
    and whether they have sent it."""
    case = _my_case(user)
    return exit_interview.state_for(str(case.meta.get("case_id")))


@router.post("/me/case/interview")
def save_my_exit_interview(user: CurrentUser,
                           payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Save progress, or submit.

    Two operations rather than two endpoints, because the payload is identical
    and splitting them would mean the page keeping two code paths that must
    send the same answers. `submitted: true` is the difference.
    """
    case = _my_case(user)
    payload = payload or {}
    try:
        return exit_interview.save(
            case_id=str(case.meta.get("case_id")),
            employee_id=str(case.meta.get("employee_id") or "") or None,
            answers=payload.get("answers"),
            submitted=bool(payload.get("submitted")),
        )
    except exit_interview.InterviewLocked as exc:
        # 409, not 403: the caller is the right person, the record is simply
        # closed. A 403 would read as "this is not your interview".
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/cases/{case_id}/interview")
def case_exit_interview(case: ScopedCase, user: HRUser) -> dict[str, Any]:
    """The submitted exit interview for a case the caller can already see.

    A sibling endpoint rather than another key on `/api/cases/{case_id}`: the
    answers are free text of unbounded length and every consumer of the case
    detail would pay for them on every load, including the ones that only want
    the stage list.

    **HR only, and the narrowing is the point.** This was `StaffUser`, which
    includes `manager` -- and a manager's scope is exactly the people who
    report to them. So the one role the instrument is partly *about* could read
    it. Question `manager` asks "how was working with your manager -- what did
    they do well, and where did you need more from them?", and the form tells
    the leaver their answers do not affect their evaluation. The UI only ever
    called this from HR's case page, so the API and the UI disagreed and the
    API is the one that decides.
    """
    return exit_interview.submitted_for_case(str(case.meta.get("case_id")))


# -- the employee's own exit checklist ----------------------------------------


def _latest_checklist_run(employee_id: str) -> dict[str, Any]:
    """The most recent successful `checklist` run for this employee."""
    from sqlalchemy import select

    from ..db.models import AgentRun

    with session_scope() as s:
        row = s.scalar(
            select(AgentRun)
            .where(AgentRun.agent_id == "checklist",
                   AgentRun.employee_id == employee_id,
                   AgentRun.error.is_(None),
                   AgentRun.result.is_not(None))
            # started_at then id: two runs in the same second would otherwise
            # come back in whatever order the planner chose.
            .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
            .limit(1)
        )
        row_finished = row.finished_at if row else None
        result = row.result if row else None

    # Annotated at read time, not regenerated. The stored run is the wording;
    # what has actually been done comes from the systems that know. See
    # `checklist_progress`.
    if isinstance(result, dict) and isinstance(result.get("checklist"), dict):
        case = store.exit_case_for_employee(employee_id)
        if case:
            result = {**result, "checklist": checklist_progress.annotate(
                result["checklist"], str(case.meta.get("case_id")))}
    return {
        "employee_id": employee_id,
        "result": result,
        "generated_at": row_finished,
    }


@router.get("/me/case/checklist")
def my_exit_checklist(user: CurrentUser) -> dict[str, Any]:
    """This person's own exit checklist, if one has been generated.

    The `checklist` agent describes the employee's own exit -- assets they hold,
    access they still have, what they have to hand over -- and until now the
    only person who could not see it was its subject, because `ROLE_AGENTS`
    gates the generic run route by agent id.
    """
    _my_case(user)
    return _latest_checklist_run(str(user.employee_id))


@router.post("/me/case/checklist")
def run_my_exit_checklist(user: CurrentUser) -> dict[str, Any]:
    """Generate this person's exit checklist.

    Takes no payload at all. `checklist` is not in `ROLE_AGENTS["employee"]`
    precisely so that an employee cannot aim it, and accepting an employee_id
    here -- even one that is then checked -- would put the id back in the
    client's hands and make this route the thing that has to get the check
    right. There is no id to get wrong: it comes off the session.
    """
    _my_case(user)
    from ..agents import REGISTRY

    agent = REGISTRY["checklist"]
    try:
        result = agent.execute({"employee_id": str(user.employee_id)})
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"LLM gateway error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("the employee exit checklist failed")
        raise HTTPException(
            status_code=500, detail="Something went wrong generating your checklist."
        ) from exc
    return {"employee_id": user.employee_id, "result": result,
            "generated_at": datetime.now(timezone.utc)}


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
            viewer_user_id=user.user_id,
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

    for doc in store.all_kt_docs():
        if str(doc.meta.get("case_id") or "") == case_id or (
                employee_id and str(doc.meta.get("employee_id") or "") == employee_id):
            ids["kt_id"] = str(doc.meta.get("kt_id") or doc.slug)
            break

    for doc in store.all_interviews():
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
        # The employee's half of a two-sided stage. Not in `case.meta` because
        # that shape is the agents' context and is pinned by the golden
        # snapshot -- but IT cannot verify a clearance without being told
        # whether the person has said they are done.
        "employee_confirmations": stage_service.employee_confirmations(
            str(case.meta.get("case_id"))),
    }


@router.post("/me/case/stages/{stage_key}/confirm")
def confirm_my_stage(user: CurrentUser, stage_key: str,
                     payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """The employee's side of a two-sided clearance.

    Scoped to the signed-in person's own case, resolved from the session --
    there is no case id to pass and therefore none to tamper with.

    This does not complete anything. It records a claim, which the owning role
    then verifies. Letting the employee's own click close their IT clearance
    would be the same hole the document uploads had: a box ticked by the one
    person with a reason to tick it.
    """
    payload = payload or {}
    if not user.employee_id:
        raise HTTPException(
            status_code=404, detail="No exit case is associated with this account.")
    case = store.exit_case_for_employee(user.employee_id)
    if not case:
        raise HTTPException(
            status_code=404, detail="No exit case is associated with this account.")
    try:
        return stage_service.confirm_employee_side(
            case_id=str(case.meta.get("case_id")),
            stage_key=stage_key,
            actor=user,
            note=str(payload.get("note") or ""),
        )
    except stage_service.TransitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


@router.post("/cases/{case_id}/stages/{stage_key}/escalate")
def escalate_stage(case: ScopedCase, stage_key: str, user: StaffUser,
                   payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record that this stage has been chased.

    Deliberately not gated on `may_act_on_stage`: chasing a stage you do not
    own is the entire point -- HR chases IT, a manager chases HR. Seeing the
    case is the only permission that applies, and `ScopedCase` has already
    established it.
    """
    payload = payload or {}
    try:
        return stage_service.record_escalation(
            case_id=str(case.meta.get("case_id")),
            stage_key=stage_key,
            actor=user,
            note=str(payload.get("note") or ""),
        )
    except stage_service.TransitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/cases/{case_id}/stages/{stage_key}/claim")
def claim_stage(case: ScopedCase, stage_key: str, user: StaffUser) -> dict[str, Any]:
    """Take ownership of a stage without changing its status.

    Shares `assign_owner` with the assign route below, so both write the owner
    and the audit event the same way. They used to each set the columns
    themselves and neither recorded anything.
    """
    from ..policy import may_act_on_stage

    if not may_act_on_stage(user.role, stage_key):
        raise HTTPException(
            status_code=403, detail="Your role does not own this clearance stage."
        )
    try:
        return stage_service.assign_owner(
            case_id=str(case.meta.get("case_id")),
            stage_key=stage_key,
            assignee_user_id=user.user_id,
            assignee_label=user.full_name or user.email,
            actor=user,
            self_claim=True,
        )
    except stage_service.StageClosedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except stage_service.TransitionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/cases/{case_id}/stages/{stage_key}/assign")
def assign_stage(case: ScopedCase, stage_key: str, user: StaffUser,
                 payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Put a named person on a stage.

    The sibling `claim` route only ever assigns to the caller, which left the
    Smart Routing agent recommending an approver with no way to apply the
    recommendation -- advice you had to carry out by hand elsewhere, which is
    most of why that agent felt useless.

    The assignee must hold a role that owns this stage type. Routing HR's
    finance clearance to an IT engineer would be a rota error, not a judgement
    call, so it is refused rather than warned about.
    """
    from ..db.models import User
    from ..policy import STAGE_OWNERS, may_act_on_stage

    payload = payload or {}
    try:
        assignee_id = int(payload.get("user_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Give the user_id to assign.") from None

    if not may_act_on_stage(user.role, stage_key):
        raise HTTPException(
            status_code=403, detail="Your role does not own this clearance stage.")

    # Resolving the assignee is an authorisation question and stays here; the
    # write and its audit record belong to the service, shared with `claim`.
    with session_scope() as s:
        assignee = s.get(User, assignee_id)
        if assignee is None or not assignee.is_active:
            raise HTTPException(status_code=404, detail="No such active account.")

        owners = STAGE_OWNERS.get(stage_key, frozenset())
        if assignee.role not in owners and assignee.role != "hr":
            raise HTTPException(
                status_code=422,
                detail=f"{assignee.full_name or assignee.email} is "
                       f"{assignee.role}, which does not own this stage.",
            )
        label = assignee.full_name or assignee.email
        assignee_identity = Identity(
            user_id=assignee.id, email=assignee.email, role=assignee.role,
            employee_id=assignee.employee_id, full_name=assignee.full_name,
            must_change_password=False,
        )

    # Holding the right role is not enough: a manager only sees their own
    # reports' cases, so assigning one a case outside their line creates work
    # that shows on nobody's queue -- invisible to them, and not owned by the
    # person who can see it. Smart Routing did exactly this on case-007, giving
    # Lokesh Ranjan's sign-off to Anjali Desai when he reports to Ritesh Sharma.
    if not can_see_case(assignee_identity, case.meta):
        raise HTTPException(
            status_code=422,
            detail=f"{label} cannot see {case.meta.get('case_id')}, so the stage "
                   "would sit on nobody's queue. Knowledge Transfer and Manager "
                   "Sign-off belong to the manager this person reports to.",
        )

    try:
        return stage_service.assign_owner(
            case_id=str(case.meta.get("case_id")),
            stage_key=stage_key,
            assignee_user_id=assignee_id,
            assignee_label=label,
            actor=user,
            note=str(payload.get("note") or ""),
        )
    except stage_service.StageClosedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except stage_service.TransitionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/me/case/issued")
def my_issued_documents(user: CurrentUser) -> dict[str, Any]:
    """What the company owes this person, and whether it can be handed over."""
    case = _my_case(user)
    return issued_documents.readiness(str(case.meta.get("case_id")))


@router.get("/me/case/issued/{document_id}")
def download_my_issued_document(document_id: str, user: CurrentUser) -> Response:
    """One generated leaving document, as a PDF.

    Scoped through the session like every other /me route, so the document id
    alone grants nothing -- these name a person and state their dates.
    """
    case = _my_case(user)
    return _issued_pdf(str(case.meta.get("case_id")), document_id)


@router.get("/cases/{case_id}/issued")
def case_issued_documents(case: ScopedCase, user: StaffUser) -> dict[str, Any]:
    return issued_documents.readiness(str(case.meta.get("case_id")))


@router.get("/cases/{case_id}/issued/{document_id}")
def download_case_issued_document(case: ScopedCase, document_id: str,
                                  user: StaffUser) -> Response:
    """The same documents from the staff side -- HR is asked to re-send these
    long after the person has lost their login."""
    return _issued_pdf(str(case.meta.get("case_id")), document_id)


def _issued_pdf(case_id: str, document_id: str) -> Response:
    try:
        pdf, filename = issued_documents.generate(case_id, document_id)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        # `attachment`: this is a record somebody keeps, not something to
        # glance at beside a verdict the way a scan is.
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/employees/managers")
def employee_manager_options(user: HRUser) -> dict[str, Any]:
    """Managers a new employee can be assigned to. Accounts only, by design --
    see `options.manager_options`."""
    from .options import manager_options

    return {"managers": manager_options()}


@router.post("/employees")
def create_employee(user: HRUser, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add a new employee record. If marked "exiting", also creates a starter
    exit case for the orchestrator and, if the email has no account yet, a
    one-time login credential."""
    from .options import manager_options

    payload = dict(payload or {})

    # A manager who is not an account is a manager the work can never reach.
    # `approver_roster` resolves Knowledge Transfer and Manager Sign-off
    # through this address, so a typo here leaves both stages permanently on
    # nobody's queue, with nothing anywhere reporting it.
    wanted = str(payload.get("manager_email") or "").strip().lower()
    if wanted:
        known = {m["value"].lower(): m for m in manager_options()}
        if wanted not in known:
            raise HTTPException(
                status_code=422,
                detail=f"{wanted} is not a manager account here, so Knowledge "
                       "Transfer and Manager Sign-off could never be routed to "
                       "them. Ask IT to create the account under People & "
                       "access first, or leave the manager blank for now.",
            )
        payload["manager_email"] = wanted

    try:
        emp_doc, case_doc, credential = store.add_employee(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "employee": emp_doc.meta,
        "case": case_doc.meta if case_doc else None,
        "credential": credential,
    }


@router.get("/employees")
def list_employees(user: HRUser) -> dict[str, Any]:
    """Everyone on the payroll, with whether they have an exit case yet.

    HR could add an employee and never see them again: there was no list, so
    somebody added as active had no route to being marked as leaving later.
    Resignations happen after the record is created, which is the normal case
    rather than the exception.
    """
    cases = {str(c.meta.get("employee_id")): c.meta for c in store.exit_cases()}
    out = []
    for emp in store.employees():
        meta = emp.meta
        case = cases.get(str(meta.get("id")))
        out.append({
            "id": meta.get("id"),
            "name": meta.get("name"),
            "role": meta.get("role"),
            "department": meta.get("department"),
            "email": meta.get("email"),
            "manager": meta.get("manager"),
            "manager_email": meta.get("manager_email"),
            "status": meta.get("status"),
            "join_date": meta.get("join_date"),
            "tenure_years": meta.get("tenure_years"),
            "case_id": case.get("case_id") if case else None,
            "last_working_day": (case or {}).get("last_working_day"),
        })
    return {"employees": out}


@router.patch("/employees/{employee_id}")
def update_employee_status(employee_id: str, user: HRUser,
                           payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mark somebody as leaving (or not), after the fact.

    Marking them `exiting` opens the exit case, exactly as adding them that way
    does -- the same `add_exit_case`, so a person who resigns six months in
    gets the identical five stages as one added on their last week. Without
    this the status chosen at creation was permanent, and a later resignation
    could only be recorded by adding the person to the system twice.
    """
    payload = payload or {}
    status = str(payload.get("status") or "").strip().lower()
    if status not in ("active", "exiting"):
        raise HTTPException(status_code=422, detail="Status must be active or exiting.")

    emp = store.employee(employee_id)
    if emp is None:
        raise HTTPException(status_code=404, detail=f"Unknown employee {employee_id!r}.")

    created = None
    credential = None
    if status == "exiting":
        # One transaction for the case, the login and the status. This was four
        # -- read "does a case exist", create the case, issue the login, set
        # the status -- with a check-then-act across them, so two HR tabs both
        # read None and both created a case. `add_employee` shares one
        # transaction for exactly this reason; this is the same situation
        # reached by the other door.
        try:
            created, credential = store.start_exit(
                employee_id,
                last_working_day=str(payload.get("last_working_day") or "") or None)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        # The case is deliberately NOT deleted when someone is set back to
        # active. A withdrawn resignation is a real thing and the case is the
        # record that it happened; deleting it would erase a clearance somebody
        # may already have worked. HR closes the stages instead.
        store.set_employee_status(employee_id, status)

    existing = store.exit_case_for_employee(employee_id)

    return {
        "employee_id": employee_id,
        "status": status,
        "case": created.meta if created else (existing.meta if existing else None),
        "case_was_created": created is not None,
        "credential": credential,
    }


def may_read_document(user: Identity, document_name: str) -> bool:
    """Whose paperwork this is to read.

    HR runs document collection and reads all of it. A manager gets the
    handover and nothing else: the rest of a case's uploads are finance and
    statutory records -- bank account confirmation, PF transfer/withdrawal
    form, advance settlement -- and a line manager is not their reader.

    `manager_routes` already filtered its own page down to the handover on
    exactly that reasoning. The route underneath was still `StaffUser`, so the
    filtering was a courtesy on one screen rather than a rule: a manager could
    list every upload on their report's case and pull the bytes of any of them.

    Same answer as the review route uses, so "may I see this" and "may I decide
    on this" cannot drift apart.
    """
    from ..agents.document_collection import who_accepts

    return user.is_hr or who_accepts(document_name) == user.role


@router.get("/cases/{case_id}/documents")
def list_case_documents(case: ScopedCase, user: StaffUser) -> dict[str, Any]:
    """The uploads on a case that this caller may read -- the raw rows behind
    Document Collection's `documents_submitted` summary, so a reviewer can see
    (and override) exactly what the model decided and why.

    Filtered rather than refused, so a manager gets their handover paperwork
    and simply does not see the finance forms.
    """
    case_id = str(case.meta.get("case_id"))
    return {"case_id": case_id,
            "documents": [d for d in store.case_documents(case_id)
                          if may_read_document(user, str(d.get("document_name") or ""))]}


@router.get("/cases/{case_id}/documents/{doc_id}/file")
def case_document_file(case: ScopedCase, doc_id: int, user: StaffUser) -> Response:
    """The stored upload itself -- a scan, a PDF, or an HTML form.

    Two checks, both needed. `ScopedCase` says this case is in the caller's
    scope at all; `may_read_document` says this *document* is theirs to read.
    Scope alone was not enough -- a manager's scope is their own reports, and
    their reports' bank details are on those cases.

    Whether it comes back inline or as a download is `_stored_file`'s call, and
    it turns on the type rather than the reader: the point of a scan or a PDF
    is to look at it next to the verdict, and the point of refusing to render
    an uploaded HTML file is that it would run as a page of this app.
    """
    case_id = str(case.meta.get("case_id"))
    document = next((d for d in store.case_documents(case_id)
                     if d.get("id") == doc_id), None)
    if document is None:
        raise HTTPException(status_code=404,
                            detail="No file is stored for that document.")
    if not may_read_document(user, str(document.get("document_name") or "")):
        raise HTTPException(
            status_code=403,
            detail=f"'{document.get('document_name')}' is not yours to read.")
    return _stored_file(store.document_file(case_id, doc_id))


@router.post("/cases/{case_id}/documents/{doc_id}/review")
def review_case_document(
    case: ScopedCase, doc_id: int, user: StaffUser,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A human decides the verdict on one uploaded document.

    The verdict this writes is exactly what `ExitCase.submitted_documents()`
    reads back, so it is what actually clears (or reinstates) the requirement
    everywhere else in the app -- not a note bolted on beside an unchanged
    status.

    HR was the only role allowed here, and for most documents that is right:
    a no-dues declaration is HR's to accept. The handover is not. Reviewing it
    is the manager's call -- the same reason `ROLE_AGENTS_EXCEPT` takes the KT
    reviewer off HR -- and with no route for them to say so, the model's read
    stood as the decision. `who_accepts` answers "whose call is this" in one
    place, shared with the validator that now declines to make it and with
    `may_read_document`, so seeing a document and deciding on it can never come
    apart.

    A person may not decide on their own exit. `assert_may_transition` carries
    that rule for stage moves ("the person leaving approved their own clearance
    is precisely what an exit audit looks for") and the verdict written here
    feeds the same completion state, so it carries it too -- a self-reporting
    manager could otherwise accept their own handover.
    """
    case_id = str(case.meta.get("case_id"))
    payload = payload or {}

    if user.employee_id and user.employee_id == str(case.meta.get("employee_id") or ""):
        raise HTTPException(
            status_code=403,
            detail="You cannot rule on paperwork for your own exit. Ask HR.")

    if not user.is_hr:
        document = next((d for d in store.case_documents(case_id)
                         if d.get("id") == doc_id), None)
        if document is None:
            raise HTTPException(status_code=404, detail="No such document on this case.")
        if not may_read_document(user, str(document.get("document_name") or "")):
            raise HTTPException(
                status_code=403,
                detail=f"Accepting '{document.get('document_name')}' is HR's "
                       "decision, not yours.",
            )
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
