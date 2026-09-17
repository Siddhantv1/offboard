"""Agent 6 — KT Document Reviewer Agent.

Reviews a Knowledge Transfer document for completeness: checks whether critical
topics are covered for the employee's role, identifies gaps, and suggests
additions.
"""
from __future__ import annotations

from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from ._context import doc_to_context
from ..data_store import store

# Role-appropriate critical topics a good KT doc should cover.
CRITICAL_TOPICS = [
    "System / architecture overview",
    "Access & credentials handover",
    "Deployment / runbook & operations",
    "Pending tickets & in-flight work",
    "Key contacts & stakeholders",
    "Recurring tasks & responsibilities",
    "Known issues & tech debt",
    "Documentation & repo locations",
]

SYSTEM = """You are a senior engineering lead reviewing a departing employee's \
Knowledge Transfer document for completeness. Judge coverage against the list of \
critical topics provided. Be strict — mark a topic covered only if there is real \
substance, not a passing mention.

Return JSON:
{
  "completeness_score": <int 0..100>,
  "coverage": [{"topic": str, "covered": bool, "notes": str}],
  "gaps": [{"topic": str, "risk": "high|medium|low", "why": str}],
  "suggested_additions": [str],
  "verdict": "ready|needs_work|incomplete"
}"""


@register
class KTReviewerAgent(BaseAgent):
    meta = AgentMeta(
        id="kt_reviewer",
        name="KT Document Reviewer",
        tagline="Finds gaps in handover docs",
        description=(
            "Reviews a Knowledge Transfer document against role-critical topics, "
            "scores completeness, and flags gaps with suggested additions."
        ),
        icon="◎",
        category="Analysis",
        fields=[
            Field("kt_id", "KT document", type="select"),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        kt_id = (inputs.get("kt_id") or "").strip()
        doc = store.kt_doc(kt_id)
        if not doc:
            return {"error": f"Unknown KT document '{kt_id}'."}

        # Enrich with the author's role if we can resolve it.
        role_hint = ""
        emp_id = str(doc.meta.get("employee_id", "")).strip()
        if emp_id:
            emp = store.employee(emp_id)
            if emp:
                role_hint = f"Author role: {emp.meta.get('role', 'unknown')} " \
                            f"({emp.meta.get('department', '')})\n"

        topics = "\n".join(f"- {t}" for t in CRITICAL_TOPICS)
        user = (
            f"{role_hint}Critical topics to check:\n{topics}\n\n"
            f"KT document:\n\n{doc_to_context(doc)}"
        )
        result = llm.complete_json(SYSTEM, user)
        return {"kt_id": doc.title, "review": result}
