"""Agent 17 — Intelligent Rehire Assessment Agent.

Evaluates whether a departing employee should be flagged eligible for future
("boomerang") rehire, from performance rating, exit-interview sentiment, and
the nature of their departure (a termination-for-cause should never clear).
"""
from __future__ import annotations

from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from ._context import doc_to_context
from ..data_store import store

SYSTEM = """You are an HR talent-strategy analyst deciding whether a departing \
employee should be flagged eligible for future rehire ("boomerang" hire). Weigh \
performance rating, tenure, exit-interview sentiment and the "would recommend" \
signal, and manager/interview feedback text. A termination for cause, or any \
policy/conduct issue mentioned in the record, disqualifies rehire regardless of \
other positives — say so plainly rather than softening it.

Return JSON:
{
  "rehire_eligible": bool,
  "confidence": "low|medium|high",
  "score": <int 0..100>,
  "positive_signals": [str],
  "concerns": [str],
  "conditions": [str],
  "reasoning": str
}"""


def _matching_interviews(emp_id: str) -> list[Any]:
    return [iv for iv in store.interviews() if str(iv.meta.get("employee_id", "")) == emp_id]


def _matching_case(emp_id: str) -> Any | None:
    for case in store.exit_cases():
        if str(case.meta.get("employee_id", "")) == emp_id:
            return case
    return None


@register
class RehireAssessmentAgent(BaseAgent):
    meta = AgentMeta(
        id="rehire_assessment",
        name="Intelligent Rehire Assessment",
        tagline="Boomerang-hire eligibility",
        description=(
            "Cross-references performance, exit-interview sentiment, and how the "
            "employee left to decide whether they should be flagged eligible for "
            "future rehire."
        ),
        icon="↻",
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

        blocks = [f"EMPLOYEE PROFILE:\n{doc_to_context(doc)}"]

        case = _matching_case(emp_id)
        if case:
            blocks.append(
                f"EXIT CASE (how they are leaving):\n{doc_to_context(case, include_body=True)}"
            )

        interviews = _matching_interviews(emp_id)
        if interviews:
            for iv in interviews:
                blocks.append(
                    f"EXIT INTERVIEW {iv.meta.get('interview_id', iv.slug)} "
                    f"(sentiment: {iv.meta.get('sentiment', 'unknown')}, "
                    f"would_recommend: {iv.meta.get('would_recommend', 'unknown')}):\n"
                    f"{iv.body.strip()}"
                )
        else:
            blocks.append("EXIT INTERVIEW: none on file.")

        result = llm.complete_json(SYSTEM, "\n\n".join(blocks))
        return {
            "employee": doc.title,
            "interviews_considered": len(interviews),
            "assessment": result,
        }
