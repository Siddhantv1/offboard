"""Agent 20 — End-to-End Exit Automation (capstone).

One unattended, start-to-finish run for a single employee.

It is a *wrapper around the orchestrator*, not a parallel implementation. What
it adds is the things the orchestrator cannot do because it needs a case to
already exist and a human to be present:

  before -- resolve the employee's exit case, or open one if there is none;
            check which documents are in
  middle -- `run_orchestration(...)`, the whole LangGraph graph, with the
            approval gate answered `approve` automatically
  after  -- if the run came back blocked, draft an exception email; then a
            stakeholder summary of the lot

Routing and escalation are NOT done here even though this agent reports both.
They belong to the orchestration, which is what actually writes them; doing
them here as well ran every one of those model calls twice and let the two
passes disagree about which stages were unowned. See the note in `run`.

**The auto-approval is the whole risk of this agent.** The orchestrator page
stops at the deprovisioning plan and waits for a person; this answers that gate
itself, so access is revoked with nobody having read the plan. That is the
point of it -- and the reason HR should use the orchestrator page whenever they
want to see what is about to be revoked.
"""
from __future__ import annotations

from typing import Any

from .base import REGISTRY, AgentMeta, BaseAgent, Field, llm, register
from ._context import require_employee
from ..data_store import store
from ..llm import LLMError
from ..orchestration import run_orchestration

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
        name="Run a Whole Exit",
        tagline="Does every other agent's job, in order, in one go",
        description=(
            "Pick a person and this does the entire offboarding in one run: "
            "finds or opens their exit case, checks which documents are in, "
            "picks who should approve each stage, chases anything past its SLA, "
            "runs the full clearance orchestration, and writes the emails to "
            "send. Use it when you want the whole thing handled and are happy "
            "to read the result afterwards, rather than driving each step. "
            "Everything it does is also available as a separate agent."
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
        emp = require_employee(inputs.get("employee_id"))
        if isinstance(emp, dict):
            return emp
        emp_id = str(emp.meta.get("id", ""))

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

        # Routing and escalation are NOT done here. They used to be, and the
        # orchestration below now does both -- so a single run asked the model
        # to pick an approver twice for every unowned stage and drafted every
        # overdue chase twice, at roughly double the gateway cost.
        #
        # Worse, the two passes disagreed about what "unowned" meant. This one
        # skipped any stage with `owner` *text*; the orchestrator's apply node
        # keys off `owner_user_id`. A seeded stage reading "Sanya Kapoor" with
        # no linked account was owned to one and unowned to the other, so the
        # recommendation this agent reported and the assignment the run
        # actually made could differ on the same stage in the same second.
        #
        # The orchestrator is the one that *writes* the assignment, so it owns
        # the decision. Both keys below are lifted from its state afterwards,
        # which keeps this agent's output shape unchanged.
        try:
            orchestration = run_orchestration(case_id, decision="approve", note=note)
        except LLMError as exc:
            orchestration = {"status": "error", "errors": [f"orchestration: LLM gateway error: {exc}"]}
        except Exception as exc:  # noqa: BLE001 - one failed run must not crash this agent
            orchestration = {"status": "error", "errors": [f"orchestration: {exc}"]}
        final_status = orchestration.get("status", "error")
        timeline.append(f"Ran full clearance orchestration -- result: {final_status}.")

        # Lifted from the run rather than recomputed. `applied` is what it
        # actually changed; the other two are what it worked out on the way.
        routing_recommendations = orchestration.get("routing") or {}
        proactive_escalations = orchestration.get("escalations") or []
        applied = orchestration.get("applied") or []
        timeline.append(
            f"Routed {len(routing_recommendations)} open stage(s) and recorded "
            f"{len(proactive_escalations)} escalation(s)."
        )
        timeline.append(
            f"Applied {len(applied)} change(s) to the case."
            if applied else "Nothing on the case needed changing."
        )

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
            "applied": applied,
            "orchestration": orchestration,
            "exception_handling": exception_handling,
            "stakeholder_summary": stakeholder_summary,
            "final_status": final_status,
        }
