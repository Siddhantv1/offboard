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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import generate_temp_password, hash_password
from .config import DATA_DIR
from .policy import stage_key_for_name
from .db import session_scope
from .db.models import CaseDocument, CaseStage, Employee, ExitCase, Interview, KTDoc, User


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


def _document_meta(d: CaseDocument) -> dict[str, Any]:
    """CaseDocument row -> the shape the review UI and its API need. Must be
    called inside the session scope that loaded the row (see `_doc`)."""
    return {
        "id": d.id,
        "document_name": d.document_name,
        "filename": d.filename,
        "verdict": d.verdict,
        "reason": d.reason,
        "uploaded_at": d.uploaded_at,
        "uploaded_by": d.uploaded_by,
        # Whether a file is actually stored, so the UI can decide whether to
        # offer "view" rather than linking to a 404 -- and what it is, so the
        # UI can pick a viewer. An <img> was the only one for a long time,
        # which is most of why only images were accepted.
        "has_file": d.image is not None,
        "content_type": d.content_type or None,
        "image_bytes": d.image_bytes,
        "reviewed_by": d.reviewed_by,
        "reviewed_at": d.reviewed_at,
        "review_note": d.review_note,
    }


#: Accepted ranges for the numeric employee fields. The form declares the same
#: bounds, but a form is a suggestion -- anyone can POST past it, and silently
#: coercing a bad value (the old behaviour: -12 became 0.0, "abc" became None)
#: writes a wrong record that looks deliberate ever after.
NUMERIC_BOUNDS: dict[str, tuple[float, float]] = {
    "tenure_years": (0, 60),
    "performance_rating": (1, 5),
}


def _as_number(value: Any, field_name: str, *, integer: bool = False,
               default: Any = None) -> Any:
    """Parse a bounded number, or raise ValueError naming the field.

    Raising rather than defaulting is the point. `add_employee` is the only
    write path for these columns, so a value that slips through here is a
    permanent wrong number on a compliance record.
    """
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name.replace('_', ' ')} must be a number.") from None

    low, high = NUMERIC_BOUNDS[field_name]
    if not (low <= parsed <= high):
        raise ValueError(
            f"{field_name.replace('_', ' ')} must be between "
            f"{low:g} and {high:g} (got {parsed:g})."
        )
    return parsed


