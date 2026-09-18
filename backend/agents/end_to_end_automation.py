"""Agent 20 — End-to-End Exit Automation (capstone).

Composes the platform's other agents into one autonomous, start-to-finish run
for a single employee: resolve or open their exit case, check documents,
recommend approvers, proactively escalate anything already overdue, run full
clearance orchestration (auto-approving the deprovisioning plan since this
agent is meant to run unattended), handle a blocked outcome as an exception,
and draft a final stakeholder summary. No human in the loop by design -- HR
runs the LangGraph orchestrator page directly when they want to review the
deprovisioning plan themselves.
"""
from __future__ import annotations

from typing import Any

from .base import REGISTRY, AgentMeta, BaseAgent, Field, llm, register
from ._context import parse_days_open
from .sla_escalation import draft_escalation
from .smart_routing import APPROVER_ROSTER
from ..data_store import store
from ..llm import LLMError
from ..orchestration import run_orchestration

ESCALATION_THRESHOLD_DAYS = 5

EXCEPTION_SYSTEM = """You are an HR exit-operations lead handling an exception: \
an automated exit clearance run came back BLOCKED. Given the compliance \
verdict, its blockers and its suggested next actions, write ONE email to the \
case owner explaining specifically what is blocking clearance and recommending \
a concrete next step. Reference the actual blockers given -- do not generalise.

Return JSON:
{"to": str, "subject": str, "body": str, "recommended_next_step": "reroute|escalate|wait", "reason": str}"""

SUMMARY_SYSTEM = """You are an HR operations coordinator closing out an \
automated end-to-end exit run. Write ONE plain-business-language summary email \
to HR and the departing employee covering what was done automatically \
(document check, approver routing, escalations, clearance verification, IT \
deprovisioning), the final outcome, and anything still needing a human's \
attention. Under 220 words, no jargon.

Return JSON: {"to": str, "subject": str, "body": str}"""


@register
class EndToEndAutomationAgent(BaseAgent):
    meta = AgentMeta(
        id="end_to_end_automation",
        name="End-to-End Exit Automation",
        tagline="Runs the whole exit, autonomously",
        description=(
            "Resolves or opens the exit case, checks documents, recommends "
            "approvers, escalates anything overdue, runs full clearance "
            "orchestration, and drafts stakeholder communications -- one call, "
            "start to finish."
        ),
        icon="⬢",
        category="Planning",
        fields=[
            Field("employee_id", "Employee", type="select"),
            Field(
                "exit_type", "Exit type", type="select", required=False,
                options=[{"value": "resignation", "label": "Resignation"},
                         {"value": "termination", "label": "Termination"}],
                help="Only used for logging/messaging; new cases always open as a resignation.",
            ),
            Field("last_working_day", "Last working day", required=False, placeholder="YYYY-MM-DD"),
            Field("note", "Context for HR / the orchestrator", type="textarea", required=False),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        emp_id = (inputs.get("employee_id") or "").strip()
        emp = store.employee(emp_id)
        if not emp:
            return {"error": f"Unknown employee '{emp_id}'."}

        requested_exit_type = (inputs.get("exit_type") or "").strip().lower()
        last_working_day = (inputs.get("last_working_day") or "").strip() or None
        note = (inputs.get("note") or "").strip()

        timeline: list[str] = []

        case = next(
            (c for c in store.exit_cases() if str(c.meta.get("employee_id", "")) == emp_id),
            None,
        )
        if case:
            timeline.append(f"Found existing exit case {case.meta.get('case_id')}.")
        else:
            case = store.add_exit_case(emp, last_working_day=last_working_day)
            timeline.append(f"No exit case existed -- opened {case.meta.get('case_id')}.")
            actual_exit_type = str(case.meta.get("exit_type", "")).lower()
            if requested_exit_type and requested_exit_type != actual_exit_type:
                timeline.append(
                    f"Note: '{requested_exit_type}' was requested as the exit type, but new cases "
                    f"always open as '{actual_exit_type}' -- HR should correct this manually if needed."
                )
        case_id = str(case.meta.get("case_id"))

        documents = REGISTRY["document_collection"].execute({"case_id": case_id})
        timeline.append("Checked required vs. submitted documents.")

        refreshed = store.exit_case(case_id)
        case_meta = refreshed.meta if refreshed else case.meta
        stages = [s for s in (case_meta.get("stages") or []) if isinstance(s, dict)]

        routing_recommendations: dict[str, Any] = {}
        for stage in stages:
            if str(stage.get("status", "")).lower() == "completed" or stage.get("owner"):
                continue
            name = stage.get("name")
            if not name:
                continue
            if name in APPROVER_ROSTER:
                routing_recommendations[name] = REGISTRY["smart_routing"].execute({"stage": name})
            else:
                routing_recommendations[name] = {"note": "No approver roster configured for this stage."}
        timeline.append(f"Drafted routing recommendations for {len(routing_recommendations)} unowned stage(s).")

        proactive_escalations: list[dict[str, Any]] = []
        for stage in stages:
            if str(stage.get("status", "")).lower() == "completed":
                continue
            if parse_days_open(stage) > ESCALATION_THRESHOLD_DAYS:
                proactive_escalations.append(
                    draft_escalation(case_meta, stage, threshold=ESCALATION_THRESHOLD_DAYS)
                )
        timeline.append(f"Proactively escalated {len(proactive_escalations)} overdue stage(s).")

        try:
            orchestration = run_orchestration(case_id, decision="approve", note=note)
        except LLMError as exc:
            orchestration = {"status": "error", "errors": [f"orchestration: LLM gateway error: {exc}"]}
        except Exception as exc:  # noqa: BLE001 - one failed run must not crash this agent
            orchestration = {"status": "error", "errors": [f"orchestration: {exc}"]}
        final_status = orchestration.get("status", "error")
        timeline.append(f"Ran full clearance orchestration -- result: {final_status}.")

        exception_handling = None
        if final_status == "blocked":
            verification = (orchestration.get("compliance") or {}).get("verification") or {}
            user = (
                f"Case: {case_id}\nEmployee: {emp.title}\n"
                f"Final verdict: {verification.get('final_verdict')}\n"
                f"Blockers: {verification.get('blockers')}\n"
                f"Compliance-suggested next actions: {verification.get('next_actions')}\n"
                f"Escalations already sent this run: {len(orchestration.get('escalations') or [])}"
            )
            exception_handling = llm.complete_json(EXCEPTION_SYSTEM, user)
            timeline.append("Clearance blocked -- drafted an exception email to the case owner.")

        doc_status = documents.get("status") or {}
        summary_user = (
            f"Case: {case_id}\nEmployee: {emp.title}\n"
            "Timeline:\n" + "\n".join(f"- {t}" for t in timeline) + "\n"
            f"Documents: {doc_status.get('completion_pct', 'n/a')}% of currently-due documents submitted.\n"
            f"Routing recommendations drafted: {len(routing_recommendations)}\n"
            f"Proactive escalations sent: {len(proactive_escalations)}\n"
            f"Orchestration final status: {final_status}"
        )
        stakeholder_summary = llm.complete_json(SUMMARY_SYSTEM, summary_user)
        timeline.append("Drafted final stakeholder summary.")

        return {
            "employee": emp.title,
            "case_id": case_id,
            "timeline": timeline,
            "documents": documents,
            "routing_recommendations": routing_recommendations,
            "proactive_escalations": proactive_escalations,
            "orchestration": orchestration,
            "exception_handling": exception_handling,
            "stakeholder_summary": stakeholder_summary,
            "final_status": final_status,
        }
