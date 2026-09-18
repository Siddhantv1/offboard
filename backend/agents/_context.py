"""Shared helpers for agent modules: LLM context rendering, id lookups that
fail the same way everywhere, and small cross-agent constants."""
from __future__ import annotations

from typing import Any

from ..data_store import Doc, store

# Days a clearance stage can sit open before it counts as overdue. One knob
# shared by the dashboard, workflow optimizer, escalation and policy agents,
# instead of each redefining its own copy of the same number.
SLA_DAYS = 5


def doc_to_context(doc: Doc, include_body: bool = True) -> str:
    """Render a doc's frontmatter + body as a plain-text block."""
    lines: list[str] = []
    for key, value in doc.meta.items():
        lines.append(f"{key}: {value}")
    text = "\n".join(lines)
    if include_body and doc.body.strip():
        text += "\n\n" + doc.body.strip()
    return text


def parse_days_open(stage: dict) -> int:
    for key in ("days_open", "daysOpen", "age_days"):
        if key in stage:
            try:
                return int(stage[key])
            except (TypeError, ValueError):
                return 0
    return 0


def require_employee(employee_id: str | None) -> Doc | dict[str, Any]:
    """`store.employee(employee_id)`, or the `{"error": ...}` dict every agent
    returns verbatim on a miss -- the same few lines were copy-pasted into a
    dozen agent modules. Callers check the type before using the result:

        emp = require_employee(inputs.get("employee_id"))
        if isinstance(emp, dict):
            return emp
    """
    employee_id = str(employee_id or "").strip()
    doc = store.employee(employee_id) if employee_id else None
    if not doc:
        return {"error": f"Unknown employee '{employee_id}'."}
    return doc


def require_case(case_id: str | None) -> Doc | dict[str, Any]:
    """Same contract as `require_employee`, for exit cases."""
    case_id = str(case_id or "").strip()
    doc = store.exit_case(case_id) if case_id else None
    if not doc:
        return {"error": f"Unknown exit case '{case_id}'."}
    return doc


def require_kt_doc(kt_id: str | None) -> Doc | dict[str, Any]:
    """Same contract as `require_employee`, for KT documents."""
    kt_id = str(kt_id or "").strip()
    doc = store.kt_doc(kt_id) if kt_id else None
    if not doc:
        return {"error": f"Unknown KT document '{kt_id}'."}
    return doc


def require_interview(interview_id: str | None) -> Doc | dict[str, Any]:
    """Same contract as `require_employee`, for exit interviews."""
    interview_id = str(interview_id or "").strip()
    doc = store.interview(interview_id) if interview_id else None
    if not doc:
        return {"error": f"Unknown interview '{interview_id}'."}
    return doc
