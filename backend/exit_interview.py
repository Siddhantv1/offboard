"""The exit interview the leaver actually fills in.

`db.models.Interview` is not this. That table holds transcripts of interviews
that already happened somewhere else -- read-only reference data the
`interview_summarizer` and `interview_trends` agents mine. Nothing in the
product ever asked the person leaving a single question. This module is the
asking.

**The questions are a constant, not a generation.** An exit interview is a
longitudinal instrument: `interview_trends` compares departments and quarters,
and that comparison is meaningless if the wording drifts between two people.
So they live in `QUESTIONS` with stable ids, and an id is never reused for a
different question -- change the wording in place only when the thing being
asked is unchanged, and add a new id otherwise.

**One row per submission, holding a JSON map -- not one row per answer.**
Both shapes were on the table and the map won on three counts:

*Submission is atomic.* "Has this been submitted?" is a column on one row,
not an aggregate over N rows that a crash can leave half-written. The lock
that refuses edits after submit therefore has exactly one thing to check.

*Nothing queries an individual answer.* A per-question row earns its keep when
you filter, sort or aggregate on one -- `CaseStage` is per-row for exactly that
reason. Here every reader (the employee's page, the staff panel, and any agent
that later summarises this) wants the whole set at once, so per-question rows
would only ever be re-assembled into the map this stores directly.

*The question set is versioned in code, not in data.* With per-question rows,
retiring a question orphans rows whose `question_id` no longer resolves and
every read has to reconcile the two lists. With a map, an answer to a retired
question is simply a key nothing renders, and the record of what was said
survives intact.

The cost is that a partial write rewrites the whole map. At seven text answers
per case that is not a cost.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from .db import session_scope
from .db.models import ExitInterviewResponse

#: The exit interview, as a person would actually be asked it.
#:
#: `id` is the storage key and is permanent. `type` is "text" for prose and
#: "choice" for the two that are really yes/no -- those two exist as choices
#: because `interview_trends` needs something countable, and free text about
#: whether you would recommend the place cannot be counted.
#:
#: `required` marks what a *final submission* must contain. Saving progress
#: never enforces it: being half-finished is the normal state of a form
#: somebody is still filling in.
QUESTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "reason_for_leaving",
        "prompt": "What made you decide to leave?",
        "type": "text",
        "required": True,
        "help": "Please be truthful about your answers, as they do not affect your "
                "evaluation.",
    },
    {
        "id": "what_worked",
        "prompt": "What worked well here — what would you keep exactly as it is?",
        "type": "text",
        "required": True,
    },
    {
        "id": "what_did_not_work",
        "prompt": "What did not work?",
        "type": "text",
        "required": True,
        "help": "Be specific if you can. Specific is more useful than kind.",
    },
    {
        "id": "manager",
        "prompt": "How was working with your manager — what did they do well, and "
                  "where did you need more from them?",
        "type": "text",
        "required": True,
    },
    {
        "id": "would_recommend",
        "prompt": "Would you recommend this company to someone looking for a job?",
        "type": "choice",
        "options": ("yes", "no", "unsure"),
        "required": True,
    },
    {
        "id": "would_return",
        "prompt": "Would you come back, if the right role/opportunity came up?",
        "type": "choice",
        "options": ("yes", "no", "unsure"),
        "required": True,
    },
    {
        "id": "anything_else",
        "prompt": "Anything else you want on the record?",
        "type": "text",
        "required": False,
        "help": "Optional. This is the one nobody asks and people most want to answer.",
    },
)

_BY_ID = {q["id"]: q for q in QUESTIONS}

#: A single answer long enough to say anything and short enough that a paste of
#: a whole mailbox does not land in JSONB. Refused rather than truncated: a
#: silently clipped answer is a misquote of the person who wrote it.
MAX_ANSWER_CHARS = 4000


class InterviewLocked(Exception):
    """The interview is submitted, so it can no longer be edited."""


def _public_questions() -> list[dict[str, Any]]:
    return [
        {
            "id": q["id"],
            "prompt": q["prompt"],
            "type": q["type"],
            "required": q["required"],
            "options": list(q["options"]) if q.get("options") else None,
            "help": q.get("help"),
        }
        for q in QUESTIONS
    ]


def _row(session, case_id: str) -> ExitInterviewResponse | None:
    return session.scalar(
        select(ExitInterviewResponse).where(ExitInterviewResponse.case_id == case_id)
    )


def _state(case_id: str, row: ExitInterviewResponse | None) -> dict[str, Any]:
    answers = dict(row.answers or {}) if row else {}
    return {
        "case_id": case_id,
        "questions": _public_questions(),
        "answers": answers,
        "submitted": bool(row and row.submitted_at),
        "submitted_at": row.submitted_at if row else None,
        "updated_at": row.updated_at if row else None,
        # What a final submit is still missing, so the page can say so before
        # the server refuses rather than after.
        "outstanding": [q["id"] for q in QUESTIONS
                        if q["required"] and not str(answers.get(q["id"]) or "").strip()],
    }


def state_for(case_id: str) -> dict[str, Any]:
    """The questions, whatever has been answered so far, and whether it is in."""
    with session_scope() as session:
        return _state(case_id, _row(session, case_id))


def clean_answers(raw: Any) -> dict[str, str]:
    """Validate a client's answer map, or raise ValueError.

    Unknown keys are refused rather than ignored. Ignoring them would let a
    caller write arbitrary JSON into the column and have the write report
    success, which is how a typo'd question id becomes an answer nobody ever
    sees again.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("`answers` must be an object of question id to answer.")

    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        question = _BY_ID.get(str(key))
        if question is None:
            raise ValueError(f"'{key}' is not one of the exit interview questions.")
        if value is None:
            value = ""
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"The answer to '{key}' must be text.")
        text = str(value).strip()
        if len(text) > MAX_ANSWER_CHARS:
            raise ValueError(
                f"That answer is too long (max {MAX_ANSWER_CHARS} characters)."
            )
        if question["type"] == "choice" and text and text not in question["options"]:
            raise ValueError(
                f"'{text}' is not an answer to that question. Choose one of: "
                + ", ".join(question["options"]) + "."
            )
        cleaned[question["id"]] = text
    return cleaned