def _require_date(value: Any, field_name: str, *, default: date | None = None) -> date | None:
    """Like `_as_date`, but refuses an unparseable value instead of falling
    back to the default -- otherwise a typo'd last working day silently
    becomes today, and the SLA clock starts from the wrong place."""
    if value is None or str(value).strip() == "":
        return default
    parsed = _as_date(value)
    if parsed is None:
        raise ValueError(
            f"{field_name.replace('_', ' ')} is not a valid date (use YYYY-MM-DD)."
        )
    if not (date(1970, 1, 1) <= parsed <= date(2100, 12, 31)):
        raise ValueError(f"{field_name.replace('_', ' ')} is out of range.")
    return parsed


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

    def submitted_interviews(self) -> list[Doc]:
        """Exit interviews the leavers actually filled in, as Docs.

        The `interviews` table is a read-only corpus of transcripts from
        interviews held before this product existed. Everything that analyses
        interviews reads that table, so the one interview the product itself
        collects -- seven questions, answered by the person leaving -- was
        invisible to every agent that exists to analyse interviews.

        Rendered into the same shape rather than given a parallel pipeline: one
        seam means `interview_trends` compares this quarter's real answers
        against the historical corpus without knowing there are two sources.
        """
        from .db.models import ExitInterviewResponse
        from .exit_interview import QUESTIONS

        prompts = {q["id"]: q["prompt"] for q in QUESTIONS}
        out: list[Doc] = []
        with session_scope() as s:
            rows = s.scalars(
                select(ExitInterviewResponse)
                .where(ExitInterviewResponse.submitted_at.isnot(None))
                .order_by(ExitInterviewResponse.case_id)
            ).all()
            cases = {c.case_id: c for c in s.scalars(select(ExitCase)).all()}
            for row in rows:
                case = cases.get(row.case_id)
                answers = dict(row.answers or {})
                body = "\n\n".join(
                    f"{prompts.get(qid, qid)}\n{text}"
                    for qid, text in answers.items() if str(text).strip()
                )
                recommend = str(answers.get("would_recommend", "")).strip().lower()
                out.append(Doc(
                    slug=f"interview-{row.case_id}",
                    body=body,
                    meta={k: v for k, v in {
                        "interview_id": f"iv-{row.case_id}",
                        "case_id": row.case_id,
                        "employee_id": row.employee_id or (
                            case.employee_id if case else None),
                        "employee_name": case.employee_name if case else None,
                        # `sentiment` is deliberately absent: the corpus rows
                        # carry a human's label and nothing here has produced
                        # one. Inventing it would poison every trend that
                        # groups by it. `interview_summarizer` derives it on
                        # demand, which is the honest place for a judgement.
                        "would_recommend": (True if recommend.startswith("y")
                                            else False if recommend.startswith("n")
                                            else None),
                        "submitted_date": (row.submitted_at.date()
                                           if row.submitted_at else None),
                        "source": "portal",
                    }.items() if v is not None},
                ))
        return out

    def submitted_kt_docs(self) -> list[Doc]:
        """Handover documents the leavers actually uploaded, as Docs.

        The `kt_docs` table is a read-only corpus of handovers written before
        this product existed, and it was the only thing the KT reviewer, its
        dropdown and the manager's workspace could see. So a leaver could
        upload their handover through the portal -- the thing the product asks
        them to do -- and every screen about handovers would go on reporting
        that none had been filed. Rahul Verma's is a 2,700-word PDF sitting in
        `case_documents`, and his manager's page said "no handover document has
        been filed for this exit yet".

        Rendered into the same shape rather than given a parallel pipeline,
        exactly as `submitted_interviews` does for the other record the product
        collects: one seam, so `kt_reviewer` reads an uploaded handover without
        knowing there are two sources.

        Latest upload per case wins, matching `_upload_checklist` -- somebody
        re-sending a rejected scan owes one document, not a series of attempts.
        Rejected ones are included, because a manager deciding whether to
        accept a handover has to be able to read the one that was refused.
        """
        out: list[Doc] = []
        with session_scope() as s:
            rows = s.scalars(
                select(CaseDocument)
                .where(func.lower(CaseDocument.document_name) == "kt document")
                # Oldest first, so the dict below keeps the newest per case.
                .order_by(CaseDocument.uploaded_at, CaseDocument.id)
            ).all()
            latest: dict[str, CaseDocument] = {r.case_id: r for r in rows}
            cases = {c.case_id: c for c in s.scalars(select(ExitCase)).all()}
            for case_id, row in latest.items():
                # The seeded rows exist only to mark the corpus's
                # `documents_submitted` as accepted; they hold no file and no
                # text, so there is nothing to review and offering one would
                # put an empty document in the dropdown.
                if not (row.ocr_text or "").strip():
                    continue
                case = cases.get(case_id)
                out.append(Doc(
                    slug=f"kt-upload-{case_id}",
                    body=row.ocr_text or "",
                    meta={k: v for k, v in {
                        "kt_id": f"kt-up-{case_id}",
                        "case_id": case_id,
                        "employee_id": case.employee_id if case else None,
                        "employee_name": case.employee_name if case else None,
                        # Whether a *person* has ruled on it, which is what
                        # the corpus field means and what the dropdown's
                        # "already reviewed" claims. Not `verdict ==
                        # "accepted"`: the validator used to write that itself,
                        # so reading the verdict would have the list announce a
                        # review nobody performed.
                        "reviewed": bool(row.reviewed_by),
                        "verdict": row.verdict,
                        "submitted_date": (row.uploaded_at.date()
                                           if row.uploaded_at else None),
                        "filename": row.filename or None,
                        # So a reader can open the actual file rather than the
                        # extracted text.
                        "document_id": row.id,
                        "content_type": row.content_type or None,
                        "source": "upload",
                    }.items() if v is not None},
                ))
        return out

    def all_kt_docs(self) -> list[Doc]:
        """The historical corpus plus the handovers uploaded here.

        What every reader of a handover should use. `kt_docs()` stays the
        corpus alone because `tests/golden/store_snapshot.json` pins it.
        """
        return [*self.kt_docs(), *self.submitted_kt_docs()]

    def all_interviews(self) -> list[Doc]:
        """The historical corpus plus what leavers have submitted here.

        What every analysis agent should read. `interviews()` stays the corpus
        alone because `tests/golden/store_snapshot.json` pins it.
        """
        return [*self.interviews(), *self.submitted_interviews()]

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
        """A handover from the corpus, or one uploaded here.

        Both, for the same reason `interview()` resolves both: the id comes
        from a select whose options are built from `all_kt_docs()`, and
        resolving only the corpus would offer a reviewer an uploaded handover
        and then refuse to open it.
        """
        found = self._lookup(KTDoc, KTDoc.id, kt_id)
        if found is not None:
            return found
        wanted = str(kt_id or "").strip()
        return next((d for d in self.submitted_kt_docs()
                     if d.meta.get("kt_id") == wanted), None)

    def interview(self, interview_id: str) -> Doc | None:
        """A transcript from the corpus, or an interview submitted here.

        Both, because the id comes from a select whose options are built from
        `all_interviews()`. Resolving only the corpus would offer the reader a
        submitted interview and then refuse to open it.
        """
        found = self._lookup(Interview, Interview.id, interview_id)
        if found is not None:
            return found
        wanted = str(interview_id or "").strip()
        return next((d for d in self.submitted_interviews()
                     if d.meta.get("interview_id") == wanted), None)

    def exit_case_for_employee(self, employee_id: str) -> Doc | None:
        """The exit case tied to an employee, most-recently-initiated first.

        Keyed off `employee_id` -- server-trusted, taken from the caller's own
        session -- rather than any case_id a client could supply, so an
        employee's self-service view can only ever resolve to their own case.
        """
        needle = str(employee_id).strip()
        if not needle:
            return None
        with session_scope() as s:
            row = s.scalar(
                select(ExitCase)
                .where(ExitCase.employee_id == needle)
                .order_by(ExitCase.initiated_date.desc())
                .limit(1)
            )
            return _doc(row) if row else None

    def case_documents(self, case_id: str) -> list[dict[str, Any]]:
        """Every uploaded document on a case -- every verdict, not just the
        accepted ones `to_meta()` folds into `documents_submitted` -- so HR can
        see (and, via `review_document`, override) what the model decided."""
        with session_scope() as s:
            case = s.get(ExitCase, case_id)
            return [_document_meta(d) for d in case.documents] if case else []

    def document_file(self, case_id: str, doc_id: int) -> tuple[bytes, str, str] | None:
        """The stored upload for one document: (bytes, content_type, filename).

        Deliberately a separate call from `case_documents`, which is a list
        view -- these rows hold whole files in BYTEA, and folding them into the
        listing would send several megabytes to render a table of filenames.

        The columns are still called `image`/`image_bytes`. They pre-date PDF
        and HTML uploads and renaming them is a migration that buys nothing but
        a tidier name; the comment on the model says what they really hold.
        """
        with session_scope() as s:
            row = s.get(CaseDocument, doc_id)
            if row is None or row.case_id != case_id or not row.image:
                return None
            return (bytes(row.image),
                    row.content_type or "application/octet-stream",
                    row.filename or f"document-{doc_id}")

    def review_document(
        self, case_id: str, doc_id: int, *, verdict: str, reviewed_by: str, note: str = "",
    ) -> dict[str, Any]:
        """A human overrides the model's verdict on one uploaded document.

        The model's judgement is a first pass, not a final answer -- OCR
        misreads, and the LLM call itself can be unavailable (see
        `document_collection.validate_upload`'s `needs_review` fallback), so
        without this an operator who can plainly see a scan is fine has no way
        to correct it, and `documents_submitted` (which drives every
        completion-percentage view) stays wrong until the employee re-uploads.
        Recording who and when is the point: "the system accepted it" is not
        an answer an auditor accepts for a human override either.
        """
        verdict = verdict.strip().lower()
        if verdict not in ("accepted", "needs_review", "rejected"):
            raise ValueError("verdict must be accepted, needs_review, or rejected.")
        with session_scope() as s:
            doc = s.get(CaseDocument, doc_id)
            if doc is None or doc.case_id != case_id:
                raise LookupError(f"No document #{doc_id} on case {case_id!r}.")
            doc.verdict = verdict
            doc.reviewed_by = reviewed_by
            doc.reviewed_at = datetime.now(timezone.utc)
            doc.review_note = note
            s.flush()
            return _document_meta(doc)

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
    def add_employee(
        self, fields: dict[str, Any]
    ) -> tuple[Doc, Doc | None, dict[str, str] | None]:
        """Create an employee, and a starter exit case if they are exiting.

        Both inserts share one transaction. An employee marked exiting but left
        without a case would be invisible to the orchestrator, which picks a
        *case*, not an employee -- a half-created record that looks fine in the
        employee list and cannot be actioned.

        An exiting employee with no email already registered also gets a login
        account (role "employee", a generated password, must_change_password
        set) -- otherwise the only way onto the portal was a separate manual
        script, and a leaver who never had one run for them could never sign in
        to see their own offboarding. The third return value is that
        account's one-time (email, password), or None if nothing was created
        (no exit case, or the email already has an account).
        """
        name = str(fields.get("name") or "").strip()
        role = str(fields.get("role") or "").strip()
        department = str(fields.get("department") or "").strip()
        if not name or not role or not department:
            raise ValueError("name, role, and department are required.")

        rating = _as_number(fields.get("performance_rating"), "performance_rating",
                            integer=True)
        join_date = _require_date(fields.get("join_date"), "join_date",
                                  default=date.today())

        # Derived from the join date rather than typed. Two fields describing
        # one fact is two fields that disagree: somebody enters a join date of
        # 2019 and a tenure of 2, and every agent reading tenure is wrong about
        # them from then on. It is also a number that changes on its own every
        # day, which is not something a form can keep true.
        #
        # An explicit value is still honoured, for importing a record whose
        # join date is genuinely unknown.
        supplied = _as_number(fields.get("tenure_years"), "tenure_years", default=None)
        tenure_years = (
            supplied if supplied is not None
            else round((date.today() - join_date).days / 365.25, 1)
        )
        last_working_day = _require_date(fields.get("last_working_day"),
                                         "last_working_day")
        if last_working_day and join_date and last_working_day < join_date:
            raise ValueError("Last working day cannot be before the join date.")

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
                join_date=join_date,
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
            credential = None
            if status == "exiting":
                case = _insert_case(s, emp, last_working_day=last_working_day)
                case_doc = _doc(case)
                credential = _issue_login(s, emp)
        return emp_doc, case_doc, credential

    def issue_login(self, employee_id: str) -> dict[str, str] | None:
        """Give a leaver a way in, if they have not got one.

        Returns the one-time credential, or None when they already have an
        account. Idempotent, so it is safe on a second resignation.
        """
        with session_scope() as s:
            emp = s.get(Employee, str(employee_id or "").strip())
            if emp is None:
                raise ValueError(f"Unknown employee {employee_id!r}.")
            return _issue_login(s, emp)

    def set_employee_status(self, employee_id: str, status: str) -> Doc:
        """Move somebody between active and exiting.

        The only writer for this column outside `add_employee`. It exists
        because the status chosen when the record was created used to be
        permanent, and people resign after they are hired.
        """
        wanted = str(status or "").strip().lower()
        if wanted not in ("active", "exiting"):
            raise ValueError("status must be 'active' or 'exiting'.")
        with session_scope() as s:
            emp = s.get(Employee, str(employee_id or "").strip())
            if emp is None:
                raise ValueError(f"Unknown employee {employee_id!r}.")
            emp.status = wanted
            s.flush()
            return _doc(emp)

    def add_exit_case(self, employee: Doc, *,
                      last_working_day: str | None = None) -> Doc:
        """Create a starter exit case (all stages pending) for an employee."""
        emp_id = str(employee.meta.get("id") or "").strip()
        with session_scope() as s:
            emp = s.get(Employee, emp_id) if emp_id else None
            if emp is None:
                raise ValueError(f"Unknown employee {emp_id!r}.")
            return _doc(_insert_case(s, emp, last_working_day=last_working_day))


