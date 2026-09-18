"""Agent 18 — Policy Compliance Auditor.

Scans exit cases (one, or the whole book of business) for SLA breaches,
out-of-order or skipped stages, approvals recorded without an owner, and
missing required documents -- all computed in code -- then has the LLM
prioritise and narrate exactly those violations, never inventing new ones.
"""
from __future__ import annotations

import json
from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from ._context import parse_days_open
from .document_collection import document_status
from ..data_store import store

DEFAULT_THRESHOLD_DAYS = 5

SYSTEM = """You are a compliance auditor reviewing exit-process violations that \
have already been computed from case data. You must only report violations \
present in the JSON you are given -- never invent one. For each case, set a \
risk level, group related violations, and draft concrete recommended_actions \
for HR to close them out.

Return JSON:
{
  "overall_risk": "low|medium|high",
  "summary": str,
  "cases": [
    {
      "case_id": str,
      "risk": "low|medium|high",
      "violations": [str],
      "recommended_actions": [str]
    }
  ]
}"""


def _case_violations(case_meta: dict[str, Any], threshold_days: int) -> list[str]:
    violations: list[str] = []
    stages = case_meta.get("stages") or []

    seen_pending = False
    for stage in stages:
        status = str(stage.get("status", "")).lower()
        name = stage.get("name", "stage")

        if status != "completed":
            days = parse_days_open(stage)
            if days > threshold_days:
                violations.append(
                    f"SLA breach: '{name}' open {days}d "
                    f"(owner: {stage.get('owner') or 'unassigned'})"
                )

        if status in ("in_progress", "completed") and seen_pending:
            violations.append(
                f"Out-of-order: '{name}' is {status} while an earlier stage is still pending"
            )
        if status == "pending":
            seen_pending = True

        if status == "completed" and not stage.get("owner"):
            violations.append(
                f"Missing approval evidence: '{name}' marked completed with no owner recorded"
            )

    doc_status = document_status(case_meta)
    for stage, docs in doc_status.get("missing_by_stage", {}).items():
        for doc in docs:
            violations.append(f"Missing document: '{doc}' ({stage})")

    return violations


def _audit_case(case_meta: dict[str, Any], threshold_days: int) -> dict[str, Any] | None:
    violations = _case_violations(case_meta, threshold_days)
    if not violations:
        return None
    return {
        "case_id": case_meta.get("case_id"),
        "employee_name": case_meta.get("employee_name"),
        "current_stage": case_meta.get("current_stage"),
        "violations": violations,
    }


@register
class PolicyAuditorAgent(BaseAgent):
    meta = AgentMeta(
        id="policy_auditor",
        name="Policy Compliance Auditor",
        tagline="SLA, ordering and document violations",
        description=(
            "Scans exit cases for SLA breaches, skipped or out-of-order stages, "
            "approvals recorded without an owner, and missing documents, then "
            "prioritises and drafts recommended actions."
        ),
        icon="⚖",
        category="Monitoring",
        fields=[
            Field("case_id", "Exit case (blank = audit every case)", type="select", required=False),
            Field(
                "threshold_days", "SLA threshold (days)", type="number", required=False,
                placeholder=str(DEFAULT_THRESHOLD_DAYS),
                help="Stages open longer than this without completing count as a breach.",
            ),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        case_id = (inputs.get("case_id") or "").strip()
        try:
            threshold = int(inputs.get("threshold_days") or DEFAULT_THRESHOLD_DAYS)
        except (TypeError, ValueError):
            threshold = DEFAULT_THRESHOLD_DAYS

        if case_id:
            case = store.exit_case(case_id)
            if not case:
                return {"error": f"Unknown exit case '{case_id}'."}
            cases = [case]
        else:
            cases = store.exit_cases()

        findings = [a for a in (_audit_case(c.meta, threshold) for c in cases) if a]

        if not findings:
            return {
                "cases_scanned": len(cases),
                "cases_with_violations": 0,
                "report": {"overall_risk": "low", "summary": "No violations found.", "cases": []},
            }

        user = "VIOLATIONS FOUND (code-computed -- do not add any not listed here):\n" + json.dumps(findings, indent=2, default=str)
        result = llm.complete_json(SYSTEM, user)
        return {
            "cases_scanned": len(cases),
            "cases_with_violations": len(findings),
            "report": result,
        }
