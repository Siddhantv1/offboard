"""SQLAlchemy models — PostgreSQL is the system of record.

Three conventions run through this file. Follow them when adding a table for a
new agent and everything else keeps working:

`slug` is the record's stable human-readable key (originally the Markdown
filename stem). Collections order by it, which reproduces the old directory
scan order that `tests/test_store_contract.py` pins.

`extra` is a `DateAwareJSONB` column holding any metadata field without a
typed column, so `Doc.meta` round-trips losslessly and a new field does not
require a migration. Give a field a typed column when agents filter, sort or
aggregate on it; leave it in `extra` otherwise.

`to_meta()` rebuilds the flat dict agents read. It is the seam that lets the
storage be relational while agent code stays unchanged — nothing outside this
module should know a case has a `case_stages` table behind it.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime,
                        Float, ForeignKey, Index, Integer, LargeBinary, String,
                        Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

# -- date-safe JSONB ----------------------------------------------------------
# Tags chosen to be implausible as real metadata keys; the decoder additionally
# requires them to be the dict's only key.
_DATE_TAG = "__date__"
_DATETIME_TAG = "__datetime__"


def _encode_dates(value: Any) -> Any:
    # datetime before date: datetime is a subclass of date.
    if isinstance(value, datetime):
        return {_DATETIME_TAG: value.isoformat()}
    if isinstance(value, date):
        return {_DATE_TAG: value.isoformat()}
    if isinstance(value, dict):
        return {k: _encode_dates(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode_dates(v) for v in value]
    return value


def _decode_dates(value: Any) -> Any:
    if isinstance(value, dict):
        if len(value) == 1:
            if _DATE_TAG in value:
                return date.fromisoformat(value[_DATE_TAG])
            if _DATETIME_TAG in value:
                return datetime.fromisoformat(value[_DATETIME_TAG])
        return {k: _decode_dates(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode_dates(v) for v in value]
    return value


class DateAwareJSONB(TypeDecorator):
    """JSONB that round-trips `date` and `datetime` objects.

    Plain JSONB stringifies a date silently, so a field that went in as a date
    comes back as a str. That is the exact defect that made `join_date` a date
    on hand-authored employees and a string on UI-created ones; putting
    untyped metadata in JSONB would have reintroduced it for every field
    without a typed column.
    """

    impl = JSONB
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        return None if value is None else _encode_dates(value)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        return None if value is None else _decode_dates(value)


class Base(DeclarativeBase):
    pass


def _clean(meta: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None.

    Frontmatter omits absent keys rather than writing nulls, so a reconstructed
    meta dict must omit them too or it will not compare equal to the golden
    snapshot. Note `is not None`: False and "" and 0 are real values and stay.
    """
    return {k: v for k, v in meta.items() if v is not None}


