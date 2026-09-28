"""Finishing an exit without the leaver's exit interview, and picking one.

Two reported problems with the same root: the product collects an exit
interview and then does nothing with the fact that it has one.

An employee who was already in the seeded corpus and has since answered the
form has two interview records — the corpus stand-in and their real answers —
and the reviewer's dropdown offered both, labelled with the same name. Half the
time you summarise the stand-in.

And nothing stopped the exit being closed without an interview at all. The
relieving letter is released the moment no stage is outstanding
(`issued_documents.readiness`), so "we'll chase it afterwards" means never.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from backend import auth, exit_interview
from backend import stages as stage_service
from backend.api.options import interview_options
from backend.auth import Identity
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import Employee, ExitCase, ExitInterviewResponse, User
from backend.scoping import identity_for

HR = Identity(user_id=None, email="hr@pytest.test", role="hr", employee_id=None,
              full_name="Pytest HR", must_change_password=False)


def _full_answers() -> dict[str, str]:
    """Every question answered. `save(submitted=True)` refuses a partial one,
    which is the interview's own rule and not what these tests are about."""
    return {q["id"]: (q["options"][0] if q.get("options") else "Yes, fine.")
            for q in exit_interview.QUESTIONS}


@pytest.fixture
def doubled_up():
    """A corpus employee who has also answered the form — the state four people
    are in on the live data, and which the test database does not seed."""
    corpus = next((d for d in store.interviews() if d.meta.get("employee_id")), None)
    if corpus is None:
        pytest.skip("no seeded interview corpus")
    employee_id = str(corpus.meta["employee_id"])
    case = store.exit_case_for_employee(employee_id)
    if case is None:
        pytest.skip("that corpus interview's employee has no exit case")
    case_id = str(case.meta["case_id"])

    exit_interview.save(case_id=case_id, employee_id=employee_id,
                        answers=_full_answers(), submitted=True)
    yield employee_id
    with session_scope() as s:
        s.execute(delete(ExitInterviewResponse).where(
            ExitInterviewResponse.case_id == case_id))


@pytest.fixture
def case():
    """A throwaway exiting employee, their case, and no exit interview."""
    suffix = uuid.uuid4().hex[:8]
    emp, exit_case, _ = store.add_employee({
        "name": "Pytest Interview Gate", "role": "Engineer",
        "department": "Engineering", "email": f"pytest-ig-{suffix}@example.com",
        "status": "exiting",
    })
    ids = {"case_id": str(exit_case.meta["case_id"]),
           "employee_id": str(emp.meta["id"])}
    yield ids
    with session_scope() as s:
        s.execute(delete(ExitInterviewResponse).where(
            ExitInterviewResponse.case_id == ids["case_id"]))
        s.execute(delete(User).where(User.employee_id == ids["employee_id"]))
        s.execute(delete(ExitCase).where(ExitCase.case_id == ids["case_id"]))
        s.execute(delete(Employee).where(Employee.id == ids["employee_id"]))


def _complete_all_but_one(case_id: str) -> str:
    """Finish four of the five stages directly, returning the key of the last.

    Written against the rows rather than through `transition`, because the
    point of the test is the final move and the first four are setup.
    """
    with session_scope() as s:
        rows = sorted(s.get(ExitCase, case_id).stages, key=lambda x: x.ordinal)
        for row in rows[:-1]:
            row.status = "completed"
        return rows[-1].stage_key


# -- the gate -----------------------------------------------------------------
def test_the_exit_cannot_be_finished_without_the_interview(case):
    last = _complete_all_but_one(case["case_id"])
    assert not exit_interview.was_submitted(case["case_id"])

    with pytest.raises(stage_service.TransitionError) as caught:
        stage_service.transition(case_id=case["case_id"], stage_key=last,
                                 to_status="completed", actor=HR)
    message = str(caught.value)
    assert "exit interview" in message
    assert "relieving letter" in message


