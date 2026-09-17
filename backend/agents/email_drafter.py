"""Agent 2 — Email Drafting Agent.

Drafts stage-specific offboarding notification emails (KT reminder, overdue
warning, completion) using structured templates + the LLM for tone and specifics.
"""
from __future__ import annotations

from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from ._context import doc_to_context
from ..data_store import store

STAGE_BRIEFS = {
    "kt_reminder": "A friendly reminder to complete the Knowledge Transfer document before the last working day.",
    "overdue_warning": "A firm but professional warning that a clearance stage is overdue past the 5-day SLA and needs immediate action.",
    "completion": "A warm confirmation that the exit clearance is complete, thanking the employee and outlining final steps (final settlement, experience letter).",
}

SYSTEM = """You are an HR communications assistant. Draft a single professional \
email for an employee offboarding scenario. Match the requested stage and tone. \
Keep it concise, specific, and actionable. Use the employee's real details where \
available.

Return JSON: {"subject": str, "body": str, "recipient": str}"""


@register
class EmailDraftingAgent(BaseAgent):
    meta = AgentMeta(
        id="email_drafter",
        name="Email Drafting Agent",
        tagline="Stage-specific notification emails",
        description=(
            "Drafts KT reminders, overdue warnings, and completion notices tailored "
            "to the employee and stage, ready to review and send."
        ),
        icon="✉︎",
        category="Communication",
        fields=[
            Field("employee_id", "Employee", type="select", required=False),
            Field(
                "stage",
                "Email type",
                type="select",
                options=[
                    {"value": "kt_reminder", "label": "KT reminder"},
                    {"value": "overdue_warning", "label": "Overdue warning"},
                    {"value": "completion", "label": "Completion notice"},
                ],
            ),
            Field(
                "tone",
                "Tone",
                type="select",
                required=False,
                options=[
                    {"value": "professional", "label": "Professional"},
                    {"value": "friendly", "label": "Friendly"},
                    {"value": "firm", "label": "Firm"},
                ],
            ),
            Field("notes", "Extra context", type="textarea", required=False,
                  placeholder="Anything specific to mention…"),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        stage = (inputs.get("stage") or "kt_reminder").strip()
        tone = (inputs.get("tone") or "professional").strip()
        notes = (inputs.get("notes") or "").strip()
        brief = STAGE_BRIEFS.get(stage, STAGE_BRIEFS["kt_reminder"])

        context = ""
        emp_id = (inputs.get("employee_id") or "").strip()
        if emp_id:
            doc = store.employee(emp_id)
            if not doc:
                return {"error": f"Unknown employee '{emp_id}'."}
            context = doc_to_context(doc, include_body=False)

        user = (
            f"Email type: {stage} — {brief}\n"
            f"Desired tone: {tone}\n"
            f"Employee details:\n{context or '(none provided)'}\n"
            f"Extra context: {notes or '(none)'}\n\n"
            "Draft the email."
        )
        result = llm.complete_json(SYSTEM, user, temperature=0.5)
        return {"email": result, "stage": stage}
