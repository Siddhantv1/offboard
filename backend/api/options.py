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
    # `all_kt_docs`, not `kt_docs`: the corpus table is three handovers written
    # before this product existed, so a manager whose report uploaded theirs
    # through the portal found an empty dropdown and no way to review the
    # document the portal had just collected.
    for d in store.all_kt_docs():
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


def _newer_interview(candidate: object, current: object) -> bool:
    """Which of two interviews for one person the reader should be offered.

    The one they filled in here wins. `all_interviews()` is the corpus plus
    the portal submissions, and for anyone who was already in the seeded data
    and has since answered the form there is one of each -- two records of the
    same conversation, differing only in that the corpus row is a stand-in
    written before this product existed. Offering both asks the reader to pick
    between two rows labelled with the same person's name, and half the time
    they will summarise the stand-in.

    Ties (two of the same source, which the data does not currently contain)
    fall back to the later submission.
    """
    cand, cur = candidate.meta, current.meta  # type: ignore[attr-defined]
    if (cand.get("source") == "portal") != (cur.get("source") == "portal"):
        return cand.get("source") == "portal"
    return str(cand.get("submitted_date") or "") > str(cur.get("submitted_date") or "")


def interview_options(user: Identity) -> list[dict[str, str]]:
    """One interview per person, not one per record.

    Deliberately narrower than `all_interviews()`, which stays a union:
    `interview_trends` compares this quarter against the historical corpus and
    needs both. This is a picker for a human, and a picker with the same name
    twice is a picker that gets answered wrong.
    """
    allowed = {str(e.meta.get("id")) for e in visible_employees(user)}
    best: dict[str, object] = {}
    for d in store.all_interviews():
        employee_id = str(d.meta.get("employee_id") or "")
        if not (user.is_hr or employee_id in allowed):
            continue
        # An interview with no employee on it cannot be collapsed with
        # anything, so it keys on its own id and always survives.
        key = employee_id or f"iv:{d.meta.get('interview_id', d.slug)}"
        if key not in best or _newer_interview(d, best[key]):
            best[key] = d

    return [
        {"value": str(d.meta.get("interview_id", d.slug)),
         "label": _label(d.meta.get("employee_name") or d.meta.get("employee_id"),
                         d.meta.get("department"),
                         d.meta.get("interview_id", d.slug))}
        for d in best.values()  # type: ignore[union-attr]
    ]


#: Field name -> filler. Consulted by `serialize_agent` for any select field
#: the agent did not populate itself.
DYNAMIC = {
    "employee_id": employee_options,
    "case_id": case_options,
    "kt_id": kt_options,
    "interview_id": interview_options,
}


def manager_options() -> list[dict[str, str]]:
    """The managers a new employee can actually be given.

    Free text here was a real flaw: HR typed a name and an address, and if the
    address matched no account -- a typo, or a manager who had never been given
    a login -- then Knowledge Transfer and Manager Sign-off were unroutable for
    that person forever. `approver_roster` returns nobody, the stage sits on no
    queue, and nothing anywhere reports it. Offering only accounts that exist
    makes the bad state unreachable rather than merely unlikely.
    """
    from sqlalchemy import select

    from ..db import session_scope
    from ..db.models import User

    with session_scope() as s:
        rows = s.scalars(
            select(User)
            .where(User.role == "manager", User.is_active.is_(True))
            .order_by(User.full_name, User.email)
        ).all()
        return [{"value": u.email, "label": _label(u.full_name or u.email, u.email)}
                for u in rows]
