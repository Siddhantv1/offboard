"""Exit Process Orchestrator: a LangGraph state machine over agents 1-10.

Why this needs a graph and not a straight-line list of API calls:
  - checklist / risk_assessment / smart_routing don't depend on each other, so
    they fan out from `load_case` and join at `compliance`.
  - if compliance comes back blocked, the case must be escalated and
    re-verified -- a cycle (escalate -> compliance -> escalate -> ...) bounded
    by MAX_RETRIES, not a one-shot pipeline.

Every node calls straight into the existing `REGISTRY` agents (checklist,
risk_assessment, smart_routing, compliance, sla_escalation) via their normal
`run(inputs) -> dict` -- none of them needed to change for this.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..agents import REGISTRY
from ..agents._context import parse_days_open
from ..agents.sla_escalation import draft_escalation
from ..data_store import store
from ..llm import LLMError

MAX_RETRIES = 2


class ExitOrchestrationState(TypedDict, total=False):
    case_id: str
    employee_id: str
    case_meta: dict[str, Any]
    checklist: dict[str, Any]
    risk: dict[str, Any]
    routing: dict[str, Any]
    compliance: dict[str, Any]
    escalations: list[dict[str, Any]]
    retries: int
    status: str
    # checklist/risk_assessment/routing run in the same parallel step and each
    # appends to these, so they need a reducer instead of last-write-wins.
    log: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]


def _agent(agent_id: str):
    agent = REGISTRY.get(agent_id)
    if not agent:
        raise RuntimeError(f"Orchestrator requires agent '{agent_id}' to be registered.")
    return agent


def _run_safe(agent_id: str, inputs: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Run a registered agent, turning a gateway failure into an error string
    instead of an exception that would abort the whole graph run."""
    try:
        return _agent(agent_id).run(inputs), None
    except LLMError as exc:
        return {}, f"{agent_id}: {exc}"


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
        "employee_id": employee_id,
        "log": [f"load_case: {state['case_id']} -> employee {employee_id}"],
    }


def run_checklist(state: ExitOrchestrationState) -> dict[str, Any]:
    if state.get("errors"):
        return {}
    result, error = _run_safe("checklist", {"employee_id": state["employee_id"]})
    update: dict[str, Any] = {"checklist": result, "log": ["checklist: done"]}
    if error:
        update["errors"] = [error]
    return update


def run_risk_assessment(state: ExitOrchestrationState) -> dict[str, Any]:
    if state.get("errors"):
        return {}
    result, error = _run_safe("risk_assessment", {"employee_id": state["employee_id"]})
    update: dict[str, Any] = {"risk": result, "log": ["risk_assessment: done"]}
    if error:
        update["errors"] = [error]
    return update


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
        result, error = _run_safe("smart_routing", {"stage": name})
        routing[name] = result
        if error:
            return {"routing": routing, "errors": [error], "log": [f"smart_routing: failed on '{name}'"]}
    return {"routing": routing, "log": [f"smart_routing: {len(routing)} open stage(s)"]}


def verify_compliance(state: ExitOrchestrationState) -> dict[str, Any]:
    if state.get("errors"):
        return {}
    result, error = _run_safe("compliance", {"case_id": state["case_id"]})
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
    retries = state.get("retries", 0) + 1
    drafted: list[dict[str, Any]] = []
    for stage in open_stages:
        try:
            drafted.append(draft_escalation(case_meta, stage, threshold=0))
        except LLMError as exc:
            return {
                "escalations": [*state.get("escalations", []), *drafted],
                "retries": retries,
                "errors": [f"sla_escalation: {exc}"],
                "log": [f"escalate: failed after {len(drafted)}/{len(open_stages)} escalation(s), retry {retries}"],
            }
    return {
        "escalations": [*state.get("escalations", []), *drafted],
        "retries": retries,
        "log": [f"escalate: drafted {len(drafted)} escalation(s), retry {retries}"],
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


def build_graph():
    graph = StateGraph(ExitOrchestrationState)
    graph.add_node("load_case", load_case)
    graph.add_node("checklist", run_checklist)
    graph.add_node("risk_assessment", run_risk_assessment)
    graph.add_node("routing", run_routing)
    graph.add_node("compliance", verify_compliance)
    graph.add_node("escalate", escalate)

    graph.add_edge(START, "load_case")
    graph.add_edge("load_case", "checklist")
    graph.add_edge("load_case", "risk_assessment")
    graph.add_edge("load_case", "routing")
    graph.add_edge("checklist", "compliance")
    graph.add_edge("risk_assessment", "compliance")
    graph.add_edge("routing", "compliance")
    graph.add_conditional_edges("compliance", _after_compliance, {"end": END, "escalate": "escalate"})
    graph.add_edge("escalate", "compliance")

    return graph.compile()


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def run_orchestration(case_id: str) -> ExitOrchestrationState:
    final_state = get_graph().invoke({"case_id": case_id, "retries": 0, "log": [], "errors": []})
    if final_state.get("errors"):
        final_state["status"] = "error"
    else:
        verdict = (final_state.get("compliance", {}).get("verification") or {}).get("final_verdict")
        final_state["status"] = "cleared" if verdict == "cleared" else "blocked"
    return final_state
