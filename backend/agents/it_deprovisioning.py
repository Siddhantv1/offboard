"""Agent 14 — Automated IT Deprovisioning Agent (human-in-the-loop).

Two phases, deliberately separated:
  1. `plan(...)`   -- the LLM turns the employee's access/asset footprint plus the
                      IT deprovisioning policy into an ordered plan: accounts to
                      disable, access to revoke, data to archive, credentials to
                      rotate. Nothing is executed.
  2. `execute(...)` -- after a human approves, each planned action is run
                      through the (mock) system connectors and an audit log with
                      timestamps comes back. No LLM involved, so what was
                      approved is exactly what runs.

Standalone: `run()` returns the plan; the UI's Approve button posts it to
`POST /api/agents/it_deprovisioning/execute`. In the orchestrator the same
plan/execute pair straddles a LangGraph interrupt (the approval gate).
"""
from __future__ import annotations

from typing import Any
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select, update

from .base import AgentMeta, BaseAgent, Field, llm, register
from ._context import doc_to_context, require_employee
from ..data_store import store
from ..db import session_scope
from ..db.models import DeprovisioningPlan
from ..systems import execute_deprovisioning_action

SYSTEM = """You are an IT security engineer preparing a deprovisioning plan for a \
separating employee. Use the employee's actual access systems and assets and \
the IT Deprovisioning policy excerpt. Sequence privileged/production access \
first. For terminations everything is immediate; for resignations schedule to \
complete by the last working day. Do NOT execute anything -- this is a plan \
for human approval.

Return JSON:
{
  "summary": "1-2 sentences",
  "urgency": "immediate|by_last_working_day",
  "actions": [
    {"step": int, "type": "disable_account|revoke_access|rotate_credential|archive_data|recover_asset",
     "target": str, "detail": str, "owner": str, "when": str, "privileged": bool}
  ],
  "dependencies": [str],
  "risks_if_delayed": [str],
  "requires_approval": true
}"""

