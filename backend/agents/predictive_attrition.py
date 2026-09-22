"""Agent 19 — Predictive Attrition Agent.

Three steps, run in sequence: Collector (pure-code signals from current
headcount, recent exits, and exit-interview sentiment, grouped by
department), Analyzer (one LLM call flags currently-active employees at risk
of leaving), Recommender (a second LLM call drafts retention interventions
for whoever the Analyzer flagged).
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from ..data_store import store

ANALYZE_SYSTEM = """You are a workforce-analytics model. You are given: (1) a \
list of currently active employees with tenure, performance rating and \
criticality, and (2) per-department signals -- recent exit counts and exit \
interview sentiment (from people who already left). Use the department signals \
as context, not as a verdict on any individual -- flag active employees whose \
own profile (short tenure with declining signals, high criticality with low \
recent investment, a department with a negative-sentiment cluster, etc.) puts \
them at real attrition risk. Do not flag someone with no supporting signal just \
to fill the list.

Return JSON:
{
  "at_risk": [
    {"employee_id": str, "name": str, "department": str, "risk_level": "low|medium|high",
     "reasons": [str]}
  ],
  "department_hotspots": [
    {"department": str, "concern": str}
  ]
}"""

RECOMMEND_SYSTEM = """You are an HR retention strategist. For each at-risk \
employee below, draft a concrete, specific intervention (not generic advice) \
that a manager or HR partner could act on this week, tied to the reasons given. \
Also give a short list of org-level recommendations addressing any pattern \
across multiple people.

Return JSON:
{
  "interventions": [
    {"employee_id": str, "name": str, "actions": [str], "priority": "low|medium|high"}
  ],
  "org_level_recommendations": [str]
}"""


def _collect_signals(department: str | None = None) -> dict[str, Any]:
    dept_filter = (department or "").strip().lower() or None

    active_profiles = [
        {
            "employee_id": e.meta.get("id", e.slug),
            "name": e.meta.get("name"),
            "department": e.meta.get("department"),
            "manager": e.meta.get("manager"),
            "tenure_years": e.meta.get("tenure_years"),
            "performance_rating": e.meta.get("performance_rating"),
            "criticality": e.meta.get("criticality"),
        }
        for e in store.employees()
        if str(e.meta.get("status", "")).lower() == "active"
        and (not dept_filter or str(e.meta.get("department", "")).lower() == dept_filter)
    ]

    dept_exits: Counter[str] = Counter()
    for case in store.exit_cases():
        emp = store.employee(str(case.meta.get("employee_id", "")))
        dept = str(emp.meta.get("department")) if emp and emp.meta.get("department") else "unknown"
        if dept_filter and dept.lower() != dept_filter:
            continue
        dept_exits[dept] += 1

    dept_sentiment: dict[str, dict[str, Any]] = {}
    for iv in store.all_interviews():
        dept = str(iv.meta.get("department") or "unknown")
        if dept_filter and dept.lower() != dept_filter:
            continue
        bucket = dept_sentiment.setdefault(dept, {"sentiments": Counter(), "would_not_recommend": 0, "total": 0})
        bucket["sentiments"][str(iv.meta.get("sentiment", "unknown")).lower()] += 1
        bucket["total"] += 1
        if str(iv.meta.get("would_recommend", "")).strip().lower() in ("no", "false"):
            bucket["would_not_recommend"] += 1

    departments = {p["department"] for p in active_profiles if p["department"]}
    departments |= set(dept_exits) | set(dept_sentiment)

    department_summary = []
    for dept in sorted(d for d in departments if d):
        bucket = dept_sentiment.get(dept, {})
        department_summary.append({
            "department": dept,
            "active_headcount": sum(1 for p in active_profiles if p["department"] == dept),
            "recent_exits": dept_exits.get(dept, 0),
            "exit_interviews_recorded": bucket.get("total", 0),
            "negative_sentiment_count": bucket.get("sentiments", Counter()).get("negative", 0),
            "would_not_recommend_count": bucket.get("would_not_recommend", 0),
        })

    return {"active_employees": active_profiles, "department_summary": department_summary}


@register
class PredictiveAttritionAgent(BaseAgent):
    meta = AgentMeta(
        id="predictive_attrition",
        name="Predictive Attrition",
        tagline="Flags flight-risk employees before they resign",
        description=(
            "Collects headcount, recent-exit and exit-interview sentiment signals by "
            "department, flags currently active employees at real attrition risk, and "
            "drafts retention interventions for each."
        ),
        icon="↝",
        category="Analysis",
        fields=[
            Field("department", "Department filter (blank = whole org)", required=False),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        department = (inputs.get("department") or "").strip() or None
        signals = _collect_signals(department)
        if not signals["active_employees"]:
            return {"error": (
                f"No active employees found in department '{department}'." if department
                else "No active employees found."
            )}

        risk_analysis = llm.complete_json(ANALYZE_SYSTEM, json.dumps(signals, indent=2, default=str))
        at_risk = risk_analysis.get("at_risk") or []

        interventions = None
        if at_risk:
            interventions = llm.complete_json(
                RECOMMEND_SYSTEM, json.dumps({"at_risk": at_risk}, indent=2, default=str)
            )

        return {"signals": signals, "risk_analysis": risk_analysis, "interventions": interventions}
