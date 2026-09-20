"""The agent catalogue, running an agent, and the deprovisioning execute step."""
from __future__ import annotations

import dataclasses
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
# Form parts arrive as this class, not fastapi.UploadFile.
from starlette.datastructures import UploadFile

from .. import auth
from ..agents import REGISTRY
from ..agents.it_deprovisioning import execute_plan
from ..auth import CurrentUser, DeprovUser, HRUser
from ..llm import LLMError
from ..policy import may_run_agent
from .deps import assert_inputs_in_scope
from .options import DYNAMIC

router = APIRouter(prefix="/api/agents", tags=["agents"])
logger = logging.getLogger(__name__)

# Per-request cap on multipart uploads (scans are a few MB at most). Checked
# against Content-Length before request.form() runs -- that call fully parses
# and spools the body first, so catching it there is what actually avoids
# buffering an oversized upload; the later per-file read() cap is
# defense-in-depth for requests with no Content-Length header.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def serialize_agent(agent: Any, user: auth.Identity) -> dict[str, Any]:
    meta = dataclasses.asdict(agent.meta)
    for field in meta["fields"]:
        filler = DYNAMIC.get(field["name"])
        if filler and not field["options"]:
            field["options"] = filler(user)
    return meta


def assert_may_run(user: auth.Identity, agent_id: str, inputs: dict[str, Any]) -> None:
    """Two independent checks, and both are load-bearing.

    `may_run_agent` gates the agent *type*. `assert_inputs_in_scope` gates its
    *arguments* -- without it, a manager cleared to run `risk_assessment` could
    run it against anyone in the company, which would make the careful
    per-case scoping on /api/cases pointless.
    """
    if not may_run_agent(user.role, agent_id):
        raise HTTPException(
            status_code=403, detail="Your account cannot run this agent."
        )
    assert_inputs_in_scope(user, inputs)


@router.get("")
def list_agents(user: CurrentUser) -> dict[str, Any]:
    # Each role sees exactly the agents it is allowed to run, via the same
    # ROLE_AGENTS table run_agent enforces -- one seam, not two rules to keep
    # in sync. The select options inside each agent are scoped to the caller
    # too, so the catalogue never hands out records the caller cannot name.
    visible = [a for aid, a in REGISTRY.items() if may_run_agent(user.role, aid)]
    return {"agents": [serialize_agent(a, user) for a in visible]}


@router.post("/{agent_id}/run")
def run_agent(agent_id: str, user: CurrentUser,
              payload: dict[str, Any] | None = None) -> dict[str, Any]:
    assert_may_run(user, agent_id, payload or {})
    agent = REGISTRY.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{agent_id}'.")
    try:
        result = agent.execute(payload or {})
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"LLM gateway error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent '%s' failed", agent_id)
        raise HTTPException(
            status_code=500, detail="Something went wrong running this agent."
        ) from exc
    return {"agent": agent_id, "result": result}


@router.post("/{agent_id}/upload")
async def run_agent_upload(agent_id: str, user: HRUser, request: Request) -> dict[str, Any]:
    """Multipart variant of /run for agents with `file` fields (Document
    Collection's scan upload). File parts arrive in `inputs[name]` as
    {"filename", "content_type", "bytes"}; other parts as plain strings."""
    agent = REGISTRY.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{agent_id}'.")
    content_length = request.headers.get("content-length")
    if content_length is not None and int(content_length) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Upload is too large (max 10 MB).")
    form = await request.form()
    inputs: dict[str, Any] = {}
    for key, value in form.multi_items():
        if isinstance(value, UploadFile):
            data = await value.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Upload is too large (max 10 MB).")
            if data:
                inputs[key] = {"filename": value.filename,
                               "content_type": value.content_type, "bytes": data}
        elif value != "":
            inputs[key] = value
    # Scope the resolved form inputs as well: a multipart body can name a
    # case_id just as a JSON one can.
    assert_may_run(user, agent_id, inputs)
    try:
        result = await run_in_threadpool(agent.execute, inputs)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"LLM gateway error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent '%s' failed on upload", agent_id)
        raise HTTPException(
            status_code=500, detail="Something went wrong running this agent."
        ) from exc
    return {"agent": agent_id, "result": result}


@router.post("/it_deprovisioning/execute")
def execute_deprovisioning(user: DeprovUser,
                           payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Phase 2 of the IT Deprovisioning agent: run a plan that has been
    approved. No LLM -- exactly the approved actions run.

    Open to IT as well as HR, because revoking access is IT's job. That widening
    is only safe because `execute_plan` now requires a *separate* HR approval
    recorded on the plan first: without it, an IT account could draft a plan
    against anyone, execute it, and have the compliance record say they
    approved it themselves.
    """
    payload = payload or {}
    plan_id = str(payload.get("plan_id") or "")
    if not plan_id:
        raise HTTPException(
            status_code=422, detail="Body must include the approved plan's `plan_id`."
        )
    try:
        execution = execute_plan(
            plan_id,
            executed_by=user.full_name or user.email,
            executor_user_id=user.user_id,
            note=str(payload.get("note") or ""),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"agent": "it_deprovisioning", "execution": execution}
