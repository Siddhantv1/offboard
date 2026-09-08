"""Agent 1 — Exit Checklist Generator.

Takes an employee (or a role/department) and generates a personalized exit
checklist: assets to return, access to revoke, and knowledge-transfer topics.
"""
from __future__ import annotations

from typing import Any

from .base import AgentMeta, BaseAgent, Field, llm, register
from ._context import doc_to_context
from ..data_store import store

SYSTEM = """You are an HR offboarding specialist. Given an exiting employee's \
profile, produce a personalized, actionable exit checklist. Be specific to the \
role, department, and the systems/assets the employee actually has. Group items \
sensibly and note who is responsible for each.

Return JSON with this shape:
{
  "employee": "name / role summary",
  "assets_to_return": [{"item": str, "owner": str, "notes": str}],
  "access_to_revoke": [{"system": str, "owner": str, "priority": "high|medium|low"}],
  "kt_topics": [{"topic": str, "why": str}],
  "other_tasks": [{"task": str, "owner": str}]
}"""


@register
class ChecklistAgent(BaseAgent):
    meta = AgentMeta(
        id="checklist",
        name="Exit Checklist Generator",
        tagline="Personalized offboarding checklists",
        description=(
            "Generates a role- and department-specific exit checklist — assets to "
            "return, access to revoke, and KT topics — from the employee's profile."
        ),
        icon="✓",
        category="Planning",
        fields=[
            Field(
                "employee_id",
                "Employee",
                type="select",
                required=False,
                help="Pick an employee to auto-fill role, department, assets and access.",
            ),
            Field("role", "Role", required=False, placeholder="e.g. Senior Backend Engineer"),
            Field("department", "Department", required=False, placeholder="e.g. Engineering"),
        ],
    )

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        emp_id = (inputs.get("employee_id") or "").strip()
        if emp_id:
            doc = store.employee(emp_id)
            if not doc:
                return {"error": f"Unknown employee '{emp_id}'."}
            context = doc_to_context(doc)
        else:
            role = inputs.get("role", "").strip()
            dept = inputs.get("department", "").strip()
            if not role and not dept:
                return {"error": "Provide an employee, or a role/department."}
            context = f"role: {role}\ndepartment: {dept}"

        result = llm.complete_json(
            SYSTEM, f"Employee profile:\n\n{context}\n\nGenerate the exit checklist."
        )
        return {"checklist": result}