def _issue_login(session: Session, emp: Employee) -> dict[str, str] | None:
    """A one-time login for a leaver who has none.

    Shared by `add_employee` and `set_employee_status` because both create the
    same situation: somebody now has an exit case and a part to play in it.
    It lived only in `add_employee`, so a person hired as active and marked as
    leaving months later -- which is how almost every real resignation
    happens -- got the case and no way to sign in. They could not confirm their
    IT handover, answer the exit interview, or download their own relieving
    letter, and nothing said why.
    """
    if not emp.email:
        return None
    if session.scalar(select(User).where(User.email == emp.email)):
        return None
    secret = generate_temp_password()
    session.add(User(
        email=emp.email,
        password_hash=hash_password(secret),
        role="employee",
        employee_id=emp.id,
        full_name=emp.name,
        must_change_password=True,
    ))
    return {"email": emp.email, "password": secret}


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
            CaseStage(
                ordinal=i,
                name=name,
                # stage_key is what routes the stage to a role; deriving it
                # here (rather than leaving the migration's backfill as the
                # only source) is what stops UI-created cases being invisible
                # to every work queue.
                stage_key=stage_key_for_name(name),
                owner="",
                status="pending",
                days_open=0,
                # Starts the clock. Without it `days_open` stays 0 forever and
                # the case could never breach SLA -- see policy.effective_days_open.
                status_changed_at=datetime.now(timezone.utc),
            )
            for i, name in enumerate(DEFAULT_CLEARANCE_STAGES)
        ],
    )
    session.add(case)
    session.flush()
    return case


store = DataStore()
