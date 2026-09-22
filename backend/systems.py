"""Mock enterprise system connectors.

Agents 11 (Multi-System Clearance) and 14 (IT Deprovisioning) need to *call
systems*: an IT asset register, the HRMS, finance, an identity provider. There
are no real ones in this demo, so this module stands in for them with small,
deterministic connectors that derive their answers from the employee and exit
case records under `data/`, so every agent sees a consistent world.

Two kinds of function live here:
  - read-only lookups (`asset_status`, `hrms_status`, `finance_status`,
    `access_status`) used as *tools* by the tool-calling clearance agent;
  - `execute_deprovisioning_action`, which "performs" one plan step and returns
    an audit record with a timestamp -- called only after human approval.

Replace this module with real API clients to go to production; the agents
only depend on the return shapes.

One thing here is not a mock: the records written by
`execute_deprovisioning_action` go to the `deprovisioning_actions` table. They
state which privileged accounts were revoked, when, and on whose approval, so
they have to outlive the process -- while they were an in-memory dict, a
restart silently resurrected every revoked account.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# (no import from .agents here: agents import this module, so that would be circular)
from .data_store import store
from .db import session_scope
from .db.models import DeprovisioningAction

# Systems whose access is privileged per the IT deprovisioning policy (2.3).
PRIVILEGED = {"aws", "github", "ci/cd", "pagerduty", "database", "okta", "sap"}

def executed_actions(employee_id: str) -> list[dict[str, Any]]:
    """Post-approval deprovisioning records for an employee, oldest first.

    Persisted, unlike the rest of this module's mocked state. These records
    state which privileged accounts were revoked, when, and on whose approval;
    they are compliance evidence and must survive a restart. They previously
    lived in a module-level dict, so a restart silently resurrected every
    revoked account in `access_status`.
    """
    with session_scope() as session:
        rows = (session.query(DeprovisioningAction)
                .filter(DeprovisioningAction.employee_id == str(employee_id))
                .order_by(DeprovisioningAction.executed_at,
                          DeprovisioningAction.id)
                .all())
        return [r.to_record() for r in rows]


def _revoked(employee_id: str, system: str,
             records: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The executed action covering `system`, if any (loose name match:
    'AWS' matches target 'AWS (IAM user + keys)'; 'Okta/SSO' matches 'Okta').

    Pass `records` when checking several systems for one employee -- otherwise
    each call is its own query, and `access_status` checks every account.
    """
    if records is None:
        records = executed_actions(employee_id)
    s = system.lower()
    for rec in records:
        # Returning a laptop is not the same as killing an account; conflating
        # them would report access revoked while it is still live.
        if rec.get("type") == "recover_asset":
            continue
        t = str(rec.get("target", "")).lower()
        if s in t or any(part and part in s for part in t.replace("/", " ").split()):
            return rec
    return None


