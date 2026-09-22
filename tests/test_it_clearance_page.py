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
from backend.db.models import (AssetReturn, CaseStage, DeprovisioningAction,
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
        # `add_employee` hands out emp ids sequentially and this fixture
        # deletes the row, so the next run reuses the id. Anything keyed on
        # employee_id and left behind is inherited by a later test as if it
        # had logged it itself -- which is exactly how an asset showed as
        # already returned in a test that never touched it.
        s.execute(delete(AssetReturn).where(
            AssetReturn.employee_id == ids["employee_id"]))
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


# -- assets are a record, not an inference ------------------------------------
def test_it_logs_an_asset_back_with_their_name_on_it(client, people, case):
    """There was no way to say a YubiKey had come back. `asset_status` inferred
    it from the IT Clearance stage, so the only way to assert it was to
    complete the clearance -- which was meant to depend on it."""
    _login(client, people["it"])
    r = client.post(f"/api/it/clearance/{case['case_id']}/assets",
                    json={"asset": "MacBook Pro", "state": "returned"})
    assert r.status_code == 200, r.text

    body = client.get(f"/api/it/clearance/{case['case_id']}").json()
    row = next(a for a in body["assets"]["assets"] if a["asset"] == "MacBook Pro")
    assert row["state"] == "returned"
    assert row["logged_by"] == "Pytest IT"
    assert row["logged_at"]


def test_an_asset_nobody_has_logged_is_outstanding_not_returned(client, people, case):
    """Absence of a record is not evidence of return."""
    _login(client, people["it"])
    body = client.get(f"/api/it/clearance/{case['case_id']}").json()
    assert body["assets"]["outstanding"] == ["MacBook Pro"]


def test_completing_the_stage_no_longer_marks_assets_returned(client, people, case):
    """The circularity. The list an engineer read to decide whether to sign off
    was computed from the sign-off, so it could never contradict them."""
    from backend.stages import transition

    _login(client, people["it"])
    transition(case_id=case["case_id"], stage_key="it", to_status="completed",
               actor=auth.Identity(user_id=None, email=people["hr"], role="hr",
                                   employee_id=None, full_name="Pytest HR",
                                   must_change_password=False),
               note="probe: signing off with items outstanding")

    body = client.get(f"/api/it/clearance/{case['case_id']}").json()
    assert body["stage"]["status"] == "completed"
    assert body["assets"]["outstanding"] == ["MacBook Pro"], (
        "completing the stage silently claimed the asset was back")


def test_writing_an_asset_off_needs_a_reason(client, people, case):
    """It is charged to their final settlement. A bare click is not enough."""
    _login(client, people["it"])
    bare = client.post(f"/api/it/clearance/{case['case_id']}/assets",
                       json={"asset": "MacBook Pro", "state": "written_off"})
    assert bare.status_code == 422
    assert "why" in bare.json()["detail"].lower()

    ok = client.post(f"/api/it/clearance/{case['case_id']}/assets",
                     json={"asset": "MacBook Pro", "state": "written_off",
                           "note": "Never returned; charged to settlement."})
    assert ok.status_code == 200


def test_a_written_off_asset_stops_blocking_the_clearance(client, people, case):
    """Otherwise one missing dongle holds a clearance open forever."""
    _login(client, people["it"])
    client.post(f"/api/it/clearance/{case['case_id']}/assets",
                json={"asset": "MacBook Pro", "state": "written_off",
                      "note": "Lost in transit."})
    body = client.get(f"/api/it/clearance/{case['case_id']}").json()
    assert body["assets"]["outstanding"] == []
    assert body["assets"]["written_off"] == ["MacBook Pro"]


# -- revocation happens one step at a time ------------------------------------
def test_a_step_can_be_carried_out_on_its_own(client, people, case):
    """A single button that ran twelve revocations at one instant recorded a
    fiction. AWS goes the hour notice is confirmed; the mailbox converts on the
    last day."""
    pid = _add_plan(case["employee_id"], case["case_id"])
    _login(client, people["hr"])
    client.post(f"/api/deprovisioning/{pid}/approve", json={})

    _login(client, people["it"])
    r = client.post(f"/api/it/clearance/{case['case_id']}/actions",
                    json={"plan_id": pid, "index": 0})
    assert r.status_code == 200, r.text
    assert r.json()["remaining"] == 1

    plan = client.get(f"/api/it/clearance/{case['case_id']}").json()["plan"]
    assert [a["done"] for a in plan["actions"]] == [True, False]
    assert plan["status"] == "approved", "the plan finished before its last step"


def test_the_plan_is_done_only_when_every_step_is(client, people, case):
    pid = _add_plan(case["employee_id"], case["case_id"])
    _login(client, people["hr"])
    client.post(f"/api/deprovisioning/{pid}/approve", json={})

    _login(client, people["it"])
    for index in (0, 1):
        client.post(f"/api/it/clearance/{case['case_id']}/actions",
                    json={"plan_id": pid, "index": index})

    plan = client.get(f"/api/it/clearance/{case['case_id']}").json()["plan"]
    assert plan["status"] == "executed"
    assert plan["done_count"] == plan["action_count"]


def test_an_unauthorised_plan_cannot_be_worked_step_by_step_either(
        client, people, case):
    """The per-step route must not become the way round the authorisation."""
    pid = _add_plan(case["employee_id"], case["case_id"])
    _login(client, people["it"])
    r = client.post(f"/api/it/clearance/{case['case_id']}/actions",
                    json={"plan_id": pid, "index": 0})
    assert r.status_code == 403
    assert "authorised" in r.json()["detail"]


def test_revoking_a_step_is_what_marks_the_account_revoked(client, people, case):
    """Not completing the stage. `access_status` used to treat a completed
    clearance as proof that five accounts had been killed."""
    pid = _add_plan(case["employee_id"], case["case_id"])
    _login(client, people["hr"])
    client.post(f"/api/deprovisioning/{pid}/approve", json={})

    _login(client, people["it"])
    before = client.get(f"/api/it/clearance/{case['case_id']}").json()
    assert "Okta/SSO" in before["access"]["still_active"]

    client.post(f"/api/it/clearance/{case['case_id']}/actions",
                json={"plan_id": pid, "index": 0})
    after = client.get(f"/api/it/clearance/{case['case_id']}").json()
    assert "Okta/SSO" not in after["access"]["still_active"]
