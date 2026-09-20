"""User administration. HR only.

Before this, accounts existed only where `scripts/create_users.py` had been run
from a terminal -- which meant onboarding a new IT engineer or manager required
shell access to the production host.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from ..auth import (HRUser, destroy_all_sessions, generate_temp_password,
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


def _assert_not_last_active_hr(session, user: User, *, action: str) -> None:
    """Refuse to remove the last way into the admin area.

    Locking every HR account out of a single-tenant deployment means the only
    recovery is a shell on the database host.
    """
    if user.role != "hr" or not user.is_active:
        return
    remaining = session.scalar(
        select(func.count()).select_from(User)
        .where(User.role == "hr", User.is_active.is_(True), User.id != user.id)
    ) or 0
    if remaining == 0:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot {action} the last active HR account -- nobody would be "
                   "able to administer the system. Create another HR account first.",
        )


@router.get("/users")
def list_users(admin: HRUser) -> dict[str, Any]:
    with session_scope() as s:
        rows = s.scalars(select(User).order_by(User.role, User.email)).all()
        return {"users": [_serialize(u) for u in rows]}


@router.post("/users")
def create_user(admin: HRUser, payload: dict[str, Any] | None = None) -> dict[str, Any]:
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
def update_user(user_id: int, admin: HRUser,
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
            if role != "hr":
                _assert_not_last_active_hr(s, user, action="demote")
            user.role = role

        if "full_name" in payload:
            user.full_name = str(payload["full_name"] or "").strip() or None

        if "is_active" in payload:
            active = bool(payload["is_active"])
            if not active:
                _assert_not_last_active_hr(s, user, action="deactivate")
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
def reset_password(user_id: int, admin: HRUser) -> dict[str, Any]:
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