def save(*, case_id: str, employee_id: str | None, answers: Any,
         submitted: bool) -> dict[str, Any]:
    """Save progress, and optionally submit.

    Saving merges rather than replaces, so a page that sends only the field
    that changed does not blank the other six.

    Submitting is one-way. An exit interview that can be revised after the fact
    is not a record of what the person said on their way out, and HR reads
    these to act on them -- a manager criticised on Monday and exonerated on
    Friday, with no trace of the first version, is worse than no interview.
    """
    incoming = clean_answers(answers)
    now = datetime.now(timezone.utc)

    with session_scope() as session:
        row = _row(session, case_id)
        if row is not None and row.submitted_at:
            raise InterviewLocked(
                "Your exit interview has already been submitted, so it cannot be "
                "changed. Talk to HR if something in it is wrong."
            )

        if row is None:
            row = ExitInterviewResponse(
                case_id=case_id, employee_id=employee_id, answers={})
            session.add(row)

        merged = {**(row.answers or {}), **incoming}

        if submitted:
            missing = [q["prompt"] for q in QUESTIONS
                       if q["required"] and not str(merged.get(q["id"]) or "").strip()]
            if missing:
                raise ValueError(
                    "These still need an answer before you can submit: "
                    + "; ".join(missing)
                )
            row.submitted_at = now

        # Reassigned rather than mutated: SQLAlchemy does not track in-place
        # edits to a JSON column, so `row.answers[k] = v` would commit nothing.
        row.answers = merged
        row.updated_at = now
        session.flush()
        return _state(case_id, row)


def submitted_for_case(case_id: str) -> dict[str, Any]:
    """The staff-side read: the interview only once the employee has sent it.

    A draft is deliberately not exposed. Half an answer to "what did not work"
    is not something the person has chosen to say, and HR reading it before
    they finish would make the box unsafe to think in -- which is the only
    thing that makes an exit interview worth running.
    """
    with session_scope() as session:
        row = _row(session, case_id)
        submitted = bool(row and row.submitted_at)
        answers = dict(row.answers or {}) if submitted else {}
        return {
            "case_id": case_id,
            "submitted": submitted,
            "submitted_at": row.submitted_at if submitted else None,
            "responses": [
                {
                    "id": q["id"],
                    "prompt": q["prompt"],
                    "type": q["type"],
                    "answer": answers.get(q["id"], ""),
                }
                for q in QUESTIONS
            ] if submitted else [],
        }
