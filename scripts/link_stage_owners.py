"""Link clearance stages to the accounts that own them.

    python -m scripts.link_stage_owners            # link what can be linked
    python -m scripts.link_stage_owners --dry-run  # show, change nothing
    python -m scripts.link_stage_owners --show     # current assignment state
    python -m scripts.link_stage_owners --repair   # also fix mis-routed rows

`CaseStage.owner` is a free-text name ("Sanya Kapoor") inherited from the
Markdown corpus. `owner_user_id` is the real link the work queues run on. This
connects the two.

**Why this is a script and not part of the migration.** Matching a name to an
account needs accounts, and accounts are created by `scripts/create_users.py`
*after* seeding. A backfill inside Alembic would therefore run before the data
it depends on exists: on a fresh clone or in CI, `alembic upgrade head` runs
first against empty tables, links nothing, and never runs again -- leaving
every install except the original developer's with permanently unassigned
queues and no error at all. Alembic's contract is that revision N is a pure
function of revision N-1; a step whose result depends on whether someone ran a
demo-data script breaks that contract invisibly.

Safe to re-run at any time, and worth re-running after adding an account.

**By default it only ever fills blanks.** A row whose `owner_user_id` is
already set was assigned by a person through the UI, and a name-matching
heuristic must never overrule that.

**`--repair` is the one exception, and it is narrow.** It touches only rows
where the owner *name* and the owner *id* are different people. Those cannot
be produced by any current write path -- `backend.stages.set_stage_owner` sets
both columns together -- so such a row is not somebody's decision, it is
damage. See `mismatches()` for where the damage came from.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

from sqlalchemy import func, select

from backend.db import session_scope
from backend.db.models import CaseStage, Employee, User


def _candidates(session) -> dict[str, int]:
    """name (lowercased) -> user id, best match first.

    Three paths, in descending order of directness. They are layered because
    the corpus names are people of three different kinds: an IT engineer who is
    an employee *and* has an account, a manager who has an account but no
    employee record at all (Vikram Sharma manages three leavers and is in no
    `employees` row), and an active employee who has neither.
    """
    by_name: dict[str, int] = {}

    # 3. weakest: employee name -> that employee's email -> an account.
    email_to_user = {
        (u.email or "").strip().lower(): u.id
        for u in session.scalars(select(User)).all()
    }
    for emp in session.scalars(select(Employee)).all():
        uid = email_to_user.get((emp.email or "").strip().lower())
        if uid is not None:
            by_name[(emp.name or "").strip().lower()] = uid

    # 2. an account explicitly linked to an employee record.
    for u in session.scalars(select(User).where(User.employee_id.isnot(None))).all():
        emp = session.get(Employee, u.employee_id)
        if emp is not None:
            by_name[(emp.name or "").strip().lower()] = u.id

    # 1. strongest: the account's own display name. Set last so it wins --
    #    it is the only one a person maintains directly.
    for u in session.scalars(select(User).where(User.full_name.isnot(None))).all():
        name = (u.full_name or "").strip().lower()
        if name:
            by_name[name] = u.id

    by_name.pop("", None)
    return by_name


def link(session, *, dry_run: bool = False) -> tuple[int, list[str]]:
    """Returns (linked_count, unmatched owner names)."""
    lookup = _candidates(session)

    rows = session.scalars(
        select(CaseStage).where(
            CaseStage.owner_user_id.is_(None),
            func.trim(CaseStage.owner) != "",
        ).order_by(CaseStage.case_id, CaseStage.ordinal)
    ).all()

    linked = 0
    unmatched: dict[str, int] = {}
    for stage in rows:
        name = (stage.owner or "").strip().lower()
        uid = lookup.get(name)
        if uid is None:
            unmatched[stage.owner.strip()] = unmatched.get(stage.owner.strip(), 0) + 1
            continue
        if not dry_run:
            stage.owner_user_id = uid
        linked += 1

    if dry_run:
        session.expunge_all()

    return linked, [f"{n} ({c} stage{'s' if c > 1 else ''})"
                    for n, c in sorted(unmatched.items())]


def mismatches(session) -> list[dict[str, Any]]:
    """Stages whose owner *name* and owner *id* are different people.

    `backend.stages.set_stage_owner` writes both columns together, so every
    assignment and every claim leaves them agreeing. A row where they disagree
    can therefore only have come from code that bypassed it -- which is what
    the old claim-on-transition did, stamping the actor's id onto a stage while
    leaving the previous owner's name in place. Those rows display one person
    and queue for another, and nothing surfaces the contradiction.

    Being able to state that invariant is what makes this repair safe: it is
    not guessing which column is right, it is finding rows that no legitimate
    write path can produce.
    """
    lookup = _candidates(session)
    users = {u.id: u for u in session.scalars(select(User)).all()}

    out: list[dict[str, Any]] = []
    rows = session.scalars(
        select(CaseStage).where(
            CaseStage.owner_user_id.isnot(None),
            func.trim(CaseStage.owner) != "",
        ).order_by(CaseStage.case_id, CaseStage.ordinal)
    ).all()

    for stage in rows:
        name = (stage.owner or "").strip()
        linked = users.get(stage.owner_user_id)
        linked_name = (linked.full_name or linked.email or "") if linked else ""
        if name.lower() == linked_name.strip().lower():
            continue                                   # consistent, leave alone

        correct = lookup.get(name.lower())
        out.append({
            "stage": stage,
            "name": name,
            "linked_to": linked_name or f"user {stage.owner_user_id}",
            "correct_id": correct,
        })
    return out


def repair(session, *, dry_run: bool = False) -> list[str]:
    """Point every mismatched row back at the person it names.

    Where that name has no account the id is *cleared* rather than left
    pointing at the wrong person. An unrouted stage is a state the queue
    already handles -- it shows as unclaimed and anyone with the role can pick
    it up -- whereas a wrongly routed one is invisible to the person
    accountable for it and silently present in somebody else's queue.
    """
    notes = []
    for m in mismatches(session):
        stage, name = m["stage"], m["name"]
        where = f"{stage.case_id} {stage.stage_key}"
        if m["correct_id"] is not None:
            if not dry_run:
                stage.owner_user_id = m["correct_id"]
            notes.append(f"{where}: {name} was routing to {m['linked_to']} "
                         f"-> relinked to {name}")
        else:
            if not dry_run:
                stage.owner_user_id = None
            notes.append(f"{where}: {name} was routing to {m['linked_to']} "
                         f"-> cleared ({name} has no account)")

    if dry_run:
        session.expunge_all()
    return notes


def show(session) -> None:
    total = session.scalar(select(func.count()).select_from(CaseStage)) or 0
    assigned = session.scalar(
        select(func.count()).select_from(CaseStage)
        .where(CaseStage.owner_user_id.isnot(None))
    ) or 0
    unowned = session.scalar(
        select(func.count()).select_from(CaseStage)
        .where(func.trim(CaseStage.owner) == "")
    ) or 0
    print(f"  {total} stage(s): {assigned} linked to an account, "
          f"{total - assigned} not ({unowned} have no owner name at all)")

    rows = session.execute(
        select(CaseStage.stage_key, func.count(), func.count(CaseStage.owner_user_id))
        .group_by(CaseStage.stage_key).order_by(CaseStage.stage_key)
    ).all()
    print(f"  {'STAGE':10} {'TOTAL':>6} {'LINKED':>7}")
    for key, n, linked in rows:
        print(f"  {str(key):10} {n:>6} {linked:>7}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report, change nothing")
    ap.add_argument("--show", action="store_true", help="current state, then exit")
    ap.add_argument("--repair", action="store_true",
                    help="also re-point rows whose owner name and owner id are "
                         "different people")
    args = ap.parse_args()

    with session_scope() as s:
        if args.show:
            show(s)
            return 0

        if args.repair:
            notes = repair(s, dry_run=args.dry_run)
            verb = "would repair" if args.dry_run else "repaired"
            print(f"\n  {verb} {len(notes)} mis-routed stage(s).")
            for n in notes:
                print(f"    - {n}")
            if not notes:
                print("    Every stage routes to the person it names.")

        linked, unmatched = link(s, dry_run=args.dry_run)

    verb = "would link" if args.dry_run else "linked"
    print(f"\n  {verb} {linked} stage(s) to an account.")
    if unmatched:
        print("\n  No account matches these owner names:")
        for entry in unmatched:
            print(f"    - {entry}")
        print("\n  That is normal for someone who is neither a leaver nor IT nor a")
        print("  manager -- they have no reason to log in. The stage stays unclaimed")
        print("  and anyone with the right role can pick it up. Re-run this after")
        print("  creating an account for them.")
    if not args.dry_run:
        print()
        with session_scope() as s:
            show(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
