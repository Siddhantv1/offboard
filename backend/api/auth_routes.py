"""Sign in, sign out, who-am-I, and password change.

Named `auth_routes` rather than `auth` so `from .. import auth` inside this
package keeps meaning the auth module, not this router.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from .. import auth
from ..auth import CurrentUser, OptionalUser
from ..config import COOKIE_SECURE

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(response: Response, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    try:
        identity, token = auth.authenticate(
            str(payload.get("email") or ""), str(payload.get("password") or "")
        )
    except auth.AuthError as exc:
        # 401 with the module's own message: it is written to be safe to show,
        # and never distinguishes "no such account" from "wrong password".
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    auth.set_session_cookie(response, token, secure=COOKIE_SECURE)
    auth.set_csrf_cookie(response, secure=COOKIE_SECURE)
    return {"user": identity.as_json()}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, str]:
    auth.destroy_session(request.cookies.get(auth.SESSION_COOKIE))
    auth.clear_session_cookie(response)
    auth.clear_csrf_cookie(response)
    return {"status": "signed out"}


@router.get("/me")
def whoami(user: OptionalUser) -> dict[str, Any]:
    """Who the browser is, or null. Deliberately 200-with-null rather than 401
    so the app can ask on load without treating "not signed in" as an error."""
    return {"user": user.as_json() if user else None}


@router.post("/password")
def change_password(
    request: Request, response: Response, user: CurrentUser,
    payload: dict[str, Any] | None = None,
) -> dict[str, str]:
    payload = payload or {}
    try:
        auth.change_password(
            user.user_id,
            str(payload.get("current_password") or ""),
            str(payload.get("new_password") or ""),
        )
    except auth.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # change_password revoked every session, including this one.
    auth.clear_session_cookie(response)
    auth.clear_csrf_cookie(response)
    return {"status": "password changed, sign in again"}
