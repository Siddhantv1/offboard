"""Offboarding policy constants and the pure predicates built on them.

Deliberately dependency-light: this module imports only `db.models` (for the
vocabulary constants) and the standard library. That matters because both the
*read* path (19 agents) and the *write* path (the stage-transition API) need
these rules, and the write path must not have to import the agent registry to
get them.

`SLA_DAYS` used to live in `backend/agents/_context.py`, which re-exports it
for backwards compatibility. Importing it from there pulls in
`backend/agents/__init__.py` -- all 19 agent modules plus the
`end_to_end_automation -> orchestration -> graph -> agents` import cycle -- to
read one integer. New code should import from here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .db.models import STAGE_KEY_BY_NAME, STAGE_KEYS, STAGE_STATUSES  # noqa: F401

# Days a clearance stage can sit open before it counts as overdue. One knob
# shared by the dashboard, workflow optimizer, escalation and policy agents,
# instead of each redefining its own copy of the same number.
#
# The comparison is strict `>` everywhere it is used, so a stage open exactly
# SLA_DAYS days is *not* a breach. All five consumers agree on this; do not
# change one of them to `>=` without changing the rest.
SLA_DAYS = 5

#: Which role owns each stage type. HR is additionally a blanket override --
#: see `may_act_on_stage` -- because it is the escalation path when the stage
#: owner is on leave and the last working day will not move.
STAGE_OWNERS: dict[str, frozenset[str]] = {
    "it": frozenset({"it"}),
    "finance": frozenset({"hr"}),
    "hr": frozenset({"hr"}),
    "kt": frozenset({"manager"}),
    "manager": frozenset({"manager"}),
}

#: Which agents each role may invoke. Replaces the old
#: `EMPLOYEE_AGENTS = {"faq_chatbot"}`. `"*"` means the whole registry.
#:
#: Read by BOTH the catalogue route and the run route, so what a role can see
#: and what it can run cannot drift apart.
ROLE_AGENTS: dict[str, object] = {
    "hr": "*",
    "it": frozenset({
        "it_deprovisioning", "multi_system_clearance", "compliance", "faq_chatbot",
    }),
    "manager": frozenset({
        "kt_reviewer", "risk_assessment", "checklist", "faq_chatbot",
    }),
    "employee": frozenset({"faq_chatbot"}),
}


def stage_key_for_name(name: str | None) -> str | None:
    """`CaseStage.name` -> stage_key, or None for a name outside the canonical
    five. None degrades safely: it matches no role, so only HR can act on it."""
    return STAGE_KEY_BY_NAME.get((name or "").strip())


def may_act_on_stage(role: str, stage_key: str | None) -> bool:
    """Can this role transition a stage of this type?

    HR may act on anything. That is a real grant, not an oversight, so the
    transition handler records it as an escalation in `case_stage_events` when
    HR acts on a stage HR does not own -- otherwise the audit trail cannot
    answer "did the stage owner actually clear this?".
    """
    if role == "hr":
        return True
    if stage_key is None:
        return False
    return role in STAGE_OWNERS.get(stage_key, frozenset())


def may_run_agent(role: str, agent_id: str) -> bool:
    allowed = ROLE_AGENTS.get(role, frozenset())
    return allowed == "*" or agent_id in allowed


def effective_days_open(stage: Mapping[str, Any], *, now: datetime | None = None) -> int:
    """How long this stage has really been open.

    `days_open` is a stored integer that nothing increments -- the only two
    writers are `data_store._insert_case` (which sets 0) and the seeder (which
    copies the corpus verbatim). So every case created through the product
    would sit at 0 forever and could never breach SLA, while the hand-authored
    corpus carries real numbers.

    Taking the max of the two keeps the seeded values authoritative (so the
    golden snapshot and every agent see exactly what they see today) while
    letting new cases age for real off `status_changed_at`.
    """
    try:
        stored = int(stage.get("days_open") or 0)
    except (TypeError, ValueError):
        stored = 0

    changed_at = stage.get("status_changed_at")
    if not isinstance(changed_at, datetime):
        return stored

    if changed_at.tzinfo is None:
        changed_at = changed_at.replace(tzinfo=timezone.utc)
    elapsed = ((now or datetime.now(timezone.utc)) - changed_at).days
    return max(stored, max(0, elapsed))


def is_breaching(status: str | None, days: int) -> bool:
    """The SLA rule, in one place.

    Matches `dashboard_insights.py:50`, `sla_escalation.py:81`,
    `workflow_optimizer.py:79`, `policy_auditor.py:48` and
    `end_to_end_automation.py:121` exactly: anything not `completed`, open
    strictly longer than the threshold.
    """
    return str(status or "").lower() != "completed" and days > SLA_DAYS
