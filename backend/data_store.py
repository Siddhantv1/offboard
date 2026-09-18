"""Data store — PostgreSQL backed.

The public surface here is deliberately identical to the Markdown
implementation it replaces: same methods, same `Doc` shape, same ordering.
That is what let 15 agents and the orchestrator migrate without a single edit.
`tests/test_store_contract.py` pins it; if you change anything in this module,
that suite must still pass unmodified.

Two things changed underneath, both on purpose:

The `@lru_cache` that sat on every read method is gone. Against files with one
worker it was a harmless speed-up; against a database with more than one worker
it is a correctness bug -- worker A writes, worker B serves stale data forever.
Postgres does the caching now.

`policies()` and `process_docs()` still read Markdown from disk. They are the
RAG pipeline's source documents, chunked and embedded rather than queried, so
there is nothing to gain from putting them in a table.
"""
from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import frontmatter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import DATA_DIR
from .db import session_scope
from .db.models import CaseStage, Employee, ExitCase, Interview, KTDoc


@dataclass
class Doc:
    """A record in the shape agents expect.

    `path` is None for database-backed records. It only ever meant anything for
    the Markdown files, and nothing outside this module reads it.
    """

    meta: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    slug: str = ""
    path: Path | None = None

    @property
    def title(self) -> str:
        return str(
            self.meta.get("name")
            or self.meta.get("title")
            or self.meta.get("id")
            or self.slug
        )


