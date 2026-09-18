"""`_compute_metrics` in `dashboard_insights.py` and `workflow_optimizer.py`.

Both are pure functions over the real seeded `store.exit_cases()` /
`store.employees()` / `store.interviews()` -- no LLM, no fixtures to build.
Following this codebase's convention (test_new_agents.py,
test_employee_case_lookup.py), assertions check structural correctness and a
handful of concrete relationships against whatever the seeded dataset holds,
with `pytest.skip()` where a precondition might not be met.
"""
from __future__ import annotations

import pytest

from backend.agents.dashboard_insights import _compute_metrics as dashboard_metrics
from backend.agents.workflow_optimizer import _compute_metrics as workflow_metrics
from backend.data_store import store


# -- dashboard_insights ---------------------------------------------------------
def test_dashboard_metrics_total_active_cases_matches_the_store():
    metrics = dashboard_metrics()
    assert metrics["total_active_cases"] == len(store.exit_cases())


def test_dashboard_metrics_cases_by_department_accounts_for_every_case():
    metrics = dashboard_metrics()
    assert sum(metrics["cases_by_department"].values()) == len(store.exit_cases())


def test_dashboard_metrics_cases_by_status_accounts_for_every_case():
    metrics = dashboard_metrics()
    assert sum(metrics["cases_by_status"].values()) == len(store.exit_cases())


def test_dashboard_metrics_overdue_stages_are_shaped_correctly():
    metrics = dashboard_metrics()
    overdue = metrics["overdue_stages"]
    assert isinstance(overdue, list)
    if not overdue:
        pytest.skip("no seeded stage is currently overdue")
    for row in overdue:
        assert {"case", "stage", "days", "department"} <= set(row)
        assert isinstance(row["days"], int)


def test_dashboard_metrics_sentiment_tally_only_uses_known_buckets():
    metrics = dashboard_metrics()
    tally = metrics["interview_sentiment_tally"]
    assert isinstance(tally, dict)
    assert set(tally) <= {"negative", "positive", "mixed"}


def test_dashboard_metrics_avg_days_open_by_stage_is_non_negative():
    metrics = dashboard_metrics()
    avgs = metrics["avg_days_open_by_stage"]
    assert isinstance(avgs, dict)
    assert all(v >= 0 for v in avgs.values())


# -- workflow_optimizer ----------------------------------------------------------
def test_workflow_metrics_cases_analyzed_matches_the_store():
    metrics = workflow_metrics()
    assert metrics["cases_analyzed"] == len(store.exit_cases())


def test_workflow_metrics_current_workflow_has_the_five_clearance_stages():
    metrics = workflow_metrics()
    assert metrics["current_workflow"] == [
        "IT Clearance", "Finance Clearance", "HR Clearance",
        "Knowledge Transfer", "Manager Sign-off",
    ]


def test_workflow_metrics_stages_ranked_by_avg_days_open_is_sorted_descending():
    metrics = workflow_metrics()
    ranked = metrics["stages_ranked_by_avg_days_open"]
    avgs = [r["avg_days"] for r in ranked]
    assert avgs == sorted(avgs, reverse=True)


def test_workflow_metrics_per_case_has_one_entry_per_case():
    metrics = workflow_metrics()
    assert len(metrics["per_case"]) == len(store.exit_cases())
    for row in metrics["per_case"]:
        assert {"case", "department", "exit_type", "overall_status",
                "overdue_stages", "open_days_total"} <= set(row)


def test_workflow_metrics_owner_load_only_counts_open_stages():
    metrics = workflow_metrics()
    total_open_stages = sum(
        1
        for case in store.exit_cases()
        for stage in (case.meta.get("stages") or [])
        if isinstance(stage, dict) and str(stage.get("status", "")).lower() != "completed"
    )
    assert sum(metrics["owner_open_stage_load"].values()) == total_open_stages


def test_workflow_metrics_sla_breaches_never_exceed_completed_plus_open_counts():
    metrics = workflow_metrics()
    for stage, breaches in metrics["sla_breaches_by_stage"].items():
        completed = metrics["completed_count_by_stage"].get(stage, 0)
        ranked = next(r for r in metrics["stages_ranked_by_avg_days_open"] if r["stage"] == stage)
        assert breaches <= ranked["n"] - completed
