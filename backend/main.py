"""FastAPI application.

Serves the unified frontend (static) and a small JSON API:
  GET  /api/agents            -> agent catalog, with select options auto-filled
  POST /api/agents/{id}/run   -> run one agent with a JSON body of inputs
  GET  /api/health            -> gateway/config sanity
  GET  /api/cases             -> exit case options (orchestrator picker)
  POST /api/employees         -> add a new employee record
  POST /api/agents/{id}/upload -> same, as multipart form (file fields, e.g. a scan)
  POST /api/agents/it_deprovisioning/execute -> run an approved deprovisioning plan
  POST /api/orchestrate/{case_id} -> run the LangGraph exit-clearance orchestrator
  GET  /api/orchestrate/{case_id}/stream -> same run, as Server-Sent Events
                                            (one event per node start/finish);
                                            pauses at the approval gate, then
                                            ?thread_id=&decision=approve|reject resumes it

"""
from __future__ import annotations

import dataclasses
import json
import threading
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile  # form parts arrive as this class, not fastapi.UploadFile

from . import ocr
from .agents import REGISTRY
from .agents.it_deprovisioning import execute_plan
from .config import FRONTEND_DIR, PORTKEY
from .data_store import store
from .llm import LLMError
from .orchestration import pending_approval, run_orchestration, stream_orchestration
from .rag import retriever as rag

app = FastAPI(title="Exit Automation Platform", version="1.1")


@app.on_event("startup")
def _build_rag_index() -> None:
    # Build the FAQ vector index on first boot so the first chat is fast.
    try:
        rag.ensure_index()
    except Exception as exc:  # noqa: BLE001 - don't block the app if RAG is degraded
        print(f"[rag] index build skipped: {exc}")
    # Load the local OCR models in the background so the first upload isn't slow.
    threading.Thread(target=ocr.warm_up, name="ocr-warmup", daemon=True).start()


# -- dynamic select options -----------------------------------------------
def _employee_options() -> list[dict[str, str]]:
    opts = []
    for d in store.employees():
        label = f"{d.meta.get('name', d.slug)} · {d.meta.get('role', '')}"
        opts.append({"value": str(d.meta.get("id", d.slug)), "label": label.strip(" ·")})
    return opts


def _case_options() -> list[dict[str, str]]:
    return [
        {"value": str(d.meta.get("case_id", d.slug)),
         "label": f"{d.meta.get('case_id', d.slug)} · {d.meta.get('employee_name', '')}".strip(" ·")}
        for d in store.exit_cases()
    ]


def _kt_options() -> list[dict[str, str]]:
    return [
        {"value": str(d.meta.get("kt_id", d.slug)),
         "label": f"{d.meta.get('kt_id', d.slug)} · emp {d.meta.get('employee_id', '?')}"}
        for d in store.kt_docs()
    ]


def _interview_options() -> list[dict[str, str]]:
    return [
        {"value": str(d.meta.get("interview_id", d.slug)),
         "label": f"{d.meta.get('interview_id', d.slug)} · {d.meta.get('department', '')}".strip(" ·")}
        for d in store.interviews()
    ]


_DYNAMIC = {
    "employee_id": _employee_options,
    "case_id": _case_options,
    "kt_id": _kt_options,
    "interview_id": _interview_options,
}


def _serialize_agent(agent: Any) -> dict[str, Any]:
    meta = dataclasses.asdict(agent.meta)
    for field in meta["fields"]:
        filler = _DYNAMIC.get(field["name"])
        if filler and not field["options"]:
            field["options"] = filler()
    return meta


# -- API -------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "gateway": PORTKEY.base_url,
        "provider": PORTKEY.provider,
        "model": PORTKEY.model,
        "agents": len(REGISTRY),
        "employees": len(store.employees()),
        "exit_cases": len(store.exit_cases()),
        "rag": rag.stats(),
    }


@app.get("/api/agents")
def list_agents() -> dict[str, Any]:
    return {"agents": [_serialize_agent(a) for a in REGISTRY.values()]}


@app.get("/api/cases")
def list_cases() -> dict[str, Any]:
    """Exit case options for the orchestrator picker (frontend-only helper)."""
    return {"cases": _case_options()}


