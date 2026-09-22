"""Request-scoped dependencies that need both auth and the data store.

These live here rather than in `backend/auth.py` because `data_store` imports
`auth` (for password hashing), so an `auth -> data_store` import would close a
cycle. Keeping the pure predicates in `auth`/`policy` and the record-resolving
dependencies here is also the honest split: the former are about *who you are*,
the latter about *what you may touch*.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException

from ..auth import CurrentUser, Identity
from ..data_store import Doc, store
# Re-exported: this used to live here, and it is still the name routes use.
from ..scoping import can_see_case, identity_for  # noqa: F401


def scoped_case(case_id: str, user: CurrentUser) -> Doc:
    """The exit case named in the path, or an error -- never an unscoped case.

    This is the *only* supported way a route obtains a case. Routes declare
    `case: ScopedCase` and never take a raw `case_id`, so "forgot the row-level
    check" is unreachable rather than merely unlikely. `tests/test_permissions.py`
    walks `app.routes` and asserts every `{case_id}` route depends on this.

    403 rather than 404 for an out-of-scope case, matching the convention the
    rest of the app already follows (`auth.require_hr`: "the caller is
    authenticated, just not permitted"). Hiding existence behind a 404 would be
    security theatre here -- case ids are sequential (`case-001`...), so
    existence is already guessable. What must not leak is the case *contents*,
    and 403 leaks none of it.
    """
    case = store.exit_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"Unknown exit case '{case_id}'.")
    if not can_see_case(user, case.meta):
        raise HTTPException(
            status_code=403, detail="This exit case is outside your scope."
        )
    return case


#: Use this in place of a `case_id: str` path parameter.
ScopedCase = Annotated[Doc, Depends(scoped_case)]


# -- collection scoping -------------------------------------------------------
# The single-record `scoped_case` above guards routes that name a case. These
# guard the two places that hand out *lists*: the agent catalogue's select
# options, and the ids a caller may pass into an agent run.


def visible_cases(user: Identity) -> list[Doc]:
    """Every exit case within the caller's scope, in store order."""
    return [c for c in store.exit_cases() if can_see_case(user, c.meta)]


def visible_employees(user: Identity) -> list[Doc]:
    """Every employee the caller may name.

    HR and IT work the whole book. A manager sees their reports. An employee
    sees only themselves -- though in practice no agent an employee can run
    takes an employee_id.
    """
    if user.role in ("hr", "it"):
        return store.employees()
    if user.role == "employee":
        emp = store.employee(user.employee_id) if user.employee_id else None
        return [emp] if emp else []
    if user.role == "manager":
        email = (user.email or "").strip().lower()
        if not email:
            return []
        out = []
        for emp in store.employees():
            manager_email = str(emp.meta.get("manager_email") or "").strip().lower()
            if manager_email and manager_email == email:
                out.append(emp)
        return out
    return []


#: Payload keys that name a record, and how to resolve the set the caller may
#: use. `interview_id` and `kt_id` are scoped via their owning employee rather
#: than directly, because that is the relationship that actually confers access.
_SCOPED_INPUT_KEYS = ("case_id", "employee_id", "kt_id", "interview_id")


def assert_inputs_in_scope(user: Identity, inputs: dict[str, Any]) -> None:
    """Reject an agent run that names a record outside the caller's scope.

    `ROLE_AGENTS` gates which agent you may invoke; it says nothing about the
    *arguments*. Without this, a manager allowed to run `risk_assessment`
    (which takes an `employee_id`) could run it against anyone in the company,
    and `scoped_case` -- carefully applied to every /api/cases route -- would be
    bypassed entirely by a read side channel.

    HR is exempt because HR's scope is everything; skipping the lookups keeps
    the common path free.
    """
    if user.role == "hr":
        return
    if not any(inputs.get(k) for k in _SCOPED_INPUT_KEYS):
        return

    case_ids = {str(c.meta.get("case_id")) for c in visible_cases(user)}
    employee_ids = {str(e.meta.get("id")) for e in visible_employees(user)}

    def _refuse(kind: str, value: str) -> None:
        raise HTTPException(
            status_code=403,
            detail=f"{kind} '{value}' is outside your scope.",
        )

    if (case_id := str(inputs.get("case_id") or "").strip()):
        if case_id not in case_ids:
            _refuse("Exit case", case_id)

    if (employee_id := str(inputs.get("employee_id") or "").strip()):
        if employee_id not in employee_ids:
            _refuse("Employee", employee_id)

    if (kt_id := str(inputs.get("kt_id") or "").strip()):
        kt = store.kt_doc(kt_id)
        if kt is None or str(kt.meta.get("employee_id") or "") not in employee_ids:
            _refuse("KT document", kt_id)

    if (interview_id := str(inputs.get("interview_id") or "").strip()):
        interview = store.interview(interview_id)
        if interview is None or str(
            interview.meta.get("employee_id") or ""
        ) not in employee_ids:
            _refuse("Interview", interview_id)
