"""Create login accounts.

    python -m scripts.create_users                      # seed all four roles
    python -m scripts.create_users --show               # re-print who exists
    python -m scripts.create_users --email a@b.com --role it --full-name "..."
    python -m scripts.create_users --reset a@b.com      # new password for one
    python -m scripts.create_users --demo-passwords     # DEV ONLY, see below

Idempotent: re-running leaves existing accounts alone unless --reset is given.

Who gets an account, and why:

* **HR** -- one operations account, plus everyone in the HR department. HR is
  the escalation path over every stage, and owns HR and Finance clearance.
* **IT** -- everyone in the IT department. They clear system access on every
  exit case, so their scope is the whole book of work rather than a team.
* **Manager** -- one per distinct `manager_email` in the employee corpus. The
  org chart already encodes who manages whom, so deriving the roster from it
  means the two cannot disagree. Note these people are mostly *not* employee
  records themselves (Vikram Sharma manages three leavers but has no row in
  `employees`), which is why they are seeded from the manager side.
* **Employee** -- only people who actually have an exit case. Everyone else has
  nothing to log in and look at, and an account that can see nothing is just an
  unnecessary credential.

Run `python -m scripts.link_stage_owners` afterwards to connect these accounts
to the clearance stages they own.

Generated passwords are printed ONCE and never stored in readable form. They
are marked must_change_password, so the holder is prompted to replace them.

`--demo-passwords` is the exception, and it is for local demos only: it puts
one shared, known password on every account and clears must_change_password so
you can sign in as each role without a detour. It refuses to run unless
COOKIE_SECURE is off, which is the same switch that says "this is local http,
not a deployment" -- the point being that the guard is a setting you have
already had to make deliberately, not a prompt anyone can click through.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select

from backend.auth import generate_temp_password, hash_password
from backend.db import session_scope
from backend.config import COOKIE_SECURE
from backend.db.models import ROLES, CaseStage, Employee, ExitCase, User

DEFAULT_HR = [
    ("hr@perficient.com", "HR Operations"),
]

#: Department -> the role its staff get. Matched case-insensitively against
#: `Employee.department`.
#:
#: Finance is deliberately absent. Finance Clearance is owned by the `hr` role
#: (there is no `finance` role), but giving the Finance team an `hr` account
#: would hand them HR's blanket override over every case and every agent --
#: far more than clearing no-dues requires. Those stages stay unclaimed until
#: an HR user picks them up, which is what "unclaimed" is for.
DEPARTMENT_ROLES = {
    "it": "it",
    "hr": "hr",
}


def upsert(session, *, email: str, role: str, full_name: str | None,
           employee_id: str | None, password: str | None,
           force: bool = False) -> tuple[str, str | None]:
    """Create the account if absent. Returns (status, plaintext-if-new)."""
    email = email.strip().lower()
    existing = session.scalar(select(User).where(User.email == email))

    if existing and not force:
        return "exists", None

    secret = password or generate_temp_password()
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

    # -- Departmental staff: IT clears access, HR clears HR and Finance ------
    for department, role in sorted(DEPARTMENT_ROLES.items()):
        staff = session.scalars(
            select(Employee)
            .where(func.lower(Employee.department) == department)
            .order_by(Employee.id)
        ).unique().all()

        for emp in staff:
            email = (emp.email or "").strip().lower()
            if not email:
                print(f"  ! {emp.id} ({emp.name}) has no email address; skipped",
                      file=sys.stderr)
                continue
            # employee_id is set even though the role is not `employee`: it
            # links them to their own record, so an IT engineer who later exits
            # can still see their own case. ck_employee_has_record only
            # *requires* the link for role='employee'.
            status, secret = upsert(
                session, email=email, role=role, full_name=emp.name,
                employee_id=emp.id, password=None)
            out.append((email, role, status, secret))

    # -- Managers: derived from the org chart already in the corpus ----------
    # Deliberately keyed on the email, not the name: the email is what the
    # scope check compares against (`deps._visible_to`), so seeding from any
    # other field could produce an account that matches nothing.
    managers: dict[str, str] = {}
    for emp in session.scalars(select(Employee).order_by(Employee.id)).unique().all():
        email = (emp.manager_email or "").strip().lower()
        if not email:
            continue
        managers.setdefault(email, (emp.manager or "").strip() or email)

    for email, name in sorted(managers.items()):
        if email in {e for e, _ in DEFAULT_HR}:
            continue  # already seeded with a wider role
        status, secret = upsert(
            session, email=email, role="manager", full_name=name,
            employee_id=None, password=None)
        out.append((email, "manager", status, secret))

    # -- Employees: only people with an exit case ----------------------------
    leavers = session.scalars(
        select(Employee).join(ExitCase, ExitCase.employee_id == Employee.id)
        .order_by(Employee.id)
    ).unique().all()

    for emp in leavers:
        email = (emp.email or "").strip().lower()
        if not email:
            print(f"  ! {emp.id} ({emp.name}) has no email address; skipped", file=sys.stderr)
            continue
        # upsert() returns "exists" for anyone already seeded above with a
        # wider role (an IT engineer who is also a leaver keeps the IT account,
        # which can see their own case anyway).
        status, secret = upsert(
            session, email=email, role="employee", full_name=emp.name,
            employee_id=emp.id, password=None)
        out.append((email, "employee", status, secret))

    return out


DEMO_PASSWORD = "offboard-demo-2026"


def demo_passwords(session) -> list[tuple[str, str, str]]:
    """Put one known password on every account. Local demos only.

    Returns (email, role, scope-description) sorted for display.
    """
    users = session.scalars(select(User)).all()
    for u in users:
        u.password_hash = hash_password(DEMO_PASSWORD)
        u.must_change_password = False
        u.failed_attempts = 0
        u.locked_until = None
        u.is_active = True

    order = {"hr": 0, "it": 1, "manager": 2, "employee": 3}
    out = []
    for u in sorted(users, key=lambda x: (order.get(x.role, 9), x.email)):
        if u.role == "employee":
            scope = "own exit case"
        else:
            # How much this account will actually see, so the list is useful
            # rather than merely accurate -- three of the seeded managers have
            # no exiting reports and would otherwise look broken.
            n = session.scalar(
                select(func.count()).select_from(CaseStage)
                .where(CaseStage.owner_user_id == u.id)
            ) or 0
            scope = f"{n} stage(s) assigned" if n else "nothing assigned yet"
        out.append((u.email, u.role, scope))
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
    ap.add_argument("--role", choices=ROLES, default="hr")
    ap.add_argument("--employee-id", help="required for --role employee")
    ap.add_argument("--full-name", help="display name shown in the UI")
    ap.add_argument("--password", help="set explicitly instead of generating one")
    ap.add_argument("--reset", metavar="EMAIL", help="issue a new password")
    ap.add_argument("--demo-passwords", action="store_true",
                    help="DEV ONLY: set one known password on every account")
    args = ap.parse_args()

    if args.demo_passwords:
        if COOKIE_SECURE:
            print("Refusing: COOKIE_SECURE is on, so this is not a local dev "
                  "environment.\nSet COOKIE_SECURE=0 in .env if it really is.",
                  file=sys.stderr)
            return 1
        with session_scope() as s:
            rows = demo_passwords(s)
        print(f"\n  Every account below now has the password:  {DEMO_PASSWORD}\n")
        last = None
        for email, role, scope in rows:
            if role != last:
                print(f"  {role.upper()}")
                last = role
            print(f"    {email:34} {scope}")
        print("\n  Local demo credentials. Do not do this anywhere real.")
        return 0

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
                s, email=args.email, role=args.role, full_name=args.full_name,
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
