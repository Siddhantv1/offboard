"""Agent 9 — Compliance Verification Agent.

A multi-step agent that verifies all compliance requirements before final
clearance: asset return, NDA acknowledgment, and access revocation confirmation.
It (1) assembles evidence from the case + employee record, (2) verifies each
requirement, then (3) issues an overall go/no-go verdict.
"""
from __future__ import annotations

from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from ._context import doc_to_context
from ..data_store import store

SYSTEM = """You are a compliance officer performing the final pre-clearance \
check for an employee exit. For each requirement, decide PASS / FAIL / \
UNVERIFIED strictly from the evidence provided. Do not assume compliance where \
evidence is missing. Keep each "evidence" string to one or two sentences.

Return JSON:
{
  "requirements": [
    {"name": "Asset return", "status": "pass|fail|unverified", "evidence": str, "blocking": bool},
    {"name": "NDA acknowledgment", "status": "pass|fail|unverified", "evidence": str, "blocking": bool},
    {"name": "Access revocation", "status": "pass|fail|unverified", "evidence": str, "blocking": bool}
  ],
  "final_verdict": "cleared|blocked",
  "blockers": [str],
  "next_actions": [str]
}"""


@register
class ComplianceAgent(BaseAgent):
    meta = AgentMeta(
        id="compliance",
        name="Compliance Verification",
        tagline="Final pre-clearance checks",
        description=(
            "Verifies asset return, NDA acknowledgment, and access revocation "
            "against case evidence before issuing a final go/no-go clearance."
        ),
        icon="◈",
        category="Monitoring",
        fields=[
            Field("case_id", "Exit case", type="select"),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        case_id = (inputs.get("case_id") or "").strip()
        case = store.exit_case(case_id)
        if not case:
            return {"error": f"Unknown exit case '{case_id}'."}

        # Step 1 — assemble evidence from the case and the linked employee.
        evidence = [f"EXIT CASE:\n{doc_to_context(case)}"]
        emp_id = str(case.meta.get("employee_id", "")).strip()
        if emp_id:
            emp = store.employee(emp_id)
            if emp:
                evidence.append(
                    "EMPLOYEE RECORD (expected assets & access):\n"
                    + doc_to_context(emp, include_body=False)
                )
        # Optional: upstream agents (system clearance, document collection,
        # deprovisioning execution) hand in what they found. The orchestrator
        # uses this so compliance isn't limited to what the case file says.
        extra = (inputs.get("extra_evidence") or "").strip()
        if extra:
            evidence.append("ADDITIONAL EVIDENCE FROM OTHER AGENTS:\n" + extra)
        evidence_block = "\n\n".join(evidence)

        # Step 2+3 — verify each requirement and produce the verdict.
        result = llm.complete_json(
            SYSTEM,
            f"Verify final clearance from this evidence:\n\n{evidence_block}",
        )
        return {"case_id": case.title, "verification": result}
