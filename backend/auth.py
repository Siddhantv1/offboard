"""Authentication: password hashing, server-side sessions, and route guards.

Design notes, because each of these is a deliberate trade:

*Argon2id* for password hashing. It is the current OWASP first choice and the
library ships sane parameters, so there is nothing here to tune wrongly.

*Server-side sessions* rather than a signed or JWT cookie. The cookie carries an
opaque random token and the state lives in the `sessions` table, so logout,
password change and deactivation revoke access immediately instead of waiting
for an expiry the server cannot influence.

*Only a hash of the token is stored.* A leak of the sessions table then yields
nothing an attacker can present as a cookie. Lookup is still one indexed
equality check, so this costs nothing.

*Guards return a frozen `Identity`, not an ORM `User`.* The ORM object would be
detached the moment its session closed, and touching a lazy attribute later
raises. A plain value object cannot.
"""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Cookie, Depends, Header, HTTPException, Request, Response
from sqlalchemy import select

from .db import session_scope
from .db.models import ROLES, User, UserSession

SESSION_COOKIE = "offboard_session"
SESSION_TTL = timedelta(hours=12)
# Sliding expiry: a session in active use is extended, so a long working day
# does not sign someone out mid-review.
SESSION_REFRESH_AFTER = timedelta(minutes=30)

# Double-submit CSRF cookie. `SameSite=Lax` on the session cookie already
# blocks cross-site POSTs, but it still attaches to a top-level cross-site GET
# navigation -- exactly what the orchestrator's resume/stream endpoint is. A
# second, JS-readable cookie set at login lets that GET carry a token a
# cross-origin page cannot read, so it cannot forge a match.
CSRF_COOKIE = "offboard_csrf"

MAX_FAILED_ATTEMPTS = 8
LOCKOUT = timedelta(minutes=15)

_hasher = PasswordHasher()

# Verified against on unknown emails so that "no such account" and "wrong
# password" take the same time. Without it, response latency tells an attacker
# which addresses are real.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Unambiguous alphabet for generated passwords: no O/0, l/1/I. These get read
# off a screen (or an HR operator's clipboard) and typed by someone else.
_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def generate_temp_password(length: int = 14) -> str:
    """A random password for a freshly provisioned account. Shown once by the
    caller and never stored except as its Argon2 hash."""
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash used weaker parameters than the current ones,
    so it can be upgraded transparently on the next successful login."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Identity:
    """Who the request is from. Safe to hold past the DB session."""

    user_id: int
    email: str
    role: str
    employee_id: str | None
    full_name: str | None
    must_change_password: bool

    @property
    def is_hr(self) -> bool:
        return self.role == "hr"

    @property
    def is_staff(self) -> bool:
        """Anyone who works a case, as opposed to being the subject of one."""
        return self.role in ("hr", "it", "manager")

    def as_json(self) -> dict[str, object]:
        return {
            "email": self.email,
            "role": self.role,
            "employee_id": self.employee_id,
            "full_name": self.full_name,
            "must_change_password": self.must_change_password,
        }


def _identity(user: User) -> Identity:
    return Identity(
        user_id=user.id,
        email=user.email,
        role=user.role,
        employee_id=user.employee_id,
        full_name=user.full_name,
        must_change_password=user.must_change_password,
    )


class AuthError(Exception):
    """Login refused. The message is safe to show the user."""


