"""The exit checklist has to tick things off, and the systems have to notice.

Two failures reported together, and they are the same shape: the employee does
the thing, and nothing anywhere changes.

1. The checklist is one stored `agent_runs` row. Read back verbatim it says
   the same words a fortnight later, after the laptop is back and AWS is gone.
2. `hrms_status` decided whether an exit interview had happened by looking in
   the read-only transcript corpus -- from before the product could ask anyone.
   So a leaver could answer all seven questions and every agent reading system
   state still reported the interview as not done.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from backend import checklist_progress, exit_interview
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import Employee, ExitCase, ExitInterviewResponse, User
from backend.systems import hrms_status


@pytest.fixture
def case():
    email = f"pytest-prog-{uuid.uuid4().hex[:8]}@example.com"
    emp, exit_case, _ = store.add_employee({
        "name": "Pytest Progress Probe", "role": "Engineer",
        "department": "Engineering", "email": email, "status": "exiting",
    })
    case_id = str(exit_case.meta["case_id"])
    yield case_id
    with session_scope() as s:
        s.execute(delete(ExitInterviewResponse).where(
            ExitInterviewResponse.case_id == case_id))
        s.execute(delete(User).where(User.email == email))
        s.execute(delete(ExitCase).where(ExitCase.case_id == case_id))
        s.execute(delete(Employee).where(Employee.id == emp.meta["id"]))


GENERATED = {
    "employee": "Pytest Progress Probe",
    "access_to_revoke": [{"system": "AWS", "priority": "high"}],
    "assets_to_return": [{"item": "MacBook Pro"}],
    "kt_topics": [{"topic": "Deploy runbook"}],
    "other_tasks": [{"task": "Complete exit interview"},
                    {"task": "Return the parking pass"}],
}


def _by_task(annotated, text):
    return next(i for i in annotated["other_tasks"] if text in i["task"].lower())


# -- the interview the product actually collected -----------------------------
def test_submitting_the_interview_is_visible_to_the_systems(case):
    """`hrms_status` feeds multi_system_clearance and compliance, and through
    them the orchestrator's verdict. Reading the wrong table meant the whole
    chain believed the interview had not happened."""
    before = hrms_status(case)["exit_interview_completed"]

    answers = {q["id"]: (q["options"][0] if q["type"] == "choice" else "said")
               for q in exit_interview.QUESTIONS if q["required"]}
    exit_interview.save(case_id=case, employee_id=None, answers=answers, submitted=True)

    assert exit_interview.was_submitted(case) is True
    assert hrms_status(case)["exit_interview_completed"] is True
    assert before is False, "the probe case already counted as interviewed"


def test_a_draft_does_not_count_as_an_interview(case):
    first = exit_interview.QUESTIONS[0]["id"]
    exit_interview.save(case_id=case, employee_id=None,
                        answers={first: "half a thought"}, submitted=False)
    assert exit_interview.was_submitted(case) is False
    assert hrms_status(case)["exit_interview_completed"] is False


# -- the checklist overlay ----------------------------------------------------
def test_the_interview_task_ticks_itself_off(case):
    before = checklist_progress.annotate(GENERATED, case)
    assert _by_task(before, "exit interview")["done"] is False

    answers = {q["id"]: (q["options"][0] if q["type"] == "choice" else "said")
               for q in exit_interview.QUESTIONS if q["required"]}
    exit_interview.save(case_id=case, employee_id=None, answers=answers, submitted=True)

    after = checklist_progress.annotate(GENERATED, case)
    assert _by_task(after, "exit interview")["done"] is True
    assert _by_task(after, "exit interview")["evidence"]


def test_a_task_nothing_tracks_is_unknown_not_outstanding(case):
    """Tri-state on purpose. Showing "not done" for something no system watches
    would claim the page is tracking it, and it is not."""
    annotated = checklist_progress.annotate(GENERATED, case)
    assert _by_task(annotated, "parking pass")["done"] is None


def test_nothing_is_ticked_without_evidence(case):
    annotated = checklist_progress.annotate(GENERATED, case)
    for group in ("access_to_revoke", "assets_to_return", "kt_topics", "other_tasks"):
        for item in annotated[group]:
            if item.get("done"):
                assert item.get("evidence"), f"{group} ticked with nothing to show for it"


def test_the_generated_wording_is_never_rewritten(case):
    """Annotated, not regenerated. A fresh LLM call would reword every line, so
    anything the reader had mentally ticked off moves or vanishes."""
    annotated = checklist_progress.annotate(GENERATED, case)
    assert annotated["access_to_revoke"][0]["system"] == "AWS"
    assert annotated["assets_to_return"][0]["item"] == "MacBook Pro"
    assert annotated["kt_topics"][0]["topic"] == "Deploy runbook"


def test_progress_counts_only_what_is_actually_tracked(case):
    annotated = checklist_progress.annotate(GENERATED, case)
    tracked = annotated["progress"]["tracked"]
    # 1 access + 1 asset + 1 kt + 1 interview task = 4; the parking pass is not.
    assert tracked == 4
    assert annotated["progress"]["done"] <= tracked


def test_an_empty_or_unknown_result_degrades_instead_of_raising(case):
    """A stored run from a bad model response must not cost the reader the
    page. An un-annotated checklist is degraded; a 500 is useless."""
    empty = checklist_progress.annotate({}, case)
    assert empty["progress"] == {"tracked": 0, "done": 0}
    assert empty["access_to_revoke"] == []

    # No such case: nothing to annotate against, so it comes back as written.
    assert checklist_progress.annotate(GENERATED, "no-such-case") == GENERATED
    assert checklist_progress.annotate(GENERATED, "") == GENERATED
