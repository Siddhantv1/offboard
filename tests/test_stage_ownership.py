"""Who owns a clearance stage, and whether the queue agrees.

Two bugs are pinned here. Both produced the same user-visible symptom -- "I
assigned this person and it never showed up on their queue" -- by different
routes.

1. A *completed* stage could be assigned. The write succeeded, the result view
   said "it is in their queue", and the queue (which filters completed work
   out, correctly) showed nothing. The action was meaningless and nothing said
   so.

2. `owner` and `owner_user_id` were written asymmetrically. `owner` is the name
   a human reads; `owner_user_id` is what the queue routes on. Claiming set the
   id unconditionally but the name only when it was blank, so HR moving someone
   else's stage quietly took the routing while leaving their name on the
   record. Seven rows in the live database ended up naming one person and
   routing to another -- including a live manager sign-off that sat in HR's
   queue instead of the manager's.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend import auth
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import CaseStage, Employee, ExitCase, User
from backend.main import app
from backend.stages import timeline, transition


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def people():
    suffix = uuid.uuid4().hex[:8]
    spec = {"hr": f"pytest-own-hr-{suffix}@example.com",
            "it": f"pytest-own-it-{suffix}@example.com"}
    with session_scope() as s:
        for role, email in spec.items():
            s.add(User(email=email, password_hash=auth.hash_password("x" * 12),
                       role=role, full_name=f"Pytest {role.upper()} {suffix[:4]}"))
    yield spec
    with session_scope() as s:
        s.execute(delete(User).where(User.email.in_(list(spec.values()))))


@pytest.fixture
def scratch_case():
    """A throwaway case. Anything that writes to a stage builds its own, or it
    corrupts the seeded corpus the golden snapshot compares against."""
    email = f"pytest-own-{uuid.uuid4().hex[:8]}@example.com"
    emp, case, _ = store.add_employee({
        "name": "Pytest Ownership Probe", "role": "Engineer",
        "department": "Engineering", "email": email, "status": "exiting",
    })
    yield str(case.meta["case_id"])
    with session_scope() as s:
        s.execute(delete(User).where(User.email == email))
        s.execute(delete(ExitCase).where(ExitCase.case_id == case.meta["case_id"]))
        s.execute(delete(Employee).where(Employee.id == emp.meta["id"]))


def _login(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": "x" * 12})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = client.cookies.get(auth.CSRF_COOKIE)


def _user(email) -> tuple[int, str]:
    with session_scope() as s:
        u = s.scalar(select(User).where(User.email == email))
        return u.id, u.full_name


def _identity(email) -> auth.Identity:
    with session_scope() as s:
        u = s.scalar(select(User).where(User.email == email))
        return auth.Identity(user_id=u.id, email=u.email, role=u.role,
                             employee_id=u.employee_id, full_name=u.full_name,
                             must_change_password=False)


def _stage_row(case_id, key="it") -> tuple[str, int | None, str]:
    """(owner name, owner_user_id, status) -- read out, not left attached."""
    with session_scope() as s:
        st = s.scalar(select(CaseStage).where(
            CaseStage.case_id == case_id, CaseStage.stage_key == key))
        return st.owner, st.owner_user_id, st.status


def _set_status(case_id, key, status):
    with session_scope() as s:
        st = s.scalar(select(CaseStage).where(
            CaseStage.case_id == case_id, CaseStage.stage_key == key))
        st.status = status


# -- 1. finished work cannot be handed to anyone ------------------------------
def test_assigning_a_completed_stage_is_refused(client, people, scratch_case):
    """The reported bug, exactly.

    IT Clearance on case-006 was already completed. Assigning it returned 200,
    the result view said "it is in their queue", and the queue showed nothing --
    because a completed stage is not work. Refuse it at the source rather than
    let the UI make a promise the queue will not keep.
    """
    it_id, _ = _user(people["it"])
    _set_status(scratch_case, "it", "completed")

    _login(client, people["hr"])
    resp = client.post(f"/api/cases/{scratch_case}/stages/it/assign",
                       json={"user_id": it_id})
    assert resp.status_code == 409, resp.text
    assert "already completed" in resp.json()["detail"].lower()


def test_claiming_a_completed_stage_is_refused(client, people, scratch_case):
    _set_status(scratch_case, "it", "completed")
    _login(client, people["it"])
    resp = client.post(f"/api/cases/{scratch_case}/stages/it/claim")
    assert resp.status_code == 409, resp.text


def test_an_open_stage_can_still_be_assigned(client, people, scratch_case):
    """The guard must not break the working path."""
    it_id, it_name = _user(people["it"])
    _login(client, people["hr"])
    resp = client.post(f"/api/cases/{scratch_case}/stages/it/assign",
                       json={"user_id": it_id})
    assert resp.status_code == 200, resp.text

    _login(client, people["it"])
    mine = [r for r in client.get("/api/queue?scope=mine").json()["stages"]
            if r["case_id"] == scratch_case]
    assert mine and mine[0]["owner"] == it_name


# -- 2. the two owner columns must never name different people ----------------
def test_hr_moving_a_stage_does_not_divert_its_owner_queue(
        client, people, scratch_case):
    """The case-003 bug.

    HR moving a stage sent `claim: true`, which reassigned `owner_user_id` to
    HR while leaving `owner` reading "Sanya Kapoor". The stage then showed her
    name everywhere and appeared in HR's queue instead of hers.
    """
    it_id, it_name = _user(people["it"])
    _login(client, people["hr"])
    assert client.post(f"/api/cases/{scratch_case}/stages/it/assign",
                       json={"user_id": it_id}).status_code == 200

    transition(case_id=scratch_case, stage_key="it", to_status="in_progress",
               actor=_identity(people["hr"]), claim=True)

    owner, owner_id, _ = _stage_row(scratch_case)
    assert owner_id == it_id, "HR hijacked the routing just by moving the stage"
    assert owner == it_name


def test_the_owner_name_and_the_owner_id_always_name_the_same_person(
        client, people, scratch_case):
    """The invariant that makes a mis-link detectable, and so repairable.

    If the two can disagree, the UI shows one name while the work goes to
    somebody else, and nothing anywhere reports a problem.
    """
    with session_scope() as s:
        st = s.scalar(select(CaseStage).where(
            CaseStage.case_id == scratch_case, CaseStage.stage_key == "it"))
        st.owner = "Somebody Else"          # an authored name, as the corpus has
        st.owner_user_id = None

    it_id, it_name = _user(people["it"])
    _login(client, people["it"])
    assert client.post(f"/api/cases/{scratch_case}/stages/it/claim").status_code == 200

    owner, owner_id, _ = _stage_row(scratch_case)
    assert owner == it_name, "claiming took the queue but left another name on the record"
    assert owner_id == it_id


# -- 3. an escalation is an act, and acts are recorded ------------------------
def test_recording_an_escalation_lands_on_the_case_timeline(
        client, people, scratch_case):
    """SLA Escalation drafted a chase message you could only copy out.

    Recording it writes the one durable fact the draft implies -- that this
    stage was chased, by whom, and when -- onto the case's own audit trail.
    """
    _login(client, people["hr"])
    resp = client.post(f"/api/cases/{scratch_case}/stages/it/escalate",
                       json={"note": "3 days past the 5-day SLA"})
    assert resp.status_code == 200, resp.text

    events = timeline(scratch_case)
    assert len(events) == 1
    assert events[0]["escalated"] is True
    assert "3 days past" in events[0]["note"]
    # An escalation is not a status change; the stage must not have moved.
    assert _stage_row(scratch_case)[2] == "pending"


# -- 4. who decided this? -----------------------------------------------------
def test_assigning_is_recorded_in_the_audit_trail(client, people, scratch_case):
    """Assignment decides who is accountable, and it left no trace.

    `assign` and `claim` wrote the owner columns straight onto the row. The
    case could say who owns IT Clearance and could not say who decided that,
    when, or who held it before -- on a record whose entire purpose is
    answering exactly that.
    """
    it_id, it_name = _user(people["it"])
    _login(client, people["hr"])
    assert client.post(f"/api/cases/{scratch_case}/stages/it/assign",
                       json={"user_id": it_id}).status_code == 200

    events = [e for e in timeline(scratch_case) if e["kind"] == "assignment"]
    assert events, "assigning a stage recorded nothing"
    assert it_name in events[-1]["note"]
    assert events[-1]["role"] == "hr", "the trail must name who decided, not who received"


def test_a_reassignment_names_who_held_it_before(client, people, scratch_case):
    """"Who had this before?" is the question an auditor actually asks."""
    it_id, it_name = _user(people["it"])
    hr_id, hr_name = _user(people["hr"])

    _login(client, people["hr"])
    client.post(f"/api/cases/{scratch_case}/stages/it/assign", json={"user_id": it_id})
    resp = client.post(f"/api/cases/{scratch_case}/stages/it/assign",
                       json={"user_id": hr_id})
    assert resp.status_code == 200
    assert resp.json()["previous_owner"] == it_name

    note = [e for e in timeline(scratch_case) if e["kind"] == "assignment"][-1]["note"]
    assert it_name in note and hr_name in note


def test_claiming_reads_as_taking_it(client, people, scratch_case):
    _login(client, people["it"])
    assert client.post(f"/api/cases/{scratch_case}/stages/it/claim").status_code == 200

    event = [e for e in timeline(scratch_case) if e["kind"] == "assignment"][-1]
    assert "Took" in event["note"]
    assert event["role"] == "it"


def test_reassigning_to_the_same_person_records_nothing(client, people, scratch_case):
    """A re-click is not a decision. Recording it would pad the trail with
    noise and bury the entries that mean something."""
    it_id, _ = _user(people["it"])
    _login(client, people["hr"])
    client.post(f"/api/cases/{scratch_case}/stages/it/assign", json={"user_id": it_id})
    again = client.post(f"/api/cases/{scratch_case}/stages/it/assign",
                        json={"user_id": it_id})
    assert again.status_code == 200
    assert again.json()["recorded"] is False
    assert len([e for e in timeline(scratch_case) if e["kind"] == "assignment"]) == 1


def test_an_assignment_is_not_recorded_as_a_stage_movement(client, people, scratch_case):
    """It must not look like a transition to anything reading the trail --
    including the case page, which used to render these as "pending → pending"."""
    it_id, _ = _user(people["it"])
    _login(client, people["hr"])
    client.post(f"/api/cases/{scratch_case}/stages/it/assign", json={"user_id": it_id})

    event = [e for e in timeline(scratch_case) if e["kind"] == "assignment"][-1]
    assert event["from"] == event["to"], "an assignment moved the stage"
    assert event["escalated"] is False
    assert _stage_row(scratch_case)[2] == "pending"


def test_every_event_declares_what_kind_it_is(client, people, scratch_case):
    """Four things are recorded in one table. Before `kind`, telling them apart
    meant sniffing `escalated` and the note text."""
    it_id, _ = _user(people["it"])
    _login(client, people["hr"])
    client.post(f"/api/cases/{scratch_case}/stages/it/assign", json={"user_id": it_id})
    client.post(f"/api/cases/{scratch_case}/stages/it/escalate", json={"note": "late"})
    client.post(f"/api/cases/{scratch_case}/stages/it/transition",
                json={"to": "in_progress"})

    kinds = [e["kind"] for e in timeline(scratch_case)]
    assert set(kinds) == {"assignment", "escalation", "transition"}
    assert all(k in ("transition", "assignment", "escalation", "confirmation")
               for k in kinds)
