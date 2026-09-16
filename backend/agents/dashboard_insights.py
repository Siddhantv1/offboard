"""Agent 10 — Dashboard Insights Agent.

Analyzes the HR offboarding data (cases, stage ageing, department patterns, exit
interview sentiment), computes aggregate metrics in code, then has the LLM write
a weekly narrative report with recommendations grounded in those metrics.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from ._context import parse_days_open
from ..data_store import store

SYSTEM = """You are an HR operations analyst writing the weekly offboarding \
report for leadership. Ground every claim in the metrics provided. Be crisp and \
executive-friendly.

Return JSON:
{
  "headline": str,
  "narrative": "2-4 short paragraphs",
  "bottlenecks": [{"where": str, "detail": str}],
  "recommendations": [{"action": str, "impact": "high|medium|low"}],
  "watch_list": [str]
}"""
SLA_DAYS = 5


def _compute_metrics() -> dict[str, Any]:
    cases = store.exit_cases()
    stage_ages: dict[str, list[int]] = {}
    overdue: list[dict[str, Any]] = []
    dept_counter: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()

    for case in cases:
        emp_id = str(case.meta.get("employee_id", ""))
        emp = store.employee(emp_id)
        dept = (emp.meta.get("department") if emp else None) or "Unknown"
        dept_counter[dept] += 1
        status_counter[str(case.meta.get("overall_status", "unknown"))] += 1
        for stage in case.meta.get("stages") or []:
            if not isinstance(stage, dict):
                continue
            name = str(stage.get("name", "?"))
            days = parse_days_open(stage)
            stage_ages.setdefault(name, []).append(days)
            if str(stage.get("status", "")).lower() != "completed" and days > SLA_DAYS:
                overdue.append({
                    "case": case.meta.get("case_id", case.title),
                    "stage": name, "days": days, "department": dept,
                })

    avg_by_stage = {
        name: round(sum(v) / len(v), 1) for name, v in stage_ages.items() if v
    }

    sentiment = Counter()
    for iv in store.interviews():
        # Cheap heuristic tally; the summarizer agent does the real analysis.
        text = iv.body.lower()
        neg = sum(text.count(w) for w in ("frustrat", "overwork", "poor", "unfair", "burnout", "toxic"))
        pos = sum(text.count(w) for w in ("great", "enjoyed", "supportive", "grateful", "loved"))
        sentiment["negative" if neg > pos else "positive" if pos > neg else "mixed"] += 1

    return {
        "total_active_cases": len(cases),
        "sla_days": SLA_DAYS,
        "cases_by_department": dict(dept_counter),
        "cases_by_status": dict(status_counter),
        "avg_days_open_by_stage": avg_by_stage,
        "overdue_stages": overdue,
        "interview_sentiment_tally": dict(sentiment),
    }


@register
class DashboardInsightsAgent(BaseAgent):
    meta = AgentMeta(
        id="dashboard_insights",
        name="Dashboard Insights",
        tagline="Weekly narrative from the data",
        description=(
            "Analyzes trends, bottlenecks, and department patterns across all exit "
            "cases and interviews, then writes a weekly narrative report."
        ),
        icon="📊",
        category="Analysis",
        fields=[],  # no inputs — analyzes the whole dataset
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        metrics = _compute_metrics()
        import json

        result = llm.complete_json(
            SYSTEM,
            "Write this week's offboarding report from these metrics:\n\n"
            + json.dumps(metrics, indent=2),
            temperature=0.4,
        )
        return {"metrics": metrics, "report": result}