@app.post("/api/employees")
def create_employee(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add a new employee record (writes a Markdown file, refreshes the cache).
    If marked "exiting", also creates a starter exit case for the orchestrator."""
    try:
        emp_doc, case_doc = store.add_employee(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"employee": emp_doc.meta, "case": case_doc.meta if case_doc else None}


@app.post("/api/agents/{agent_id}/run")
def run_agent(agent_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    agent = REGISTRY.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{agent_id}'.")
    try:
        result = agent.run(payload or {})
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"LLM gateway error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"agent": agent_id, "result": result}


@app.post("/api/agents/{agent_id}/upload")
async def run_agent_upload(agent_id: str, request: Request) -> dict[str, Any]:
    """Multipart variant of /run for agents with `file` fields (Document
    Collection's scan upload). File parts arrive in `inputs[name]` as
    {"filename", "content_type", "bytes"}; other parts as plain strings."""
    agent = REGISTRY.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{agent_id}'.")
    form = await request.form()
    inputs: dict[str, Any] = {}
    for key, value in form.multi_items():
        if isinstance(value, UploadFile):
            data = await value.read()
            if data:
                inputs[key] = {"filename": value.filename, "content_type": value.content_type, "bytes": data}
        elif value != "":
            inputs[key] = value
    try:
        result = await run_in_threadpool(agent.run, inputs)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"LLM gateway error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"agent": agent_id, "result": result}


@app.post("/api/agents/it_deprovisioning/execute")
def execute_deprovisioning(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Phase 2 of the IT Deprovisioning agent: run a plan the operator has
    approved on the agent page. No LLM -- exactly the approved actions run."""
    payload = payload or {}
    plan = payload.get("plan")
    if not isinstance(plan, dict) or not plan.get("actions"):
        raise HTTPException(status_code=422, detail="Body must include the approved `plan` with its actions.")
    execution = execute_plan(
        plan,
        approved_by=str(payload.get("approved_by") or "hr-operator (ui)"),
        note=str(payload.get("note") or ""),
    )
    return {"agent": "it_deprovisioning", "execution": execution}


@app.post("/api/orchestrate/{case_id}")
def orchestrate_case(case_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the multi-agent LangGraph orchestrator for one exit case end to end.
    Non-interactive: the human approval gate is answered with `decision`
    from the body (default "approve"). Use the /stream route for a real pause."""
    if not store.exit_case(case_id):
        raise HTTPException(status_code=404, detail=f"Unknown exit case '{case_id}'.")
    decision = str((payload or {}).get("decision") or "approve").lower()
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="decision must be 'approve' or 'reject'.")
    try:
        result = run_orchestration(case_id, decision=decision, note=str((payload or {}).get("note") or ""))
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"LLM gateway error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"case_id": case_id, "orchestration": result}


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@app.get("/api/orchestrate/{case_id}/stream")
def orchestrate_case_stream(
    case_id: str, thread_id: str | None = None, decision: str | None = None, note: str = "",
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
    than POST because the browser's EventSource can only GET.
    """
    if not store.exit_case(case_id):
        raise HTTPException(status_code=404, detail=f"Unknown exit case '{case_id}'.")

    resume: dict[str, Any] | None = None
    if thread_id or decision:
        if not (thread_id and decision):
            raise HTTPException(status_code=422, detail="Resuming needs both thread_id and decision.")
        decision = decision.lower()
        if decision not in ("approve", "reject"):
            raise HTTPException(status_code=422, detail="decision must be 'approve' or 'reject'.")
        pending = pending_approval(thread_id)
        if not pending:
            raise HTTPException(status_code=409, detail="No approval is pending for that run (it may have finished or been restarted).")
        if str(pending.get("case_id")) != case_id:
            raise HTTPException(status_code=409, detail="That run belongs to a different exit case.")
        resume = {"decision": decision, "note": note, "by": "hr-operator (ui)"}

    def events() -> Iterator[str]:
        try:
            for ev in stream_orchestration(case_id, thread_id=thread_id, resume=resume):
                yield _sse(ev.pop("event"), ev)
        except LLMError as exc:
            yield _sse("error", {"message": f"LLM gateway error: {exc}"})
        except Exception as exc:  # noqa: BLE001
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# -- static frontend (mounted last so it doesn't shadow /api) --------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")
