"""Exit Process Orchestrator: a LangGraph state machine over the platform's agents.

Why this needs a graph and not a straight-line list of API calls:
  - checklist / risk_assessment / smart_routing / multi-system clearance /
    document collection / deprovisioning *planning* don't depend on each other,
    so they fan out from `load_case` and join at the approval gate.
  - the deprovisioning plan must be approved by a human before anything is
    executed: `approval_gate` calls LangGraph's `interrupt()`, which pauses the
    run (state is checkpointed per thread) until the UI resumes it with a
    decision. Approve -> `deprov_execute` runs the plan through the (mock)
    connectors; reject -> execution is skipped and compliance is told so.
  - if compliance comes back blocked, the case must be escalated and
    re-verified -- a cycle (escalate -> compliance -> ...) bounded by MAX_RETRIES.

Every node calls straight into the existing `REGISTRY` agents via their normal
`run(inputs) -> dict`, or into the small helper functions those agents expose.

    START -> load_case
          -> checklist | risk_assessment | routing | clearance | documents | deprov_plan
          -> approval_gate  (interrupt: waits for approve / reject)
          -> deprov_execute
          -> apply          (writes the run's conclusions onto the case)
          -> compliance -> END (cleared) | escalate -> compliance ... (MAX_RETRIES)
"""
from __future__ import annotations

import json
import operator
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg_pool import ConnectionPool
from sqlalchemy import select

from ..agents import REGISTRY
from ..agents._context import parse_days_open
from ..agents.it_deprovisioning import (approve_plan, execute_plan,
                                        plan_deprovisioning)
from ..agents.sla_escalation import draft_escalation
from ..config import PG_URI
from ..data_store import store
from ..llm import LLMError
from ..systems import access_status

MAX_RETRIES = 2
APPROVAL_NODE = "approval_gate"
PARALLEL_NODES = ("checklist", "risk_assessment", "routing", "clearance", "documents", "deprov_plan")


class ExitOrchestrationState(TypedDict, total=False):
    case_id: str
    employee_id: str
    case_meta: dict[str, Any]
    case_notes: str
    checklist: dict[str, Any]
    risk: dict[str, Any]
    routing: dict[str, Any]
    clearance: dict[str, Any]        # agent 11: cross-system status
    documents: dict[str, Any]        # agent 12: required vs submitted + reminder
    deprov_plan: dict[str, Any]      # agent 14, phase 1
    approval: dict[str, Any]         # human decision from the gate
    deprov_execution: dict[str, Any] # agent 14, phase 2 (or skipped)
    compliance: dict[str, Any]
    escalations: list[dict[str, Any]]
    retries: int
    status: str
    # What the run actually CHANGED about the case, as opposed to what it
    # worked out. Kept separate from `log` on purpose: the log is a trace of
    # nodes that executed, which is true of a run that touched nothing at all.
    # This is the answer to "what is different now", and it is the first thing
    # the result view shows.
    applied: Annotated[list[dict[str, Any]], operator.add]
    # The parallel branch nodes run in the same step and each append to
    # these, so they need a reducer instead of last-write-wins.
    log: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]


class _Orchestrator:
    """The actor recorded against anything the orchestration writes.

    Not a `User` row: the orchestrator has no session and must never borrow the
    identity of whoever pressed the button, because the audit trail would then
    claim a person did something a machine did. `actor_user_id` stays NULL and
    `actor_label` carries the name -- exactly the denormalisation
    `CaseStageEvent` already exists to support.

    The role is `hr` because that is the only role permitted to act across
    every stage type; the alternative is an orchestration that can route IT's
    work but not Finance's.
    """

    user_id = None
    email = "orchestrator@offboard.internal"
    role = "hr"
    employee_id = None
    full_name = "Exit Process Orchestrator"


def _agent(agent_id: str):
    agent = REGISTRY.get(agent_id)
    if not agent:
        raise RuntimeError(f"Orchestrator requires agent '{agent_id}' to be registered.")
    return agent


