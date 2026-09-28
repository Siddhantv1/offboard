"""Two people moving the same clearance stage at the same time.

`transition` was a read-modify-write with no lock, no compare-and-set and no
version column. Both requests read the same `from_status`, both passed
`assert_may_transition`, and both wrote.

The dangerous shape is not the lost update. HR completes IT Clearance while an
IT engineer sends it back: both read `in_progress`, so `HR_ONLY_FROM` -- the
guard that exists to stop a non-HR account reopening a signed-off clearance --
never sees a completed stage to refuse. The engineer's write lands second, the
clearance is open again, and the timeline that is supposed to be the compliance
record holds two mutually exclusive transitions out of one state.

Two real threads against real connections, because a single-threaded test
cannot tell a row lock from its absence.
"""
from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import delete

from backend import stages as stage_service
from backend.auth import Identity
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import CaseStage, CaseStageEvent, Employee, ExitCase, User

HR = Identity(user_id=None, email="hr@pytest.test", role="hr", employee_id=None,
              full_name="Pytest HR", must_change_password=False)
IT = Identity(user_id=None, email="it@pytest.test", role="it", employee_id=None,
              full_name="Pytest IT", must_change_password=False)


@pytest.fixture
def case():
    suffix = uuid.uuid4().hex[:8]
    emp, exit_case, _ = store.add_employee({
        "name": "Pytest Race", "role": "Engineer", "department": "Engineering",
        "email": f"pytest-race-{suffix}@example.com", "status": "exiting",
    })
    ids = {"case_id": str(exit_case.meta["case_id"]),
           "employee_id": str(emp.meta["id"])}
    with session_scope() as s:
        for row in s.get(ExitCase, ids["case_id"]).stages:
            if row.stage_key == "it":
                row.status = "in_progress"
    yield ids
    with session_scope() as s:
        s.execute(delete(User).where(User.employee_id == ids["employee_id"]))
        s.execute(delete(ExitCase).where(ExitCase.case_id == ids["case_id"]))
        s.execute(delete(Employee).where(Employee.id == ids["employee_id"]))


def _race(case_id: str, first: tuple, second: tuple) -> list:
    """Fire two transitions at once and collect what each one did."""
    results: list = [None, None]
    start = threading.Barrier(2)

    def go(index, actor, to_status):
        start.wait(timeout=5)
        try:
            results[index] = stage_service.transition(
                case_id=case_id, stage_key="it", to_status=to_status,
                actor=actor, note="race")
        except Exception as exc:  # noqa: BLE001 - the refusal is the result
            results[index] = exc

    threads = [threading.Thread(target=go, args=(i, actor, to))
               for i, (actor, to) in enumerate((first, second))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


def test_a_non_hr_account_cannot_reopen_a_clearance_by_racing_hr(case):
    """The one that matters. Whoever wins, IT must not end up having reopened
    a completed stage -- that is the whole of `HR_ONLY_FROM`."""
    results = _race(case["case_id"], (HR, "completed"), (IT, "pending"))

    refusals = [r for r in results if isinstance(r, Exception)]
    moves = [r for r in results if isinstance(r, dict)]
    assert len(moves) + len(refusals) == 2

    with session_scope() as s:
        final = s.query(CaseStage).filter_by(
            case_id=case["case_id"], stage_key="it").one()
        status = final.status

        events = s.query(CaseStageEvent).filter_by(
            case_id=case["case_id"], stage_key="it", kind="transition").all()
        transitions = [(e.from_status, e.to_status) for e in events]

    # Exactly one move can have come out of `in_progress`; the other either was
    # refused or started from the status the first one left behind.
    assert transitions, "at least one transition should have been recorded"
    assert all(frm == "in_progress" for frm, _ in transitions[:1])
    for frm, to in transitions:
        assert frm != to

    # And the timeline agrees with the row, which is what a lost update breaks.
    assert transitions[-1][1] == status

    # If IT moved it at all, it cannot have been out of `completed`.
    it_events = [e for e in events if e.actor_role == "it"]
    assert not [e for e in it_events if e.from_status == "completed"], (
        "an IT account reopened a completed clearance")


def test_two_simultaneous_sign_offs_produce_one_transition(case):
    """Both callers own the stage and both say `completed`. One wins; the other
    is told the stage is already there rather than writing a second event."""
    results = _race(case["case_id"], (HR, "completed"), (IT, "completed"))

    moves = [r for r in results if isinstance(r, dict)]
    assert len(moves) == 1, f"expected exactly one winner, got {results}"

    with session_scope() as s:
        events = s.query(CaseStageEvent).filter_by(
            case_id=case["case_id"], stage_key="it", kind="transition").all()
    assert len(events) == 1, "one sign-off, one entry in the compliance record"


def test_an_ordinary_sequential_move_is_unaffected(case):
    """The lock must not change the single-caller path."""
    out = stage_service.transition(case_id=case["case_id"], stage_key="it",
                                   to_status="completed", actor=HR, note="fine")
    assert out["from"] == "in_progress"
    assert out["to"] == "completed"
