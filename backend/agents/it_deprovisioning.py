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

from .base import AgentMeta, BaseAgent, Field, llm, register
from ._context import doc_to_context
from ..data_store import store
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


def plan_deprovisioning(employee_id: str, *, exit_type: str = "", last_working_day: str = "",
                        case_notes: str = "") -> dict[str, Any]:
    """Phase 1: build the plan (LLM). Raises LLMError on gateway failure."""
    emp = store.employee(employee_id)
    if not emp:
        return {"error": f"Unknown employee '{employee_id}'."}
    user = (
        f"{POLICY_EXCERPT}\n\n"
        f"Exit type: {exit_type or 'unknown'}\nLast working day: {last_working_day or 'unknown'}\n"
        + (f"Case notes:\n{case_notes}\n" if case_notes else "")
        + f"\nEmployee record:\n{doc_to_context(emp)}\n\nProduce the deprovisioning plan."
    )
    plan = llm.complete_json(SYSTEM, user)
    if isinstance(plan, dict):
        plan["employee_id"] = emp.meta.get("id", employee_id)
        plan["employee"] = emp.meta.get("name", emp.title)
        plan["requires_approval"] = True
    return plan


def execute_plan(plan: dict[str, Any], *, approved_by: str = "hr-operator",
                 note: str = "") -> dict[str, Any]:
    """Phase 2: run each approved action through the connectors (no LLM).

    `approved_by` and `note` are carried down to each action so the persisted
    audit trail records the authorisation, not just the revocation.
    """
    actions = [a for a in (plan.get("actions") or []) if isinstance(a, dict)]
    emp_id = str(plan.get("employee_id") or "")
    case_id = str(plan.get("case_id") or "")
    records = [
        execute_deprovisioning_action(a, employee_id=emp_id, case_id=case_id,
                                      approved_by=approved_by, note=note)
        for a in actions
    ]
    return {
        "employee_id": plan.get("employee_id"),
        "approved_by": approved_by,
        "approval_note": note,
        "executed": len(records),
        "audit_log": records,
        "status": "executed" if records else "nothing_to_execute",
    }


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
        icon="🔐",
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
        # If the employee has an exit case, borrow its exit type / LWD.
        for case in store.exit_cases():
            if str(case.meta.get("employee_id", "")) == emp_id:
                exit_type = exit_type or str(case.meta.get("exit_type", ""))
                lwd = str(case.meta.get("last_working_day", ""))
                break
        plan = plan_deprovisioning(emp_id, exit_type=exit_type, last_working_day=lwd)
        if "error" in plan:
            return plan
        return {"plan": plan, "awaiting_approval": True}