def _run_safe(agent_id: str, inputs: dict[str, Any],
              thread_id: str | None = None) -> tuple[dict[str, Any], str | None]:
    """Run a registered agent, turning a gateway failure into an error string
    instead of an exception that would abort the whole graph run.

    Goes through `execute()`, not `run()`, so the attempt lands in the
    agent_runs history whether it succeeds or fails.
    """
    try:
        result = _agent(agent_id).execute(inputs, thread_id=thread_id)
    except LLMError as exc:
        return {}, f"{agent_id}: {exc}"
    if isinstance(result, dict) and result.get("error"):
        return result, f"{agent_id}: {result['error']}"
    return result, None


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# -- nodes ---------------------------------------------------------------------
def load_case(state: ExitOrchestrationState) -> dict[str, Any]:
    case = store.exit_case(state["case_id"])
    if not case:
        return {
            "errors": [f"Unknown exit case '{state['case_id']}'."],
            "log": ["load_case: not found"],
        }
    employee_id = str(case.meta.get("employee_id", "")).strip()
    return {
        "case_meta": case.meta,
        "case_notes": case.body.strip(),
        "employee_id": employee_id,
        "log": [f"load_case: {state['case_id']} -> employee {employee_id}"],
    }


def _simple_agent_node(agent_id: str, state_key: str, input_key: str, label: str):
    def node(state: ExitOrchestrationState) -> dict[str, Any]:
        if state.get("errors"):
            return {}
        result, error = _run_safe(agent_id, {input_key: state[input_key]})
        update: dict[str, Any] = {state_key: result, "log": [f"{label}: done"]}
        if error:
            update["errors"] = [error]
        return update
    node.__name__ = f"run_{state_key}"
    return node


run_checklist = _simple_agent_node("checklist", "checklist", "employee_id", "checklist")
run_risk_assessment = _simple_agent_node("risk_assessment", "risk", "employee_id", "risk_assessment")
run_clearance = _simple_agent_node("multi_system_clearance", "clearance", "case_id", "multi_system_clearance")
run_documents = _simple_agent_node("document_collection", "documents", "case_id", "document_collection")


def run_routing(state: ExitOrchestrationState) -> dict[str, Any]:
    if state.get("errors"):
        return {}
    routing: dict[str, Any] = {}
    for stage in state.get("case_meta", {}).get("stages", []):
        if not isinstance(stage, dict):
            continue
        if str(stage.get("status", "")).lower() == "completed":
            continue
        name = stage.get("name")
        if not name or name in routing:
            continue
        # Pass the case, not just the stage name. Without it smart_routing
        # cannot resolve its recommendation to a real account, and the
        # orchestration has a name it cannot act on -- which is most of why
        # this node produced nothing anyone could use.
        result, error = _run_safe("smart_routing",
                                  {"stage": name, "case_id": state["case_id"]})
        routing[name] = result
        if error:
            return {"routing": routing, "errors": [error], "log": [f"smart_routing: failed on '{name}'"]}
    return {"routing": routing, "log": [f"smart_routing: {len(routing)} open stage(s)"]}


def run_deprov_plan(state: ExitOrchestrationState) -> dict[str, Any]:
    if state.get("errors"):
        return {}
    meta = state.get("case_meta", {})
    try:
        plan = plan_deprovisioning(
            state["employee_id"],
            exit_type=str(meta.get("exit_type", "")),
            last_working_day=str(meta.get("last_working_day", "")),
            case_notes=state.get("case_notes", ""),
            case_id=state["case_id"],
        )
    except LLMError as exc:
        return {"deprov_plan": {}, "errors": [f"it_deprovisioning: {exc}"], "log": ["deprov_plan: failed"]}
    if not isinstance(plan, dict) or plan.get("error"):
        err = (plan or {}).get("error", "no plan returned") if isinstance(plan, dict) else "no plan returned"
        return {"deprov_plan": {}, "errors": [f"it_deprovisioning: {err}"], "log": ["deprov_plan: failed"]}
    n = len(plan.get("actions") or [])
    return {"deprov_plan": plan, "log": [f"deprov_plan: {n} action(s) drafted, awaiting approval"]}


