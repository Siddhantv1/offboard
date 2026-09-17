"""Agent 11 — Multi-System Clearance Agent (tool use).

Verifies an exit case's clearances *across systems* -- IT asset management,
identity/access, HRMS, finance -- by letting the model call those systems as
tools, then consolidating what it found into one status per system and an
overall go/no-go. This is real function calling through the Portkey gateway
(the model decides which connectors to query); the connectors themselves are
the mock adapters in `backend/systems.py`.
"""
from __future__ import annotations

from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from ..data_store import store
from ..llm import LLMError
from ..systems import CLEARANCE_HANDLERS, CLEARANCE_TOOLS

SYSTEM = """You are a clearance verification agent for employee offboarding. \
You have tools that query the company's IT asset register, identity provider, \
HRMS and finance system. Call EVERY relevant tool for the given case (all four \
apply to every exit) before answering; never guess a system's state. Then \
consolidate the results.

Answer with JSON only:
{
  "systems": [
    {"system": str, "status": "clear|blocked|pending", "summary": str,
     "open_items": [str], "owner": str}
  ],
  "overall": "clear|blocked",
  "blocking_items": [str],
  "policy_flags": [str],
  "summary": "2-3 sentences for the case manager"
}"""


@register
class MultiSystemClearanceAgent(BaseAgent):
    meta = AgentMeta(
        id="multi_system_clearance",
        name="Multi-System Clearance",
        tagline="Tool-calling across IT, HRMS, finance",
        description=(
            "Queries the IT asset register, identity provider, HRMS and finance "
            "system as tools, then consolidates clearance status across all of them "
            "into one verdict with the open items per system."
        ),
        icon="⧉",
        category="Monitoring",
        fields=[
            Field("case_id", "Exit case", type="select"),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        case_id = (inputs.get("case_id") or "").strip()
        case = store.exit_case(case_id)
        if not case:
            return {"error": f"Unknown exit case '{case_id}'."}

        user = (
            f"Verify clearances across all systems for exit case {case.meta.get('case_id', case_id)} "
            f"({case.meta.get('employee_name', '')}, exit type {case.meta.get('exit_type', 'unknown')}, "
            f"last working day {case.meta.get('last_working_day', 'unknown')})."
        )
        text, trace = llm.complete_with_tools(SYSTEM, user, CLEARANCE_TOOLS, CLEARANCE_HANDLERS)
        try:
            consolidated = llm._parse_json(text)
        except LLMError:
            consolidated = {"summary": text}
        return {
            "case_id": case.meta.get("case_id", case_id),
            "consolidated": consolidated,
            "systems_called": sorted({t["tool"] for t in trace}),
            "tool_trace": trace,
        }
