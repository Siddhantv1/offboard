"""Manager stages belong to the manager the leaver reports to.

Smart Routing picked an approver by workload from everyone holding the owning
role. For IT that is right -- any IT engineer can clear IT access. For
Knowledge Transfer and Manager Sign-off it is wrong, and wrong in a way that
produces work nobody can do:

  case-007's employee reports to Ritesh Sharma. Routing gave the sign-off to
  Anjali Desai, who was less busy. A manager only sees their own reports'
  cases, so case-007 was invisible to her -- her "Awaiting sign-off" page was
  empty and always would be -- while Ritesh, who could see it, did not own the
  stage. It sat on nobody's queue.

Two guards, deliberately independent. The roster stops the agent recommending
it; the assign route stops anything else doing it by another path.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend import auth
from backend.agents.smart_routing import approver_roster
from backend.api.deps import can_see_case
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import CaseStage, Employee, ExitCase, User
from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def cast():
    """An exiting employee, the manager they report to, and one who they do not."""
    suffix = uuid.uuid4().hex[:8]
    theirs = f"pytest-mgr-theirs-{suffix}@example.com"
    other = f"pytest-mgr-other-{suffix}@example.com"
    hr = f"pytest-mgr-hr-{suffix}@example.com"
    staff_email = f"pytest-mgr-emp-{suffix}@example.com"

    with session_scope() as s:
        for email, role, name in ((theirs, "manager", "Pytest Their Manager"),
                                  (other, "manager", "Pytest Other Manager"),
                                  (hr, "hr", "Pytest HR")):
            s.add(User(email=email, password_hash=auth.hash_password("x" * 12),
                       role=role, full_name=name))

    emp, case, _ = store.add_employee({
        "name": "Pytest Reports Upward", "role": "Engineer",
        "department": "Engineering", "email": staff_email,
        "manager_email": theirs, "status": "exiting",
    })
    yield {"theirs": theirs, "other": other, "hr": hr,
           "case_id": str(case.meta["case_id"])}
    with session_scope() as s:
        s.execute(delete(User).where(User.email.in_([theirs, other, hr, staff_email])))
        s.execute(delete(ExitCase).where(ExitCase.case_id == case.meta["case_id"]))
        s.execute(delete(Employee).where(Employee.id == emp.meta["id"]))


def _login(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": "x" * 12})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = client.cookies.get(auth.CSRF_COOKIE)


def _uid(email):
    with session_scope() as s:
        return s.scalar(select(User).where(User.email == email)).id


# -- the roster -----------------------------------------------------------
def test_a_manager_stage_offers_only_the_reporting_manager(cast):
    names = [a["email"] for a in approver_roster("Manager Sign-off", cast["case_id"])]
    assert names == [cast["theirs"]]
    assert cast["other"] not in names, "routing offered a manager off the reporting line"


def test_knowledge_transfer_follows_the_same_rule(cast):
    names = [a["email"] for a in approver_roster("Knowledge Transfer", cast["case_id"])]
    assert names == [cast["theirs"]]


def test_it_clearance_stays_an_open_roster(cast):
    """Any IT engineer can clear IT access. Narrowing this would be a different
    bug -- a rota of one where the company has a team."""
    names = [a["email"] for a in approver_roster("IT Clearance", cast["case_id"])]
    assert len(names) >= 1
    assert cast["theirs"] not in names


def test_a_leaver_whose_manager_has_no_account_gets_an_empty_roster(cast):
    """Better than offering somebody who cannot do it. The agent turns this
    into a message naming the real problem."""
    with session_scope() as s:
        s.execute(delete(User).where(User.email == cast["theirs"]))
    assert approver_roster("Manager Sign-off", cast["case_id"]) == []


# -- the route ------------------------------------------------------------
def test_assigning_a_manager_off_the_reporting_line_is_refused(client, cast):
    _login(client, cast["hr"])
    r = client.post(f"/api/cases/{cast['case_id']}/stages/manager/assign",
                    json={"user_id": _uid(cast["other"])})
    assert r.status_code == 422, r.text
    assert "cannot see" in r.json()["detail"]


def test_assigning_the_right_manager_lands_on_their_sign_off_page(client, cast):
    _login(client, cast["hr"])
    assert client.post(f"/api/cases/{cast['case_id']}/stages/manager/assign",
                       json={"user_id": _uid(cast["theirs"])}).status_code == 200

    _login(client, cast["theirs"])
    mine = [r for r in client.get("/api/queue?scope=mine").json()["stages"]
            if r["case_id"] == cast["case_id"] and r["stage_key"] == "manager"]
    assert mine, "the assigned sign-off never reached the manager's queue"


# -- the corpus -----------------------------------------------------------
def test_no_stage_anywhere_is_owned_by_someone_who_cannot_see_its_case():
    """The invariant behind both guards, checked against real data.

    An assignment the holder cannot see is not merely odd -- it is work that
    exists and can never be done, and nothing anywhere reports it. This caught
    two live rows on case-007.
    """
    stranded = []
    with session_scope() as s:
        users = {u.id: u for u in s.scalars(select(User)).all()}
        for case in store.exit_cases():
            rows = s.scalars(select(CaseStage).where(
                CaseStage.case_id == str(case.meta.get("case_id")),
                CaseStage.owner_user_id.isnot(None))).all()
            for st in rows:
                holder = users.get(st.owner_user_id)
                if holder is None:
                    continue
                identity = auth.Identity(
                    user_id=holder.id, email=holder.email, role=holder.role,
                    employee_id=holder.employee_id, full_name=holder.full_name,
                    must_change_password=False)
                if not can_see_case(identity, case.meta):
                    stranded.append(
                        f"{st.case_id} {st.stage_key}: held by "
                        f"{holder.full_name or holder.email} ({holder.role}), "
                        "who cannot see the case")
    assert not stranded, (
        "stages assigned to someone who cannot see them; run "
        "`python -m scripts.link_stage_owners --repair`:\n  " + "\n  ".join(stranded)
    )


def test_the_service_refuses_it_too_not_just_the_route(cast):
    """Defence in depth, and the reason the predicate moved out of `api/deps`.

    The orchestrator assigns stages without going through a route. While the
    check lived only in the route, the guard that stopped a person stranding
    work did not stop the machine doing it -- which is how the case-007 rows
    were written in the first place.
    """
    from backend.stages import TransitionError, assign_owner

    with pytest.raises(TransitionError, match="cannot see"):
        assign_owner(
            case_id=cast["case_id"], stage_key="manager",
            assignee_user_id=_uid(cast["other"]),
            assignee_label="Pytest Other Manager",
            actor=auth.Identity(user_id=_uid(cast["hr"]), email=cast["hr"], role="hr",
                                employee_id=None, full_name="Pytest HR",
                                must_change_password=False),
        )
