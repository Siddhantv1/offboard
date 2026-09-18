"""Agent 5 — SLA Escalation Agent.

Autonomously scans all active exit cases for clearance stages that have breached
the SLA (default > 5 days open) and composes a context-rich escalation message
for each: who is blocking, how long, and the impact.
"""
from __future__ import annotations

from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from ._context import SLA_DAYS, parse_days_open
from ..data_store import store

SYSTEM = """You are an operations escalation agent for HR offboarding. Given a \
specific overdue clearance stage, write a concise escalation message to the \
stage owner (and CC their manager) that states: what is blocking, who owns it, \
how many days overdue vs the SLA, and the concrete business impact of further \
delay. Professional, firm, action-oriented.

Return JSON: {"to": str, "cc": str, "subject": str, "body": str, "severity": "high|critical"}"""


def draft_escalation(case_meta: dict[str, Any], stage: dict[str, Any], *, threshold: int = SLA_DAYS) -> dict[str, Any]:
    """Compose one escalation for one overdue stage of one case.

    Split out from `SLAEscalationAgent.run` so the orchestrator can escalate a
    single case's own stages without scanning (and depending on the LLM output
    of) every other case in the dataset.
    """
    emp = case_meta.get("employee_name") or case_meta.get("employee_id") or case_meta.get("case_id")
    days = parse_days_open(stage)
    ctx = (
        f"Employee: {emp}\nCase: {case_meta.get('case_id')}\n"
        f"Last working day: {case_meta.get('last_working_day', 'unknown')}\n"
        f"Overdue stage: {stage.get('name')}\n"
        f"Stage owner: {stage.get('owner', 'unassigned')}\n"
        f"Status: {stage.get('status')}\nDays open: {days} (SLA {threshold})"
    )
    message = llm.complete_json(SYSTEM, f"Compose the escalation.\n\n{ctx}", temperature=0.4)
    return {
        "case_id": case_meta.get("case_id"),
        "employee": emp,
        "stage": stage.get("name"),
        "days_open": days,
        "owner": stage.get("owner"),
        "message": message,
    }


@register
class SLAEscalationAgent(BaseAgent):
    meta = AgentMeta(
        id="sla_escalation",
        name="SLA Escalation Agent",
        tagline="Auto-escalates overdue clearances",
        description=(
            "Monitors every active exit case, flags stages past the SLA, and drafts "
            "a context-rich escalation for each blocker — who, how long, what impact."
        ),
        icon="⧗",
        category="Monitoring",
        fields=[
            Field("threshold_days", "SLA threshold (days)", type="number",
                  required=False, placeholder=str(SLA_DAYS)),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        try:
            threshold = int(inputs.get("threshold_days") or SLA_DAYS)
        except (TypeError, ValueError):
            threshold = SLA_DAYS

        escalations: list[dict[str, Any]] = []
        for case in store.exit_cases():
            for stage in case.meta.get("stages") or []:
                if not isinstance(stage, dict):
                    continue
                status = str(stage.get("status", "")).lower()
                if status == "completed" or parse_days_open(stage) <= threshold:
                    continue
                escalations.append(draft_escalation(case.meta, stage, threshold=threshold))

        return {
            "threshold_days": threshold,
            "breaches_found": len(escalations),
            "escalations": escalations,
        }