def test_the_gate_is_on_the_last_stage_not_a_named_one(case):
    """Gating HR Clearance alone would let the case be finished around it —
    four teams work these in whatever order suits them."""
    with session_scope() as s:
        rows = sorted(s.get(ExitCase, case["case_id"]).stages, key=lambda x: x.ordinal)
        # Three of five done: finishing a fourth does not finish the exit.
        for row in rows[:3]:
            row.status = "completed"
        fourth = rows[3].stage_key

    out = stage_service.transition(case_id=case["case_id"], stage_key=fourth,
                                   to_status="completed", actor=HR)
    assert out["to"] == "completed"

    # ...and now the fifth is refused, whichever stage it happens to be.
    with pytest.raises(stage_service.TransitionError):
        stage_service.transition(case_id=case["case_id"], stage_key=rows[4].stage_key,
                                 to_status="completed", actor=HR)


def test_a_submitted_interview_lets_the_exit_close(case):
    last = _complete_all_but_one(case["case_id"])
    exit_interview.save(case_id=case["case_id"], employee_id=case["employee_id"],
                        answers=_full_answers(), submitted=True)
    assert exit_interview.was_submitted(case["case_id"])

    out = stage_service.transition(case_id=case["case_id"], stage_key=last,
                                   to_status="completed", actor=HR)
    assert out["to"] == "completed"


def test_a_note_closes_it_without_one_and_the_reason_is_recorded(case):
    """Not a hard block, deliberately. The same lesson `transition`'s IT gate
    records: a leaver who has already gone, or who never logged in, must not be
    able to freeze their own case forever. It costs a sentence instead."""
    last = _complete_all_but_one(case["case_id"])

    out = stage_service.transition(
        case_id=case["case_id"], stage_key=last, to_status="completed", actor=HR,
        note="Left mid-notice and is not contactable; closing without it.")
    assert out["to"] == "completed"

    trail = stage_service.timeline(case["case_id"])
    assert any("not contactable" in (e.get("note") or "") for e in trail)


def test_reopening_and_other_moves_are_untouched(case):
    """The gate is on `completed` only — it must not block starting a stage or
    sending one back."""
    with session_scope() as s:
        rows = sorted(s.get(ExitCase, case["case_id"]).stages, key=lambda x: x.ordinal)
        for row in rows[:-1]:
            row.status = "completed"
        last = rows[-1].stage_key

    out = stage_service.transition(case_id=case["case_id"], stage_key=last,
                                   to_status="in_progress", actor=HR)
    assert out["to"] == "in_progress"


# -- the dropdown -------------------------------------------------------------
def test_one_interview_per_person_and_it_is_the_one_they_filled_in(doubled_up):
    """An employee who was already in the corpus and has since answered the
    form has two records. The picker offered both under the same name."""
    options = interview_options(HR)
    values = [o["value"] for o in options]

    assert values, "expected some interviews in the corpus"
    assert len(values) == len(set(values))

    by_person = {}
    for doc in store.all_interviews():
        by_person.setdefault(str(doc.meta.get("employee_id") or ""), []).append(doc)
    doubled = [emp for emp, docs in by_person.items() if emp and len(docs) > 1]
    assert doubled_up in doubled, "the fixture should have produced both kinds"

    for emp in doubled:
        offered = [v for v in values
                   if v in {str(d.meta.get("interview_id")) for d in by_person[emp]}]
        assert len(offered) == 1, f"{emp} is offered {offered}"
        chosen = next(d for d in by_person[emp]
                      if str(d.meta.get("interview_id")) == offered[0])
        assert chosen.meta.get("source") == "portal"


def test_every_offered_interview_can_be_opened():
    """The failure `store.interview()` already carries a comment about: offer a
    record in a select and then refuse to resolve it."""
    for option in interview_options(HR):
        assert store.interview(option["value"]) is not None, option["value"]


def test_the_union_is_untouched_so_trends_still_see_the_corpus(doubled_up):
    """`interview_trends` compares this quarter against the historical corpus;
    narrowing `all_interviews()` would have silently changed what it measures."""
    assert len(store.all_interviews()) > len(interview_options(HR))


def test_a_manager_is_only_offered_their_own_reports():
    with session_scope() as s:
        managers = s.query(User).filter(User.role == "manager").all()
        identities = [identity_for(m) for m in managers]

    for identity in identities:
        allowed = {str(e.meta.get("id")) for e in _visible(identity)}
        for option in interview_options(identity):
            doc = store.interview(option["value"])
            assert str(doc.meta.get("employee_id") or "") in allowed


def _visible(identity):
    from backend.api.deps import visible_employees

    return visible_employees(identity)
