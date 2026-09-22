"""Which exit cases a person may see.

Lives below the API layer on purpose. It began in `api/deps.py`, where it
answered one question -- may this request read this case -- and that was the
only place it could be called from. But the same predicate answers a second
question that belongs to the write path: *would an assignment to this person be
workable?* A stage handed to somebody who cannot see the case appears on no
queue at all -- filtered out of theirs, and not owned by anyone who can see it.

Keeping it in `api/deps.py` meant only routes could ask. The orchestrator
assigns stages without going through a route, so it could not, and the guard
that stops a human creating stranded work did not stop the machine creating it.
Both call this now.

`api/deps.py` re-exports it, so existing imports keep working.
"""
from __future__ import annotations

from typing import Any

from .auth import Identity
from .data_store import store


def can_see_case(user: Identity, case_meta: dict[str, Any]) -> bool:
    """Is this exit case within that person's scope?

    Separate from the role guard on purpose: passing `require_roles("manager")`
    says you are *a* manager, not that this is *your* case.
    """
    if user.role in ("hr", "it"):
        # HR owns the process; IT clears access on every case. Both legitimately
        # see the whole book of work.
        return True

    if user.role == "employee":
        return bool(user.employee_id) and str(
            case_meta.get("employee_id") or ""
        ) == user.employee_id

    if user.role == "manager":
        employee_id = str(case_meta.get("employee_id") or "")
        employee = store.employee(employee_id) if employee_id else None
        if employee is None:
            return False
        manager_email = str(employee.meta.get("manager_email") or "").strip().lower()
        # Both guards matter. `add_employee` writes "" for an absent manager,
        # so without the emptiness check a manager account with a blank email
        # would match every such employee. And `manager_email` is stored with
        # .strip() but no .lower() while `User.email` is lowercased on write,
        # so a mixed-case address in the corpus would silently match nothing --
        # a scope check that fails closed and reads as "no work assigned".
        return bool(manager_email) and manager_email == (user.email or "").strip().lower()

    return False


def identity_for(user: Any) -> Identity:
    """An `Identity` for a `User` row, for asking what that person could see.

    Not a session: `must_change_password` is False because nothing here acts on
    their behalf, it only asks what would be visible to them.
    """
    return Identity(
        user_id=user.id,
        email=user.email,
        role=user.role,
        employee_id=user.employee_id,
        full_name=user.full_name,
        must_change_password=False,
    )
