"""Create login accounts.

    python -m scripts.create_users                      # seed HR + every leaver
    python -m scripts.create_users --show               # re-print who exists
    python -m scripts.create_users --email a@b.com --role hr --password ...
    python -m scripts.create_users --reset a@b.com      # new password for one

Idempotent: re-running leaves existing accounts alone unless --reset is given.

Employee accounts are created only for people who actually have an exit case.
Everyone else has nothing to log in and look at, and an account that can see
nothing is just an unnecessary credential.

Generated passwords are printed ONCE and never stored in readable form. They
are marked must_change_password, so the holder is prompted to replace them.
"""
from __future__ import annotations

import argparse
import secrets
import string
import sys

from sqlalchemy import select

from backend.auth import hash_password
from backend.db import session_scope
from backend.db.models import Employee, ExitCase, User

# Unambiguous alphabet: no O/0, l/1/I. These get read off a screen and typed.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"

DEFAULT_HR = [
    ("hr@perficient.com", "HR Operations"),
]


def make_password(length: int = 14) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def upsert(session, *, email: str, role: str, full_name: str | None,
           employee_id: str | None, password: str | None,
           force: bool = False) -> tuple[str, str | None]:
    """Create the account if absent. Returns (status, plaintext-if-new)."""
    email = email.strip().lower()
    existing = session.scalar(select(User).where(User.email == email))

    if existing and not force:
        return "exists", None

    secret = password or make_password()
    if existing:
        existing.password_hash = hash_password(secret)
        existing.must_change_password = password is None
        existing.failed_attempts = 0
        existing.locked_until = None
        existing.is_active = True
        return "reset", secret

    session.add(User(
        email=email,
        password_hash=hash_password(secret),
        role=role,
        employee_id=employee_id,
        full_name=full_name,
        must_change_password=password is None,
    ))
    return "created", secret


def seed(session) -> list[tuple[str, str, str, str | None]]:
    """(email, role, status, password) for everything seeded."""
    out = []

    for email, name in DEFAULT_HR:
        status, secret = upsert(
            session, email=email, role="hr", full_name=name,
            employee_id=None, password=None)
        out.append((email, "hr", status, secret))

    # Only people with an exit case get a portal account.
    leavers = session.scalars(
        select(Employee).join(ExitCase, ExitCase.employee_id == Employee.id)
        .order_by(Employee.id)
    ).unique().all()

    for emp in leavers:
        email = (emp.email or "").strip().lower()
        if not email:
            print(f"  ! {emp.id} ({emp.name}) has no email address; skipped", file=sys.stderr)
            continue
        status, secret = upsert(
            session, email=email, role="employee", full_name=emp.name,
            employee_id=emp.id, password=None)
        out.append((email, "employee", status, secret))

    return out


def show(session) -> None:
    rows = session.scalars(select(User).order_by(User.role, User.email)).all()
    if not rows:
        print("  no accounts yet -- run without --show to seed them")
        return
    print(f"  {'EMAIL':38} {'ROLE':9} {'EMPLOYEE':10} ACTIVE")
    for u in rows:
        print(f"  {u.email:38} {u.role:9} {u.employee_id or '-':10} {'yes' if u.is_active else 'NO'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--show", action="store_true", help="list accounts and exit")
    ap.add_argument("--email", help="create or update a single account")
    ap.add_argument("--role", choices=("hr", "employee"), default="hr")
    ap.add_argument("--employee-id", help="required for --role employee")
    ap.add_argument("--password", help="set explicitly instead of generating one")
    ap.add_argument("--reset", metavar="EMAIL", help="issue a new password")
    args = ap.parse_args()

    with session_scope() as s:
        if args.show:
            show(s)
            return 0

        if args.reset:
            status, secret = upsert(
                s, email=args.reset, role="hr", full_name=None,
                employee_id=None, password=args.password, force=True)
            if status == "created":
                print(f"No account for {args.reset}; created one.")
            print(f"\n  {args.reset}\n  {secret}\n\nAll existing sessions for this "
                  "account are now invalid.")
            return 0

        if args.email:
            if args.role == "employee" and not args.employee_id:
                print("--role employee needs --employee-id", file=sys.stderr)
                return 1
            status, secret = upsert(
                s, email=args.email, role=args.role, full_name=None,
                employee_id=args.employee_id, password=args.password)
            if status == "exists":
                print(f"{args.email} already exists. Use --reset to change its password.")
                return 0
            print(f"\n  {args.email}  ({args.role})\n  {secret}\n")
            return 0

        results = seed(s)

    fresh = [r for r in results if r[3]]
    print(f"\n{len(results)} account(s) checked, {len(fresh)} created.\n")
    if fresh:
        print(f"  {'EMAIL':38} {'ROLE':9} PASSWORD")
        for email, role, _, secret in fresh:
            print(f"  {email:38} {role:9} {secret}")
        print("\nThese are shown once. They are stored only as Argon2 hashes, so a")
        print("lost password must be reissued with --reset, not recovered.")
    else:
        print("  Everything already existed. --reset EMAIL to issue a new password.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
