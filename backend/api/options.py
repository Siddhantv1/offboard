"""Scoped dropdown options for the agent forms.

Two rules, both learned from the UI being unusable without them.

**Scope.** Every filler takes the caller, because these options ARE data: the
employee list is the company roster and the case list names every departing
person. Handing an unscoped copy to anyone who can load the agent catalogue
would be an org dump through a dropdown.

**Label by person, not by key.** These used to read `kt-001 - emp emp-001`,
which asks a manager to know the employee code of someone on their own team
before they can review their handover document. The record id stays in the
label -- it is what support will ask for -- but the name leads, because that is
how the person choosing actually thinks about it.
"""
from __future__ import annotations

from ..auth import Identity
from ..data_store import store
from .deps import visible_cases, visible_employees

SEP = " \u00b7 "


def _label(*parts: object) -> str:
    """Join the non-empty parts with a middle dot."""
    return SEP.join(str(p).strip() for p in parts if str(p or "").strip())


def employee_options(user: Identity) -> list[dict[str, str]]:
    return [
        {"value": str(d.meta.get("id", d.slug)),
         "label": _label(d.meta.get("name", d.slug), d.meta.get("role"),
                         d.meta.get("department"))}
        for d in visible_employees(user)
    ]


def case_options(user: Identity) -> list[dict[str, str]]:
    return [
        {"value": str(d.meta.get("case_id", d.slug)),
         "label": _label(d.meta.get("employee_name"),
                         d.meta.get("exit_type"),
                         d.meta.get("case_id", d.slug))}
        for d in visible_cases(user)
    ]


def kt_options(user: Identity) -> list[dict[str, str]]:
    """Whose handover this is, then how complete it is -- a reviewer picking
    from this list is choosing a person, not a document id."""
    allowed = {str(e.meta.get("id")) for e in visible_employees(user)}
    out = []
    for d in store.kt_docs():
        if not (user.is_hr or str(d.meta.get("employee_id") or "") in allowed):
            continue
        reviewed = d.meta.get("reviewed")
        out.append({
            "value": str(d.meta.get("kt_id", d.slug)),
            "label": _label(
                d.meta.get("employee_name") or d.meta.get("employee_id"),
                d.meta.get("role"),
                "already reviewed" if reviewed else "not yet reviewed",
                d.meta.get("kt_id", d.slug),
            ),
        })
    return out


def interview_options(user: Identity) -> list[dict[str, str]]:
    allowed = {str(e.meta.get("id")) for e in visible_employees(user)}
    return [
        {"value": str(d.meta.get("interview_id", d.slug)),
         "label": _label(d.meta.get("employee_name") or d.meta.get("employee_id"),
                         d.meta.get("department"),
                         d.meta.get("interview_id", d.slug))}
        for d in store.all_interviews()
        if user.is_hr or str(d.meta.get("employee_id") or "") in allowed
    ]


#: Field name -> filler. Consulted by `serialize_agent` for any select field
#: the agent did not populate itself.
DYNAMIC = {
    "employee_id": employee_options,
    "case_id": case_options,
    "kt_id": kt_options,
    "interview_id": interview_options,
}
