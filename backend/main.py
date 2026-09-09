"""FastAPI application.

Serves the unified frontend (static) and a small JSON API:
  GET  /api/agents            -> agent catalog, with select options auto-filled
  POST /api/agents/{id}/run   -> run one agent with a JSON body of inputs
  GET  /api/health            -> gateway/config sanity
  GET  /api/cases             -> exit case options (orchestrator picker)
  POST /api/employees         -> add a new employee record
  POST /api/orchestrate/{case_id} -> run the LangGraph exit-clearance orchestrator

Run:  uvicorn backend.main:app --reload   (from the project root)
"""
from __future__ import annotations

import dataclasses
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agents import REGISTRY
from .config import FRONTEND_DIR, PORTKEY
from .data_store import store
from .llm import LLMError
from .orchestration import run_orchestration
from .rag import retriever as rag

app = FastAPI(title="Exit Automation Platform", version="1.0")


@app.on_event("startup")
def _build_rag_index() -> None:
    # Build the FAQ vector index on first boot so the first chat is fast.
    try:
        rag.ensure_index()
    except Exception as exc:  # noqa: BLE001 - don't block the app if RAG is degraded
        print(f"[rag] index build skipped: {exc}")


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


@app.post("/api/orchestrate/{case_id}")
def orchestrate_case(case_id: str) -> dict[str, Any]:
    """Run the multi-agent LangGraph orchestrator for one exit case: fans out to
    checklist/risk/routing, verifies compliance, and escalates + re-verifies
    (bounded retries) if the case is blocked."""
    if not store.exit_case(case_id):
        raise HTTPException(status_code=404, detail=f"Unknown exit case '{case_id}'.")
    try:
        result = run_orchestration(case_id)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"LLM gateway error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"case_id": case_id, "orchestration": result}


# -- static frontend (mounted last so it doesn't shadow /api) --------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")
