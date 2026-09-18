"""HTTP-layer coverage for `main.py`'s routes.

Every route here is exercised through a real `TestClient` request -- status
codes, auth gating, and error shapes -- rather than by calling the underlying
functions directly (those have their own unit tests elsewhere). No test ever
lets an agent run to completion or lets the orchestrator actually invoke the
graph: every case is chosen so the route 401/403/404/422s *before* reaching
the LLM/Portkey call, per this codebase's rule that tests never hit the
network.

`TestClient(app)` is built without the `with` context-manager form on
purpose: entering it would run the startup event, which calls
`rag.ensure_index()` (a real embedding call against the Portkey gateway) and
spawns the OCR warm-up thread. Every route under test is reachable without
that lifespan ever running.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import auth
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import Employee, User
from backend.main import app

HR_EMAIL = "pytest-api-hr@example.com"
HR_PASSWORD = "correct-horse-battery-api"


def _purge_user(email: str) -> None:
    with session_scope() as s:
        for u in s.scalars(select(User).where(User.email == email)):
            s.delete(u)  # sessions cascade


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def hr_account():
    _purge_user(HR_EMAIL)
    with session_scope() as s:
        s.add(User(
            email=HR_EMAIL,
            password_hash=auth.hash_password(HR_PASSWORD),
            role="hr",
            full_name="Pytest API HR",
        ))
    yield HR_EMAIL
    _purge_user(HR_EMAIL)


def _employee_with_case() -> str | None:
    cases = store.exit_cases()
    return cases[0].meta["employee_id"] if cases else None


def _employee_without_case() -> str | None:
    exiting_ids = {c.meta["employee_id"] for c in store.exit_cases()}
    return next((e.meta["id"] for e in store.employees() if e.meta["id"] not in exiting_ids), None)


@pytest.fixture
def employee_account():
    """A throwaway employee account bound to a real seeded employee who has
    an exit case, so `/api/me/case` has something to return."""
    emp_id = _employee_with_case()
    if not emp_id:
        pytest.skip("no seeded employee has an exit case")
    email = f"pytest-api-employee-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-employee"
    with session_scope() as s:
        s.add(User(
            email=email, password_hash=auth.hash_password(password),
            role="employee", employee_id=emp_id, full_name="Pytest API Employee",
        ))
    yield email, password
    _purge_user(email)


@pytest.fixture
def employee_without_case_account():
    emp_id = _employee_without_case()
    if not emp_id:
        pytest.skip("every seeded employee already has an exit case")
    email = f"pytest-api-employee-nocase-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-employee"
    with session_scope() as s:
        s.add(User(
            email=email, password_hash=auth.hash_password(password),
            role="employee", employee_id=emp_id, full_name="Pytest API Employee No Case",
        ))
    yield email, password
    _purge_user(email)


def _login(client: TestClient, email: str, password: str) -> str:
    """Log in (cookies land on the client automatically) and return the CSRF token."""
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return client.cookies.get(auth.CSRF_COOKIE)


def _a_real_case_id() -> str:
    cases = store.exit_cases()
    if not cases:
        pytest.skip("no seeded exit case")
    return cases[0].meta["case_id"]


# -- login / session ------------------------------------------------------------
def test_login_succeeds_with_correct_credentials(client, hr_account):
    resp = client.post("/api/auth/login", json={"email": HR_EMAIL, "password": HR_PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == HR_EMAIL
    assert body["user"]["role"] == "hr"
    assert resp.cookies.get(auth.SESSION_COOKIE) is not None
    assert resp.cookies.get(auth.CSRF_COOKIE) is not None


def test_login_fails_with_wrong_password(client, hr_account):
    resp = client.post("/api/auth/login", json={"email": HR_EMAIL, "password": "wrong"})
    assert resp.status_code == 401


def test_login_fails_with_unknown_email(client):
    resp = client.post("/api/auth/login", json={"email": "no-such-person@example.com", "password": "x"})
    assert resp.status_code == 401


def test_whoami_is_null_when_signed_out(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json() == {"user": None}


def test_whoami_reflects_the_signed_in_user(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == HR_EMAIL


def test_logout_revokes_the_session(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200

    resp = client.get("/api/auth/me")
    assert resp.json() == {"user": None}


# -- health / agents catalog -----------------------------------------------------
def test_health_requires_authentication(client):
    resp = client.get("/api/health")
    assert resp.status_code == 401


def test_health_is_forbidden_for_an_employee(client, employee_account):
    email, password = employee_account
    _login(client, email, password)
    resp = client.get("/api/health")
    assert resp.status_code == 403


def test_health_returns_gateway_info_for_hr(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert {"gateway", "provider", "model", "agents", "employees", "exit_cases", "rag"} <= set(body)


def test_list_agents_is_scoped_to_the_employee_allowlist(client, employee_account):
    email, password = employee_account
    _login(client, email, password)
    resp = client.get("/api/agents")
    assert resp.status_code == 200
    ids = {a["id"] for a in resp.json()["agents"]}
    assert ids == {"faq_chatbot"}


def test_list_agents_returns_the_full_catalog_for_hr(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    resp = client.get("/api/agents")
    assert resp.status_code == 200
    ids = {a["id"] for a in resp.json()["agents"]}
    assert "faq_chatbot" in ids
    assert len(ids) > 1


# -- self-service case ------------------------------------------------------------
def test_my_case_404s_when_the_employee_has_no_case(client, employee_without_case_account):
    email, password = employee_without_case_account
    _login(client, email, password)
    resp = client.get("/api/me/case")
    assert resp.status_code == 404


def test_my_case_returns_the_case_and_document_status(client, employee_account):
    email, password = employee_account
    _login(client, email, password)
    resp = client.get("/api/me/case")
    assert resp.status_code == 200
    body = resp.json()
    assert "case" in body and "documents" in body
    assert body["case"]["employee_id"]


# -- HR case management -----------------------------------------------------------
def test_list_cases_is_forbidden_for_an_employee(client, employee_account):
    email, password = employee_account
    _login(client, email, password)
    resp = client.get("/api/cases")
    assert resp.status_code == 403


def test_list_cases_returns_case_options_for_hr(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    resp = client.get("/api/cases")
    assert resp.status_code == 200
    assert isinstance(resp.json()["cases"], list)


def test_create_employee_rejects_missing_fields(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    resp = client.post("/api/employees", json={"name": "", "role": "Engineer", "department": "IT"})
    assert resp.status_code == 422


def test_create_employee_succeeds(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    email = f"pytest-api-newhire-{uuid.uuid4().hex[:8]}@example.com"
    emp_id = None
    try:
        resp = client.post("/api/employees", json={
            "name": "Pytest Api Newhire", "role": "Engineer", "department": "Platform",
            "email": email, "status": "active",
        })
        assert resp.status_code == 200
        body = resp.json()
        emp_id = body["employee"]["id"]
        assert body["case"] is None
        assert body["credential"] is None
    finally:
        if emp_id:
            with session_scope() as s:
                s.query(Employee).filter(Employee.id == emp_id).delete()


def test_list_case_documents_404s_for_an_unknown_case(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    resp = client.get("/api/cases/no-such-case/documents")
    assert resp.status_code == 404


def test_list_case_documents_returns_the_documents_for_a_real_case(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    case_id = _a_real_case_id()
    resp = client.get(f"/api/cases/{case_id}/documents")
    assert resp.status_code == 200
    assert resp.json()["case_id"] == case_id


def test_review_case_document_rejects_a_bad_verdict(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    case_id = _a_real_case_id()
    resp = client.post(f"/api/cases/{case_id}/documents/999999/review", json={"verdict": "bogus"})
    assert resp.status_code == 422


def test_review_case_document_404s_for_an_unknown_case(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    resp = client.post("/api/cases/no-such-case/documents/1/review", json={"verdict": "accepted"})
    assert resp.status_code == 404


def test_review_case_document_404s_for_an_unknown_document(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    case_id = _a_real_case_id()
    resp = client.post(f"/api/cases/{case_id}/documents/999999/review", json={"verdict": "accepted"})
    assert resp.status_code == 404


# -- agent runs / deprovisioning execute --------------------------------------------
def test_run_agent_404s_for_an_unknown_agent(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    resp = client.post("/api/agents/no-such-agent/run", json={})
    assert resp.status_code == 404


def test_run_agent_is_forbidden_for_an_employee_on_an_hr_only_agent(client, employee_account):
    email, password = employee_account
    _login(client, email, password)
    resp = client.post("/api/agents/dashboard_insights/run", json={})
    assert resp.status_code == 403


def test_execute_deprovisioning_requires_a_plan_id(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    resp = client.post("/api/agents/it_deprovisioning/execute", json={})
    assert resp.status_code == 422


def test_execute_deprovisioning_404s_for_an_unknown_plan(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    resp = client.post("/api/agents/it_deprovisioning/execute", json={"plan_id": "no-such-plan"})
    assert resp.status_code == 404


# -- approvals / orchestration ------------------------------------------------------
def test_list_approvals_is_forbidden_for_an_employee(client, employee_account):
    email, password = employee_account
    _login(client, email, password)
    resp = client.get("/api/approvals")
    assert resp.status_code == 403


def test_list_approvals_returns_a_list_for_hr(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    resp = client.get("/api/approvals")
    assert resp.status_code == 200
    assert isinstance(resp.json()["approvals"], list)


def test_orchestrate_case_404s_for_an_unknown_case(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    resp = client.post("/api/orchestrate/no-such-case", json={})
    assert resp.status_code == 404


def test_orchestrate_case_rejects_a_bad_decision(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    case_id = _a_real_case_id()
    resp = client.post(f"/api/orchestrate/{case_id}", json={"decision": "maybe"})
    assert resp.status_code == 422


def test_orchestrate_stream_404s_for_an_unknown_case(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    resp = client.get("/api/orchestrate/no-such-case/stream")
    assert resp.status_code == 404


def test_orchestrate_stream_rejects_a_partial_resume(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    case_id = _a_real_case_id()
    resp = client.get(f"/api/orchestrate/{case_id}/stream", params={"thread_id": "t1"})
    assert resp.status_code == 422


def test_orchestrate_stream_rejects_a_resume_with_a_bad_csrf_token(client, hr_account):
    _login(client, HR_EMAIL, HR_PASSWORD)
    case_id = _a_real_case_id()
    resp = client.get(f"/api/orchestrate/{case_id}/stream", params={
        "thread_id": "t1", "decision": "approve", "csrf_token": "wrong-token",
    })
    assert resp.status_code == 403


def test_orchestrate_stream_rejects_a_resume_with_a_bad_decision(client, hr_account):
    csrf_token = _login(client, HR_EMAIL, HR_PASSWORD)
    case_id = _a_real_case_id()
    resp = client.get(f"/api/orchestrate/{case_id}/stream", params={
        "thread_id": "t1", "decision": "maybe", "csrf_token": csrf_token,
    })
    assert resp.status_code == 422


# -- SPA catch-all -----------------------------------------------------------------
def test_unknown_api_route_returns_a_json_404(client):
    resp = client.get("/api/no-such-route-at-all")
    assert resp.status_code == 404
    assert "No such endpoint" in resp.json()["detail"]