def authenticate(email: str, password: str) -> tuple[Identity, str]:
    """Check credentials and open a session. Returns (identity, raw token).

    Raises AuthError with a deliberately vague message on bad credentials: the
    response must not reveal whether the address exists.
    """
    email = (email or "").strip().lower()
    generic = AuthError("Email or password is incorrect.")

    with session_scope() as s:
        user = s.scalar(select(User).where(User.email == email))

        if user is None:
            verify_password(_DUMMY_HASH, password)  # equalise timing
            raise generic
        if not user.is_active:
            verify_password(_DUMMY_HASH, password)
            raise AuthError("This account is disabled. Contact HR.")

        if verify_password(user.password_hash, password):
            # A correct password always succeeds, even mid-lockout. The
            # lockout throttles an *attacker's* guessing; it must never deny
            # the real owner -- who knows the password -- their own account.
            # Locking on email alone (as this used to, checked before the
            # password) let anyone who merely knew the address deny the owner
            # access indefinitely, just by repeating a few wrong guesses every
            # lockout window forever.
            user.failed_attempts = 0
            user.locked_until = None
            user.last_login_at = _now()
            if needs_rehash(user.password_hash):
                user.password_hash = hash_password(password)

            token = secrets.token_urlsafe(32)
            s.add(
                UserSession(
                    token_hash=_token_hash(token),
                    user_id=user.id,
                    expires_at=_now() + SESSION_TTL,
                )
            )
            return _identity(user), token

        # Wrong password: the lockout throttle applies only to further wrong
        # guesses, never to the correct one above.
        if user.locked_until and user.locked_until > _now():
            mins = max(1, int((user.locked_until - _now()).total_seconds() // 60) + 1)
            raise AuthError(f"Too many failed attempts. Try again in {mins} minutes.")

        failed_user_id = user.id

    # Outside the transaction on purpose. session_scope rolls back on the
    # exception, so counting the attempt and then raising inside that block
    # would discard the increment and leave the lockout unreachable.
    _record_failed_attempt(failed_user_id)
    raise generic


def _record_failed_attempt(user_id: int) -> None:
    """Count a bad password, and lock the account once there are too many.

    Deliberately a separate committed transaction: the caller raises straight
    after, and an exception inside the login transaction would roll this away.
    """
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            return
        user.failed_attempts += 1
        if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = _now() + LOCKOUT
            user.failed_attempts = 0


def resolve_session(token: str | None) -> Identity | None:
    """Identity behind a session cookie, or None. Extends a session in use."""
    if not token:
        return None
    with session_scope() as s:
        row = s.get(UserSession, _token_hash(token))
        if row is None:
            return None
        now = _now()
        if row.expires_at <= now:
            s.delete(row)  # expired sessions do not linger
            return None
        user = s.get(User, row.user_id)
        if user is None or not user.is_active:
            s.delete(row)  # deactivating an account kills its sessions
            return None
        if now - row.last_seen_at.astimezone(timezone.utc) > SESSION_REFRESH_AFTER:
            row.last_seen_at = now
            row.expires_at = now + SESSION_TTL
        return _identity(user)


def destroy_session(token: str | None) -> None:
    if not token:
        return
    with session_scope() as s:
        row = s.get(UserSession, _token_hash(token))
        if row is not None:
            s.delete(row)


def destroy_all_sessions(user_id: int) -> None:
    """Used on password change: every other browser is signed out."""
    with session_scope() as s:
        for row in s.scalars(select(UserSession).where(UserSession.user_id == user_id)):
            s.delete(row)


def change_password(user_id: int, current: str, new: str) -> None:
    """Rotate a password and revoke every existing session.

    Requiring the current password means a borrowed unlocked laptop cannot be
    used to lock the real owner out of their own account.
    """
    if len(new) < 10:
        raise AuthError("Choose a password of at least 10 characters.")
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            raise AuthError("Account not found.")
        if not verify_password(user.password_hash, current):
            raise AuthError("Current password is incorrect.")
        user.password_hash = hash_password(new)
        user.must_change_password = False
    destroy_all_sessions(user_id)


def set_session_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,  # unreadable from JavaScript, so XSS cannot steal it
        samesite="lax",  # blocks cross-site POSTs while keeping normal navigation
        secure=secure,  # HTTPS only in production
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def set_csrf_cookie(response: Response, *, secure: bool) -> str:
    """Issue a fresh double-submit CSRF token, readable by the frontend JS so
    it can echo it back on the resume/stream request."""
    token = secrets.token_urlsafe(24)
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=False,  # must be readable by frontend JS to echo back
        samesite="lax",
        secure=secure,
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
    )
    return token


def clear_csrf_cookie(response: Response) -> None:
    response.delete_cookie(CSRF_COOKIE, path="/")


def verify_csrf(request: Request, token: str | None) -> None:
    """Raise 403 unless `token` matches this session's CSRF cookie.

    Constant-time comparison: this is a secret-equality check, and a
    timing-leaky one would let an attacker recover the cookie value byte by
    byte instead of needing to read it directly.
    """
    cookie_value = request.cookies.get(CSRF_COOKIE)
    if not token or not cookie_value or not secrets.compare_digest(token, cookie_value):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token.")


# -- FastAPI dependencies -----------------------------------------------------
def optional_user(
    token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> Identity | None:
    return resolve_session(token)


def current_user(
    request: Request,
    user: Annotated[Identity | None, Depends(optional_user)],
) -> Identity:
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    # Enforced server-side, not just hidden client-side: otherwise anyone who
    # skips the frontend gate (a direct API call, a stale tab) keeps full
    # access on a password the account owner was required to change.
    if user.must_change_password and request.url.path != "/api/auth/password":
        raise HTTPException(
            status_code=403,
            detail="You must set a new password before continuing.",
        )
    return user


#: Human-readable names for the 403 message. Naming the roles that *would*
#: work turns "forbidden" into something the reader can act on.
_ROLE_LABELS = {"hr": "HR", "it": "IT", "manager": "manager", "employee": "employee"}


def _forbidden(allowed: tuple[str, ...]) -> HTTPException:
    names = [_ROLE_LABELS.get(r, r) for r in allowed]
    if len(names) == 1:
        who = f"{names[0]} accounts"
    else:
        who = ", ".join(names[:-1]) + f" and {names[-1]} accounts"
    # 403, not 404: the caller is authenticated, just not permitted.
    return HTTPException(status_code=403, detail=f"This area is for {who}.")


def require_roles(*allowed: str) -> Callable[..., Identity]:
    """Dependency factory: 403 unless the caller holds one of `allowed`.

    Replaces the single `require_hr`. A factory rather than a named-permission
    table because four roles and ~20 routes do not earn the indirection -- the
    role set at each route *is* the policy, readable in the signature.

    Unknown role names are rejected at import time rather than silently never
    matching, which is how a typo'd guard becomes an open route.
    """
    unknown = set(allowed) - set(ROLES)
    if unknown:
        raise ValueError(f"require_roles got unknown role(s): {sorted(unknown)}")

    def guard(user: Annotated[Identity, Depends(current_user)]) -> Identity:
        if user.role not in allowed:
            raise _forbidden(allowed)
        return user

    return guard


def require_hr(user: Annotated[Identity, Depends(current_user)]) -> Identity:
    """Kept as a named function: `main.py` and the tests reference it directly,
    and `HRUser` below is by far the most common guard in the app."""
    if not user.is_hr:
        raise _forbidden(("hr",))
    return user


def csrf_protected(
    request: Request,
    user: Annotated[Identity, Depends(current_user)],
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> Identity:
    """Double-submit CSRF check for any state-changing route.

    Previously only the SSE resume route called `verify_csrf`, which left every
    other mutating endpoint -- create employee, run agent, execute a
    deprovisioning plan, change password -- relying on `SameSite=Lax` alone.
    Lax stops a cross-site POST but is a single cookie-attribute away from not
    doing so, and it is the kind of protection that is easy to lose silently.

    Attached as a router-level dependency rather than remembered per route, so
    a new mutating endpoint is protected by where it lives, not by its author.
    """
    verify_csrf(request, x_csrf_token)
    return user


CurrentUser = Annotated[Identity, Depends(current_user)]
HRUser = Annotated[Identity, Depends(require_hr)]
OptionalUser = Annotated[Identity | None, Depends(optional_user)]

#: Staff who work cases. Employees are excluded: they are the subject of a
#: case, not a participant in clearing one.
StaffUser = Annotated[Identity, Depends(require_roles("hr", "it", "manager"))]
#: IT owns access revocation; HR is the escalation path over it.
DeprovUser = Annotated[Identity, Depends(require_roles("hr", "it"))]
#: The handover workspace. Managers only, and unlike `DeprovUser` this one
#: does NOT admit HR.
#:
#: It was given to HR at first by analogy with IT's clearance page, on the
#: reasoning that HR is the escalation path. The analogy does not hold: HR
#: already has a page for every case, with all five stages on it and the
#: override recorded as an escalation, so admitting them here adds no
#: capability -- it only puts a second, manager-shaped view of the same case in
#: front of the role the split exists to keep out. If HR has to clear a
#: handover because the manager is away, they do it from the case page, where
#: the audit trail marks it as somebody acting outside their own stage.
ManagerUser = Annotated[Identity, Depends(require_roles("manager"))]
#: Account administration. IT owns it, not HR: creating, disabling and
#: role-changing logins is access management, which is the same job as
#: revoking a leaver's systems -- and the team already trusted with the
#: deprovisioning connectors is the one that should hold it.
#:
#: This is the one place HR is *not* a blanket override. HR keeping a path to
#: grant itself any role would make the separation cosmetic. If every IT
#: account is ever locked out, recovery is `python -m scripts.create_users`
#: on the host, which is a deliberate, logged, physical-access step.
AdminUser = Annotated[Identity, Depends(require_roles("it"))]
#: Anyone whose queue contains stages (i.e. not an employee).
QueueUser = StaffUser
