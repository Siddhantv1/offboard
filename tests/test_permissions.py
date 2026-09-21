"""The role x route matrix, and the structural guarantee behind it.

The single most valuable test here is `test_every_case_route_resolves_through_
scoped_case`. Everything else checks that today's routes behave; that one
checks that tomorrow's will, by making "forgot the row-level scope check"
impossible to merge rather than merely unlikely.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend import auth
from backend.api.deps import scoped_case
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import Employee, User
from backend.main import app


@pytest.fixture
def client():
    # Deliberately not the context-manager form: entering it runs the startup
    # event, which makes a real embedding call against the Portkey gateway.
    return TestClient(app)


def _purge(email: str) -> None:
    with session_scope() as s:
        s.execute(delete(User).where(User.email == email))


@pytest.fixture
def accounts():
    """One live account per role, with a manager who really does manage someone."""
    suffix = uuid.uuid4().hex[:8]
    password = "correct-horse-battery-perm"

    # A manager account only sees anything if its email matches an employee's
    # manager_email, so borrow a real one from the corpus.
    cases = store.exit_cases()
    if not cases:
        pytest.skip("no seeded exit case")
    case = cases[0]
    employee = store.employee(str(case.meta["employee_id"]))
    manager_email = str(employee.meta.get("manager_email") or "").strip().lower()
    if not manager_email:
        pytest.skip("seeded employee has no manager_email")

    spec = {
        "hr": (f"pytest-perm-hr-{suffix}@example.com", None),
        "it": (f"pytest-perm-it-{suffix}@example.com", None),
        "manager": (manager_email, None),
        "employee": (f"pytest-perm-emp-{suffix}@example.com", str(case.meta["employee_id"])),
    }

    created: dict[str, str] = {}
    existing_manager_password_reset = False
    with session_scope() as s:
        for role, (email, employee_id) in spec.items():
            row = s.scalar(select(User).where(User.email == email))
            if row is not None:
                # The manager address is a real seeded account; borrow it by
                # resetting the password rather than creating a duplicate.
                row.password_hash = auth.hash_password(password)
                row.role = role
                row.is_active = True
                row.must_change_password = False
                existing_manager_password_reset = True
            else:
                s.add(User(email=email, password_hash=auth.hash_password(password),
                           role=role, employee_id=employee_id,
                           full_name=f"Pytest {role}", must_change_password=False))
            created[role] = email

    yield {"emails": created, "password": password,
           "case_id": str(case.meta["case_id"]),
           "employee_id": str(case.meta["employee_id"])}

    for role, email in created.items():
        if role == "manager" and existing_manager_password_reset:
            continue  # it was already there; leave it for the next run
        _purge(email)


def _login(client: TestClient, email: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    token = client.cookies.get(auth.CSRF_COOKIE)
    client.headers["X-CSRF-Token"] = token
    return token


# -- the structural guarantee -------------------------------------------------
def _api_routes() -> list[APIRoute]:
    """Flatten the route tree.

    `include_router` stores a lazy `_IncludedRouter` in this FastAPI version
    rather than copying routes up, so a flat walk of `app.routes` finds almost
    nothing. Recursing through `original_router` is what actually enumerates
    the API.
    """
    found: list[APIRoute] = []

    def walk(routes) -> None:
        for route in routes:
            if isinstance(route, APIRoute):
                found.append(route)
            inner = getattr(route, "original_router", None)
            if inner is not None:
                walk(inner.routes)

    walk(app.routes)
    return found


def test_the_route_walker_actually_finds_the_api():
    """Guards the guard. If `_api_routes` silently returned [] -- which it would
    if FastAPI changed how includes are stored -- every introspection test
    below would vacuously pass."""
    paths = {r.path for r in _api_routes()}
    assert len(paths) > 20, f"only found {len(paths)} routes; the walker is broken"
    for expected in ("/api/auth/login", "/api/agents", "/api/queue",
                     "/api/cases/{case_id}/transition".replace("/transition", "")):
        assert expected in paths, f"{expected} missing -- walker is not seeing the API"


def test_every_case_route_resolves_through_scoped_case():
    """Any route that names a case must obtain it via `scoped_case`.

    This is the difference between a convention and a guarantee. A helper you
    have to remember to call is one someone will eventually forget, and the
    failure mode is not a crash -- it is a manager quietly reading another
    team's exit case. Adding a `{case_id}` route without the dependency fails
    here instead.
    """
    offenders = []
    for route in _api_routes():
        if "{case_id}" not in route.path:
            continue
        deps = {d.call for d in route.dependant.dependencies}

        def uses_scoped(dependant) -> bool:
            for sub in dependant.dependencies:
                if sub.call is scoped_case or uses_scoped(sub):
                    return True
            return False

        if scoped_case not in deps and not uses_scoped(route.dependant):
            offenders.append(f"{','.join(sorted(route.methods))} {route.path}")

    assert not offenders, (
        "these routes take a {case_id} but never resolve it through "
        "scoped_case, so nothing scopes them to the caller:\n  "
        + "\n  ".join(offenders)
    )


def test_no_api_route_is_left_unauthenticated():
    """Every /api route except the three public auth endpoints must depend on a
    session. A new route that forgets its guard is otherwise wide open."""
    public = {"/api/auth/login", "/api/auth/logout", "/api/auth/me"}
    offenders = []
    for route in _api_routes():
        if not route.path.startswith("/api") or route.path in public:
            continue
        names = set()

        def collect(dependant) -> None:
            for sub in dependant.dependencies:
                if sub.call is not None:
                    names.add(getattr(sub.call, "__name__", ""))
                collect(sub)

        collect(route.dependant)
        if not names & {"current_user", "guard", "require_hr", "scoped_case",
                        "optional_user", "csrf_protected"}:
            offenders.append(f"{','.join(sorted(route.methods))} {route.path}")
    assert not offenders, "unauthenticated /api routes:\n  " + "\n  ".join(offenders)


# -- the role x route matrix --------------------------------------------------
@pytest.mark.parametrize(
    "role,path,expected",
    [
        # HR reaches everything operational -- but NOT account administration,
        # which is IT's. HR being able to grant itself any role would make that
        # split cosmetic, so this is the one route where HR is not an override.
        ("hr", "/api/health", 200),
        ("hr", "/api/admin/users", 403),
        ("hr", "/api/queue", 200),
        ("hr", "/api/cases", 200),
        # IT works every case and owns account administration.
        ("it", "/api/health", 200),
        ("it", "/api/queue", 200),
        ("it", "/api/cases", 200),
        ("it", "/api/approvals", 200),
        ("it", "/api/admin/users", 200),
        # A manager sees their own team and nothing operational.
        ("manager", "/api/queue", 200),
        ("manager", "/api/cases", 200),
        ("manager", "/api/admin/users", 403),
        ("manager", "/api/approvals", 403),
        # An employee is the subject of a case, not a worker of one.
        ("employee", "/api/queue", 403),
        ("employee", "/api/cases", 403),
        ("employee", "/api/health", 403),
        ("employee", "/api/admin/users", 403),
        ("employee", "/api/approvals", 403),
    ],
)
def test_role_route_matrix(client, accounts, role, path, expected):
    _login(client, accounts["emails"][role], accounts["password"])
    assert client.get(path).status_code == expected


def test_a_manager_cannot_read_a_case_outside_their_team(client, accounts):
    """The row-level check, end to end through HTTP."""
    _login(client, accounts["emails"]["manager"], accounts["password"])

    # Their own report's case is fine...
    assert client.get(f"/api/cases/{accounts['case_id']}").status_code == 200

    # ...and somebody else's is not. Find a case they do not manage.
    their_email = accounts["emails"]["manager"]
    other = None
    for case in store.exit_cases():
        emp = store.employee(str(case.meta["employee_id"]))
        if emp and str(emp.meta.get("manager_email") or "").lower() != their_email:
            other = str(case.meta["case_id"])
            break
    if other is None:
        pytest.skip("every seeded case reports to this manager")

    resp = client.get(f"/api/cases/{other}")
    assert resp.status_code == 403, (
        "a manager read a case outside their team -- the row scope is not applied"
    )
    # 403 rather than 404 is a deliberate, documented choice: case ids are
    # sequential and already guessable, so hiding existence buys nothing, and
    # a clear refusal is debuggable. What must not leak is the contents.
    assert "outside your scope" in resp.json()["detail"]


def test_an_employee_reaches_only_their_own_case(client, accounts):
    _login(client, accounts["emails"]["employee"], accounts["password"])

    own = client.get("/api/me/case")
    assert own.status_code == 200
    assert own.json()["case"]["case_id"] == accounts["case_id"]

    # Even their own case is not reachable through the staff route.
    assert client.get(f"/api/cases/{accounts['case_id']}").status_code in (403, 404)


def test_the_agent_catalogue_is_scoped_per_role(client, accounts):
    """What a role can see and what it can run come from one table, so this
    also pins that they cannot drift apart."""
    seen = {}
    for role in ("hr", "it", "manager", "employee"):
        _login(client, accounts["emails"][role], accounts["password"])
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        seen[role] = {a["id"] for a in resp.json()["agents"]}
        client.post("/api/auth/logout")

    assert seen["employee"] == {"faq_chatbot"}
    assert "it_deprovisioning" in seen["it"]
    assert "it_deprovisioning" not in seen["manager"]
    assert "kt_reviewer" in seen["manager"]
    assert "kt_reviewer" not in seen["it"]
    assert seen["hr"] > seen["it"] and seen["hr"] > seen["manager"]


def test_the_catalogue_does_not_leak_the_roster_to_a_manager(client, accounts):
    """`/api/agents` fills select options from the employee and case tables.
    Unscoped, that is an org dump through a dropdown."""
    _login(client, accounts["emails"]["manager"], accounts["password"])
    agents = client.get("/api/agents").json()["agents"]

    offered: set[str] = set()
    for agent in agents:
        for field in agent["fields"]:
            if field["name"] in ("employee_id", "case_id"):
                offered |= {o["value"] for o in field["options"]}

    all_employees = {str(e.meta["id"]) for e in store.employees()}
    assert not (offered & all_employees) or offered < all_employees, (
        "a manager was offered every employee in the company"
    )


def test_an_agent_run_against_an_out_of_scope_record_is_refused(client, accounts):
    """Gating the agent type is not enough: the arguments need scoping too."""
    _login(client, accounts["emails"]["manager"], accounts["password"])

    outsider = None
    their_email = accounts["emails"]["manager"]
    for emp in store.employees():
        if str(emp.meta.get("manager_email") or "").lower() != their_email:
            outsider = str(emp.meta["id"])
            break
    if outsider is None:
        pytest.skip("every seeded employee reports to this manager")

    resp = client.post("/api/agents/risk_assessment/run",
                       json={"employee_id": outsider})
    assert resp.status_code == 403
    assert "outside your scope" in resp.json()["detail"]


# -- CSRF ---------------------------------------------------------------------
def test_a_mutating_request_without_the_csrf_header_is_refused(client, accounts):
    """Enforced by middleware, so it cannot be forgotten on a new route.

    Before this, exactly one route (the orchestrator SSE resume) checked the
    token and every other mutating endpoint relied on SameSite=Lax alone.
    """
    _login(client, accounts["emails"]["hr"], accounts["password"])
    client.headers.pop("X-CSRF-Token", None)

    resp = client.post("/api/employees", json={"name": "x", "role": "y", "department": "z"})
    assert resp.status_code == 403
    assert "CSRF" in resp.json()["detail"]


def test_a_wrong_csrf_token_is_refused(client, accounts):
    _login(client, accounts["emails"]["hr"], accounts["password"])
    client.headers["X-CSRF-Token"] = "not-the-right-token"
    assert client.post("/api/employees", json={}).status_code == 403


def test_login_and_logout_stay_reachable_without_a_token(client, accounts):
    """They must be exempt: login has no session yet, and logout has to work
    even when the session is already gone."""
    resp = client.post("/api/auth/login", json={
        "email": accounts["emails"]["hr"], "password": accounts["password"]})
    assert resp.status_code == 200
    client.headers.pop("X-CSRF-Token", None)
    assert client.post("/api/auth/logout").status_code == 200


def test_an_expired_session_gets_401_not_a_csrf_error(client, accounts):
    """CSRF enforcement must not mask "sign in again".

    The middleware runs before routing, so without a carve-out a write on an
    expired session answers "Missing or invalid CSRF token" -- which tells the
    user nothing actionable and never prompts a re-login.
    """
    resp = client.post("/api/employees", json={"name": "x"})
    assert resp.status_code == 401
    assert "Sign in" in resp.json()["detail"]

    # ...and a *live* session with no token is still refused.
    _login(client, accounts["emails"]["hr"], accounts["password"])
    client.headers.pop("X-CSRF-Token", None)
    assert client.post("/api/employees", json={}).status_code == 403