def approval_gate(state: ExitOrchestrationState) -> dict[str, Any]:
    """Human-in-the-loop: pause here until someone approves or rejects the
    deprovisioning plan. `interrupt()` raises on the first pass (the run is
    checkpointed and the stream ends with `awaiting_approval`); when the UI
    resumes with `Command(resume=decision)` this node re-runs and `interrupt()`
    returns that decision instead."""
    if state.get("errors"):
        return {}
    plan = state.get("deprov_plan") or {}
    if not plan:
        return {"approval": {"decision": "skipped", "reason": "no plan to approve"},
                "log": ["approval_gate: skipped (no plan)"]}

    decision = interrupt({
        "case_id": state.get("case_id"),
        "employee": plan.get("employee") or state.get("case_meta", {}).get("employee_name"),
        "plan": plan,
    })
    if not isinstance(decision, dict):
        decision = {"decision": str(decision)}
    verdict = str(decision.get("decision", "")).lower()
    if verdict not in ("approve", "reject"):
        verdict = "reject"
    approval = {
        "decision": verdict,
        "by": str(decision.get("by") or "hr-operator"),
        "note": str(decision.get("note") or ""),
        "decided_at": _now(),
        "actions_reviewed": len(plan.get("actions") or []),
    }
    past = {"approve": "approved", "reject": "rejected"}[verdict]
    return {"approval": approval, "log": [f"approval_gate: {past} by {approval['by']}"]}


def deprov_execute(state: ExitOrchestrationState) -> dict[str, Any]:
    if state.get("errors"):
        return {}
    approval = state.get("approval") or {}
    plan = state.get("deprov_plan") or {}
    if approval.get("decision") != "approve":
        why = f"rejected by {approval['by']}" if approval.get("decision") == "reject" else approval.get("reason") or "not approved"
        if approval.get("note"):
            why += f": {approval['note']}"
        return {
            "deprov_execution": {"status": "skipped", "reason": why, "executed": 0},
            "log": [f"deprov_execute: skipped ({why})"],
        }
    plan_id = plan.get("plan_id")
    if not plan_id:
        result = {"status": "error", "reason": "plan has no plan_id", "executed": 0}
        return {
            "deprov_execution": result,
            "errors": ["deprov_execute: plan has no plan_id"],
            "log": ["deprov_execute: failed (no plan_id)"],
        }
    try:
        # The gate above IS the human approval, so record it as one before
        # running. `execute_plan` refuses an unapproved plan -- that refusal is
        # what makes it safe for IT to hold the execute button in the UI, and
        # the orchestrator has to satisfy the same rule rather than bypass it.
        #
        # No approver user id: the gate identifies its approver by name (it can
        # be answered by the API with no session). That leaves the
        # separation-of-duties check inapplicable rather than violated -- the
        # executor here is the orchestrator, never the person who approved.
        approve_plan(plan_id, approved_by=approval.get("by", "hr-operator"),
                     approver_user_id=None, note=approval.get("note", ""))
        result = execute_plan(plan_id, executed_by="orchestrator",
                              note=approval.get("note", ""))
    except PermissionError as exc:
        result = {"status": "error", "reason": str(exc), "executed": 0}
        return {
            "deprov_execution": result,
            "errors": [f"deprov_execute: {exc}"],
            "log": ["deprov_execute: refused"],
        }
    except LookupError as exc:
        result = {"status": "error", "reason": str(exc), "executed": 0}
        return {
            "deprov_execution": result,
            "errors": [f"deprov_execute: {exc}"],
            "log": ["deprov_execute: failed (unknown plan)"],
        }
    except Exception as exc:  # noqa: BLE001 - a connector failure must not crash the orchestration
        result = {"status": "error", "reason": str(exc), "executed": 0}
        return {
            "deprov_execution": result,
            "errors": [f"deprov_execute: {exc}"],
            "log": ["deprov_execute: failed"],
        }
    return {"deprov_execution": result, "log": [f"deprov_execute: {result['executed']} action(s) executed"]}