POLICY_EXCERPT = """IT Deprovisioning & Access Revocation Policy (pol-it-002):
- Resignations: revocation completes by end of last working day. Terminations: immediately.
- Privileged/production access (AWS admin, CI/CD secrets, database root, PagerDuty) within 4 hours.
- Checklist: Okta/SSO disable + revoke sessions; GitHub org removal + rotate deploy keys;
  AWS IAM removal + rotate keys; business apps (Salesforce, SAP, Workday, Jira, Confluence);
  VPN cert revocation + MFA/YubiKey deactivation; email -> shared mailbox 30 days then archive.
- Any shared secret / service-account password / API key known to the employee rotated within 24h.
- Hardware returned and logged; endpoints wiped after backup confirmation.
- Where blocked by incomplete Knowledge Transfer, record the dependency and escalate."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def plan_deprovisioning(employee_id: str, *, exit_type: str = "", last_working_day: str = "",
                        case_notes: str = "", case_id: str = "") -> dict[str, Any]:
    """Phase 1: build the plan (LLM) and persist it server-side.

    Raises LLMError on gateway failure. Persisting the plan -- rather than
    trusting whatever the client echoes back at execute time -- is what lets
    `execute_plan()` take a `plan_id` instead of an arbitrary client-supplied
    plan body.
    """
    emp = require_employee(employee_id)
    if isinstance(emp, dict):
        return emp
    user = (
        f"{POLICY_EXCERPT}\n\n"
        f"Exit type: {exit_type or 'unknown'}\nLast working day: {last_working_day or 'unknown'}\n"
        + (f"Case notes:\n{case_notes}\n" if case_notes else "")
        + f"\nEmployee record:\n{doc_to_context(emp)}\n\nProduce the deprovisioning plan."
    )
    plan = llm.complete_json(SYSTEM, user)
    if isinstance(plan, dict) and not plan.get("error"):
        plan_id = f"dep-{uuid4().hex[:8]}"
        emp_id = str(emp.meta.get("id", employee_id))
        plan["employee_id"] = emp_id
        plan["employee"] = emp.meta.get("name", emp.title)
        plan["case_id"] = case_id or None
        plan["plan_id"] = plan_id
        plan["requires_approval"] = True
        with session_scope() as s:
            # One plan awaiting authorisation per person. Every run used to
            # insert another, so the orchestrator and a couple of manual runs
            # left HR four identical pending drafts for one employee -- eight
            # rows for emp-001 across the two states. Authorising one of four
            # identical drafts is not a decision anybody can make well.
            #
            # Only `pending` rows are replaced. An executed plan is the record
            # of access that was actually revoked and must never be discarded.
            superseded = s.scalars(
                select(DeprovisioningPlan).where(
                    DeprovisioningPlan.employee_id == emp_id,
                    DeprovisioningPlan.status == "pending",
                )
            ).all()
            for old in superseded:
                s.delete(old)
            if superseded:
                plan["superseded"] = len(superseded)

            s.add(DeprovisioningPlan(
                id=plan_id,
                employee_id=emp_id,
                case_id=case_id or None,
                plan=plan,
                status="pending",
            ))
    return plan


def approve_plan(plan_id: str, *, approved_by: str, approver_user_id: int | None,
                 note: str = "") -> dict[str, Any]:
    """Phase 1b: an HR account authorises a drafted plan.

    Deliberately a separate act from executing it. The caller is expected to be
    HR (the route enforces that); this function enforces the two conditions
    that must hold regardless of who calls it.
    """
    with session_scope() as s:
        row = s.get(DeprovisioningPlan, plan_id)
        if row is None:
            raise LookupError(f"Unknown deprovisioning plan '{plan_id}'.")
        if row.status == "executed":
            raise PermissionError("That plan has already been executed.")

        # A plan against someone who is not actually leaving is not a
        # deprovisioning, it is an account takeover with paperwork. `plan_*`
        # only checks the employee exists, so this is where it gets caught.
        case = store.exit_case_for_employee(str(row.employee_id))
        if case is None:
            raise PermissionError(
                f"Employee {row.employee_id} has no exit case. A deprovisioning "
                "plan can only be approved for someone who is actually leaving."
            )

        row.status = "approved"
        row.approved_by = approved_by
        row.approved_by_user_id = approver_user_id
        row.approved_at = _now()
        if note:
            row.note = note
        return {
            "plan_id": plan_id,
            "status": "approved",
            "approved_by": approved_by,
            "case_id": case.meta.get("case_id"),
        }


def execute_plan(plan_id: str, *, executed_by: str, executor_user_id: int | None = None,
                 note: str = "") -> dict[str, Any]:
    """Phase 2: run each approved action through the connectors (no LLM).

    Takes the persisted plan's id rather than a client-supplied plan body, and
    atomically flips it approved->executed inside one transaction (CAS update +
    action execution + result write-back), so a retried, double-clicked, or
    replayed call cannot run the connectors twice: a concurrent call on the
    same plan_id blocks on Postgres's row lock until this transaction commits,
    then finds status already 'executed' and returns the cached result.

    Refuses a plan that has not been approved by *somebody identifiable, who is
    not the executor*. That is what makes it safe for IT to hold this button --
    and for a while it was not true, which is worth spelling out because the
    sentence above was load-bearing and false.

    The check used to be `if executor_user_id is not None and approved_by_user_id
    == executor_user_id`. Both sides default to `None`, and the orchestrator
    passed `None` for both: `approve_plan(..., approver_user_id=None)` followed
    by `execute_plan(..., executed_by="orchestrator")`. `None is not None` is
    False, so the guard was skipped entirely rather than tripped. One HR account
    posting to `/api/orchestrate/{case_id}` therefore approved and executed a
    model-authored revocation plan with no second person anywhere, and the row
    recorded `approved_by=<that person>, approved_by_user_id=NULL`, so the audit
    trail could not even show it.

    An unidentified approver is now a refusal, not a pass. A machine actor that
    genuinely has no human behind it has not satisfied a two-person rule.
    """
    with session_scope() as s:
        row = s.get(DeprovisioningPlan, plan_id)
        if row is None:
            raise LookupError(f"Unknown deprovisioning plan '{plan_id}'.")
        if row.status == "executed":
            return row.execution or {"status": "executed", "executed": 0}
        if row.status != "approved":
            raise PermissionError(
                "This plan has not been approved yet. An HR account must "
                "approve it before it can be executed."
            )
        if row.approved_by_user_id is None:
            raise PermissionError(
                "This plan was approved without a named account, so there is "
                f"nobody to check the executor against. It says it was "
                f"approved by '{row.approved_by or 'unknown'}'. Have an HR "
                "account approve it again before running it."
            )
        if executor_user_id is None:
            raise PermissionError(
                "Executing a deprovisioning plan has to be attributable to an "
                "account. This call supplied no executor."
            )
        if row.approved_by_user_id == executor_user_id:
            raise PermissionError(
                "The person who approved a deprovisioning plan cannot also "
                "execute it. Ask a colleague to run it."
            )

        claimed = s.execute(
            update(DeprovisioningPlan)
            .where(DeprovisioningPlan.id == plan_id,
                   DeprovisioningPlan.status == "approved")
            .values(status="executed", executed_by=executed_by,
                    executed_by_user_id=executor_user_id, executed_at=func.now())
        ).rowcount == 1
        if not claimed:
            s.expire(row)
            return row.execution or {"status": "executed", "executed": 0}

        plan = dict(row.plan)
        actions = [a for a in (plan.get("actions") or []) if isinstance(a, dict)]
        emp_id = str(plan.get("employee_id") or "")
        case_id = str(plan.get("case_id") or "")
        approved_by = row.approved_by or "unknown"
        records = [
            execute_deprovisioning_action(a, employee_id=emp_id, case_id=case_id,
                                          approved_by=approved_by, note=note)
            for a in actions
        ]
        execution = {
            "employee_id": plan.get("employee_id"),
            "approved_by": approved_by,
            "executed_by": executed_by,
            "approval_note": row.note or note,
            "executed": len(records),
            "audit_log": records,
            "status": "executed" if records else "nothing_to_execute",
        }
        row.execution = execution
        return execution


@register
class ITDeprovisioningAgent(BaseAgent):
    meta = AgentMeta(
        id="it_deprovisioning",
        name="Automated IT Deprovisioning",
        tagline="Plan, approve, then execute",
        description=(
            "Generates an ordered deprovisioning plan (accounts to disable, access to "
            "revoke, data to archive, credentials to rotate) from the employee's real "
            "footprint and IT policy, then executes it only after a human approves."
        ),
        icon="⊘",
        category="Planning",
        fields=[
            Field("employee_id", "Employee", type="select"),
            Field("exit_type", "Exit type", type="select", required=False,
                  options=[{"value": "resignation", "label": "Resignation"},
                           {"value": "termination", "label": "Termination"}],
                  help="Terminations are sequenced as immediate per policy."),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        emp_id = (inputs.get("employee_id") or "").strip()
        if not emp_id:
            return {"error": "Select an employee."}
        exit_type = (inputs.get("exit_type") or "").strip()
        lwd = ""
        case_id = ""
        # If the employee has an exit case, borrow its exit type / LWD.
        for case in store.exit_cases():
            if str(case.meta.get("employee_id", "")) == emp_id:
                exit_type = exit_type or str(case.meta.get("exit_type", ""))
                lwd = str(case.meta.get("last_working_day", ""))
                case_id = str(case.meta.get("case_id", ""))
                break
        plan = plan_deprovisioning(emp_id, exit_type=exit_type, last_working_day=lwd, case_id=case_id)
        if "error" in plan:
            return plan
        return {"plan": plan, "awaiting_approval": True}


def action_states(plan_id: str) -> list[dict[str, Any]]:
    """Each action in a plan, and whether it has actually been carried out.

    Matched against the `deprovisioning_actions` records by target, because
    that is what a record names. An action with no record has not been done --
    absence of evidence is not evidence of revocation.
    """
    from ..systems import _revoked, executed_actions

    with session_scope() as s:
        row = s.get(DeprovisioningPlan, plan_id)
        if row is None:
            raise LookupError(f"Unknown deprovisioning plan '{plan_id}'.")
        plan = row.plan or {}
        employee_id = str(row.employee_id)

    records = executed_actions(employee_id)
    out = []
    for i, a in enumerate(plan.get("actions") or []):
        if not isinstance(a, dict):
            continue
        target = str(a.get("target") or "")
        rec = None
        for r in records:
            if (str(r.get("target", "")).lower() == target.lower()
                    and str(r.get("type", "")) == str(a.get("type", ""))):
                rec = r
                break
        if rec is None and a.get("type") != "recover_asset":
            rec = _revoked(employee_id, target, records=records)
        out.append({
            **a,
            "step": a.get("step", i + 1),
            "index": i,
            "done": rec is not None,
            "done_at": (rec or {}).get("executed_at"),
            "done_via": (rec or {}).get("connector"),
        })
    return out


def execute_one_action(plan_id: str, *, index: int, executed_by: str,
                       executor_user_id: int | None = None,
                       note: str = "") -> dict[str, Any]:
    """Carry out a single step of an approved plan.

    Deprovisioning is not one event. The laptop comes back at a desk on
    Tuesday, AWS goes the hour notice is confirmed because it is privileged,
    and the mailbox converts on the last day. A single "run the plan" button
    asserted that all of it happened at one instant, by one person, and always
    succeeded -- so the audit trail recorded a fiction even when the work was
    done properly.

    The same two guards as the batch route: the plan must be authorised, and
    not by the person carrying it out.
    """
    with session_scope() as s:
        row = s.get(DeprovisioningPlan, plan_id)
        if row is None:
            raise LookupError(f"Unknown deprovisioning plan '{plan_id}'.")
        if row.status == "pending":
            raise PermissionError(
                "This plan has not been authorised yet. An HR account must "
                "authorise it before anything can be revoked."
            )
        # Same correction as `execute_plan`: an unidentified approver or an
        # unidentified executor used to make this check vanish rather than
        # fire, because both sides default to None.
        if row.approved_by_user_id is None:
            raise PermissionError(
                "This plan was authorised without a named account, so there is "
                "nobody to check you against. Have an HR account authorise it "
                "again before revoking anything."
            )
        if executor_user_id is None:
            raise PermissionError(
                "Revoking access has to be attributable to an account. This "
                "call supplied no executor."
            )
        if row.approved_by_user_id == executor_user_id:
            raise PermissionError(
                "The person who authorised a deprovisioning plan cannot also "
                "carry it out. Ask a colleague."
            )
        plan = dict(row.plan or {})
        actions = [a for a in (plan.get("actions") or []) if isinstance(a, dict)]
        if not 0 <= index < len(actions):
            raise LookupError(f"This plan has no step {index + 1}.")
        action = actions[index]
        emp_id = str(plan.get("employee_id") or row.employee_id)
        case_id = str(plan.get("case_id") or row.case_id or "")
        approved_by = row.approved_by or "unknown"

    record = execute_deprovisioning_action(
        action, employee_id=emp_id, case_id=case_id,
        approved_by=approved_by, note=note)

    # The plan is executed once every step has a record -- derived, so it
    # cannot claim completion while a step is outstanding.
    remaining = [a for a in action_states(plan_id) if not a["done"]]
    if not remaining:
        with session_scope() as s:
            row = s.get(DeprovisioningPlan, plan_id)
            if row is not None and row.status != "executed":
                row.status = "executed"
                row.executed_by = executed_by
                row.executed_by_user_id = executor_user_id
                row.executed_at = _now()
    return {"record": record, "remaining": len(remaining)}