# -- core records -------------------------------------------------------------
class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(Text, index=True)
    manager: Mapped[str | None] = mapped_column(Text)
    manager_email: Mapped[str | None] = mapped_column(Text)
    tenure_years: Mapped[float | None] = mapped_column(Float)
    join_date: Mapped[date | None] = mapped_column(Date)
    location: Mapped[str | None] = mapped_column(Text)
    employment_type: Mapped[str | None] = mapped_column(Text)
    performance_rating: Mapped[int | None] = mapped_column(Integer)
    criticality: Mapped[str | None] = mapped_column(String(16), index=True)
    status: Mapped[str | None] = mapped_column(String(16), index=True)
    # ARRAY rather than JSONB: these are flat string lists that systems.py
    # iterates, and ARRAY supports containment queries ("who still has AWS").
    project_assignments: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    access_systems: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    assets_assigned: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    body: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict[str, Any]] = mapped_column(DateAwareJSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    cases: Mapped[list["ExitCase"]] = relationship(back_populates="employee")

    def to_meta(self) -> dict[str, Any]:
        return _clean({
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "role": self.role,
            "department": self.department,
            "manager": self.manager,
            "manager_email": self.manager_email,
            "tenure_years": self.tenure_years,
            "join_date": self.join_date,
            "location": self.location,
            "employment_type": self.employment_type,
            "performance_rating": self.performance_rating,
            "criticality": self.criticality,
            "status": self.status,
            "project_assignments": list(self.project_assignments) if self.project_assignments is not None else None,
            "access_systems": list(self.access_systems) if self.access_systems is not None else None,
            "assets_assigned": list(self.assets_assigned) if self.assets_assigned is not None else None,
            **(self.extra or {}),
        })


class ExitCase(Base):
    __tablename__ = "exit_cases"

    case_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # RESTRICT, not CASCADE: an employee with exit history must not be
    # silently erasable -- the case is the compliance record.
    employee_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False, index=True)
    employee_name: Mapped[str | None] = mapped_column(Text)
    exit_type: Mapped[str | None] = mapped_column(String(32), index=True)
    last_working_day: Mapped[date | None] = mapped_column(Date)
    initiated_date: Mapped[date | None] = mapped_column(Date)
    current_stage: Mapped[str | None] = mapped_column(Text)
    overall_status: Mapped[str | None] = mapped_column(String(32), index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict[str, Any]] = mapped_column(DateAwareJSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    employee: Mapped["Employee"] = relationship(back_populates="cases")
    # selectin: every caller of to_meta() needs the stages, so lazy loading
    # would produce an N+1 across the six-case dashboard queries.
    stages: Mapped[list["CaseStage"]] = relationship(
        back_populates="case", cascade="all, delete-orphan",
        order_by="CaseStage.ordinal", lazy="selectin")
    # id breaks ties on uploaded_at. Rows inserted in one transaction share a
    # server_default timestamp, so without it the document order -- and hence
    # documents_submitted -- would vary between runs.
    documents: Mapped[list["CaseDocument"]] = relationship(
        back_populates="case", cascade="all, delete-orphan",
        order_by="CaseDocument.uploaded_at, CaseDocument.id", lazy="selectin")

    def to_meta(self) -> dict[str, Any]:
        """Rebuild the flat frontmatter shape agents expect.

        `stages` and `documents_submitted` are relational underneath but come
        back as the plain list-of-dicts and list-of-str that `systems._stage`,
        `document_collection.document_status` and both analysis agents index
        into. That is the whole seam.
        """
        return _clean({
            "case_id": self.case_id,
            "employee_id": self.employee_id,
            "employee_name": self.employee_name,
            "exit_type": self.exit_type,
            "last_working_day": self.last_working_day,
            "initiated_date": self.initiated_date,
            "current_stage": self.current_stage,
            "overall_status": self.overall_status,
            "stages": [s.to_meta() for s in self.stages],
            "documents_submitted": self.submitted_documents(),
            **(self.extra or {}),
        })

    def submitted_documents(self) -> list[str]:
        """Names of documents currently accepted for this case.

        Only accepted uploads count: a rejected scan stays on record as
        evidence someone tried, without satisfying the requirement.

        The *latest* upload of a given document decides its state, so
        re-submitting a rejected form clears it and re-submitting a bad copy
        of an accepted one un-clears it. Names appear in first-upload order,
        which keeps the list stable as documents are replaced.
        """
        latest: dict[str, CaseDocument] = {}
        for doc in self.documents:          # ordered by (uploaded_at, id)
            latest[doc.document_name] = doc
        return [name for name, doc in latest.items() if doc.verdict == "accepted"]


class CaseStage(Base):
    """One clearance stage.

    Rows rather than a JSONB blob, so "which stages breached SLA" is an
    indexed query instead of a scan over every case in Python — which is what
    `dashboard_insights` and `workflow_optimizer` do today.
    """

    __tablename__ = "case_stages"
    __table_args__ = (
        UniqueConstraint("case_id", "ordinal", name="uq_case_stage_ordinal"),
        # Serves the SLA-breach query both agents run.
        Index("ix_case_stage_status_days", "status", "days_open"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("exit_cases.case_id", ondelete="CASCADE"),
        nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    owner: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    days_open: Mapped[int] = mapped_column(Integer, default=0)
    extra: Mapped[dict[str, Any]] = mapped_column(DateAwareJSONB, default=dict)

    case: Mapped["ExitCase"] = relationship(back_populates="stages")

    def to_meta(self) -> dict[str, Any]:
        # Deliberately not _clean()ed: agents read `stage.get("owner") or
        # "unassigned"`, and test_stages_shape_is_stable asserts all four keys
        # exist even when empty.
        return {
            "name": self.name,
            "owner": self.owner,
            "status": self.status,
            "days_open": self.days_open,
            **(self.extra or {}),
        }


class CaseDocument(Base):
    """An uploaded exit form, its OCR text, and the model's verdict.

    Fixes the reported bug: `validate_upload` previously returned this and
    wrote nothing, so an accepted document never appeared against the case.
    """

    __tablename__ = "case_documents"
    __table_args__ = (
        Index("ix_case_document_lookup", "case_id", "document_name", "uploaded_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("exit_cases.case_id", ondelete="CASCADE"),
        nullable=False, index=True)
    document_name: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, default="")
    content_type: Mapped[str] = mapped_column(Text, default="")
    ocr_engine: Mapped[str | None] = mapped_column(Text)
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    ocr_seconds: Mapped[float | None] = mapped_column(Float)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    validation: Mapped[dict[str, Any]] = mapped_column(DateAwareJSONB, default=dict)
    # The scan itself. BYTEA is right at this volume (single-page forms, low
    # rate) and keeps the artifact in the same transaction as its verdict.
    # Move to object storage behind a key column if rows reach a few MB.
    image: Mapped[bytes | None] = mapped_column(LargeBinary)
    image_bytes: Mapped[int | None] = mapped_column(Integer)
    uploaded_by: Mapped[str | None] = mapped_column(Text)
    # A human decision overrides the model's. Recording who and when is the
    # point: "the system accepted it" is not an answer an auditor accepts.
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # server_default as well as default: the Python-side default only applies
    # to new ORM objects, and this column was added to a populated table.
    review_note: Mapped[str] = mapped_column(Text, default="", server_default="")
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)

    case: Mapped["ExitCase"] = relationship(back_populates="documents")


class DeprovisioningAction(Base):
    """The post-approval audit trail.

    Replaces `_EXECUTED`, a module-level dict that lost every record on
    restart — unacceptable for data stating which privileged accounts were
    revoked, when, and on whose approval.
    """

    __tablename__ = "deprovisioning_actions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    case_id: Mapped[str | None] = mapped_column(String(32), index=True)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str] = mapped_column(String(16), default="done")
    connector: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, default="")
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)

    def to_record(self) -> dict[str, Any]:
        """The exact shape `_EXECUTED` held, so `systems._revoked` and
        `access_status` need no change beyond their data source."""
        return {
            "type": self.action_type,
            "target": self.target,
            "result": self.result,
            "executed_at": self.executed_at.replace(microsecond=0).isoformat(),
            "connector": self.connector,
        }


