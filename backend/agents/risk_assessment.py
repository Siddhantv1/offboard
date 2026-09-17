"""Agent 8 — Exit Risk Assessment Agent.

Analyzes an exiting employee's profile (tenure, role criticality, project
dependencies) and assigns a risk score with recommended mitigation actions.
"""
from __future__ import annotations

from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from ._context import doc_to_context
from ..data_store import store

SYSTEM = """You are a workforce-risk analyst. Assess the knowledge/continuity \
risk of this employee leaving. Weigh tenure, role criticality, project \
dependencies, access breadth, and how replaceable the role is.

Return JSON:
{
  "risk_score": <int 0..100>,
  "risk_level": "low|medium|high|critical",
  "drivers": [{"factor": str, "impact": "high|medium|low", "detail": str}],
  "mitigations": [{"action": str, "owner": str, "urgency": "immediate|soon|routine"}],
  "single_points_of_failure": [str]
}"""


@register
class RiskAssessmentAgent(BaseAgent):
    meta = AgentMeta(
        id="risk_assessment",
        name="Exit Risk Assessment",
        tagline="Continuity risk scoring",
        description=(
            "Scores the knowledge-continuity risk of a departure from tenure, "
            "criticality, and project dependencies — with mitigation actions."
        ),
        icon="⚠︎",
        category="Analysis",
        fields=[
            Field("employee_id", "Employee", type="select"),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        emp_id = (inputs.get("employee_id") or "").strip()
        doc = store.employee(emp_id)
        if not doc:
            return {"error": f"Unknown employee '{emp_id}'."}
        result = llm.complete_json(
            SYSTEM, f"Employee profile:\n\n{doc_to_context(doc)}"
        )
        return {"employee": doc.title, "assessment": result}