def apply_decisions(state: ExitOrchestrationState) -> dict[str, Any]:
    """Write the run's conclusions onto the case.

    Everything before this node produces *advice*. Routing worked out the best
    approver for every open stage and threw it away; the escalation node
    drafted chase messages nobody recorded; nothing moved a stage. So a full
    orchestration could complete and leave the exit case byte-for-byte
    identical -- which is exactly what "it doesn't actually do anything" means.

    Two rules bound what it is allowed to change, and both are about not
    overruling a person:

    - **Only unowned stages are assigned.** A stage someone already holds was
      given to them by a human, and a machine reassigning it on a schedule is
      how you lose track of who is accountable.
    - **It never completes a clearance.** Marking work done is a judgement
      about the real world -- was the laptop returned, did the handover
      happen -- and no agent here can see the real world. It moves `pending`
      to `in_progress` where work has demonstrably started, and stops.
    """
    if state.get("errors"):
        return {}

    from ..db import session_scope
    from ..db.models import CaseStage
    from ..policy import STAGE_KEY_BY_NAME
    from ..stages import TransitionError, set_stage_owner, transition

    applied: list[dict[str, Any]] = []
    case_id = state["case_id"]

    # 1. Put the recommended approver on every open stage nobody holds.
    for stage_name, routed in (state.get("routing") or {}).items():
        if not isinstance(routed, dict) or routed.get("error"):
            continue
        chosen = routed.get("chosen")
        stage_key = routed.get("stage_key") or STAGE_KEY_BY_NAME.get(stage_name)
        if not (chosen and chosen.get("user_id") and stage_key):
            continue
        with session_scope() as s:
            stage = s.scalar(select(CaseStage).where(
                CaseStage.case_id == case_id, CaseStage.stage_key == stage_key))
            if stage is None or stage.status == "completed":
                continue
            if stage.owner_user_id is not None:
                continue
            set_stage_owner(stage, int(chosen["user_id"]), str(chosen.get("name") or ""))
        applied.append({
            "kind": "assigned",
            "stage": stage_name,
            "detail": f"{chosen.get('name')} now owns {stage_name}",
            "why": (routed.get("routing") or {}).get("reason") or "",
        })

    # 2. A drafted deprovisioning plan means IT has work in flight, so an IT
    #    Clearance still sitting at `pending` is stale. Starting it is safe --
    #    it is the one transition that asserts nothing about the real world.
    if (state.get("deprov_plan") or {}).get("plan_id"):
        try:
            transition(case_id=case_id, stage_key="it", to_status="in_progress",
                       actor=_Orchestrator(),
                       note="Deprovisioning plan drafted by the orchestrator.")
            applied.append({
                "kind": "started",
                "stage": "IT Clearance",
                "detail": "IT Clearance moved to in progress",
                "why": "a deprovisioning plan was drafted, so IT has work in flight",
            })
        except TransitionError:
            # Already started, already done, or not present on this case. All
            # three are ordinary, and none is a reason to fail the run.
            pass

    log = [f"apply: {len(applied)} change(s) written to the case"] if applied else [
        "apply: nothing needed changing"]
    return {"applied": applied, "log": log}


def _extra_evidence(state: ExitOrchestrationState) -> str:
    """What the other agents found, handed to compliance as evidence."""
    parts: list[str] = []
    consolidated = (state.get("clearance") or {}).get("consolidated")
    if consolidated:
        parts.append("Cross-system clearance status (live tool calls to IT assets, IAM, HRMS, finance):\n"
                     + json.dumps(consolidated, indent=1, default=str))
    doc_status = (state.get("documents") or {}).get("status")
    if doc_status:
        parts.append("Document collection: "
                     f"{doc_status.get('submitted_count')} of {doc_status.get('required_now_count')} "
                     f"currently-due documents submitted. Missing: "
                     + (json.dumps(doc_status.get("missing_by_stage"), default=str) or "none"))
    execution = state.get("deprov_execution")
    if execution:
        if execution.get("status") == "executed":
            parts.append(f"IT deprovisioning: {execution.get('executed')} approved action(s) executed at "
                         f"{execution.get('audit_log', [{}])[0].get('executed_at', 'n/a')} "
                         f"(approved by {execution.get('approved_by')}). Targets: "
                         + ", ".join(str(r.get("target")) for r in execution.get("audit_log") or []))
            # The clearance snapshot above was taken *before* execution; give
            # compliance a fresh identity check so it can see what changed.
            try:
                post = access_status(state["case_id"])
                parts.append("POST-EXECUTION identity check (live, after the approved actions ran): "
                             f"still active = {post.get('still_active') or 'none'}; "
                             f"privileged still active = {post.get('privileged_still_active') or 'none'}. "
                             "Treat this as superseding the earlier clearance snapshot for access revocation.")
            except LookupError:
                pass
        else:
            parts.append(f"IT deprovisioning: NOT performed ({execution.get('reason')}). "
                         "Treat access revocation as unverified/failed.")
    return "\n\n".join(parts)


