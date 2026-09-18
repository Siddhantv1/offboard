"""Agents 17-20 (rehire assessment, policy auditor, predictive attrition, and
the end-to-end capstone).

Following this codebase's convention (see test_document_persistence.py and
test_deprovisioning_audit.py): exercise the pure, non-LLM logic and the
`run()` short-circuit paths that return before any gateway call, rather than
mocking `backend.llm`. The seeded dataset is read directly, never mutated, so
these tests need no cleanup fixture.
"""
from __future__ import annotations

import pytest

from backend.agents.document_collection import REQUIRED_DOCUMENTS
from backend.agents.policy_auditor import (DEFAULT_THRESHOLD_DAYS,
                                            PolicyAuditorAgent,
                                            _audit_case, _case_violations)
from backend.agents.predictive_attrition import (PredictiveAttritionAgent,
                                                  _collect_signals)
from backend.agents.rehire_assessment import (RehireAssessmentAgent,
                                               _matching_case,
                                               _matching_interviews)
from backend.agents.end_to_end_automation import EndToEndAutomationAgent
from backend.data_store import store

ALL_COLLECTABLE_DOCS = [d["name"] for d in REQUIRED_DOCUMENTS if not d.get("issued")]

# A case_meta with every due document submitted and every stage completed
# with an owner -- zero violations by construction.
CLEAN_CASE_META = {
    "case_id": "case-test-clean",
    "employee_name": "Clean Case",
    "current_stage": "Manager Sign-off",
    "documents_submitted": list(ALL_COLLECTABLE_DOCS),
    "stages": [
        {"name": "IT Clearance", "status": "completed", "owner": "it-1", "days_open": 2},
        {"name": "Finance Clearance", "status": "completed", "owner": "fin-1", "days_open": 2},
        {"name": "HR Clearance", "status": "completed", "owner": "hr-1", "days_open": 2},
        {"name": "Knowledge Transfer", "status": "completed", "owner": "mgr-1", "days_open": 2},
        {"name": "Manager Sign-off", "status": "completed", "owner": "mgr-2", "days_open": 2},
    ],
}


# -- rehire_assessment: agent 17 ----------------------------------------------
def test_matching_interviews_returns_only_that_employees_interviews():
    interviews = store.interviews()
    if not interviews:
        pytest.skip("no seeded interviews")
    emp_id = str(interviews[0].meta.get("employee_id"))

    found = _matching_interviews(emp_id)

    assert found, "expected at least the seeded interview back"
    assert all(str(iv.meta.get("employee_id")) == emp_id for iv in found)
    expected = [iv for iv in interviews if str(iv.meta.get("employee_id")) == emp_id]
    assert len(found) == len(expected)


def test_matching_interviews_is_empty_for_an_employee_with_none():
    interviewed = {str(iv.meta.get("employee_id")) for iv in store.interviews()}
    candidate = next(
        (e for e in store.employees() if str(e.meta.get("id")) not in interviewed), None
    )
    if candidate is None:
        pytest.skip("every seeded employee has an interview")

    assert _matching_interviews(str(candidate.meta.get("id"))) == []


def test_matching_case_finds_the_right_case():
    cases = store.exit_cases()
    if not cases:
        pytest.skip("no seeded exit cases")
    emp_id = str(cases[0].meta.get("employee_id"))

    found = _matching_case(emp_id)

    assert found is not None
    assert str(found.meta.get("employee_id")) == emp_id


def test_matching_case_is_none_for_an_employee_with_no_case():
    exiting = {str(c.meta.get("employee_id")) for c in store.exit_cases()}
    candidate = next(
        (e for e in store.employees() if str(e.meta.get("id")) not in exiting), None
    )
    if candidate is None:
        pytest.skip("every seeded employee already has an exit case")

    assert _matching_case(str(candidate.meta.get("id"))) is None


def test_rehire_run_rejects_an_unknown_employee_without_calling_the_llm():
    result = RehireAssessmentAgent().run({"employee_id": "no-such-employee"})
    assert "error" in result


# -- policy_auditor: agent 18 --------------------------------------------------
def test_case_violations_flags_an_sla_breach():
    case_meta = {
        "case_id": "case-test-sla",
        "documents_submitted": ALL_COLLECTABLE_DOCS,
        "stages": [
            {"name": "IT Clearance", "status": "in_progress", "owner": "it-1", "days_open": 10},
        ],
    }
    violations = _case_violations(case_meta, DEFAULT_THRESHOLD_DAYS)
    assert any("SLA breach" in v and "IT Clearance" in v for v in violations)


