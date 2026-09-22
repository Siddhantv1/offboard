"""The exit interview the leaver fills in, and their own exit checklist.

Both features exist because the person an offboarding is *about* could
previously only read it. What is worth pinning is therefore not "the endpoint
returns 200" -- it is the three places where giving the employee a write
surface could have leaked something:

  * the routes carry no case id, so there is nothing to substitute;
  * a submitted interview is closed, so the record cannot be rewritten after
    HR has acted on it;
  * the checklist takes its employee_id off the session, so a payload cannot
    aim it at somebody else.

No test here touches the network. The one route that would call the gateway
has its agent's `run` replaced.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend import auth, exit_interview
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import AgentRun, ExitInterviewResponse, User
from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _make_employee(case, label: str) -> tuple[str, str]:
    email = f"pytest-interview-{label}-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-interview"
    with session_scope() as s:
        s.add(User(email=email, password_hash=auth.hash_password(password),
                   role="employee", employee_id=str(case.meta["employee_id"]),
                   full_name=f"Pytest {label}", must_change_password=False))
    return email, password


def _clear(case_id: str) -> None:
    with session_scope() as s:
        s.execute(delete(ExitInterviewResponse).where(
            ExitInterviewResponse.case_id == case_id))


@pytest.fixture
def two_cases():
    """Two seeded exit cases, so "somebody else's" is a real other case."""
    cases = store.exit_cases()
    if len(cases) < 2:
        pytest.skip("need two seeded exit cases")
    return cases[0], cases[1]


@pytest.fixture
def employee(two_cases):
    """An account on the first case, with its interview reset around the test."""
    case = two_cases[0]
    email, password = _make_employee(case, "emp")
    _clear(str(case.meta["case_id"]))
    yield email, password, str(case.meta["case_id"])
    _clear(str(case.meta["case_id"]))
    with session_scope() as s:
        s.execute(delete(User).where(User.email == email))


def _login(client: TestClient, email: str, password: str) -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    client.headers["X-CSRF-Token"] = client.cookies.get(auth.CSRF_COOKIE)


def _full_answers() -> dict[str, str]:
    """Every required question answered, using a legal value for the choices."""
    out = {}
    for q in exit_interview.QUESTIONS:
        if not q["required"]:
            continue
        out[q["id"]] = q["options"][0] if q["type"] == "choice" else f"answer to {q['id']}"
    return out


# -- the question set ---------------------------------------------------------
def test_the_questions_are_fixed_and_cover_what_was_asked_for():
    """They are a constant, not a generation: `interview_trends` compares
    departments and quarters, which is meaningless if the wording drifts."""
    ids = [q["id"] for q in exit_interview.QUESTIONS]
    assert len(ids) == len(set(ids)), f"duplicate question ids: {ids}"
    assert 6 <= len(ids) <= 8
    for expected in ("reason_for_leaving", "what_worked", "what_did_not_work",
                     "manager", "would_recommend", "would_return", "anything_else"):
        assert expected in ids

    for q in exit_interview.QUESTIONS:
        assert q["prompt"].strip().endswith("?"), q
        if q["type"] == "choice":
            assert q["options"], q


# -- saving and submitting ----------------------------------------------------
def test_an_employee_saves_progress_then_submits(employee, client):
    email, password, _ = employee
    _login(client, email, password)

    start = client.get("/api/me/case/interview")
    assert start.status_code == 200
    body = start.json()
    assert body["submitted"] is False
    assert [q["id"] for q in body["questions"]] == [
        q["id"] for q in exit_interview.QUESTIONS]
    assert body["answers"] == {}
    assert body["outstanding"], "nothing answered yet, so everything is outstanding"

    # Half-finished is the normal state of a form, so a partial save is fine.
    partial = client.post("/api/me/case/interview",
                          json={"answers": {"reason_for_leaving": "Closer to home."}})
    assert partial.status_code == 200, partial.text
    assert partial.json()["submitted"] is False
    assert partial.json()["answers"]["reason_for_leaving"] == "Closer to home."

    # A later save sends only what changed and must not blank the rest.
    second = client.post("/api/me/case/interview",
                         json={"answers": {"what_worked": "The team."}})
    assert second.json()["answers"]["reason_for_leaving"] == "Closer to home."
    assert second.json()["answers"]["what_worked"] == "The team."

    done = client.post("/api/me/case/interview",
                       json={"answers": _full_answers(), "submitted": True})
    assert done.status_code == 200, done.text
    assert done.json()["submitted"] is True
    assert done.json()["submitted_at"]
    assert done.json()["outstanding"] == []


def test_a_submitted_interview_cannot_be_edited(employee, client):
    """One-way on purpose. HR acts on these; a manager criticised on Monday and
    exonerated on Friday with no trace of the first version is worse than no
    interview at all."""
    email, password, _ = employee
    _login(client, email, password)
    assert client.post("/api/me/case/interview",
                       json={"answers": _full_answers(), "submitted": True}
                       ).status_code == 200

    again = client.post("/api/me/case/interview",
                        json={"answers": {"manager": "Actually they were fine."}})
    assert again.status_code == 409
    assert "already been submitted" in again.json()["detail"]

    # And the stored answer is the original one, not the attempted edit.
    assert client.get("/api/me/case/interview").json()["answers"]["manager"] == (
        "answer to manager")


def test_submitting_half_an_interview_is_refused(employee, client):
    email, password, _ = employee
    _login(client, email, password)
    resp = client.post("/api/me/case/interview",
                       json={"answers": {"reason_for_leaving": "x"}, "submitted": True})
    assert resp.status_code == 422
    assert "before you can submit" in resp.json()["detail"]
    # Refused means refused: it must not have latched the submit.
    assert client.get("/api/me/case/interview").json()["submitted"] is False


def test_an_unknown_question_id_is_refused_rather_than_ignored(employee, client):
    """Ignoring it would let a caller write arbitrary JSON into the column and
    have the write report success."""
    email, password, _ = employee
    _login(client, email, password)
    resp = client.post("/api/me/case/interview",
                       json={"answers": {"salary_of_my_replacement": "?"}})
    assert resp.status_code == 422
    assert "not one of the exit interview questions" in resp.json()["detail"]


def test_a_choice_question_only_takes_one_of_its_options(employee, client):
    email, password, _ = employee
    _login(client, email, password)
    resp = client.post("/api/me/case/interview",
                       json={"answers": {"would_recommend": "maybe one day"}})
    assert resp.status_code == 422
    assert "Choose one of" in resp.json()["detail"]


def test_the_interview_routes_need_a_session_and_a_csrf_token(employee, client):
    assert client.post("/api/me/case/interview", json={}).status_code == 401
    assert client.get("/api/me/case/interview").status_code == 401

    email, password, _ = employee
    _login(client, email, password)
    client.headers.pop("X-CSRF-Token", None)
    resp = client.post("/api/me/case/interview", json={})
    assert resp.status_code == 403
    assert "CSRF" in resp.json()["detail"]


# -- scoping ------------------------------------------------------------------
def test_an_employee_cannot_read_anyone_elses_interview(two_cases, client):
    """Two independent guarantees, because each covers what the other cannot.

    The /api/me route takes no case id, so there is nothing to substitute. The
    staff route takes one but is staff-only, so an employee is refused before
    the case is even resolved.
    """
    import inspect

    from backend.api import cases_routes

    mine, theirs = two_cases
    email, password = _make_employee(mine, "scope")
    _clear(str(theirs.meta["case_id"]))
    try:
        # Their case has a submitted interview sitting there to be leaked.
        with session_scope() as s:
            s.add(ExitInterviewResponse(
                case_id=str(theirs.meta["case_id"]),
                employee_id=str(theirs.meta["employee_id"]),
                answers={"reason_for_leaving": "A secret from the other case."},
                submitted_at=auth._now(),
            ))

        _login(client, email, password)

        # The route that would name it refuses them outright.
        resp = client.get(f"/api/cases/{theirs.meta['case_id']}/interview")
        assert resp.status_code == 403, resp.text

        # ...including on their own case, which is the /api/me route's job.
        assert client.get(
            f"/api/cases/{mine.meta['case_id']}/interview").status_code == 403

        # And their own route answers with their own case, never that one.
        own = client.get("/api/me/case/interview")
        assert own.status_code == 200
        assert own.json()["case_id"] == str(mine.meta["case_id"])
        assert "A secret from the other case." not in str(own.json()["answers"])
    finally:
        _clear(str(theirs.meta["case_id"]))
        with session_scope() as s:
            s.execute(delete(User).where(User.email == email))

    # Structural, not incidental: there is no case id in the signature to abuse.
    for route in (cases_routes.my_exit_interview,
                  cases_routes.save_my_exit_interview,
                  cases_routes.my_exit_checklist,
                  cases_routes.run_my_exit_checklist):
        assert "case_id" not in inspect.signature(route).parameters, route.__name__


def test_hr_can_read_a_submitted_interview_but_not_a_draft(two_cases, client):
    case = two_cases[0]
    case_id = str(case.meta["case_id"])
    hr_email = f"pytest-interview-hr-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-interview"
    with session_scope() as s:
        s.add(User(email=hr_email, password_hash=auth.hash_password(password),
                   role="hr", full_name="Pytest HR", must_change_password=False))
    _clear(case_id)
    try:
        _login(client, hr_email, password)

        # Nothing written yet.
        empty = client.get(f"/api/cases/{case_id}/interview")
        assert empty.status_code == 200
        assert empty.json()["submitted"] is False
        assert empty.json()["responses"] == []

        # A draft is deliberately not exposed: half an answer to "what did not
        # work" is not something the person has chosen to say.
        with session_scope() as s:
            s.add(ExitInterviewResponse(
                case_id=case_id, employee_id=str(case.meta["employee_id"]),
                answers={"what_did_not_work": "half a thought"}))
        draft = client.get(f"/api/cases/{case_id}/interview").json()
        assert draft["submitted"] is False
        assert "half a thought" not in str(draft)

        # Submitted, and HR reads the whole thing with its prompts.
        with session_scope() as s:
            row = s.scalar(select(ExitInterviewResponse).where(
                ExitInterviewResponse.case_id == case_id))
            row.answers = {"what_did_not_work": "the on-call rota"}
            row.submitted_at = auth._now()

        body = client.get(f"/api/cases/{case_id}/interview").json()
        assert body["submitted"] is True
        assert body["submitted_at"]
        answers = {r["id"]: r["answer"] for r in body["responses"]}
        assert answers["what_did_not_work"] == "the on-call rota"
        assert [r["id"] for r in body["responses"]] == [
            q["id"] for q in exit_interview.QUESTIONS]
        assert all(r["prompt"] for r in body["responses"]), (
            "the answers are unreadable without the questions"
        )
    finally:
        _clear(case_id)
        with session_scope() as s:
            s.execute(delete(User).where(User.email == hr_email))


# -- the employee's own exit checklist ----------------------------------------
def test_the_checklist_route_uses_the_session_id_and_ignores_the_payload(
        two_cases, client, monkeypatch):
    """The whole point of this route.

    `checklist` is deliberately not in `ROLE_AGENTS["employee"]`, so the generic
    agent route refuses it. This one runs it -- and must run it against the
    signed-in person, whatever the body says.
    """
    from backend.agents import REGISTRY

    mine, theirs = two_cases
    my_employee_id = str(mine.meta["employee_id"])
    their_employee_id = str(theirs.meta["employee_id"])
    assert my_employee_id != their_employee_id

    seen: list[dict] = []

    def fake_run(self, inputs):
        seen.append(dict(inputs))
        return {"checklist": {"assets_to_return": [{"item": "Laptop"}]},
                "employee_id": inputs.get("employee_id")}

    monkeypatch.setattr(type(REGISTRY["checklist"]), "run", fake_run)

    email, password = _make_employee(mine, "chk")
    try:
        _login(client, email, password)

        # The generic route stays shut, which is what makes this one the seam.
        assert client.post("/api/agents/checklist/run",
                           json={"employee_id": my_employee_id}).status_code == 403

        resp = client.post("/api/me/case/checklist",
                           json={"employee_id": their_employee_id})
        assert resp.status_code == 200, resp.text
        assert seen == [{"employee_id": my_employee_id}], (
            "the payload's employee_id reached the agent"
        )
        assert resp.json()["employee_id"] == my_employee_id
        assert resp.json()["result"]["checklist"]["assets_to_return"]

        # ...and reading it back finds the run that was just recorded, for this
        # employee and no other.
        got = client.get("/api/me/case/checklist")
        assert got.status_code == 200
        assert got.json()["employee_id"] == my_employee_id
        assert got.json()["result"]["checklist"]["assets_to_return"][0]["item"] == "Laptop"
    finally:
        with session_scope() as s:
            s.execute(delete(AgentRun).where(
                AgentRun.agent_id == "checklist",
                AgentRun.employee_id == my_employee_id,
                AgentRun.inputs["employee_id"].astext == my_employee_id))
            s.execute(delete(User).where(User.email == email))


def test_the_checklist_read_never_returns_another_employees_run(two_cases, client):
    mine, theirs = two_cases
    their_employee_id = str(theirs.meta["employee_id"])
    email, password = _make_employee(mine, "chkread")
    with session_scope() as s:
        s.add(AgentRun(agent_id="checklist", employee_id=their_employee_id,
                       inputs={"employee_id": their_employee_id},
                       result={"checklist": {"assets_to_return": [{"item": "Not yours"}]}},
                       finished_at=auth._now()))
    try:
        _login(client, email, password)
        body = client.get("/api/me/case/checklist").json()
        assert body["employee_id"] == str(mine.meta["employee_id"])
        assert "Not yours" not in str(body)
    finally:
        with session_scope() as s:
            s.execute(delete(AgentRun).where(
                AgentRun.agent_id == "checklist",
                AgentRun.employee_id == their_employee_id,
                AgentRun.inputs["employee_id"].astext == their_employee_id))
            s.execute(delete(User).where(User.email == email))


def test_the_checklist_routes_need_a_session_and_a_csrf_token(two_cases, client):
    assert client.get("/api/me/case/checklist").status_code == 401

    email, password = _make_employee(two_cases[0], "chkcsrf")
    try:
        _login(client, email, password)
        client.headers.pop("X-CSRF-Token", None)
        resp = client.post("/api/me/case/checklist", json={})
        assert resp.status_code == 403
        assert "CSRF" in resp.json()["detail"]
    finally:
        with session_scope() as s:
            s.execute(delete(User).where(User.email == email))


def test_it_may_run_the_checklist_agent_and_the_employee_catalogue_is_unchanged():
    """IT was asked for it -- the access-to-revoke list is the input to the
    deprovisioning plan they already draft. The employee's own route is what
    gives them their copy, so their catalogue stays at one agent."""
    from backend.policy import ROLE_AGENTS, may_run_agent

    assert may_run_agent("it", "checklist")
    assert may_run_agent("hr", "checklist")
    assert may_run_agent("manager", "checklist")
    assert not may_run_agent("employee", "checklist")
    assert ROLE_AGENTS["employee"] == frozenset({"faq_chatbot"})


def test_an_account_with_no_case_gets_a_clear_404(client):
    """An HR account has no employee_id, so there is no case to resolve."""
    email = f"pytest-interview-nocase-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-battery-interview"
    with session_scope() as s:
        s.add(User(email=email, password_hash=auth.hash_password(password),
                   role="hr", full_name="Pytest No Case", must_change_password=False))
    try:
        _login(client, email, password)
        for path in ("/api/me/case/interview", "/api/me/case/checklist"):
            resp = client.get(path)
            assert resp.status_code == 404, path
            assert "No exit case" in resp.json()["detail"]
    finally:
        with session_scope() as s:
            s.execute(delete(User).where(User.email == email))


# -- being asked for it at all ------------------------------------------------
def test_a_leaver_who_owes_an_interview_is_flagged_on_sign_in(client, employee):
    """The page existed and nothing pointed at it.

    An exit interview nobody is asked for is one nobody records, and
    `interview_trends` compares departments and quarters -- a comparison that
    means nothing at a response rate near zero. The flag is what raises the
    prompt on sign-in.
    """
    email, password, _ = employee
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["exit_interview_outstanding"] is True


def test_the_flag_clears_once_it_is_submitted(client, employee):
    email, password, _ = employee
    _login(client, email, password)
    assert client.post("/api/me/case/interview",
                       json={"answers": _full_answers(),
                             "submitted": True}).status_code == 200

    me = client.get("/api/auth/me").json()["user"]
    assert me["exit_interview_outstanding"] is False


def test_a_draft_does_not_clear_the_flag(client, employee):
    """Saving progress is not recording an interview."""
    email, password, _ = employee
    _login(client, email, password)
    first = exit_interview.QUESTIONS[0]["id"]
    client.post("/api/me/case/interview", json={"answers": {first: "halfway"}})
    assert client.get("/api/auth/me").json()["user"]["exit_interview_outstanding"] is True


def test_staff_are_never_prompted(client, employee):
    """Only the person leaving has an interview to record."""
    suffix = uuid.uuid4().hex[:8]
    email = f"pytest-interview-hr-{suffix}@example.com"
    with session_scope() as s:
        s.add(User(email=email, password_hash=auth.hash_password("x" * 12),
                   role="hr", full_name="Pytest Interview HR"))
    try:
        _login(client, email, "x" * 12)
        me = client.get("/api/auth/me").json()["user"]
        assert me["exit_interview_outstanding"] is False
    finally:
        with session_scope() as s:
            s.execute(delete(User).where(User.email == email))
