"""The deprovisioning queue: seeing plans, and the two halves of acting on one.

These exist because the page was unusable and no test noticed. Nothing listed
`deprovisioning_plans` at all -- the UI read the orchestrator's paused-run
endpoint instead -- so seven drafted plans sat in the database unreachable by
anyone. Every test below would have failed against that.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend import auth
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import DeprovisioningAction, DeprovisioningPlan, User
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
    spec = {"hr": f"pytest-dq-hr-{suffix}@example.com",
            "it": f"pytest-dq-it-{suffix}@example.com",
            "manager": f"pytest-dq-mgr-{suffix}@example.com"}
    with session_scope() as s:
        for role, email in spec.items():
            s.add(User(email=email, password_hash=auth.hash_password("x" * 12),
                       role=role, full_name=f"Pytest {role.upper()}"))
    yield spec
    with session_scope() as s:
        s.execute(delete(User).where(User.email.in_(list(spec.values()))))


@pytest.fixture
def plan():
    cases = store.exit_cases()
    if not cases:
        pytest.skip("no seeded exit case")
    case = cases[0]
    pid = f"dep-q-{uuid.uuid4().hex[:8]}"
    employee_id = str(case.meta["employee_id"])
    with session_scope() as s:
        s.add(DeprovisioningPlan(
            id=pid, employee_id=employee_id, case_id=str(case.meta["case_id"]),
            plan={"employee_id": employee_id, "plan_id": pid,
                  "employee": case.meta.get("employee_name"),
                  "summary": "Test plan.", "actions": list(ACTIONS)},
            status="pending"))
    yield pid, employee_id
    with session_scope() as s:
        row = s.get(DeprovisioningPlan, pid)
        if row is not None:
            s.delete(row)
        s.execute(delete(DeprovisioningAction).where(
            DeprovisioningAction.employee_id == employee_id))


def _login(client, email):
    r = client.post("/api/auth/login",
                    json={"email": email, "password": "x" * 12})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = client.cookies.get(auth.CSRF_COOKIE)


def _find(client, pid):
    body = client.get("/api/deprovisioning/plans").json()
    return next((p for p in body["plans"] if p["plan_id"] == pid), None)


def test_a_drafted_plan_is_visible_to_hr_and_it(client, people, plan):
    """The whole bug: drafted plans existed and nothing could show them."""
    pid, _ = plan
    for role in ("hr", "it"):
        _login(client, people[role])
        row = _find(client, pid)
        assert row is not None, f"{role} cannot see a drafted plan"
        assert row["action_count"] == len(ACTIONS)
        assert row["privileged_count"] == 1


def test_it_can_see_what_hr_has_not_yet_authorised(client, people, plan):
    """IT's most common question is whether they are being waited on."""
    pid, _ = plan
    _login(client, people["it"])
    row = _find(client, pid)
    assert row["status"] == "pending"
    assert row["can_approve"] is False, "authorising is HR's call"
    assert row["can_execute"] is False, "nothing to run until it is authorised"


def test_hr_is_offered_the_authorise_action(client, people, plan):
    pid, _ = plan
    _login(client, people["hr"])
    assert _find(client, pid)["can_approve"] is True


def test_the_full_two_person_flow(client, people, plan):
    pid, _ = plan

    _login(client, people["it"])
    blocked = client.post("/api/agents/it_deprovisioning/execute", json={"plan_id": pid})
    assert blocked.status_code == 403
    assert "not been approved" in blocked.json()["detail"]

    _login(client, people["hr"])
    assert client.post(f"/api/deprovisioning/{pid}/approve",
                       json={"note": "checked"}).status_code == 200
    assert _find(client, pid)["status"] == "approved"

    _login(client, people["it"])
    row = _find(client, pid)
    assert row["can_execute"] is True
    done = client.post("/api/agents/it_deprovisioning/execute", json={"plan_id": pid})
    assert done.status_code == 200
    execution = done.json()["execution"]
    assert execution["status"] == "executed"
    # Two different people, recorded separately.
    assert execution["approved_by"] != execution["executed_by"]


def test_a_manager_cannot_reach_the_queue(client, people, plan):
    _login(client, people["manager"])
    assert client.get("/api/deprovisioning/plans").status_code == 403


def test_an_unknown_status_filter_is_refused(client, people):
    _login(client, people["hr"])
    assert client.get("/api/deprovisioning/plans?status=nonsense").status_code == 422
    assert client.get("/api/deprovisioning/plans?status=pending").status_code == 200


# -- routing a stage onto someone's queue -------------------------------------
@pytest.fixture
def scratch_case():
    """A throwaway case, because assigning rewrites `CaseStage.owner`.

    An earlier version of these tests assigned a seeded case and left
    "Pytest IT" on case-001, which `test_store_contract` compares against the
    golden snapshot -- so it went red on every later run. Anything that writes
    to a stage builds its own case.
    """
    email = f"pytest-assign-{uuid.uuid4().hex[:8]}@example.com"
    emp, case, _ = store.add_employee({
        "name": "Pytest Assign Probe", "role": "Engineer",
        "department": "Engineering", "email": email, "status": "exiting",
    })
    yield str(case.meta["case_id"])
    from backend.db.models import Employee, ExitCase
    with session_scope() as s:
        s.execute(delete(User).where(User.email == email))
        s.execute(delete(ExitCase).where(ExitCase.case_id == case.meta["case_id"]))
        s.execute(delete(Employee).where(Employee.id == emp.meta["id"]))


def test_assigning_a_stage_puts_it_on_that_person_s_queue(client, people, scratch_case):
    """The whole point of Smart Routing.

    The agent recommended an approver and there was no way to act on it, so the
    advice went nowhere. Assigning writes `owner_user_id`, which is exactly what
    "My queue" filters on -- this asserts the two actually meet.
    """
    case_id = scratch_case

    with session_scope() as s:
        it_user = s.scalar(select(User).where(User.email == people["it"]))
        it_id, it_name = it_user.id, it_user.full_name

    _login(client, people["it"])
    before = client.get("/api/queue?scope=mine").json()["stages"]
    assert not any(r["case_id"] == case_id and r["stage_key"] == "it" for r in before)

    _login(client, people["hr"])
    resp = client.post(f"/api/cases/{case_id}/stages/it/assign", json={"user_id": it_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"] == it_name

    _login(client, people["it"])
    after = client.get("/api/queue?scope=mine").json()["stages"]
    mine = [r for r in after if r["case_id"] == case_id and r["stage_key"] == "it"]
    assert mine, "the assigned stage did not reach the assignee's own queue"
    assert mine[0]["owner"] == it_name
    assert mine[0]["claimed"] is True


def test_a_stage_cannot_be_routed_to_someone_whose_role_does_not_own_it(
        client, people, scratch_case):
    """A rota error, not a judgement call, so it is refused rather than warned."""
    case_id = scratch_case
    with session_scope() as s:
        mgr = s.scalar(select(User).where(User.email == people["manager"]))
        mgr_id = mgr.id

    _login(client, people["hr"])
    resp = client.post(f"/api/cases/{case_id}/stages/it/assign", json={"user_id": mgr_id})
    assert resp.status_code == 422
    assert "does not own this stage" in resp.json()["detail"]