def test_case_violations_flags_out_of_order_stages():
    case_meta = {
        "case_id": "case-test-order",
        "documents_submitted": ALL_COLLECTABLE_DOCS,
        "stages": [
            {"name": "IT Clearance", "status": "pending", "owner": "", "days_open": 0},
            {"name": "Finance Clearance", "status": "in_progress", "owner": "fin-1", "days_open": 1},
        ],
    }
    violations = _case_violations(case_meta, DEFAULT_THRESHOLD_DAYS)
    assert any("Out-of-order" in v and "Finance Clearance" in v for v in violations)


def test_case_violations_flags_a_completed_stage_with_no_owner():
    case_meta = {
        "case_id": "case-test-owner",
        "documents_submitted": ALL_COLLECTABLE_DOCS,
        "stages": [
            {"name": "IT Clearance", "status": "completed", "owner": "", "days_open": 1},
        ],
    }
    violations = _case_violations(case_meta, DEFAULT_THRESHOLD_DAYS)
    assert any("Missing approval evidence" in v and "IT Clearance" in v for v in violations)


def test_case_violations_flags_a_missing_document():
    case_meta = {
        "case_id": "case-test-docs",
        "documents_submitted": [],
        "stages": [],
    }
    violations = _case_violations(case_meta, DEFAULT_THRESHOLD_DAYS)
    assert any(
        "Missing document" in v and "Resignation letter" in v for v in violations
    ), violations


def test_case_violations_is_empty_for_a_clean_case():
    assert _case_violations(CLEAN_CASE_META, DEFAULT_THRESHOLD_DAYS) == []


def test_audit_case_returns_none_when_there_are_no_violations():
    assert _audit_case(CLEAN_CASE_META, DEFAULT_THRESHOLD_DAYS) is None


def test_audit_case_reports_case_id_and_violations_when_dirty():
    case_meta = {
        "case_id": "case-test-dirty",
        "employee_name": "Dirty Case",
        "current_stage": "IT Clearance",
        "documents_submitted": [],
        "stages": [
            {"name": "IT Clearance", "status": "in_progress", "owner": "it-1", "days_open": 99},
        ],
    }
    report = _audit_case(case_meta, DEFAULT_THRESHOLD_DAYS)
    assert report is not None
    assert report["case_id"] == "case-test-dirty"
    assert report["violations"]


def test_policy_auditor_run_rejects_an_unknown_case_without_calling_the_llm():
    result = PolicyAuditorAgent().run({"case_id": "case-does-not-exist"})
    assert "error" in result


# -- predictive_attrition: agent 19 -------------------------------------------
def test_collect_signals_returns_active_employees_and_department_summary():
    signals = _collect_signals()
    assert isinstance(signals["active_employees"], list)
    assert isinstance(signals["department_summary"], list)
    if not signals["active_employees"]:
        pytest.skip("no active employees seeded")
    assert all(
        str(e.meta.get("status", "")).lower() == "active" for e in store.employees()
        if e.meta.get("id") in {p["employee_id"] for p in signals["active_employees"]}
    )


def test_collect_signals_filters_by_department():
    active = [e for e in store.employees() if str(e.meta.get("status", "")).lower() == "active"]
    dept = next((str(e.meta.get("department")) for e in active if e.meta.get("department")), None)
    if dept is None:
        pytest.skip("no active employee has a department set")

    signals = _collect_signals(dept)

    assert signals["active_employees"], "expected at least the employee used to pick this department"
    assert all(
        str(p["department"]).lower() == dept.lower() for p in signals["active_employees"]
    )
    assert all(
        s["department"].lower() == dept.lower() for s in signals["department_summary"]
    )


def test_collect_signals_is_empty_for_an_unknown_department():
    signals = _collect_signals("No-Such-Department-XYZ")
    assert signals["active_employees"] == []
    assert signals["department_summary"] == []


def test_predictive_attrition_run_rejects_an_unknown_department_without_calling_the_llm():
    result = PredictiveAttritionAgent().run({"department": "No-Such-Department-XYZ"})
    assert "error" in result


# -- end_to_end_automation: agent 20 (capstone) -------------------------------
def test_end_to_end_run_rejects_an_unknown_employee_without_calling_the_llm():
    result = EndToEndAutomationAgent().run({"employee_id": "no-such-employee"})
    assert "error" in result
