"""Agent 7 — Smart Routing Agent.

Determines the optimal approver for a clearance stage based on availability,
current workload, and delegation rules (handles out-of-office). Uses a demo
approver roster; in production this would query the HRMS / calendar.
"""
from __future__ import annotations

from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register

# Demo roster: approvers per clearance stage with live-ish signals.
APPROVER_ROSTER = {
    "IT Clearance": [
        {"name": "Ravi Menon", "workload": 8, "available": True, "ooo_until": None, "delegate": "Priya Nair"},
        {"name": "Priya Nair", "workload": 3, "available": True, "ooo_until": None, "delegate": None},
    ],
    "Finance Clearance": [
        {"name": "Anita Desai", "workload": 5, "available": False, "ooo_until": "2026-09-15", "delegate": "Karan Shah"},
        {"name": "Karan Shah", "workload": 6, "available": True, "ooo_until": None, "delegate": None},
    ],
    "HR Clearance": [
        {"name": "Meera Iyer", "workload": 4, "available": True, "ooo_until": None, "delegate": "Sam Fernandes"},
        {"name": "Sam Fernandes", "workload": 9, "available": True, "ooo_until": None, "delegate": None},
    ],
    "Manager Sign-off": [
        {"name": "Line Manager", "workload": 7, "available": True, "ooo_until": None, "delegate": "Skip-level Manager"},
    ],
}

SYSTEM = """You are a workflow routing agent. Choose the single best approver for \
a clearance stage. Rules, in priority order:
1. Never route to someone who is out-of-office; use their delegate instead.
2. Prefer the available person with the LOWEST current workload.
3. If a delegation applies, explain the hop.
Return JSON: {"chosen_approver": str, "reason": str, "reroute_from": str|null, "fallback": str|null, "confidence": "high|medium|low"}"""


@register
class SmartRoutingAgent(BaseAgent):
    meta = AgentMeta(
        id="smart_routing",
        name="Smart Routing Agent",
        tagline="Optimal approver, OOO-aware",
        description=(
            "Picks the best approver for a stage using availability, workload, and "
            "delegation rules — automatically rerouting around out-of-office owners."
        ),
        icon="🧭",
        category="Planning",
        fields=[
            Field(
                "stage",
                "Clearance stage",
                type="select",
                options=[{"value": s, "label": s} for s in APPROVER_ROSTER],
            ),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        stage = (inputs.get("stage") or "").strip()
        roster = APPROVER_ROSTER.get(stage)
        if not roster:
            return {"error": f"No approver roster for stage '{stage}'."}

        lines = []
        for a in roster:
            status = "OOO until " + a["ooo_until"] if not a["available"] else "available"
            lines.append(
                f"- {a['name']}: {status}, workload {a['workload']}/10, "
                f"delegate: {a['delegate'] or 'none'}"
            )
        user = f"Stage: {stage}\nCandidate approvers:\n" + "\n".join(lines)
        result = llm.complete_json(SYSTEM, user, temperature=0.2)
        return {"stage": stage, "routing": result, "roster": roster}
