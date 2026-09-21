"""Import the Markdown corpus under `data/` into PostgreSQL.

After the migration the Markdown files are seed data, not the system of record.
This script is the bridge, and is idempotent so it can be re-run to refresh a
development database:

    python -m backend.db.seed

Fields with a typed column are mapped explicitly; anything else is carried in
`extra`, so a new field in the source data is preserved rather than dropped
silently. `extra` is a DateAwareJSONB column, so a date in there survives as a
date -- no stringifying needed here.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from typing import Any, Iterable

import frontmatter
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import DATA_DIR
from ..policy import stage_key_for_name
from . import session_scope
from .models import (CaseDocument, CaseStage, Employee, ExitCase, Interview,
                     KTDoc)

# Frontmatter keys that have a typed column. Anything not listed lands in
# `extra`. Keep these in step with the models or a field will be stored twice.
EMPLOYEE_COLUMNS = {
    "id", "name", "email", "role", "department", "manager", "manager_email",
    "tenure_years", "join_date", "location", "employment_type",
    "performance_rating", "criticality", "status", "project_assignments",
    "access_systems", "assets_assigned",
}
CASE_COLUMNS = {
    "case_id", "employee_id", "employee_name", "exit_type", "last_working_day",
    "initiated_date", "current_stage", "overall_status", "stages",
    "documents_submitted",
}
STAGE_COLUMNS = {"name", "owner", "status", "days_open"}
KT_COLUMNS = {
    "kt_id", "case_id", "employee_id", "employee_name", "role", "successor",
    "reviewed", "submitted_date",
}
INTERVIEW_COLUMNS = {
    "interview_id", "employee_id", "employee_name", "department", "sentiment",
    "would_recommend", "submitted_date",
}

# Marks a document row reconstructed from a case's `documents_submitted` list
# rather than uploaded through the app. Those predate the system, so there is
# no scan, no OCR text and no real verdict behind the acceptance.
SEED_UPLOADER = "seed:exit-case-record"


def _docs(subdir: str) -> list[tuple[str, dict[str, Any], str]]:
    """(slug, metadata, body) for each Markdown file, in filename order."""
    folder = DATA_DIR / subdir
    if not folder.exists():
        return []
    out: list[tuple[str, dict[str, Any], str]] = []
    for path in sorted(folder.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        post = frontmatter.load(path)
        out.append((path.stem, dict(post.metadata), post.content))
    return out


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _as_list(value: Any) -> list[str] | None:
    """Preserve the distinction between an absent field and an empty list --
    `to_meta` omits None but keeps [], and the contract test compares exactly."""
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rest(meta: dict[str, Any], known: set[str]) -> dict[str, Any]:
    """Fields without a typed column. Dates stay dates: DateAwareJSONB keeps them."""
    return {k: v for k, v in meta.items() if k not in known}


def seed_employees(session: Session) -> int:
    rows = _docs("employees")
    for slug, meta, body in rows:
        emp_id = str(meta.get("id") or slug)
        emp = session.get(Employee, emp_id) or Employee(id=emp_id)
        emp.slug = slug
        emp.name = str(meta.get("name") or slug)
        emp.email = meta.get("email")
        emp.role = meta.get("role")
        emp.department = meta.get("department")
        emp.manager = meta.get("manager")
        emp.manager_email = meta.get("manager_email")
        emp.tenure_years = meta.get("tenure_years")
        emp.join_date = _as_date(meta.get("join_date"))
        emp.location = meta.get("location")
        emp.employment_type = meta.get("employment_type")
        emp.performance_rating = _as_int(meta.get("performance_rating"))
        emp.criticality = meta.get("criticality")
        emp.status = meta.get("status")
        emp.project_assignments = _as_list(meta.get("project_assignments"))
        emp.access_systems = _as_list(meta.get("access_systems"))
        emp.assets_assigned = _as_list(meta.get("assets_assigned"))
        emp.body = body
        emp.extra = _rest(meta, EMPLOYEE_COLUMNS)
        session.merge(emp)
    session.flush()
    return len(rows)


def _seed_stages(session: Session, case_id: str, stages: Iterable[Any]) -> None:
    """Replace a case's stages wholesale.

    `ordinal` is positional, so a reordered or shortened source list must not
    leave orphaned rows behind at the old positions.
    """
    session.query(CaseStage).filter(CaseStage.case_id == case_id).delete(
        synchronize_session=False)
    for i, stage in enumerate(stages or []):
        if not isinstance(stage, dict):
            continue
        name = str(stage.get("name") or "?")
        session.add(CaseStage(
            case_id=case_id,
            ordinal=i,
            name=name,
            # Without this every seeded stage lands with a NULL stage_key, and
            # since that is what routes work to a role, a freshly bootstrapped
            # database has empty queues for everybody and refuses every
            # assignment. Migration c3a8d16b42f7 backfills the column, but it
            # runs before the seeder inserts any rows, so it catches nothing on
            # a clean install -- only on the developer's existing data.
            stage_key=stage_key_for_name(name),
            owner=str(stage.get("owner") or ""),
            status=str(stage.get("status") or "pending"),
            days_open=_as_int(stage.get("days_open")) or 0,
            extra=_rest(stage, STAGE_COLUMNS),
        ))


def _seed_submitted_documents(session: Session, case_id: str,
                              names: Iterable[str]) -> None:
    """Materialise `documents_submitted` as accepted document rows.

    The exit case records these as already collected, but the rows behind them
    predate the system: no scan, no OCR, no model verdict. They are marked with
    `uploaded_by = SEED_UPLOADER` so a real upload is always distinguishable
    from a historical assertion -- which matters, because an auditor asking to
    see the signed form will find nothing attached to these.

    Only seeded rows are removed on re-run; genuine uploads are left alone.
    """
    session.query(CaseDocument).filter(
        CaseDocument.case_id == case_id,
        CaseDocument.uploaded_by == SEED_UPLOADER,
    ).delete(synchronize_session=False)
    for name in names or []:
        session.add(CaseDocument(
            case_id=case_id,
            document_name=str(name),
            verdict="accepted",
            reason="Recorded as submitted in the source exit case record.",
            uploaded_by=SEED_UPLOADER,
            validation={},
        ))


def seed_cases(session: Session) -> int:
    rows = _docs("exit-cases")
    for slug, meta, body in rows:
        case_id = str(meta.get("case_id") or slug)
        case = session.get(ExitCase, case_id) or ExitCase(case_id=case_id)
        case.slug = slug
        case.employee_id = str(meta.get("employee_id") or "")
        case.employee_name = meta.get("employee_name")
        case.exit_type = meta.get("exit_type")
        case.last_working_day = _as_date(meta.get("last_working_day"))
        case.initiated_date = _as_date(meta.get("initiated_date"))
        case.current_stage = meta.get("current_stage")
        case.overall_status = meta.get("overall_status")
        case.body = body
        case.extra = _rest(meta, CASE_COLUMNS)
        session.merge(case)
        session.flush()   # the case must exist before its children reference it

        _seed_stages(session, case_id, meta.get("stages"))
        _seed_submitted_documents(session, case_id, meta.get("documents_submitted"))
    session.flush()
    return len(rows)


def seed_kt_docs(session: Session) -> int:
    rows = _docs("kt-docs")
    for slug, meta, body in rows:
        kt_id = str(meta.get("kt_id") or slug)
        doc = session.get(KTDoc, kt_id) or KTDoc(id=kt_id)
        doc.slug = slug
        doc.case_id = meta.get("case_id")
        doc.employee_id = meta.get("employee_id")
        doc.employee_name = meta.get("employee_name")
        doc.role = meta.get("role")
        doc.successor = meta.get("successor")
        doc.reviewed = meta.get("reviewed")
        doc.submitted_date = _as_date(meta.get("submitted_date"))
        doc.body = body
        doc.extra = _rest(meta, KT_COLUMNS)
        session.merge(doc)
    session.flush()
    return len(rows)


def seed_interviews(session: Session) -> int:
    rows = _docs("interviews")
    for slug, meta, body in rows:
        iv_id = str(meta.get("interview_id") or slug)
        iv = session.get(Interview, iv_id) or Interview(id=iv_id)
        iv.slug = slug
        iv.employee_id = meta.get("employee_id")
        iv.employee_name = meta.get("employee_name")
        iv.department = meta.get("department")
        iv.sentiment = meta.get("sentiment")
        iv.would_recommend = meta.get("would_recommend")
        iv.submitted_date = _as_date(meta.get("submitted_date"))
        iv.body = body
        iv.extra = _rest(meta, INTERVIEW_COLUMNS)
        session.merge(iv)
    session.flush()
    return len(rows)


def seed_all(session: Session) -> dict[str, int]:
    """Employees first: exit cases carry a foreign key to them."""
    return {
        "employees": seed_employees(session),
        "exit_cases": seed_cases(session),
        "kt_docs": seed_kt_docs(session),
        "interviews": seed_interviews(session),
    }


def _check_referential_integrity(session: Session) -> list[str]:
    """Cases whose employee_id matches no employee.

    The FK makes this impossible to insert, but the seeder runs before anyone
    reads the data and a clear message here beats an IntegrityError traceback.
    """
    known = set(session.scalars(select(Employee.id)))
    return [
        f"{case.case_id} references unknown employee {case.employee_id!r}"
        for case in session.scalars(select(ExitCase))
        if case.employee_id not in known
    ]


def main() -> int:
    try:
        with session_scope() as session:
            counts = seed_all(session)
            problems = _check_referential_integrity(session)
    except Exception as exc:  # noqa: BLE001 - one clear message, not a traceback
        print(f"Seed failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    for name, count in counts.items():
        print(f"  {name}: {count}")
    if problems:
        print("\nReferential problems:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
