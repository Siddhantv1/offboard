"""Data store.

Loads the Markdown corpus under `data/` (employees, exit cases, policies,
process docs, KT docs, interviews) into memory. Each file is parsed as YAML
frontmatter + body via python-frontmatter. The store is intentionally simple —
directory scans, cached at first use — since the dataset is small demo data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import frontmatter

from .config import DATA_DIR


@dataclass
class Doc:
    """A parsed Markdown document."""

    path: Path
    meta: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    @property
    def slug(self) -> str:
        return self.path.stem

    @property
    def title(self) -> str:
        return str(
            self.meta.get("name")
            or self.meta.get("title")
            or self.meta.get("id")
            or self.slug
        )


def _load_dir(subdir: str) -> list[Doc]:
    folder = DATA_DIR / subdir
    if not folder.exists():
        return []
    docs: list[Doc] = []
    for path in sorted(folder.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        try:
            post = frontmatter.load(path)
            docs.append(Doc(path=path, meta=dict(post.metadata), body=post.content))
        except Exception:  # noqa: BLE001 - skip a malformed file, keep the rest
            docs.append(Doc(path=path, meta={}, body=path.read_text(encoding="utf-8")))
    return docs


def _next_id(existing: list[Doc], prefix: str) -> str:
    nums = [
        int(m.group(1))
        for d in existing
        if (m := re.match(rf"{prefix}-(\d+)$", d.slug))
    ]
    return f"{prefix}-{max(nums, default=0) + 1:03d}"


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


class DataStore:
    @lru_cache(maxsize=1)  # noqa: B019 - single long-lived instance
    def employees(self) -> list[Doc]:
        return _load_dir("employees")

    @lru_cache(maxsize=1)  # noqa: B019
    def exit_cases(self) -> list[Doc]:
        return _load_dir("exit-cases")

    @lru_cache(maxsize=1)  # noqa: B019
    def policies(self) -> list[Doc]:
        return _load_dir("policies")

    @lru_cache(maxsize=1)  # noqa: B019
    def process_docs(self) -> list[Doc]:
        return _load_dir("process-docs")

    @lru_cache(maxsize=1)  # noqa: B019
    def kt_docs(self) -> list[Doc]:
        return _load_dir("kt-docs")

    @lru_cache(maxsize=1)  # noqa: B019
    def interviews(self) -> list[Doc]:
        return _load_dir("interviews")

    def add_employee(self, fields: dict[str, Any]) -> tuple[Doc, Doc | None]:
        """Write a new employee record to `data/employees/` and refresh the
        in-memory cache so it's visible immediately (no server restart).

        If the employee is marked "exiting", also creates a starter exit case
        for them -- otherwise they'd have no case, and the orchestrator (which
        picks a *case*, not an employee) would never be able to list them."""
        name = str(fields.get("name") or "").strip()
        role = str(fields.get("role") or "").strip()
        department = str(fields.get("department") or "").strip()
        if not name or not role or not department:
            raise ValueError("name, role, and department are required.")

        emp_id = _next_id(self.employees(), "emp")
        email = str(fields.get("email") or "").strip() or (
            name.lower().replace(" ", ".") + "@perficient.com"
        )
        try:
            tenure_years = float(fields.get("tenure_years") or 0)
        except (TypeError, ValueError):
            tenure_years = 0.0
        performance_rating = fields.get("performance_rating")
        try:
            performance_rating = int(performance_rating) if performance_rating else None
        except (TypeError, ValueError):
            performance_rating = None

        meta: dict[str, Any] = {
            "id": emp_id,
            "name": name,
            "email": email,
            "role": role,
            "department": department,
            "manager": str(fields.get("manager") or "").strip(),
            "manager_email": str(fields.get("manager_email") or "").strip(),
            "tenure_years": tenure_years,
            "join_date": str(fields.get("join_date") or date.today().isoformat()),
            "location": str(fields.get("location") or "").strip(),
            "employment_type": str(fields.get("employment_type") or "Full-time").strip(),
            "project_assignments": _split_list(fields.get("project_assignments")),
            "access_systems": _split_list(fields.get("access_systems")),
            "assets_assigned": _split_list(fields.get("assets_assigned")),
            "performance_rating": performance_rating,
            "criticality": str(fields.get("criticality") or "medium").strip().lower(),
            "status": str(fields.get("status") or "active").strip().lower(),
        }
        notes = str(fields.get("notes") or "").strip()
        body = f"# {name}\n\n" + (notes or f"{name} is a {role} in {department}.") + "\n"

        path = DATA_DIR / "employees" / f"{emp_id}.md"
        frontmatter.dump(frontmatter.Post(body, **meta), path)

        self.employees.cache_clear()
        doc = self.employee(emp_id)
        assert doc is not None  # just written it

        case_doc = None
        if meta["status"] == "exiting":
            case_doc = self.add_exit_case(doc, last_working_day=fields.get("last_working_day"))
        return doc, case_doc

    def add_exit_case(self, employee: Doc, *, last_working_day: str | None = None) -> Doc:
        """Create a starter exit case (all stages pending) for an employee."""
        case_id = _next_id(self.exit_cases(), "case")
        today = date.today().isoformat()
        meta: dict[str, Any] = {
            "case_id": case_id,
            "employee_id": employee.meta.get("id"),
            "employee_name": employee.meta.get("name"),
            "exit_type": "resignation",
            "last_working_day": str(last_working_day or today),
            "initiated_date": today,
            "current_stage": DEFAULT_CLEARANCE_STAGES[0],
            "overall_status": "on_track",
            "stages": [
                {"name": name, "owner": "", "status": "pending", "days_open": 0}
                for name in DEFAULT_CLEARANCE_STAGES
            ],
            # Filled in by the Document Collection agent as forms come in.
            "documents_submitted": [],
        }
        body = (
            f"# Exit Case {case_id} — {employee.meta.get('name')} ({employee.meta.get('id')})\n\n"
            "Auto-created when the employee was added; all clearance stages start pending.\n"
        )
        path = DATA_DIR / "exit-cases" / f"{case_id}.md"
        frontmatter.dump(frontmatter.Post(body, **meta), path)

        self.exit_cases.cache_clear()
        case_doc = self.exit_case(case_id)
        assert case_doc is not None  # just written it
        return case_doc

    # -- lookups -----------------------------------------------------------
    def employee(self, emp_id: str) -> Doc | None:
        return self._by_id(self.employees(), emp_id, ("id",))

    def exit_case(self, case_id: str) -> Doc | None:
        return self._by_id(self.exit_cases(), case_id, ("case_id", "id"))

    def kt_doc(self, kt_id: str) -> Doc | None:
        return self._by_id(self.kt_docs(), kt_id, ("kt_id", "id"))

    def interview(self, interview_id: str) -> Doc | None:
        return self._by_id(self.interviews(), interview_id, ("interview_id", "id"))

    @staticmethod
    def _by_id(docs: list[Doc], value: str, keys: tuple[str, ...]) -> Doc | None:
        """Resolve by id or by slug, case-insensitively.

        The empty-value guard is load-bearing. Callers pass
        `str(inputs.get("case_id") or "")` straight through, and exit cases are
        matched on ("case_id", "id") -- a key they do not carry. Without the
        guard, `.get("id", "")` returned "" for every case, matched the empty
        search value, and handed back case-001: an agent asked for no case at
        all would silently act on somebody else's.
        """
        value = str(value).strip().lower()
        if not value:
            return None
        for doc in docs:
            if doc.slug.lower() == value:
                return doc
            for key in keys:
                candidate = doc.meta.get(key)
                # A missing key must not compare equal to anything.
                if candidate is not None and str(candidate).strip().lower() == value:
                    return doc
        return None


store = DataStore()