def _case_and_employee(case_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    case = store.exit_case(case_id)
    if not case:
        raise LookupError(f"Unknown exit case '{case_id}'.")
    emp = store.employee(str(case.meta.get("employee_id", "")))
    return case.meta, (emp.meta if emp else {})


def _stage(case_meta: dict[str, Any], name: str) -> dict[str, Any]:
    for s in case_meta.get("stages") or []:
        if isinstance(s, dict) and str(s.get("name", "")).lower() == name.lower():
            return s
    return {}


def _days_open(stage: dict[str, Any]) -> int:
    try:
        return int(stage.get("days_open", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# -- read-only connectors (tools for agent 11) --------------------------------
def asset_status(case_id: str) -> dict[str, Any]:
    """IT asset management: which assigned devices have been logged as returned.

    Demo rule: assets count as returned once the IT Clearance stage is
    completed; while it's in progress the first asset (the laptop) is returned
    and the rest are outstanding; pending means nothing has come back yet."""
    case_meta, emp = _case_and_employee(case_id)
    assets = [str(a) for a in emp.get("assets_assigned") or []]
    stage = _stage(case_meta, "IT Clearance")
    status = str(stage.get("status", "pending")).lower()
    items = []
    for i, asset in enumerate(assets):
        if status == "completed":
            returned = True
        elif status == "in_progress":
            returned = i == 0
        else:
            returned = False
        items.append({"asset": asset, "returned": returned})
    return {
        "system": "IT Asset Management",
        "employee_id": emp.get("id"),
        "assets": items,
        "outstanding": [a["asset"] for a in items if not a["returned"]],
        "stage_status": status,
        "stage_owner": stage.get("owner") or "unassigned",
    }


def access_status(case_id: str) -> dict[str, Any]:
    """Identity provider / IT: which system accounts are still active.

    Demo rule: access is revoked only when IT Clearance is completed. For a
    termination, anything privileged still active is flagged as a policy
    breach (policy 2.2: revoke immediately)."""
    case_meta, emp = _case_and_employee(case_id)
    emp_id = str(emp.get("id", ""))
    systems = [str(s) for s in emp.get("access_systems") or []]
    stage = _stage(case_meta, "IT Clearance")
    stage_done = str(stage.get("status", "")).lower() == "completed"
    # Fetched once for the whole loop: per-account lookups would be one query
    # each, and this runs inside the orchestrator's compliance re-check.
    executed = executed_actions(emp_id)
    accounts = []
    for s in systems:
        priv = s.lower() in PRIVILEGED
        rec = _revoked(emp_id, s, records=executed)
        active = not (stage_done or rec)
        entry: dict[str, Any] = {"system": s, "active": active, "privileged": priv}
        if rec:
            entry["revoked_at"] = rec.get("executed_at")
            entry["revoked_via"] = rec.get("connector")
        accounts.append(entry)
    breaches = [
        a["system"] for a in accounts
        if a["active"] and a["privileged"] and str(case_meta.get("exit_type", "")).lower() == "termination"
    ]
    return {
        "system": "Identity & Access",
        "accounts": accounts,
        "still_active": [a["system"] for a in accounts if a["active"]],
        "privileged_still_active": [a["system"] for a in accounts if a["active"] and a["privileged"]],
        "policy_breaches": breaches,
        "deprovisioning_actions_executed": len(executed),
        "days_open": _days_open(stage),
    }


def hrms_status(case_id: str) -> dict[str, Any]:
    """HRMS: HR clearance facts -- NDA reaffirmation, exit interview, relieving letter.

    Demo rule: NDA is done when HR Clearance is in progress or completed; the
    relieving letter only on completion.

    The exit interview is not a demo rule. It is true when the person has
    actually submitted one -- which used to mean only "a transcript exists in
    the corpus", from before the product could ask them. So a leaver could fill
    in all seven questions and every agent reading system state would still
    report the interview as not done, because it was looking somewhere the new
    answers never land. `interview_submitted` checks the table they submit into.
    """
    from .exit_interview import was_submitted

    case_meta, emp = _case_and_employee(case_id)
    stage = _stage(case_meta, "HR Clearance")
    status = str(stage.get("status", "pending")).lower()
    emp_id = str(emp.get("id", ""))
    has_interview = (
        was_submitted(case_id)
        or any(str(iv.meta.get("employee_id", "")) == emp_id
               for iv in store.interviews())
    )
    started = status in ("in_progress", "completed")
    return {
        "system": "HRMS",
        "employee_id": emp_id,
        "employment_status": emp.get("status"),
        "last_working_day": case_meta.get("last_working_day"),
        "nda_reaffirmation_signed": started,
        "exit_interview_completed": has_interview or started,
        "relieving_letter_issued": status == "completed",
        "stage_status": status,
        "stage_owner": stage.get("owner") or "unassigned",
    }


def finance_status(case_id: str) -> dict[str, Any]:
    """Finance: dues, advances, and full-and-final settlement readiness.

    Demo rule: no-dues is cleared when Finance Clearance is completed; while
    in progress the declaration is received but FnF is unsettled; pending
    means nothing has started."""
    case_meta, emp = _case_and_employee(case_id)
    stage = _stage(case_meta, "Finance Clearance")
    status = str(stage.get("status", "pending")).lower()
    return {
        "system": "Finance",
        "employee_id": emp.get("id"),
        "no_dues_declaration_received": status in ("in_progress", "completed"),
        "advances_outstanding": status != "completed" and str(emp.get("criticality", "")) == "high",
        "fnf_settled": status == "completed",
        "stage_status": status,
        "stage_owner": stage.get("owner") or "unassigned",
    }


# OpenAI-style tool schemas for the read-only connectors.
CLEARANCE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": doc,
            "parameters": {
                "type": "object",
                "properties": {"case_id": {"type": "string", "description": "Exit case id, e.g. case-001"}},
                "required": ["case_id"],
            },
        },
    }
    for name, doc in (
        ("asset_status", "IT asset management system: which assigned hardware has been returned."),
        ("access_status", "Identity provider / IT: which system accounts are still active, privileged ones flagged."),
        ("hrms_status", "HRMS: NDA reaffirmation, exit interview, relieving letter status."),
        ("finance_status", "Finance system: no-dues declaration, outstanding advances, full-and-final settlement."),
    )
]

CLEARANCE_HANDLERS = {
    "asset_status": asset_status,
    "access_status": access_status,
    "hrms_status": hrms_status,
    "finance_status": finance_status,
}


# -- write connector (agent 14, post-approval only) ---------------------------
def execute_deprovisioning_action(action: dict[str, Any], employee_id: str = "",
                                  case_id: str = "", approved_by: str = "",
                                  note: str = "") -> dict[str, Any]:
    """'Perform' one approved deprovisioning step and return an audit record.

    In production this would call the identity provider / cloud / SaaS admin
    APIs. Here it stamps the action as done and records it against the employee
    so `access_status` reflects the revocation afterwards. Deterministic and
    LLM-free so that what was approved is exactly what gets executed.

    `approved_by` and `note` are persisted: an audit trail that records what
    was revoked but not who authorised it answers only half the question.
    """
    kind = str(action.get("type") or action.get("kind") or "action")
    target = str(action.get("target") or action.get("system") or action.get("item") or "?")
    connector = _connector_for(target)
    record = {
        "type": kind,
        "target": target,
        "result": "done",
        "executed_at": _now(),
        "connector": connector,
    }
    if employee_id:
        with session_scope() as session:
            session.add(DeprovisioningAction(
                employee_id=str(employee_id),
                case_id=case_id or None,
                action_type=kind,
                target=target,
                result="done",
                connector=connector,
                approved_by=approved_by or None,
                note=note or "",
            ))
    return record


def _connector_for(target: str) -> str:
    t = target.lower()
    if any(k in t for k in ("okta", "sso", "email", "mailbox")):
        return "identity-provider"
    if "aws" in t:
        return "aws-iam"
    if "github" in t:
        return "github-admin"
    if any(k in t for k in ("vpn", "mfa", "yubikey")):
        return "network-security"
    if any(k in t for k in ("macbook", "laptop", "iphone", "monitor", "dell")):
        return "asset-register"
    return "saas-admin"