def verify_compliance(state: ExitOrchestrationState) -> dict[str, Any]:
    if state.get("errors"):
        return {}
    result, error = _run_safe("compliance", {"case_id": state["case_id"],
                                             "extra_evidence": _extra_evidence(state)})
    update: dict[str, Any] = {"compliance": result, "log": ["compliance: checked"]}
    if error:
        update["errors"] = [error]
    return update


def escalate(state: ExitOrchestrationState) -> dict[str, Any]:
    """Draft escalations for this case's own open stages only -- deliberately
    not the whole-dataset `sla_escalation` agent, so one malformed LLM response
    on an unrelated case can't abort this case's run (see draft_escalation)."""
    case_meta = state.get("case_meta", {})
    open_stages = [
        s for s in case_meta.get("stages", [])
        if isinstance(s, dict)
        and str(s.get("status", "")).lower() != "completed"
        and parse_days_open(s) > 0
    ]
    from ..policy import STAGE_KEY_BY_NAME
    from ..stages import TransitionError, record_escalation

    retries = state.get("retries", 0) + 1
    drafted: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []

    def _record(stage_name: str) -> None:
        """Put the chase on the case's own record.

        Drafting alone left no trace, so the next sweep re-drafted the same
        chase and the case could never answer "has anyone pushed on this?".
        A failure here must not fail the run -- the draft is still useful.
        """
        key = STAGE_KEY_BY_NAME.get(stage_name)
        if not key:
            return
        try:
            record_escalation(
                case_id=case_meta.get("case_id"), stage_key=key,
                actor=_Orchestrator(),
                note=f"Escalated by the orchestrator: {stage_name} is past its SLA.")
            applied.append({
                "kind": "escalated",
                "stage": stage_name,
                "detail": f"{stage_name} chased and recorded on the timeline",
                "why": "it is open past its SLA",
            })
        except TransitionError:
            pass

    for stage in open_stages:
        try:
            drafted.append(draft_escalation(case_meta, stage, threshold=0))
        except LLMError as exc:
            return {
                "escalations": [*state.get("escalations", []), *drafted],
                "applied": applied,
                "retries": retries,
                "errors": [f"sla_escalation: {exc}"],
                "log": [f"escalate: failed after {len(drafted)}/{len(open_stages)} escalation(s), retry {retries}"],
            }
        _record(str(stage.get("name") or ""))

    return {
        "escalations": [*state.get("escalations", []), *drafted],
        "applied": applied,
        "retries": retries,
        "log": [f"escalate: drafted and recorded {len(drafted)} escalation(s), retry {retries}"],
    }


def _after_compliance(state: ExitOrchestrationState) -> str:
    if state.get("errors"):
        return "end"
    verdict = (state.get("compliance", {}).get("verification") or {}).get("final_verdict")
    if verdict == "cleared":
        return "end"
    if state.get("retries", 0) >= MAX_RETRIES:
        return "end"
    return "escalate"