def _load_dir(subdir: str) -> list[Doc]:
    """Markdown loader, still used for the RAG source documents."""
    folder = DATA_DIR / subdir
    if not folder.exists():
        return []
    docs: list[Doc] = []
    for path in sorted(folder.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        try:
            post = frontmatter.load(path)
            docs.append(Doc(meta=dict(post.metadata), body=post.content,
                            slug=path.stem, path=path))
        except Exception:  # noqa: BLE001 - skip a malformed file, keep the rest
            docs.append(Doc(meta={}, body=path.read_text(encoding="utf-8"),
                            slug=path.stem, path=path))
    return docs


def _doc(row: Any) -> Doc:
    """ORM row -> Doc. Must be called inside the session scope that loaded the
    row, so relationship attributes are still attached."""
    return Doc(meta=row.to_meta(), body=row.body or "", slug=row.slug)


def _as_date(value: Any, default: date | None = None) -> date | None:
    """Coerce a UI/YAML value to a real `date`.

    Dates are stored as dates, never strings. Writing `str(d)` here is what
    made `join_date` a date on hand-authored records and a string on
    UI-created ones -- the type depended on the record's provenance, so date
    arithmetic worked on some rows and raised on others.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return date.fromisoformat(text)
    except ValueError:
        return default


def _split_list(value: Any) -> list[str]:
    """UI sends multi-line/comma text for list fields; split it into items."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    parts = re.split(r"[\n,]", str(value))
    return [p.strip() for p in parts if p.strip()]


DEFAULT_CLEARANCE_STAGES = ("IT Clearance", "Finance Clearance", "HR Clearance",
                            "Knowledge Transfer", "Manager Sign-off")


def _next_id(session: Session, id_col: Any, prefix: str) -> str:
    """Next free `prefix-NNN`.

    Derived by scanning existing ids rather than a sequence, so the
    human-readable numbering stays contiguous with the seeded data and a
    re-seed does not leave a gap.

    Takes a transaction-scoped advisory lock keyed by `prefix` first: two
    concurrent callers both scanning-then-inserting without it could compute
    the same "next" id and collide (or worse, both succeed against
    different rows and silently skip a number). The lock releases itself
    when the enclosing transaction commits or rolls back.
    """
    session.execute(select(func.pg_advisory_xact_lock(zlib.crc32(prefix.encode()))))
    nums = [
        int(m.group(1))
        for value in session.scalars(select(id_col))
        if (m := re.fullmatch(rf"{prefix}-(\d+)", str(value)))
    ]
    return f"{prefix}-{max(nums, default=0) + 1:03d}"


class DataStore:
    # -- collections -------------------------------------------------------
    # Ordered by slug, which reproduces the filename order of the original
    # directory scan. The UI pickers and _next_id both depend on it.
    def employees(self) -> list[Doc]:
        with session_scope() as s:
            return [_doc(r) for r in s.scalars(
                select(Employee).order_by(Employee.slug)).all()]

    def exit_cases(self) -> list[Doc]:
        with session_scope() as s:
            return [_doc(r) for r in s.scalars(
                select(ExitCase).order_by(ExitCase.slug)).all()]

    def kt_docs(self) -> list[Doc]:
        with session_scope() as s:
            return [_doc(r) for r in s.scalars(
                select(KTDoc).order_by(KTDoc.slug)).all()]

    def interviews(self) -> list[Doc]:
        with session_scope() as s:
            return [_doc(r) for r in s.scalars(
                select(Interview).order_by(Interview.slug)).all()]

    # RAG source documents: still files. See the module docstring.
    def policies(self) -> list[Doc]:
        return _load_dir("policies")

    def process_docs(self) -> list[Doc]:
        return _load_dir("process-docs")

    # -- lookups -----------------------------------------------------------
    def employee(self, emp_id: str) -> Doc | None:
        return self._lookup(Employee, Employee.id, emp_id)

    def exit_case(self, case_id: str) -> Doc | None:
        return self._lookup(ExitCase, ExitCase.case_id, case_id)

    def kt_doc(self, kt_id: str) -> Doc | None:
        return self._lookup(KTDoc, KTDoc.id, kt_id)

    def interview(self, interview_id: str) -> Doc | None:
        return self._lookup(Interview, Interview.id, interview_id)

    @staticmethod
    def _lookup(model: Any, id_col: Any, value: str) -> Doc | None:
        """Resolve by id or by slug, case-insensitively, tolerating whitespace.

        Both paths are live: main.py falls back to the slug when a record has
        no id in its metadata, and ids arrive from HTML form fields and LLM
        tool-call arguments, neither of which reliably trims.

        The empty-value guard is load-bearing. Callers pass
        `str(inputs.get("case_id") or "")` straight through, and an empty
        needle used to match the first record whose id key was absent --
        handing back case-001 to an agent that had asked for no case at all.
        """
        needle = str(value).strip().lower()
        if not needle:
            return None
        with session_scope() as s:
            row = s.scalar(
                select(model)
                .where((func.lower(id_col) == needle)
                       | (func.lower(model.slug) == needle))
                .limit(1)
            )
            return _doc(row) if row else None

    # -- writes ------------------------------------------------------------
    def add_employee(self, fields: dict[str, Any]) -> tuple[Doc, Doc | None]:
        """Create an employee, and a starter exit case if they are exiting.

        Both inserts share one transaction. An employee marked exiting but left
        without a case would be invisible to the orchestrator, which picks a
        *case*, not an employee -- a half-created record that looks fine in the
        employee list and cannot be actioned.
        """
        name = str(fields.get("name") or "").strip()
        role = str(fields.get("role") or "").strip()
        department = str(fields.get("department") or "").strip()
        if not name or not role or not department:
            raise ValueError("name, role, and department are required.")

        try:
            tenure_years = float(fields.get("tenure_years") or 0)
        except (TypeError, ValueError):
            tenure_years = 0.0
        rating: Any = fields.get("performance_rating")
        try:
            rating = int(rating) if rating else None
        except (TypeError, ValueError):
            rating = None

        notes = str(fields.get("notes") or "").strip()
        status = str(fields.get("status") or "active").strip().lower()

        with session_scope() as s:
            emp_id = _next_id(s, Employee.id, "emp")
            emp = Employee(
                id=emp_id,
                slug=emp_id,
                name=name,
                role=role,
                department=department,
                email=(str(fields.get("email") or "").strip()
                       or f"{name.lower().replace(' ', '.')}@perficient.com"),
                manager=str(fields.get("manager") or "").strip(),
                manager_email=str(fields.get("manager_email") or "").strip(),
                tenure_years=tenure_years,
                join_date=_as_date(fields.get("join_date"), default=date.today()),
                location=str(fields.get("location") or "").strip(),
                employment_type=str(fields.get("employment_type") or "Full-time").strip(),
                performance_rating=rating,
                criticality=str(fields.get("criticality") or "medium").strip().lower(),
                status=status,
                project_assignments=_split_list(fields.get("project_assignments")),
                access_systems=_split_list(fields.get("access_systems")),
                assets_assigned=_split_list(fields.get("assets_assigned")),
                body=(f"# {name}\n\n"
                      + (notes or f"{name} is a {role} in {department}.") + "\n"),
                extra={},
            )
            s.add(emp)
            s.flush()

            emp_doc = _doc(emp)
            case_doc = None
            if status == "exiting":
                case = _insert_case(s, emp, last_working_day=fields.get("last_working_day"))
                case_doc = _doc(case)
        return emp_doc, case_doc

    def add_exit_case(self, employee: Doc, *,
                      last_working_day: str | None = None) -> Doc:
        """Create a starter exit case (all stages pending) for an employee."""
        emp_id = str(employee.meta.get("id") or "").strip()
        with session_scope() as s:
            emp = s.get(Employee, emp_id) if emp_id else None
            if emp is None:
                raise ValueError(f"Unknown employee {emp_id!r}.")
            return _doc(_insert_case(s, emp, last_working_day=last_working_day))


def _insert_case(session: Session, emp: Employee, *,
                 last_working_day: Any) -> ExitCase:
    """Insert a starter case with all five clearance stages pending."""
    case_id = _next_id(session, ExitCase.case_id, "case")
    today = date.today()
    case = ExitCase(
        case_id=case_id,
        slug=case_id,
        employee_id=emp.id,
        employee_name=emp.name,
        exit_type="resignation",
        last_working_day=_as_date(last_working_day, default=today),
        initiated_date=today,
        current_stage=DEFAULT_CLEARANCE_STAGES[0],
        overall_status="on_track",
        body=(f"# Exit Case {case_id} — {emp.name} ({emp.id})\n\n"
              "Auto-created when the employee was added; all clearance stages "
              "start pending.\n"),
        extra={},
        stages=[
            CaseStage(ordinal=i, name=name, owner="", status="pending", days_open=0)
            for i, name in enumerate(DEFAULT_CLEARANCE_STAGES)
        ],
    )
    session.add(case)
    session.flush()
    return case


store = DataStore()
