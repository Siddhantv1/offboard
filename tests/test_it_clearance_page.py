"""IT's per-person clearance workspace, and one plan per person.

Two problems, reported together.

IT's work was spread over three pages. The queue said a clearance was open;
clicking it landed on HR's case page, which opens with five stages IT does not
own; and whether the revocation had been authorised lived on a separate page
listing every plan in the company. The one question an engineer has -- what
does this person still have, and may I take it away yet -- was on no screen.

And that separate page listed the same employee four times, because every run
of the deprovisioning agent inserted another pending row. Eight rows existed
for emp-001. Authorising one of four identical drafts is not a decision.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend import auth
from backend.agents.it_deprovisioning import approve_plan
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import (CaseStage, DeprovisioningAction,
                               DeprovisioningPlan, Employee, ExitCase, User)
from backend.main import app

ACTIONS = [
    {"step": 1, "type": "disable_account", "target": "Okta/SSO", "privileged": False},
    {"step": 2, "type": "revoke_access", "target": "AWS IAM", "privileged": True},
]


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def people():
    suffix = uuid.uuid4().hex[:8]
    spec = {"hr": f"pytest-itp-hr-{suffix}@example.com",
            "it": f"pytest-itp-it-{suffix}@example.com",
            "manager": f"pytest-itp-mgr-{suffix}@example.com"}
    with session_scope() as s:
        for role, email in spec.items():
            s.add(User(email=email, password_hash=auth.hash_password("x" * 12),
                       role=role, full_name=f"Pytest {role.upper()}"))
    yield spec
    with session_scope() as s:
        s.execute(delete(User).where(User.email.in_(list(spec.values()))))


@pytest.fixture
def case():
    email = f"pytest-itp-{uuid.uuid4().hex[:8]}@example.com"
    emp, exit_case, _ = store.add_employee({
        "name": "Pytest Clearance Probe", "role": "Engineer",
        "department": "Engineering", "email": email, "status": "exiting",
        "access_systems": ["Okta/SSO", "AWS IAM"],
        "assets_assigned": ["MacBook Pro"],
    })
    ids = {"case_id": str(exit_case.meta["case_id"]),
           "employee_id": str(emp.meta["id"])}
    yield ids
    with session_scope() as s:
        s.execute(delete(DeprovisioningAction).where(
            DeprovisioningAction.employee_id == ids["employee_id"]))
        s.execute(delete(DeprovisioningPlan).where(
            DeprovisioningPlan.employee_id == ids["employee_id"]))
        s.execute(delete(User).where(User.email == email))
        s.execute(delete(ExitCase).where(ExitCase.case_id == ids["case_id"]))
        s.execute(delete(Employee).where(Employee.id == ids["employee_id"]))


def _login(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": "x" * 12})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = client.cookies.get(auth.CSRF_COOKIE)


def _add_plan(employee_id, case_id, status="pending"):
    pid = f"dep-itp-{uuid.uuid4().hex[:8]}"
    with session_scope() as s:
        s.add(DeprovisioningPlan(
            id=pid, employee_id=employee_id, case_id=case_id,
            plan={"plan_id": pid, "employee_id": employee_id, "actions": list(ACTIONS),
                  "summary": "Probe plan."},
            status=status))
    return pid


# -- everything in one request ------------------------------------------------
def test_the_page_gets_stage_access_assets_and_plan_together(client, people, case):
    """One call, because the page is useless in pieces: a revoke button beside a
    stale access list is how something gets revoked twice, or not at all."""
    _add_plan(case["employee_id"], case["case_id"])
    _login(client, people["it"])
    body = client.get(f"/api/it/clearance/{case['case_id']}").json()

    assert body["case"]["employee"] == "Pytest Clearance Probe"
    assert body["stage"]["status"] in ("pending", "in_progress", "completed")
    assert "accounts" in body["access"]
    assert "assets" in body["assets"]
    assert body["plan"]["action_count"] == 2
    assert body["plan"]["privileged_count"] == 1


def test_it_sees_the_authorisation_state_but_cannot_authorise(client, people, case):
    """The separate page existed to tell IT whether it was waiting on HR. That
    answer belongs next to the work; the permission does not move with it."""
    _add_plan(case["employee_id"], case["case_id"])
    _login(client, people["it"])
    plan = client.get(f"/api/it/clearance/{case['case_id']}").json()["plan"]
    assert plan["status"] == "pending"
    assert plan["can_approve"] is False
    assert plan["can_execute"] is False


def test_hr_authorising_flips_it_to_executable_for_it(client, people, case):
    pid = _add_plan(case["employee_id"], case["case_id"])
    _login(client, people["hr"])
    assert client.post(f"/api/deprovisioning/{pid}/approve", json={}).status_code == 200

    _login(client, people["it"])
    plan = client.get(f"/api/it/clearance/{case['case_id']}").json()["plan"]
    assert plan["status"] == "approved"
    assert plan["can_execute"] is True
    assert plan["can_approve"] is False


def test_a_manager_cannot_open_an_it_clearance(client, people, case):
    _login(client, people["manager"])
    assert client.get(f"/api/it/clearance/{case['case_id']}").status_code == 403


def test_a_case_with_no_it_stage_says_so(client, people, case):
    with session_scope() as s:
        s.execute(delete(CaseStage).where(
            CaseStage.case_id == case["case_id"], CaseStage.stage_key == "it"))
    _login(client, people["it"])
    r = client.get(f"/api/it/clearance/{case['case_id']}")
    assert r.status_code == 404
    assert "no IT clearance stage" in r.json()["detail"]


def test_no_plan_yet_is_a_state_not_an_error(client, people, case):
    _login(client, people["it"])
    body = client.get(f"/api/it/clearance/{case['case_id']}").json()
    assert body["plan"] is None


# -- one plan awaiting authorisation ------------------------------------------
def _pending(employee_id):
    with session_scope() as s:
        return s.scalars(select(DeprovisioningPlan).where(
            DeprovisioningPlan.employee_id == employee_id,
            DeprovisioningPlan.status == "pending")).all()


def test_drafting_again_replaces_the_pending_plan(case, monkeypatch):
    """Eight rows existed for emp-001 because each run inserted another. HR
    cannot meaningfully authorise one of four identical drafts."""
    from backend.agents import it_deprovisioning as agent

    monkeypatch.setattr(agent.llm, "complete_json",
                        lambda *a, **k: {"summary": "drafted", "actions": list(ACTIONS)})
    for _ in range(3):
        agent.plan_deprovisioning(case["employee_id"], case_id=case["case_id"])

    assert len(_pending(case["employee_id"])) == 1, "duplicate pending drafts again"


def test_an_executed_plan_is_never_discarded(case, monkeypatch):
    """It is the record of access that was actually revoked."""
    from backend.agents import it_deprovisioning as agent

    pid = _add_plan(case["employee_id"], case["case_id"])
    with session_scope() as s:
        s.get(DeprovisioningPlan, pid).status = "executed"

    monkeypatch.setattr(agent.llm, "complete_json",
                        lambda *a, **k: {"summary": "drafted", "actions": list(ACTIONS)})
    agent.plan_deprovisioning(case["employee_id"], case_id=case["case_id"])

    with session_scope() as s:
        assert s.get(DeprovisioningPlan, pid) is not None
        assert s.get(DeprovisioningPlan, pid).status == "executed"
    assert len(_pending(case["employee_id"])) == 1


def test_an_authorised_plan_is_not_silently_replaced(case, monkeypatch):
    """HR has signed it; redrafting must not quietly drop that decision."""
    from backend.agents import it_deprovisioning as agent

    pid = _add_plan(case["employee_id"], case["case_id"])
    approve_plan(pid, approved_by="Pytest HR", approver_user_id=None)

    monkeypatch.setattr(agent.llm, "complete_json",
                        lambda *a, **k: {"summary": "drafted", "actions": list(ACTIONS)})
    agent.plan_deprovisioning(case["employee_id"], case_id=case["case_id"])

    with session_scope() as s:
        assert s.get(DeprovisioningPlan, pid).status == "approved"