# -- graph ---------------------------------------------------------------------
def build_graph():
    graph = StateGraph(ExitOrchestrationState)
    graph.add_node("load_case", load_case)
    graph.add_node("checklist", run_checklist)
    graph.add_node("risk_assessment", run_risk_assessment)
    graph.add_node("routing", run_routing)
    graph.add_node("clearance", run_clearance)
    graph.add_node("documents", run_documents)
    graph.add_node("deprov_plan", run_deprov_plan)
    graph.add_node(APPROVAL_NODE, approval_gate)
    graph.add_node("deprov_execute", deprov_execute)
    graph.add_node("apply", apply_decisions)
    graph.add_node("compliance", verify_compliance)
    graph.add_node("escalate", escalate)

    graph.add_edge(START, "load_case")
    for node in PARALLEL_NODES:
        graph.add_edge("load_case", node)
        graph.add_edge(node, APPROVAL_NODE)
    graph.add_edge(APPROVAL_NODE, "deprov_execute")
    graph.add_edge("deprov_execute", "apply")
    graph.add_edge("apply", "compliance")
    graph.add_conditional_edges("compliance", _after_compliance, {"end": END, "escalate": "escalate"})
    graph.add_edge("escalate", "compliance")

    # The checkpointer is what lets interrupt() pause a run and resume it
    # later on the same thread_id.
    return graph.compile(checkpointer=get_checkpointer())


_graph = None
_checkpointer: PostgresSaver | None = None
_pool: ConnectionPool | None = None


def get_checkpointer() -> PostgresSaver:
    """The durable checkpointer, created on first use.

    A run paused at the approval gate may sit for days. With MemorySaver the
    checkpoint lived in the process and the thread_id lived only in a browser
    tab, so a restart stranded the run: resuming returned 409 and every
    parallel agent's work was lost. There was also no way to ask what was
    waiting.

    Built lazily so importing this module does not require a reachable
    database -- the test suite and the CLI scripts import it freely.
    """
    global _checkpointer, _pool
    if _checkpointer is None:
        # PostgresSaver manages its own transactions and needs autocommit.
        # prepare_threshold=0 keeps it working behind a connection pooler.
        _pool = ConnectionPool(
            conninfo=PG_URI,
            max_size=10,
            open=True,
            kwargs={"autocommit": True, "prepare_threshold": 0},
        )
        _checkpointer = PostgresSaver(_pool)
        _checkpointer.setup()   # idempotent; creates the checkpoint tables
    return _checkpointer


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _initial_state(case_id: str) -> dict[str, Any]:
    return {"case_id": case_id, "retries": 0, "log": [], "errors": []}


def _finalize(state: ExitOrchestrationState) -> ExitOrchestrationState:
    """Stamp the final `status` (error / cleared / blocked) onto a finished state."""
    state.pop("__interrupt__", None)
    if state.get("errors"):
        state["status"] = "error"
    else:
        verdict = (state.get("compliance", {}).get("verification") or {}).get("final_verdict")
        state["status"] = "cleared" if verdict == "cleared" else "blocked"
    return state


def pending_approval(thread_id: str) -> dict[str, Any] | None:
    """If this thread is paused at the approval gate, return the interrupt
    payload (case_id, employee, plan); otherwise None."""
    snapshot = get_graph().get_state(_config(thread_id))
    if APPROVAL_NODE not in (snapshot.next or ()):
        return None
    for task in snapshot.tasks:
        if task.name == APPROVAL_NODE and task.interrupts:
            return dict(task.interrupts[0].value)
    return None


# How many checkpoint rows to scan when looking for paused runs. A run writes
# one per step, so this covers a few hundred recent runs; older paused threads
# would be missed, which is the right trade against an unbounded table scan on
# a route the UI polls.
_CHECKPOINT_SCAN_LIMIT = 2000


