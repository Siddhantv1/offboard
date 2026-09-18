"""Agent 4 — Exit Interview Summarizer.

Reads an exit-interview submission and produces a structured summary with
sentiment analysis, key themes, and actionable recommendations.
"""
from __future__ import annotations

from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from ._context import doc_to_context, require_interview

SYSTEM = """You are an HR people-analytics specialist. Read an exit interview and \
produce a structured, objective summary.

Return JSON:
{
  "overall_sentiment": "positive|neutral|mixed|negative",
  "sentiment_score": <int -100..100>,
  "summary": "2-3 sentence neutral summary",
  "key_themes": [{"theme": str, "sentiment": "positive|negative|neutral", "evidence": str}],
  "reasons_for_leaving": [str],
  "recommendations": [{"action": str, "owner": str, "priority": "high|medium|low"}],
  "rehire_signal": "strong|neutral|weak"
}"""


@register
class InterviewSummarizerAgent(BaseAgent):
    meta = AgentMeta(
        id="interview_summarizer",
        name="Exit Interview Summarizer",
        tagline="Structured summaries + sentiment",
        description=(
            "Turns free-text exit interviews into a structured summary with "
            "sentiment, key themes, and prioritized recommendations."
        ),
        icon="✎",
        category="Analysis",
        fields=[
            Field("interview_id", "Interview", type="select", required=False,
                  help="Pick a submitted interview…"),
            Field("text", "…or paste interview text", type="textarea", required=False),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        interview_id = (inputs.get("interview_id") or "").strip()
        if interview_id:
            doc = require_interview(interview_id)
            if isinstance(doc, dict):
                return doc
            context = doc_to_context(doc)
        else:
            text = (inputs.get("text") or "").strip()
            if not text:
                return {"error": "Select an interview or paste text."}
            context = text

        result = llm.complete_json(SYSTEM, f"Exit interview:\n\n{context}")
        return {"analysis": result}