class DeprovisioningPlan(Base):
    """A drafted deprovisioning plan awaiting (or having received) approval.

    Persisting the plan server-side closes the gap where the execute route
    used to accept an arbitrary client-supplied `plan` body: the client now
    only ever sends back a `plan_id`. `status` starts 'pending' and moves to
    'executed' exactly once via a compare-and-swap in `execute_plan()`.
    """

    __tablename__ = "deprovisioning_plans"
    __table_args__ = (
        CheckConstraint("status in ('pending', 'executed')", name="ck_deprov_plan_status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    employee_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    case_id: Mapped[str | None] = mapped_column(String(32), index=True)
    plan: Mapped[dict[str, Any]] = mapped_column(DateAwareJSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    approved_by: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, default="")
    execution: Mapped[dict[str, Any] | None] = mapped_column(DateAwareJSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRun(Base):
    """Execution history for every agent.

    Written automatically by `BaseAgent.execute`, so the five agents still to
    be added get run history without implementing anything. Nothing recorded
    agent activity before: there was no way to answer "what did the
    orchestrator decide on this case last Tuesday".
    """

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_run_agent_started", "agent_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    case_id: Mapped[str | None] = mapped_column(String(32), index=True)
    employee_id: Mapped[str | None] = mapped_column(String(32), index=True)
    # LangGraph thread, when the run came from the orchestrator rather than a
    # direct agent invocation. Ties an agent run to its approval checkpoint.
    thread_id: Mapped[str | None] = mapped_column(String(64), index=True)
    inputs: Mapped[dict[str, Any]] = mapped_column(DateAwareJSONB, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(DateAwareJSONB)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def ok(self) -> bool:
        return self.error is None and self.finished_at is not None


# -- reference records --------------------------------------------------------
class KTDoc(Base):
    """Knowledge-transfer document. Read-only reference data."""

    __tablename__ = "kt_docs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    case_id: Mapped[str | None] = mapped_column(String(32), index=True)
    employee_id: Mapped[str | None] = mapped_column(String(32), index=True)
    employee_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    successor: Mapped[str | None] = mapped_column(Text)
    reviewed: Mapped[bool | None] = mapped_column(Boolean)
    submitted_date: Mapped[date | None] = mapped_column(Date)
    body: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict[str, Any]] = mapped_column(DateAwareJSONB, default=dict)

    def to_meta(self) -> dict[str, Any]:
        return _clean({
            "kt_id": self.id,
            "case_id": self.case_id,
            "employee_id": self.employee_id,
            "employee_name": self.employee_name,
            "role": self.role,
            "successor": self.successor,
            "reviewed": self.reviewed,
            "submitted_date": self.submitted_date,
            **(self.extra or {}),
        })


class Interview(Base):
    """Exit interview transcript. Read-only reference data."""

    __tablename__ = "interviews"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    employee_id: Mapped[str | None] = mapped_column(String(32), index=True)
    employee_name: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(Text, index=True)
    sentiment: Mapped[str | None] = mapped_column(String(16), index=True)
    would_recommend: Mapped[bool | None] = mapped_column(Boolean)
    submitted_date: Mapped[date | None] = mapped_column(Date)
    body: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict[str, Any]] = mapped_column(DateAwareJSONB, default=dict)

    def to_meta(self) -> dict[str, Any]:
        return _clean({
            "interview_id": self.id,
            "employee_id": self.employee_id,
            "employee_name": self.employee_name,
            "department": self.department,
            "sentiment": self.sentiment,
            "would_recommend": self.would_recommend,
            "submitted_date": self.submitted_date,
            **(self.extra or {}),
        })


# -- identity -----------------------------------------------------------------
#: The only two roles. An employee sees their own offboarding and nothing else;
#: HR manages everyone's. Kept as a plain string with a CHECK rather than a
#: Postgres ENUM, because altering an ENUM in a migration is far more painful
#: than altering a constraint.
ROLES = ("hr", "employee")


class User(Base):
    """A person who can log in.

    `employee_id` is set for employee accounts and links them to the record
    they are allowed to see. It is NULL for HR, who are scoped by role instead.
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role in ('hr','employee')", name="ck_user_role"),
        # An employee account without a linked record could not be scoped to
        # anything, so it must not exist.
        CheckConstraint(
            "role <> 'employee' or employee_id is not null", name="ck_employee_has_record"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Normalised to lowercase on write; the unique index is what makes
    # "Alice@x.com" and "alice@x.com" the same account rather than two.
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    employee_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("employees.id", ondelete="RESTRICT"), index=True
    )
    full_name: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Throttling state. Counting failures in the row rather than in memory
    # means a restart does not hand an attacker a fresh budget.
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    employee: Mapped["Employee | None"] = relationship()
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan")

    @property
    def is_hr(self) -> bool:
        return self.role == "hr"

    def to_identity(self) -> dict[str, Any]:
        """What the browser is told about itself. Deliberately minimal — no
        hash, no lockout state, nothing about other people."""
        return {
            "email": self.email,
            "role": self.role,
            "employee_id": self.employee_id,
            "full_name": self.full_name,
            "must_change_password": self.must_change_password,
        }


class UserSession(Base):
    """A logged-in browser.

    The primary key is a SHA-256 of the session token, never the token itself.
    A database leak then yields no usable cookies, and the lookup is still a
    single indexed equality check.

    Server-side rather than a signed cookie so that logout, password change and
    deactivation can revoke access immediately instead of waiting for expiry.
    """

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    user_agent: Mapped[str | None] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="sessions")
