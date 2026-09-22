"""User administration. IT only.

Before this, accounts existed only where `scripts/create_users.py` had been run
from a terminal -- which meant onboarding a new IT engineer or manager required
shell access to the production host.

Owned by IT rather than HR: creating, disabling and role-changing logins is
access management, the same job as revoking a leaver's systems. See `AdminUser`
in `backend/auth.py` for why HR is deliberately not an override here.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from ..auth import (AdminUser, destroy_all_sessions, generate_temp_password,
                    hash_password)
from ..db import session_scope
from ..db.models import ROLES, Employee, User

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _serialize(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "employee_id": user.employee_id,
        "full_name": user.full_name,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "locked_until": user.locked_until,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
    }


#: Roles that cannot be emptied, and what stops working if they are.
#: `it` administers accounts, so losing it means the only way back in is a
#: shell on the host. `hr` is the sole authoriser of a deprovisioning plan and
#: the only role that can reopen a signed-off clearance, so losing it strands
#: every pending plan and every completed-in-error stage.
IRREPLACEABLE = {
    "it": "administer accounts",
    "hr": "authorise a deprovisioning plan or reopen a clearance",
}


def _assert_not_last_of_role(session, user: User, *, action: str) -> None:
    """Refuse to remove the last active holder of a role nothing else covers.

    This used to guard HR only, from when HR ran the admin area. Account
    administration is IT's now, so the guard follows it -- an IT engineer
    deactivating the last IT account would otherwise lock the product's own
    admins out of it, and be allowed to do so by the very page they were using.
    """
    if not user.is_active or user.role not in IRREPLACEABLE:
        return
    remaining = session.scalar(
        select(func.count()).select_from(User)
        .where(User.role == user.role, User.is_active.is_(True), User.id != user.id)
    ) or 0
    if remaining == 0:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot {action} the last active {user.role.upper()} account -- "
                   f"nobody would be able to {IRREPLACEABLE[user.role]}. "
                   f"Create another {user.role.upper()} account first.",
        )


@router.get("/users")
def list_users(admin: AdminUser) -> dict[str, Any]:
    """Accounts, and the people who have none.

    The page is called People & access and listed only accounts, so an employee
    HR had just added was nowhere on it -- an account is created for a leaver,
    not for a new hire, so the one person IT most needs to set up was the one
    person invisible here. Listing them as awaiting an account turns the page
    into what its name promises, and gives the invite button something to
    start from.
    """
    with session_scope() as s:
        rows = s.scalars(select(User).order_by(User.role, User.email)).all()
        accounts = [_serialize(u) for u in rows]
        linked = {u.employee_id for u in rows if u.employee_id}
        by_email = {(u.email or "").strip().lower() for u in rows}

        waiting = []
        for emp in s.scalars(select(Employee).order_by(Employee.name)).all():
            email = (emp.email or "").strip().lower()
            if emp.id in linked or (email and email in by_email):
                continue
            waiting.append({
                "employee_id": emp.id,
                "name": emp.name,
                "email": emp.email,
                "role": emp.role,
                "department": emp.department,
                "status": emp.status,
            })

    return {"users": accounts, "awaiting_account": waiting}


@router.post("/users")
def create_user(admin: AdminUser, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Invite someone. Returns a one-time password, shown once and never stored
    in readable form."""
    payload = payload or {}
    email = str(payload.get("email") or "").strip().lower()
    role = str(payload.get("role") or "").strip().lower()
    full_name = str(payload.get("full_name") or "").strip() or None
    employee_id = str(payload.get("employee_id") or "").strip() or None

    # A blank or malformed address is not merely untidy: manager scope is
    # resolved by comparing this against `Employee.manager_email`, so an empty
    # one on a manager account would match every employee with no manager set.
    if not email or "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    if role not in ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"Role must be one of: {', '.join(ROLES)}.",
        )
    if role == "employee" and not employee_id:
        raise HTTPException(
            status_code=422,
            detail="An employee account must be linked to an employee record.",
        )

    with session_scope() as s:
        if s.scalar(select(User).where(User.email == email)):
            raise HTTPException(status_code=409, detail=f"{email} already has an account.")
        if employee_id and not s.get(Employee, employee_id):
            raise HTTPException(status_code=422, detail=f"Unknown employee '{employee_id}'.")

        secret = generate_temp_password()
        user = User(
            email=email,
            password_hash=hash_password(secret),
            role=role,
            employee_id=employee_id,
            full_name=full_name,
            must_change_password=True,
        )
        s.add(user)
        s.flush()
        return {"user": _serialize(user), "credential": {"email": email, "password": secret}}


@router.patch("/users/{user_id}")
def update_user(user_id: int, admin: AdminUser,
                payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Change a role, a display name, or active state."""
    payload = payload or {}
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="No such account.")

        if "role" in payload:
            role = str(payload["role"] or "").strip().lower()
            if role not in ROLES:
                raise HTTPException(
                    status_code=422, detail=f"Role must be one of: {', '.join(ROLES)}.")
            if role == "employee" and not (user.employee_id or payload.get("employee_id")):
                raise HTTPException(
                    status_code=422,
                    detail="An employee account must be linked to an employee record.")
            # Whenever the role actually changes, not just when it leaves "hr":
            # the old guard was written when HR was the only protected role, and
            # kept as-is it would let someone demote the last IT account.
            if role != user.role:
                _assert_not_last_of_role(s, user, action="demote")
            user.role = role

        if "full_name" in payload:
            user.full_name = str(payload["full_name"] or "").strip() or None

        if "is_active" in payload:
            active = bool(payload["is_active"])
            if not active:
                _assert_not_last_of_role(s, user, action="deactivate")
            user.is_active = active
            if not active:
                # resolve_session already drops sessions for inactive users on
                # the next request; this closes them now rather than on next use.
                destroy_all_sessions(user.id)

        # Clearing a lockout is the common support request after someone
        # fat-fingers their password eight times.
        if payload.get("unlock"):
            user.locked_until = None
            user.failed_attempts = 0

        s.flush()
        return {"user": _serialize(user)}


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, admin: AdminUser) -> dict[str, Any]:
    """Issue a new one-time password and sign the account out everywhere."""
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="No such account.")
        secret = generate_temp_password()
        user.password_hash = hash_password(secret)
        user.must_change_password = True
        user.failed_attempts = 0
        user.locked_until = None
        email = user.email
    destroy_all_sessions(user_id)
    return {"credential": {"email": email, "password": secret}}
