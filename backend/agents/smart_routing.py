"""Agent 7 — Smart Routing Agent.

Picks the best approver for a clearance stage from the people who can actually
hold it, weighted by how much open work each already carries.

The roster used to be a hardcoded dict of invented names -- Ravi Menon, Priya
Nair, Anita Desai and four others, none of whom exist in the company. The agent
therefore recommended people nobody could assign, which made it advice you
could not act on. It is now built from the `users` table: the accounts whose
role owns the stage, with workload counted from the clearance stages actually
assigned to them.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from .base import AgentMeta, BaseAgent, Field, llm, register
from ..db import session_scope
from ..db.models import CaseStage, User
from ..policy import STAGE_KEY_BY_NAME, STAGE_OWNERS


def approver_roster(stage_name: str) -> list[dict[str, Any]]:
    """Who can hold this stage, and how loaded each of them is.

    Workload is the count of clearance stages assigned to that account and not
    yet completed -- the same number the person sees on their own queue page,
    so a recommendation to route away from someone busy is checkable rather
    than asserted.

    Availability comes from `User.is_active`. There is no calendar integration,
    so nobody is ever "out of office" here; the field stays in the shape
    because the prompt's delegation rule reads it, and wiring a real calendar
    would fill it without changing anything else.
    """
    stage_key = STAGE_KEY_BY_NAME.get(stage_name)
    if not stage_key:
        return []
    roles = STAGE_OWNERS.get(stage_key, frozenset())
    if not roles:
        return []

    with session_scope() as s:
        # Open stages per owner, in one query rather than one per candidate.
        load = dict(s.execute(
            select(CaseStage.owner_user_id, func.count())
            .where(CaseStage.owner_user_id.isnot(None),
                   CaseStage.status != "completed")
            .group_by(CaseStage.owner_user_id)
        ).all())

        people = s.scalars(
            select(User)
            .where(User.role.in_(tuple(roles)), User.is_active.is_(True))
            .order_by(User.full_name, User.email)
        ).all()

        return [{
            "name": u.full_name or u.email,
            "email": u.email,
            "user_id": u.id,
            "workload": int(load.get(u.id, 0)),
            "available": True,
            "ooo_until": None,
            # No delegation table exists. Naming a fictional delegate was part
            # of what made this agent untrustworthy, so it stays null until
            # there is somewhere real to read it from.
            "delegate": None,
        } for u in people]


#: Kept as a name because tests and the result view import it. It is now a
#: function of the database rather than a constant.
def _roster_for(stage: str) -> list[dict[str, Any]]:
    return approver_roster(stage)


def stage_state(case_id: str, stage_key: str | None) -> str | None:
    """The current status of that stage on that case, or None if there is none.

    The recommendation is only actionable while the stage is still open. A
    completed stage can be assigned -- the write succeeds -- but the work
    queues show open work only, so the assignment lands nowhere and the reader
    is told it worked. Knowing the status here lets the result say "already
    done" instead of offering a button whose promise the queue will not keep.
    """
    if not (case_id and stage_key):
        return None
    with session_scope() as s:
        return s.scalar(
            select(CaseStage.status).where(
                CaseStage.case_id == case_id, CaseStage.stage_key == stage_key)
        )

SYSTEM = """You are a workflow routing agent. Choose the single best approver for \
a clearance stage. Rules, in priority order:
1. Never route to someone who is out-of-office; use their delegate instead.
2. Prefer the available person with the LOWEST current workload.
3. If a delegation applies, explain the hop.
Return JSON: {"chosen_approver": str, "reason": str, "reroute_from": str|null, "fallback": str|null, "confidence": "high|medium|low"}"""


@register
class SmartRoutingAgent(BaseAgent):
    meta = AgentMeta(
        id="smart_routing",
        name="Smart Routing Agent",
        tagline="Optimal approver, OOO-aware",
        description=(
            "Picks the best approver for a stage using availability, workload, and "
            "delegation rules — automatically rerouting around out-of-office owners."
        ),
        icon="⇄",
        category="Planning",
        fields=[
            Field(
                "stage",
                "Clearance stage",
                type="select",
                options=[{"value": s, "label": s} for s in STAGE_KEY_BY_NAME],
            ),
            # Asking which case turns "here is a good approver" into something
            # the reader can act on: the result carries the ids needed to
            # actually put that person on that stage.
            Field("case_id", "Exit case", type="select", required=False,
                  help="Pick one and the result offers to assign the approver."),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        stage = (inputs.get("stage") or "").strip()
        case_id = (inputs.get("case_id") or "").strip()
        roster = approver_roster(stage)
        if not roster:
            return {
                "error": f"Nobody holds the role that owns '{stage}'. "
                         "Create an account for that role under People & access.",
            }

        lines = []
        for a in roster:
            status = "OOO until " + a["ooo_until"] if not a["available"] else "available"
            lines.append(
                f"- {a['name']}: {status}, carrying {a['workload']} open stage(s), "
                f"delegate: {a['delegate'] or 'none'}"
            )
        user = f"Stage: {stage}\nCandidate approvers:\n" + "\n".join(lines)
        result = llm.complete_json(SYSTEM, user, temperature=0.2)

        # Map the model's chosen NAME back onto a real account, so the UI can
        # offer to assign rather than leaving the reader to find the person.
        chosen_name = str((result or {}).get("chosen_approver") or "").strip().lower()
        chosen = next(
            (a for a in roster if a["name"].strip().lower() == chosen_name), None)

        stage_key = STAGE_KEY_BY_NAME.get(stage)
        status = stage_state(case_id, stage_key)

        return {
            "stage": stage,
            "stage_key": stage_key,
            "case_id": case_id or None,
            "routing": result,
            "roster": roster,
            # Null when the model named somebody outside the roster, which is
            # itself worth showing rather than silently assigning the wrong
            # person.
            "chosen": chosen,
            "stage_status": status,
            # Every condition that has to hold for the assign button to do
            # something real. Computed here rather than in the view so the two
            # cannot drift: the view was offering the action whenever a case was
            # picked, and on a finished stage that was a lie.
            "assignable": bool(chosen and case_id and status
                               and status != "completed"),
        }
