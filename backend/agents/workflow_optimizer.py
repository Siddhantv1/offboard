"""Agent 13 — Exit Workflow Optimizer Agent.

Analyzes the historical exit data (every case, every stage), finds the
bottlenecks in code -- which stages take longest, which departments and owners
delay, how often the SLA is breached, which stages queue behind others -- and
has the LLM propose concrete workflow reconfigurations grounded in those
numbers. Dataset-wide, so it is not part of the per-case orchestrator.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from ._context import parse_days_open
from ..data_store import store

SLA_DAYS = 5

# The workflow as it stands today. Named here once so the prompt below and the
# "before" side of the UI's before/after diagram can't drift apart.
CURRENT_WORKFLOW = ["IT Clearance", "Finance Clearance", "HR Clearance",
                    "Knowledge Transfer", "Manager Sign-off"]

SYSTEM = """You are a process-improvement consultant redesigning an employee \
offboarding workflow. You are given metrics computed from the company's exit \
cases and the current workflow (five sequential-by-convention stages, each with \
a 5-business-day SLA). Identify the real bottlenecks and propose specific, \
implementable reconfigurations: stages to run in parallel, ownership changes, \
SLA changes, automation candidates, and policy tweaks. Ground every claim in the \
metrics; quantify expected gains where the data allows.

Return JSON:
{
  "headline": str,
  "bottlenecks": [{"where": str, "evidence": str, "severity": "high|medium|low"}],
  "reconfigurations": [
    {"change": str, "rationale": str, "expected_gain": str, "risk": str, "effort": "low|medium|high"}
  ],
  "proposed_workflow": [{"order": int, "stage": str, "runs_in_parallel_with": [str], "sla_days": int, "owner": str}],
  "quick_wins": [str]
}"""


def _compute_metrics() -> dict[str, Any]:
    cases = store.exit_cases()
    stage_days: dict[str, list[int]] = defaultdict(list)
    stage_breaches: Counter[str] = Counter()
    stage_completed: Counter[str] = Counter()
    dept_days: dict[str, list[int]] = defaultdict(list)
    dept_breaches: Counter[str] = Counter()
    owner_load: Counter[str] = Counter()
    owner_overdue: Counter[str] = Counter()
    exit_type_days: dict[str, list[int]] = defaultdict(list)
    queued_behind_kt = 0
    per_case: list[dict[str, Any]] = []

    for case in cases:
        meta = case.meta
        emp = store.employee(str(meta.get("employee_id", "")))
        dept = (emp.meta.get("department") if emp else None) or "Unknown"
        exit_type = str(meta.get("exit_type", "unknown"))
        open_total = 0
        overdue_stages: list[str] = []
        for stage in meta.get("stages") or []:
            if not isinstance(stage, dict):
                continue
            name = str(stage.get("name", "?"))
            days = parse_days_open(stage)
            status = str(stage.get("status", "")).lower()
            owner = str(stage.get("owner") or "unassigned")
            stage_days[name].append(days)
            dept_days[dept].append(days)
            exit_type_days[exit_type].append(days)
            if status == "completed":
                stage_completed[name] += 1
            if status != "completed":
                owner_load[owner] += 1
                open_total += days
            if status != "completed" and days > SLA_DAYS:
                stage_breaches[name] += 1
                dept_breaches[dept] += 1
                owner_overdue[owner] += 1
                overdue_stages.append(f"{name} ({days}d)")
        body = case.body.lower()
        if "behind kt" in body or "pending kt" in body or "queued behind" in body:
            queued_behind_kt += 1
        per_case.append({
            "case": meta.get("case_id", case.title), "department": dept, "exit_type": exit_type,
            "overall_status": meta.get("overall_status"), "overdue_stages": overdue_stages,
            "open_days_total": open_total,
        })

    def summarise(d: dict[str, list[int]]) -> dict[str, dict[str, float]]:
        return {
            k: {"avg_days": round(sum(v) / len(v), 1), "max_days": max(v), "n": len(v)}
            for k, v in d.items() if v
        }

    ranked = sorted(summarise(stage_days).items(), key=lambda kv: kv[1]["avg_days"], reverse=True)
    return {
        "cases_analyzed": len(cases),
        "sla_days": SLA_DAYS,
        "current_workflow": list(CURRENT_WORKFLOW),
        "stages_ranked_by_avg_days_open": [{"stage": k, **v} for k, v in ranked],
        "sla_breaches_by_stage": dict(stage_breaches),
        "completed_count_by_stage": dict(stage_completed),
        "departments": summarise(dept_days),
        "sla_breaches_by_department": dict(dept_breaches),
        "exit_types": summarise(exit_type_days),
        "owner_open_stage_load": dict(owner_load.most_common()),
        "owner_overdue_stages": dict(owner_overdue.most_common()),
        "cases_with_stages_queued_behind_kt": queued_behind_kt,
        "per_case": per_case,
    }


@register
class WorkflowOptimizerAgent(BaseAgent):
    meta = AgentMeta(
        id="workflow_optimizer",
        name="Exit Workflow Optimizer",
        tagline="Bottlenecks → workflow redesign",
        description=(
            "Mines every exit case for where time is lost — slowest stages, delaying "
            "departments, overloaded owners, stages queued behind KT — and proposes "
            "concrete workflow reconfigurations with expected gains."
        ),
        icon="⚙",
        category="Analysis",
        fields=[
            Field("focus", "Focus (optional)", required=False,
                  placeholder="e.g. reduce IT Clearance time for terminations"),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        metrics = _compute_metrics()
        focus = (inputs.get("focus") or "").strip()
        user = (
            f"Current workflow: {' -> '.join(CURRENT_WORKFLOW)} (each SLA {SLA_DAYS} "
            "business days; in practice IT deprovisioning often waits for KT to "
            "finish).\n\n"
            + (f"Optimization focus: {focus}\n\n" if focus else "")
            + "Historical metrics:\n" + json.dumps(metrics, indent=2)
        )
        proposal = llm.complete_json(SYSTEM, user, temperature=0.3)
        return {"metrics": metrics, "proposal": proposal}
