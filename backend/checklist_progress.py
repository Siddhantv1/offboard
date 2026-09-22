"""Overlay what has actually happened onto a generated exit checklist.

The checklist is produced once by an LLM and then stored as an `agent_runs`
row. Read back verbatim it is a photograph: it says "return the laptop, revoke
AWS, hand over the deploy runbook" on the day it was generated and says exactly
the same thing a fortnight later, after the laptop is back and AWS is gone.
A to-do list that never ticks anything off is a document, not a checklist, and
people stop opening it.

Regenerating it instead would be worse: a fresh LLM call produces differently
worded items, so yesterday's list and today's cannot be compared and anything
the reader had mentally ticked off moves or disappears.

So the generated text is kept as written and *annotated* at read time from the
systems that actually know:

    access_to_revoke  -> executed deprovisioning actions (`systems._revoked`)
    assets_to_return  -> IT asset management (`systems.asset_status`)
    kt_topics         -> the Knowledge Transfer stage
    other_tasks       -> only the exit interview, which we track exactly

Every annotation carries `evidence` saying which system said so. An item marked
done with no evidence would be a second thing to distrust, and the point of
this is to be checkable.
"""
from __future__ import annotations

from typing import Any

from .data_store import store
from .exit_interview import was_submitted
from .systems import _revoked, asset_status, executed_actions

#: Words in an `other_tasks` entry that mean the exit interview. Deliberately
#: narrow: a wrong tick is worse than no tick, because it tells someone a thing
#: is handled when it is not.
_INTERVIEW_WORDS = ("exit interview", "offboarding interview", "exit survey")


def _matches_interview(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in _INTERVIEW_WORDS)


def _tick(item: dict[str, Any], done: bool, evidence: str) -> dict[str, Any]:
    """`done` is tri-state on purpose. None means nothing authoritative covers
    this line -- which reads differently from "not done yet", and stops the
    page implying it is tracking something it is not."""
    return {**item, "done": done, "evidence": evidence if done else None}


def annotate(checklist: dict[str, Any], case_id: str) -> dict[str, Any]:
    """Return the checklist with each item marked against real state."""
    if not isinstance(checklist, dict) or not case_id:
        return checklist

    case = store.exit_case(case_id)
    if not case:
        return checklist
    employee_id = str(case.meta.get("employee_id") or "")

    stages = {str(s.get("name")): str(s.get("status", "")).lower()
              for s in (case.meta.get("stages") or []) if isinstance(s, dict)}
    kt_done = stages.get("Knowledge Transfer") == "completed"

    executed = executed_actions(employee_id)
    try:
        returned = {str(a["asset"]).lower()
                    for a in asset_status(case_id).get("assets", [])
                    if a.get("returned")}
    except Exception:                                   # noqa: BLE001
        # A connector fault must not cost the reader their checklist. An
        # un-annotated list is degraded; a 500 is useless.
        returned = set()

    out = dict(checklist)

    out["access_to_revoke"] = [
        _tick(i, bool(_revoked(employee_id, str(i.get("system") or ""), executed)),
              "IT confirmed this was revoked")
        for i in (checklist.get("access_to_revoke") or []) if isinstance(i, dict)
    ]

    out["assets_to_return"] = [
        _tick(i, str(i.get("item") or "").lower() in returned,
              "IT asset management has it back")
        for i in (checklist.get("assets_to_return") or []) if isinstance(i, dict)
    ]

    # Per-topic coverage is the KT reviewer's job, not this one's. All we can
    # say here is whether the handover as a whole has been accepted.
    out["kt_topics"] = [
        _tick(i, kt_done, "your manager signed off the handover")
        for i in (checklist.get("kt_topics") or []) if isinstance(i, dict)
    ]

    interview_done = was_submitted(case_id)
    out["other_tasks"] = [
        _tick(i, interview_done, "you submitted it")
        if _matches_interview(str(i.get("task") or ""))
        else {**i, "done": None, "evidence": None}
        for i in (checklist.get("other_tasks") or []) if isinstance(i, dict)
    ]

    tracked = [i for group in ("access_to_revoke", "assets_to_return",
                               "kt_topics", "other_tasks")
               for i in out.get(group, []) if i.get("done") is not None]
    out["progress"] = {
        "tracked": len(tracked),
        "done": sum(1 for i in tracked if i["done"]),
    }
    return out