def pending_approvals(limit: int = 100) -> list[dict[str, Any]]:
    """Every run currently paused at the approval gate, newest first.

    Without this the only handle on a paused run is the thread_id held in one
    browser tab: close the tab and the run is unreachable, with no way to
    discover that an exit case is sitting waiting on a human. Enumerating
    threads is only possible at all because the checkpoints are in Postgres.
    """
    checkpointer = get_checkpointer()

    # Collect the thread ids BEFORE inspecting any of them. PostgresSaver
    # serialises every operation behind one non-reentrant lock, and list() is
    # a generator that holds it while yielding -- calling pending_approval()
    # inside this loop deadlocks the process against itself.
    latest_ts: dict[str, Any] = {}
    for tup in checkpointer.list(None, limit=_CHECKPOINT_SCAN_LIMIT):
        thread_id = ((tup.config or {}).get("configurable") or {}).get("thread_id")
        if thread_id and thread_id not in latest_ts:
            # Newest first, so the first sighting is the current checkpoint.
            latest_ts[thread_id] = (tup.checkpoint or {}).get("ts")

    out: list[dict[str, Any]] = []
    for thread_id, ts in latest_ts.items():
        # Ask the graph for live state rather than trusting a checkpoint that
        # may since have been resumed.
        payload = pending_approval(thread_id)
        if payload is None:
            continue
        out.append({"thread_id": thread_id, "paused_at": ts, **payload})
        if len(out) >= limit:
            break
    return out


def run_orchestration(
    case_id: str, *, decision: str = "approve", note: str = "", by: str = "api (auto)"
) -> ExitOrchestrationState:
    """Blocking, non-interactive run (POST endpoint). The approval gate is
    answered automatically with `decision` so the call can complete."""
    graph = get_graph()
    config = _config(uuid4().hex)
    state = graph.invoke(_initial_state(case_id), config=config)
    if graph.get_state(config).next:
        state = graph.invoke(
            Command(resume={"decision": decision, "by": by, "note": note or f"auto-{decision}"}),
            config=config,
        )
    return _finalize(dict(state))


def stream_orchestration(case_id: str, *, thread_id: str | None = None,
                         resume: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
    """Run (or resume) the graph, yielding one event per node start/finish.

    Uses LangGraph's `debug` stream (task / task_result per node, so the UI can
    show each parallel branch spinning independently) alongside `values`
    (full state after each superstep, so the last one is the finished run).

    Events:
      {"event": "run_start", "thread_id", "resumed": bool}
      {"event": "node_start", "node", "step"}
      {"event": "node_end",   "node", "step", "error", "log", "retries"}
      {"event": "awaiting_approval", "node", "thread_id", "case_id", "employee", "plan"}
                              -- the run is paused; call again with thread_id + resume
      {"event": "done", "result": <final state incl. status>}
    """
    graph = get_graph()
    thread_id = thread_id or uuid4().hex
    config = _config(thread_id)
    yield {"event": "run_start", "thread_id": thread_id, "case_id": case_id, "resumed": resume is not None}

    graph_input: Any = Command(resume=resume) if resume is not None else _initial_state(case_id)
    latest: dict[str, Any] = {}
    paused = False
    for mode, chunk in graph.stream(graph_input, config=config, stream_mode=["debug", "values"]):
        if mode == "values":
            latest = chunk
            continue
        kind = chunk.get("type")
        payload = chunk.get("payload") or {}
        name = payload.get("name")
        if not name:
            continue
        if kind == "task":
            yield {"event": "node_start", "node": name, "step": chunk.get("step")}
        elif kind == "task_result":
            interrupts = payload.get("interrupts") or []
            if interrupts:
                # The node didn't finish -- it paused. Leave it "running" in the UI.
                value = interrupts[0].get("value") if isinstance(interrupts[0], dict) else getattr(interrupts[0], "value", {})
                paused = True
                yield {"event": "awaiting_approval", "node": name, "thread_id": thread_id, **(value or {})}
                continue
            result = payload.get("result") or {}
            # `result` is the node's state update: a dict, or a list of (key, value)
            # tuples depending on LangGraph version -- normalise to a dict.
            if not isinstance(result, dict):
                try:
                    result = dict(result)
                except (TypeError, ValueError):
                    result = {}
            error = payload.get("error")
            node_errors = result.get("errors") or []
            yield {
                "event": "node_end",
                "node": name,
                "step": chunk.get("step"),
                "error": str(error) if error else (node_errors[0] if node_errors else None),
                "log": list(result.get("log") or []),
                "retries": result.get("retries"),
            }
    if paused or graph.get_state(config).next:
        return
    yield {"event": "done", "result": _finalize(dict(latest))}
