# PostgreSQL Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the file-backed Markdown data store with PostgreSQL as the system of record, so employee/case/document/audit data persists durably and supports concurrent CRUD.

**Architecture:** PostgreSQL 17 accessed through SQLAlchemy 2.x ORM (sync, psycopg3 driver), with Alembic managing schema versions. The existing `DataStore` facade and its `Doc(.meta/.body/.slug/.title)` return shape are preserved exactly, so all 15 agents and the orchestrator are untouched. Case stages become real relational rows and are reassembled into the `meta["stages"]` list by the adapter, so SQL can query them while agent code cannot tell the difference. Markdown files under `data/` are demoted from system-of-record to seed data.

**Tech Stack:** PostgreSQL 17, SQLAlchemy 2.x, Alembic, psycopg[binary] 3.x, langgraph-checkpoint-postgres, pytest, Docker Compose.

**Spec:** Embedded below (derived from the session's requirements discussion; there is no separate spec document).

---

## Requirements

The migration must deliver all of the following:

1. **PostgreSQL only.** No SQLite, no dual-engine support, no silent fallback. Postgres-native types (`JSONB`, `TEXT[]`, `TIMESTAMPTZ`) are used deliberately.
2. **Fix the reported bug.** An uploaded document's OCR + validation verdict must persist against the exit case and be visible in the next `document_status()` call. Today `validate_upload()` returns the verdict and discards it.
3. **Fix the audit trail.** `_EXECUTED` in `backend/systems.py:34` is a module-level dict holding the entire post-approval deprovisioning record. It must become a table.
4. **Fix approval durability.** `MemorySaver` in `backend/orchestration/graph.py:336` loses every paused human-approval run on restart, and pending approvals cannot be enumerated. Replace with `PostgresSaver` and add a listing route.
5. **Agents unchanged.** All 34 `store.*` call sites across 15 agents keep working with no edits. The `Doc` contract is the seam.
6. **Safety net first.** Zero tests exist today. A characterisation suite that pins current `store` behaviour must be written and passing *before* the storage layer changes.

## Global Constraints

- **PostgreSQL 17.** Pinned in `docker-compose.yml`.
- **SQLAlchemy >= 2.0** — use the 2.x `Mapped[...]` / `mapped_column()` declarative style, not the 1.x `Column()` style.
- **Sync SQLAlchemy, not async.** All 15 agents and the store facade are synchronous. Introducing `async` would force rewriting every agent. FastAPI routes that block already use `run_in_threadpool`.
- **`psycopg` (v3), not `psycopg2`.** Install as `psycopg[binary]`.
- **Every table carries a `slug TEXT UNIQUE NOT NULL`** holding the original Markdown filename stem. Ordering by `slug` exactly reproduces today's `sorted(folder.glob("*.md"))` order, which the characterisation tests depend on.
- **`extra JSONB` on every document-backed table** captures frontmatter keys without a typed column, so `Doc.meta` round-trips losslessly.
- **No secret may be committed.** `DATABASE_URL` is read via `os.getenv` in `backend/config.py`. `.env` stays untracked.
- **`policies/` and `process-docs/` stay as Markdown files.** They are the RAG pipeline's source documents (`backend/rag/index.py:16`), consumed by the chunker, not CRUD records. Out of scope.
- **Work only in the `lang-graf` worktree on branch `worktree-lang-graf`.** Never touch `main`.
- Commit messages end with: `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## Out of Scope (deliberate, with reasons)

- **Stage-transition APIs.** Advancing a case stage still requires a direct DB write; there is no `PATCH /api/cases/{id}/stages/{n}` route. The migration makes this *possible*; building the workflow UI is separate work.
- **Authentication.** There is none today. Adding it is a separate concern and would expand this plan past reviewability.
- **Migrating `policies/` and `process-docs/`.** See Global Constraints.
- **The API key in git history.** Still needs rotating before any push. Unrelated to storage.

## File Structure

**Create:**

| Path | Responsibility |
|---|---|
| `docker-compose.yml` | Postgres 17 service, named volume, healthcheck |
| `alembic.ini` | Alembic config; URL injected from env, not hardcoded |
| `backend/db/__init__.py` | Engine, `SessionLocal`, `session_scope()` contextmanager |
| `backend/db/models.py` | All SQLAlchemy ORM models |
| `backend/db/migrations/env.py` | Alembic environment, reads `config.DATABASE_URL` |
| `backend/db/migrations/versions/0001_initial.py` | Initial schema |
| `backend/db/seed.py` | One-off idempotent Markdown → Postgres import |
| `tests/conftest.py` | Session-scoped test DB fixture |
| `tests/golden/store_snapshot.json` | Captured behaviour of the file-backed store |
| `tests/test_store_contract.py` | Characterisation suite — the migration's oracle |
| `tests/test_document_persistence.py` | Upload write-back (the reported bug) |
| `tests/test_deprovisioning_audit.py` | Audit-trail durability |

**Modify:**

| Path | Change |
|---|---|
| `requirements.txt` | Add DB + test dependencies |
| `backend/config.py:29-31` | Add `DATABASE_URL`; fix stale Bedrock defaults + docstring |
| `backend/data_store.py` | Reimplement over SQLAlchemy, same public surface |
| `backend/systems.py:34,37-51,222-240` | `_EXECUTED` dict → `deprovisioning_actions` table |
| `backend/agents/document_collection.py:81,130-156` | Persist upload; read submitted docs from DB |
| `backend/orchestration/graph.py:336` | `MemorySaver` → `PostgresSaver` |
| `backend/main.py` | Add `GET /api/approvals` |
| `.env.example` | Add `DATABASE_URL`; fix stale provider/model |
| `README.md` | Document Postgres setup; drop Bedrock references |

---

### Task 1: Test harness and characterisation snapshot

Nothing else in this plan is safe without this. This task captures exactly what the file-backed store returns today, into a golden file that the Postgres implementation must reproduce byte-for-byte.

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/capture_golden.py`
- Create: `tests/golden/store_snapshot.json`
- Create: `tests/test_store_contract.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: the current `backend.data_store.store` singleton.
- Produces: `tests/golden/store_snapshot.json` with top-level keys `employees`, `exit_cases`, `kt_docs`, `interviews`, `policies`, `process_docs` (each a list of `{slug, title, meta, body}` in store order); `tests/test_store_contract.py::test_collection_matches_golden` and `::test_lookup_matches_golden`, parametrised per collection.

- [ ] **Step 1: Add test dependencies**

Append to `requirements.txt`:

```
# Tests
pytest
pytest-asyncio
```

Run: `pip install -r requirements.txt`

- [ ] **Step 2: Write the golden capture script**

Create `tests/__init__.py` as an empty file. Create `tests/capture_golden.py`:

```python
"""Capture the file-backed store's behaviour as a golden snapshot.

Run this ONCE, against the Markdown-backed DataStore, before the Postgres
migration begins. `tests/test_store_contract.py` then asserts that whatever
implementation is in place reproduces this exactly. That is what makes
swapping the storage layer under 15 agents a safe refactor rather than a
hopeful one.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from backend.data_store import store

COLLECTIONS = ("employees", "exit_cases", "kt_docs", "interviews",
               "policies", "process_docs")

OUT = Path(__file__).parent / "golden" / "store_snapshot.json"


def _encode(value):
    """Dates must survive the JSON round-trip distinguishably from strings."""
    if isinstance(value, (date, datetime)):
        return {"__date__": value.isoformat()}
    raise TypeError(f"Unserialisable {type(value).__name__}: {value!r}")


def snapshot() -> dict:
    return {
        name: [
            {"slug": d.slug, "title": d.title, "meta": d.meta, "body": d.body}
            for d in getattr(store, name)()
        ]
        for name in COLLECTIONS
    }


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snapshot(), indent=2, default=_encode,
                              ensure_ascii=False), encoding="utf-8")
    data = json.loads(OUT.read_text(encoding="utf-8"))
    print(f"Wrote {OUT}")
    for k, v in data.items():
        print(f"  {k}: {len(v)}")
```

- [ ] **Step 3: Run the capture and eyeball the counts**

Run: `python -m tests.capture_golden`

Expected output — the corpus is 13 employees, 6 exit cases, 3 kt-docs, 4 interviews, 4 policies, 3 process-docs:

```
Wrote .../tests/golden/store_snapshot.json
  employees: 13
  exit_cases: 6
  kt_docs: 3
  interviews: 4
  policies: 4
  process_docs: 3
```

If any count is zero, `DATA_DIR` is resolving wrong — stop and fix that before continuing.

- [ ] **Step 4: Write the contract test**

Create `tests/conftest.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden" / "store_snapshot.json"


def _decode(obj):
    """Inverse of capture_golden._encode."""
    if "__date__" in obj and len(obj) == 1:
        from datetime import date
        return date.fromisoformat(obj["__date__"])
    return obj


@pytest.fixture(scope="session")
def golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"), object_hook=_decode)
```

Create `tests/test_store_contract.py`:

```python
"""The migration's oracle.

Every assertion here passes against the Markdown-backed store today. After the
Postgres swap they must all still pass, unchanged. If one fails, the new store
is not a drop-in replacement and some agent will break in a way no other test
in this repo would catch.
"""
from __future__ import annotations

import pytest

from backend.data_store import store

COLLECTIONS = ("employees", "exit_cases", "kt_docs", "interviews",
               "policies", "process_docs")

# Which lookup method resolves each collection, and the meta key holding its id.
LOOKUPS = {
    "employees": ("employee", "id"),
    "exit_cases": ("exit_case", "case_id"),
    "kt_docs": ("kt_doc", "kt_id"),
    "interviews": ("interview", "interview_id"),
}


@pytest.mark.parametrize("name", COLLECTIONS)
def test_collection_matches_golden(golden, name):
    """Same documents, same order, same parsed metadata, same body."""
    expected = golden[name]
    actual = getattr(store, name)()
    assert [d["slug"] for d in expected] == [d.slug for d in actual]
    for exp, got in zip(expected, actual):
        assert got.meta == exp["meta"], f"{name}/{exp['slug']} metadata drifted"
        assert got.body == exp["body"], f"{name}/{exp['slug']} body drifted"
        assert got.title == exp["title"], f"{name}/{exp['slug']} title drifted"


@pytest.mark.parametrize("name", sorted(LOOKUPS))
def test_lookup_matches_golden(golden, name):
    """Every record is retrievable by its id, and by its slug."""
    method_name, id_key = LOOKUPS[name]
    lookup = getattr(store, method_name)
    for exp in golden[name]:
        by_id = lookup(str(exp["meta"][id_key]))
        assert by_id is not None, f"{method_name}({exp['meta'][id_key]!r}) returned None"
        assert by_id.slug == exp["slug"]
        by_slug = lookup(exp["slug"])
        assert by_slug is not None, f"{method_name}({exp['slug']!r}) returned None"
        assert by_slug.slug == exp["slug"]


@pytest.mark.parametrize("name", sorted(LOOKUPS))
def test_lookup_is_case_insensitive(golden, name):
    method_name, id_key = LOOKUPS[name]
    lookup = getattr(store, method_name)
    sample = golden[name][0]
    assert lookup(str(sample["meta"][id_key]).upper()) is not None


@pytest.mark.parametrize("name", sorted(LOOKUPS))
def test_unknown_id_returns_none(name):
    assert getattr(store, LOOKUPS[name][0])("definitely-not-a-real-id") is None


def test_every_case_resolves_to_an_employee():
    """Agents dereference case.meta['employee_id'] without guarding. If this
    breaks, dashboard_insights and workflow_optimizer silently bucket
    everything under 'Unknown' instead of erroring."""
    for case in store.exit_cases():
        emp_id = str(case.meta.get("employee_id", ""))
        assert store.employee(emp_id) is not None, f"{case.slug} -> {emp_id!r}"


def test_stages_shape_is_stable():
    """systems.py:_stage and both analysis agents index into these keys."""
    for case in store.exit_cases():
        stages = case.meta.get("stages")
        assert isinstance(stages, list) and stages, f"{case.slug} has no stages"
        for stage in stages:
            assert isinstance(stage, dict)
            assert {"name", "owner", "status", "days_open"} <= set(stage)
```

- [ ] **Step 5: Run the contract suite against the *current* store**

Run: `python -m pytest tests/test_store_contract.py -v`

Expected: **all tests PASS.** They are describing the store as it exists right now, so a failure here means the golden capture is wrong, not the code. Do not proceed until this is green — this suite is the only thing standing between the migration and a silent regression across 15 agents.

- [ ] **Step 6: Commit**

```bash
git add tests/ requirements.txt
git commit -m "test: pin file-backed store behaviour before Postgres migration

Zero tests existed. Captures every collection, lookup, and the stage
shape agents depend on, as a golden snapshot. This suite is the oracle
the Postgres DataStore must satisfy.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Postgres infrastructure and configuration

**Files:**
- Create: `docker-compose.yml`
- Modify: `requirements.txt`
- Modify: `backend/config.py:1-7` (docstring), `:29-31` (stale defaults), `:57-58` (add DB settings)
- Modify: `.env.example`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `backend.config.DATABASE_URL: str`, `backend.config.DB_ECHO: bool`.

- [ ] **Step 1: Write the compose file**

Create `docker-compose.yml`:

```yaml
services:
  db:
    image: postgres:17
    container_name: offboard-db
    environment:
      POSTGRES_USER: offboard
      POSTGRES_PASSWORD: offboard
      POSTGRES_DB: offboard
    ports:
      - "5432:5432"
    volumes:
      - offboard-pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U offboard -d offboard"]
      interval: 5s
      timeout: 3s
      retries: 10

  # Throwaway database for the test suite. Separate container so a test run
  # can TRUNCATE freely without touching development data.
  db-test:
    image: postgres:17
    container_name: offboard-db-test
    environment:
      POSTGRES_USER: offboard
      POSTGRES_PASSWORD: offboard
      POSTGRES_DB: offboard_test
    ports:
      - "5433:5432"
    tmpfs:
      - /var/lib/postgresql/data     # in-memory: fast, and wiped on restart
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U offboard -d offboard_test"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  offboard-pgdata:
```

- [ ] **Step 2: Add the database dependencies**

Replace the `# Tests` block at the end of `requirements.txt` with:

```
# Persistence: PostgreSQL is the system of record for employees, exit cases,
# stages, submitted documents, the deprovisioning audit trail, and agent runs.
sqlalchemy>=2.0
psycopg[binary]>=3.1
alembic

# Durable LangGraph checkpoints: a paused human-approval run must survive a
# restart, and pending approvals must be enumerable.
langgraph-checkpoint-postgres

# Tests
pytest
pytest-asyncio
```

Run: `pip install -r requirements.txt`

- [ ] **Step 3: Start Postgres and confirm it is reachable**

Run: `docker compose up -d db db-test`

Then: `docker compose ps`

Expected: both `offboard-db` and `offboard-db-test` show `(healthy)`. If Docker Desktop is not running, start it first — this is the one hard prerequisite the project did not previously have.

- [ ] **Step 4: Add configuration**

In `backend/config.py`, replace the module docstring (lines 1-7) with:

```python
"""Central configuration.

The LLM layer talks to the Portkey AI gateway. Persistence is PostgreSQL,
reached via `DATABASE_URL`. Values are read from the environment (.env) so no
secret is hard-coded.
"""
```

Replace lines 30-31 (the stale Bedrock defaults, which return
`400 bedrock error: Operation not allowed` — the provider is no longer
permitted) with:

```python
    provider: str = os.getenv("PORTKEY_PROVIDER", "@dsvertex")
    model: str = os.getenv("PORTKEY_MODEL", "gemini-2.5-flash")
```

Append after line 58 (`PORT = ...`):

```python
# PostgreSQL is the system of record. No SQLite fallback: a silent downgrade to
# a single-writer engine would mask concurrency bugs until production.
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://offboard:offboard@localhost:5432/offboard"
)
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://offboard:offboard@localhost:5433/offboard_test",
)
DB_ECHO = os.getenv("DB_ECHO", "").lower() in ("1", "true", "yes")
```

- [ ] **Step 5: Update `.env.example`**

Add to `.env.example`, and correct the provider/model lines there to
`@dsvertex` / `gemini-2.5-flash` if they still name Bedrock:

```
# PostgreSQL (docker compose up -d db)
DATABASE_URL=postgresql+psycopg://offboard:offboard@localhost:5432/offboard
TEST_DATABASE_URL=postgresql+psycopg://offboard:offboard@localhost:5433/offboard_test
```

- [ ] **Step 6: Verify the connection from Python**

Run:

```bash
python -c "
import sqlalchemy as sa
from backend.config import DATABASE_URL
print(sa.create_engine(DATABASE_URL).connect().execute(sa.text('select version()')).scalar())
"
```

Expected: a line beginning `PostgreSQL 17.`

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml requirements.txt backend/config.py .env.example
git commit -m "feat: add PostgreSQL service, driver, and DATABASE_URL config

Also corrects the Portkey defaults, which still named the Bedrock
provider that now returns 'Operation not allowed'.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Schema and Alembic baseline

**Files:**
- Create: `backend/db/__init__.py`
- Create: `backend/db/models.py`
- Create: `alembic.ini`
- Create: `backend/db/migrations/env.py`
- Create: `backend/db/migrations/script.py.mako`
- Create: `backend/db/migrations/versions/` (autogenerated initial revision)

**Interfaces:**
- Consumes: `backend.config.DATABASE_URL`, `DB_ECHO`.
- Produces:
  - `backend.db.engine`, `backend.db.SessionLocal`, `backend.db.session_scope()`
  - `backend.db.models.Base`, and models `Employee`, `ExitCase`, `CaseStage`, `CaseDocument`, `DeprovisioningAction`, `AgentRun`, `KTDoc`, `Interview`
  - Every document model exposes `.slug`, `.body`, `.extra`, and a `.to_meta() -> dict`

- [ ] **Step 1: Write the session module**

Create `backend/db/__init__.py`:

```python
"""Database engine and session management.

Sync SQLAlchemy deliberately: all 15 agents and the `store` facade are
synchronous, and FastAPI already wraps the blocking routes in
`run_in_threadpool`. Going async here would force rewriting every agent for
no benefit at this concurrency.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import DATABASE_URL, DB_ECHO

engine = create_engine(
    DATABASE_URL,
    echo=DB_ECHO,
    pool_pre_ping=True,   # a recycled connection killed by the server is retried
    future=True,
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on any exception."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

- [ ] **Step 2: Write the models**

Create `backend/db/models.py`:

```python
"""SQLAlchemy models — PostgreSQL is the system of record.

Two conventions run through this file:

`slug` is the original Markdown filename stem. Ordering by it reproduces the
old `sorted(folder.glob("*.md"))` order exactly, which the store contract
tests assert on.

`extra` (JSONB) holds any frontmatter key without a typed column, so
`Doc.meta` round-trips losslessly. Typed columns exist for the fields agents
actually filter and aggregate on; everything else lives in `extra` rather than
forcing a migration for each new field.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, ForeignKey,
                        Integer, Numeric, String, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _clean(meta: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None so a reconstructed meta dict matches the
    frontmatter, which simply omits absent keys."""
    return {k: v for k, v in meta.items() if v is not None}


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
    tenure_years: Mapped[float | None] = mapped_column(Numeric(5, 2))
    join_date: Mapped[date | None] = mapped_column(Date)
    location: Mapped[str | None] = mapped_column(Text)
    employment_type: Mapped[str | None] = mapped_column(Text)
    performance_rating: Mapped[int | None] = mapped_column(Integer)
    criticality: Mapped[str | None] = mapped_column(String(16), index=True)
    status: Mapped[str | None] = mapped_column(String(16), index=True)
    project_assignments: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    access_systems: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    assets_assigned: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    body: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
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
            # Numeric comes back as Decimal; frontmatter yielded a float.
            "tenure_years": float(self.tenure_years) if self.tenure_years is not None else None,
            "join_date": self.join_date,
            "location": self.location,
            "employment_type": self.employment_type,
            "performance_rating": self.performance_rating,
            "criticality": self.criticality,
            "status": self.status,
            "project_assignments": list(self.project_assignments or []),
            "access_systems": list(self.access_systems or []),
            "assets_assigned": list(self.assets_assigned or []),
            **(self.extra or {}),
        })


class ExitCase(Base):
    __tablename__ = "exit_cases"

    case_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    employee_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False, index=True)
    employee_name: Mapped[str | None] = mapped_column(Text)
    exit_type: Mapped[str | None] = mapped_column(String(32), index=True)
    last_working_day: Mapped[date | None] = mapped_column(Date)
    initiated_date: Mapped[date | None] = mapped_column(Date)
    current_stage: Mapped[str | None] = mapped_column(Text)
    overall_status: Mapped[str | None] = mapped_column(String(32), index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    employee: Mapped["Employee"] = relationship(back_populates="cases")
    stages: Mapped[list["CaseStage"]] = relationship(
        back_populates="case", cascade="all, delete-orphan",
        order_by="CaseStage.ordinal", lazy="selectin")
    documents: Mapped[list["CaseDocument"]] = relationship(
        back_populates="case", cascade="all, delete-orphan",
        order_by="CaseDocument.uploaded_at", lazy="selectin")

    def to_meta(self) -> dict[str, Any]:
        """Reassembles the frontmatter shape agents expect.

        `stages` and `documents_submitted` are relational underneath but are
        handed back as the plain list-of-dicts and list-of-str that
        `systems.py:_stage`, `document_collection.document_status`, and both
        analysis agents already index into. That is the whole seam: relational
        storage, unchanged agent code."""
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
            "documents_submitted": [
                d.document_name for d in self.documents if d.verdict == "accepted"
            ],
            **(self.extra or {}),
        })


class CaseStage(Base):
    """One clearance stage. Rows, not JSONB, so 'which stages breached SLA'
    is an indexed query instead of a full scan in Python — which is what
    dashboard_insights and workflow_optimizer do today."""

    __tablename__ = "case_stages"
    __table_args__ = (UniqueConstraint("case_id", "ordinal", name="uq_case_stage_ordinal"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("exit_cases.case_id", ondelete="CASCADE"),
        nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    owner: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    days_open: Mapped[int] = mapped_column(Integer, default=0)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    case: Mapped["ExitCase"] = relationship(back_populates="stages")

    def to_meta(self) -> dict[str, Any]:
        # No _clean here: agents read stage["owner"] expecting "" not a
        # missing key, and test_stages_shape_is_stable asserts all four exist.
        return {"name": self.name, "owner": self.owner, "status": self.status,
                "days_open": self.days_open, **(self.extra or {})}


class CaseDocument(Base):
    """An uploaded exit form, its OCR text, and the model's verdict.

    Fixes the reported bug: `validate_upload` previously returned this and
    wrote nothing, so an accepted document never appeared in the case."""

    __tablename__ = "case_documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("exit_cases.case_id", ondelete="CASCADE"),
        nullable=False, index=True)
    document_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(Text, default="")
    content_type: Mapped[str] = mapped_column(Text, default="")
    ocr_engine: Mapped[str | None] = mapped_column(Text)
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    ocr_confidence: Mapped[float | None] = mapped_column(Numeric(6, 4))
    ocr_seconds: Mapped[float | None] = mapped_column(Numeric(8, 3))
    verdict: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # The scan itself. BYTEA is correct at this scale (single-page forms,
    # low volume) and keeps the artifact transactional with its verdict.
    # Move to object storage behind a key column if rows exceed a few MB.
    image: Mapped[bytes | None] = mapped_column(nullable=True)
    image_bytes: Mapped[int | None] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)

    case: Mapped["ExitCase"] = relationship(back_populates="documents")


class DeprovisioningAction(Base):
    """The post-approval audit trail. Replaces `_EXECUTED`, a module-level
    dict that lost every record on restart — unacceptable for data that says
    which privileged accounts were revoked, when, and on whose approval."""

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
        return {"type": self.action_type, "target": self.target,
                "result": self.result, "connector": self.connector,
                "executed_at": self.executed_at.replace(microsecond=0).isoformat()}


class AgentRun(Base):
    """Execution history. Nothing recorded agent runs before; without this
    there is no way to answer 'what did the orchestrator decide last Tuesday'."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    case_id: Mapped[str | None] = mapped_column(String(32), index=True)
    thread_id: Mapped[str | None] = mapped_column(String(64), index=True)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class _DocMixin:
    """KT docs and interviews are read-only reference records: no typed
    columns worth extracting, so the whole frontmatter lives in `extra`."""

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    employee_id: Mapped[str | None] = mapped_column(String(32), index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    def to_meta(self) -> dict[str, Any]:
        return dict(self.extra or {})


class KTDoc(_DocMixin, Base):
    __tablename__ = "kt_docs"


class Interview(_DocMixin, Base):
    __tablename__ = "interviews"
```

- [ ] **Step 3: Initialise Alembic**

Run: `alembic init -t generic backend/db/migrations`

This writes `alembic.ini` at the repo root and scaffolds `backend/db/migrations/`.

- [ ] **Step 4: Point Alembic at the app config**

In `alembic.ini`, find the `sqlalchemy.url = ...` line and blank it — the URL comes from the environment, and a committed URL is how credentials leak:

```ini
sqlalchemy.url =
```

Replace the body of `backend/db/migrations/env.py` with:

```python
"""Alembic environment. Reads the URL from backend.config so there is exactly
one place the database location is defined."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.config import DATABASE_URL
from backend.db.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=DATABASE_URL, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 5: Generate the initial migration**

Run: `alembic revision --autogenerate -m "initial schema"`

Then **read the generated file** in `backend/db/migrations/versions/`. Autogenerate is a draft, not an oracle. Confirm it contains `create_table` for all eight tables and that `employees.project_assignments` is `postgresql.ARRAY(sa.Text())`, not `sa.ARRAY`. Fix by hand if not.

- [ ] **Step 6: Apply it**

Run: `alembic upgrade head`

Verify:

```bash
docker compose exec db psql -U offboard -d offboard -c "\dt"
```

Expected: `agent_runs`, `alembic_version`, `case_documents`, `case_stages`, `deprovisioning_actions`, `employees`, `exit_cases`, `interviews`, `kt_docs`.

- [ ] **Step 7: Commit**

```bash
git add alembic.ini backend/db/
git commit -m "feat: add PostgreSQL schema and Alembic baseline

Case stages are relational rows rather than JSONB so SLA queries become
indexed lookups; to_meta() reassembles the list-of-dicts agents expect.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Seed the corpus from Markdown

**Files:**
- Create: `backend/db/seed.py`
- Test: `tests/test_seed.py`

**Interfaces:**
- Consumes: `backend.db.session_scope`, all models from `backend.db.models`.
- Produces: `backend.db.seed.seed_all(session) -> dict[str, int]`, and `python -m backend.db.seed` as a CLI. Idempotent — re-running updates in place rather than duplicating.

- [ ] **Step 1: Write the failing test**

Create `tests/test_seed.py`:

```python
"""Seeding must be idempotent: re-running it is the normal way to refresh a
development database, and duplicated employees would silently corrupt every
aggregate the analysis agents compute."""
from __future__ import annotations

from sqlalchemy import func, select

from backend.db import session_scope
from backend.db.models import CaseStage, Employee, ExitCase
from backend.db.seed import seed_all


def test_seed_is_idempotent():
    with session_scope() as s:
        seed_all(s)
    with session_scope() as s:
        first = s.scalar(select(func.count()).select_from(Employee))
        first_stages = s.scalar(select(func.count()).select_from(CaseStage))
    with session_scope() as s:
        seed_all(s)
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Employee)) == first
        assert s.scalar(select(func.count()).select_from(CaseStage)) == first_stages


def test_seed_loads_the_whole_corpus():
    with session_scope() as s:
        seed_all(s)
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Employee)) == 13
        assert s.scalar(select(func.count()).select_from(ExitCase)) == 6
        # Every case has its stages, ordered.
        case = s.scalar(select(ExitCase).order_by(ExitCase.slug))
        assert [st.ordinal for st in case.stages] == list(range(len(case.stages)))
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_seed.py -v`

Expected: `ModuleNotFoundError: No module named 'backend.db.seed'`

- [ ] **Step 3: Write the seeder**

Create `backend/db/seed.py`:

```python
"""Import the Markdown corpus under `data/` into PostgreSQL.

After the migration the Markdown files are seed data, not the system of
record. This script is the bridge, and is idempotent so it can be re-run to
refresh a development database.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import frontmatter
from sqlalchemy.orm import Session

from ..config import DATA_DIR
from . import session_scope
from .models import Employee, ExitCase, CaseStage, KTDoc, Interview

# Frontmatter keys that have a typed column, per table. Anything else lands in
# `extra`, so a new field in the Markdown never silently disappears.
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


def _docs(subdir: str) -> list[tuple[str, dict[str, Any], str]]:
    folder = DATA_DIR / subdir
    if not folder.exists():
        return []
    out = []
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
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)] if value else []


def _rest(meta: dict[str, Any], known: set[str]) -> dict[str, Any]:
    """Unknown keys, JSON-safe (dates stringified) for the JSONB column."""
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if k in known:
            continue
        out[k] = v.isoformat() if isinstance(v, (date, datetime)) else v
    return out


def seed_employees(session: Session) -> int:
    for slug, meta, body in _docs("employees"):
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
        emp.performance_rating = meta.get("performance_rating")
        emp.criticality = meta.get("criticality")
        emp.status = meta.get("status")
        emp.project_assignments = _as_list(meta.get("project_assignments"))
        emp.access_systems = _as_list(meta.get("access_systems"))
        emp.assets_assigned = _as_list(meta.get("assets_assigned"))
        emp.body = body
        emp.extra = _rest(meta, EMPLOYEE_COLUMNS)
        session.merge(emp)
    session.flush()
    return len(_docs("employees"))


def seed_cases(session: Session) -> int:
    for slug, meta, body in _docs("exit-cases"):
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
        session.flush()

        # Replace stages wholesale: ordinal is positional, so a reordered
        # source file must not leave orphaned rows behind.
        session.query(CaseStage).filter(CaseStage.case_id == case_id).delete()
        for i, stage in enumerate(meta.get("stages") or []):
            if not isinstance(stage, dict):
                continue
            session.add(CaseStage(
                case_id=case_id, ordinal=i,
                name=str(stage.get("name") or "?"),
                owner=str(stage.get("owner") or ""),
                status=str(stage.get("status") or "pending"),
                days_open=int(stage.get("days_open") or 0),
                extra=_rest(stage, STAGE_COLUMNS),
            ))
    session.flush()
    return len(_docs("exit-cases"))


def _seed_reference(session: Session, subdir: str, model, id_key: str) -> int:
    for slug, meta, body in _docs(subdir):
        rec_id = str(meta.get(id_key) or slug)
        row = session.get(model, rec_id) or model(id=rec_id)
        row.slug = slug
        row.employee_id = str(meta.get("employee_id")) if meta.get("employee_id") else None
        row.body = body
        row.extra = _rest(meta, set())
        session.merge(row)
    session.flush()
    return len(_docs(subdir))


def seed_all(session: Session) -> dict[str, int]:
    return {
        "employees": seed_employees(session),
        "exit_cases": seed_cases(session),
        "kt_docs": _seed_reference(session, "kt-docs", KTDoc, "kt_id"),
        "interviews": _seed_reference(session, "interviews", Interview, "interview_id"),
    }


if __name__ == "__main__":
    with session_scope() as s:
        counts = seed_all(s)
    for k, v in counts.items():
        print(f"  {k}: {v}")
```

- [ ] **Step 4: Run the seeder, then the test**

Run: `python -m backend.db.seed`

Expected:

```
  employees: 13
  exit_cases: 6
  kt_docs: 3
  interviews: 4
```

Run: `python -m pytest tests/test_seed.py -v`

Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/db/seed.py tests/test_seed.py
git commit -m "feat: idempotent Markdown-to-Postgres seed import

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Reimplement DataStore over PostgreSQL

The load-bearing task. The contract suite from Task 1 is the acceptance gate: it must pass unmodified.

**Files:**
- Modify: `backend/data_store.py` (full rewrite, same public surface)

**Interfaces:**
- Consumes: `backend.db.session_scope`, models, `backend.db.seed` helpers.
- Produces: unchanged public surface — `store.employees()`, `.exit_cases()`, `.kt_docs()`, `.interviews()`, `.policies()`, `.process_docs()`, `.employee(id)`, `.exit_case(id)`, `.kt_doc(id)`, `.interview(id)`, `.add_employee(fields)`, `.add_exit_case(employee, last_working_day=)`. `Doc` keeps `.meta`, `.body`, `.slug`, `.title`; `.path` becomes `Path | None` and is `None` for DB-backed records.

- [ ] **Step 1: Confirm the contract suite is green before you touch anything**

Run: `python -m pytest tests/test_store_contract.py -v`

Expected: all PASS (still against the file-backed store). This is the "before" reading. If it is already red, stop — fix that first.

- [ ] **Step 2: Rewrite the store**

Replace `backend/data_store.py` entirely:

```python
"""Data store — PostgreSQL backed.

The public surface is deliberately identical to the previous Markdown
implementation: same methods, same `Doc` shape, same ordering. That is what
lets 15 agents and the orchestrator migrate without a single edit; see
`tests/test_store_contract.py`, which pins it.

Two things changed underneath and both are intentional:

The `@lru_cache` that used to sit on every read method is gone. With files and
one worker it was a harmless speed-up; with a real database and more than one
worker it is a correctness bug — worker A writes, worker B serves stale data
forever. Postgres does the caching now.

`policies()` and `process_docs()` still read Markdown from disk. They are the
RAG pipeline's source documents, not CRUD records.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import frontmatter
from sqlalchemy import func, select

from .config import DATA_DIR
from .db import session_scope
from .db.models import Employee, ExitCase, CaseStage, KTDoc, Interview


@dataclass
class Doc:
    """A record in the shape agents expect.

    `path` is `None` for database-backed records — it only ever meant
    something for the Markdown files, and nothing outside this module reads
    it."""

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


def _doc(row) -> Doc:
    return Doc(meta=row.to_meta(), body=row.body or "", slug=row.slug)


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


def _next_id(session, model, id_col, prefix: str) -> str:
    """Next free `prefix-NNN`. Scans existing ids rather than using a sequence
    so the human-readable numbering stays contiguous with the seeded data."""
    nums = [
        int(m.group(1))
        for (value,) in session.execute(select(id_col))
        if (m := re.match(rf"{prefix}-(\d+)$", str(value)))
    ]
    return f"{prefix}-{max(nums, default=0) + 1:03d}"


class DataStore:
    # -- collections -------------------------------------------------------
    def employees(self) -> list[Doc]:
        with session_scope() as s:
            rows = s.scalars(select(Employee).order_by(Employee.slug)).all()
            return [_doc(r) for r in rows]

    def exit_cases(self) -> list[Doc]:
        with session_scope() as s:
            rows = s.scalars(select(ExitCase).order_by(ExitCase.slug)).all()
            return [_doc(r) for r in rows]

    def kt_docs(self) -> list[Doc]:
        with session_scope() as s:
            return [_doc(r) for r in s.scalars(select(KTDoc).order_by(KTDoc.slug)).all()]

    def interviews(self) -> list[Doc]:
        with session_scope() as s:
            return [_doc(r) for r in s.scalars(select(Interview).order_by(Interview.slug)).all()]

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
    def _lookup(model, id_col, value: str) -> Doc | None:
        """By id or by slug, case-insensitively — the old `_by_id` accepted
        both and callers rely on it (main.py falls back to slug when a record
        has no id in its metadata)."""
        needle = str(value).strip().lower()
        if not needle:
            return None
        with session_scope() as s:
            row = s.scalar(
                select(model).where(
                    (func.lower(id_col) == needle) | (func.lower(model.slug) == needle)
                ).limit(1)
            )
            return _doc(row) if row else None

    # -- writes ------------------------------------------------------------
    def add_employee(self, fields: dict[str, Any]) -> tuple[Doc, Doc | None]:
        """Create an employee. If marked "exiting", also create a starter exit
        case — otherwise the orchestrator, which picks a *case*, could never
        reach them.

        Both inserts share one transaction: an employee marked exiting but
        left with no case would be a half-created record."""
        name = str(fields.get("name") or "").strip()
        role = str(fields.get("role") or "").strip()
        department = str(fields.get("department") or "").strip()
        if not name or not role or not department:
            raise ValueError("name, role, and department are required.")

        try:
            tenure_years = float(fields.get("tenure_years") or 0)
        except (TypeError, ValueError):
            tenure_years = 0.0
        rating = fields.get("performance_rating")
        try:
            rating = int(rating) if rating else None
        except (TypeError, ValueError):
            rating = None

        notes = str(fields.get("notes") or "").strip()
        status = str(fields.get("status") or "active").strip().lower()

        with session_scope() as s:
            emp_id = _next_id(s, Employee, Employee.id, "emp")
            emp = Employee(
                id=emp_id, slug=emp_id, name=name, role=role, department=department,
                email=str(fields.get("email") or "").strip()
                      or f"{name.lower().replace(' ', '.')}@perficient.com",
                manager=str(fields.get("manager") or "").strip(),
                manager_email=str(fields.get("manager_email") or "").strip(),
                tenure_years=tenure_years,
                join_date=date.fromisoformat(str(fields.get("join_date")))
                          if fields.get("join_date") else date.today(),
                location=str(fields.get("location") or "").strip(),
                employment_type=str(fields.get("employment_type") or "Full-time").strip(),
                performance_rating=rating,
                criticality=str(fields.get("criticality") or "medium").strip().lower(),
                status=status,
                project_assignments=_split_list(fields.get("project_assignments")),
                access_systems=_split_list(fields.get("access_systems")),
                assets_assigned=_split_list(fields.get("assets_assigned")),
                body=f"# {name}\n\n" + (notes or f"{name} is a {role} in {department}.") + "\n",
                extra={},
            )
            s.add(emp)
            s.flush()
            emp_doc = _doc(emp)

            case_doc = None
            if status == "exiting":
                case_doc = _doc(self._insert_case(
                    s, emp, last_working_day=fields.get("last_working_day")))
        return emp_doc, case_doc

    def add_exit_case(self, employee: Doc, *, last_working_day: str | None = None) -> Doc:
        with session_scope() as s:
            emp = s.get(Employee, str(employee.meta.get("id")))
            if emp is None:
                raise ValueError(f"Unknown employee '{employee.meta.get('id')}'.")
            return _doc(self._insert_case(s, emp, last_working_day=last_working_day))

    @staticmethod
    def _insert_case(session, emp: Employee, *, last_working_day: Any) -> ExitCase:
        case_id = _next_id(session, ExitCase, ExitCase.case_id, "case")
        today = date.today()
        try:
            lwd = date.fromisoformat(str(last_working_day)) if last_working_day else today
        except ValueError:
            lwd = today
        case = ExitCase(
            case_id=case_id, slug=case_id, employee_id=emp.id, employee_name=emp.name,
            exit_type="resignation", last_working_day=lwd, initiated_date=today,
            current_stage=DEFAULT_CLEARANCE_STAGES[0], overall_status="on_track",
            body=(f"# Exit Case {case_id} — {emp.name} ({emp.id})\n\n"
                  "Auto-created when the employee was added; all clearance "
                  "stages start pending.\n"),
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
```

- [ ] **Step 3: Run the contract suite against Postgres**

Run: `python -m pytest tests/test_store_contract.py -v`

Expected: all PASS, with no edits to the test file.

Two failures are likely and both are real bugs to fix in `to_meta()`/`seed.py`, not in the tests:

- `tenure_years` comes back as `Decimal('3.50')` rather than `3.5` → the `float(...)` cast in `Employee.to_meta` handles it; check it is present.
- A date arrives as `str` rather than `datetime.date` → `_as_date` in the seeder was not applied to that field.

- [ ] **Step 4: Verify a write round-trips**

Run:

```bash
python -c "
from backend.data_store import store
emp, case = store.add_employee({
    'name': 'Contract Test', 'role': 'QA', 'department': 'Engineering',
    'status': 'exiting', 'access_systems': 'Okta, AWS',
})
print(emp.meta['id'], emp.meta['access_systems'])
print(case.meta['case_id'], [s['name'] for s in case.meta['stages']])
assert store.employee(emp.meta['id']) is not None
assert store.exit_case(case.meta['case_id']) is not None
print('round-trip OK')
"
```

Expected: a new `emp-0NN` with `['Okta', 'AWS']`, a new `case-0NN` with all five stage names, then `round-trip OK`. Unlike the old implementation this is **visible to other workers immediately** — there is no per-process cache to go stale.

- [ ] **Step 5: Clean up the test record**

```bash
docker compose exec db psql -U offboard -d offboard \
  -c "DELETE FROM employees WHERE name = 'Contract Test';"
```

The exit case goes with it only if you delete the case first — the FK is `ON DELETE RESTRICT` deliberately, so an employee with history cannot be silently erased. Delete the case first if Postgres refuses.

- [ ] **Step 6: Commit**

```bash
git add backend/data_store.py
git commit -m "refactor: back DataStore with PostgreSQL

Public surface and Doc shape unchanged, so all 15 agents are untouched;
tests/test_store_contract.py passes unmodified. Drops the per-method
lru_cache, which would serve permanently stale data across workers.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Persist uploaded documents (the reported bug)

**Files:**
- Modify: `backend/agents/document_collection.py:130-156` (`validate_upload`)
- Test: `tests/test_document_persistence.py`

**Interfaces:**
- Consumes: `CaseDocument`, `session_scope`, `ExitCase.to_meta()` (which already surfaces accepted documents as `documents_submitted`).
- Produces: `record_upload(case_id, document_name, filename, content_type, image_bytes, extracted, judgement) -> int` returning the new row id. `validate_upload` gains a `case_id: str` parameter and a `"persisted"` key in its return value.

- [ ] **Step 1: Write the failing test**

Create `tests/test_document_persistence.py`:

```python
"""The reported bug: an accepted upload vanished.

`validate_upload` OCR'd the scan, asked the model to judge it, returned the
verdict — and wrote nothing. `documents_submitted` was only ever read, and only
ever written as `[]` at case creation, so the next `document_status()` call
still showed the document missing.
"""
from __future__ import annotations

import pytest

from backend.agents.document_collection import document_status, record_upload
from backend.data_store import store
from backend.db import session_scope
from backend.db.models import CaseDocument

DOC = "No-Dues Declaration"


@pytest.fixture
def case_id() -> str:
    case = store.exit_cases()[0]
    cid = case.meta["case_id"]
    yield cid
    with session_scope() as s:
        s.query(CaseDocument).filter(
            CaseDocument.case_id == cid, CaseDocument.document_name == DOC).delete()


def _upload(case_id: str, verdict: str) -> int:
    return record_upload(
        case_id=case_id, document_name=DOC, filename="scan.png",
        content_type="image/png", image_bytes=b"\x89PNG-not-a-real-image",
        extracted={"engine": "winocr", "text": "No dues outstanding.",
                   "confidence": 0.94, "seconds": 0.41},
        judgement={"verdict": verdict, "reason": "Signed and dated.",
                   "signed": True, "dated": True},
    )


def test_accepted_upload_appears_in_documents_submitted(case_id):
    before = store.exit_case(case_id).meta["documents_submitted"]
    assert DOC not in before

    _upload(case_id, "accepted")

    after = store.exit_case(case_id).meta["documents_submitted"]
    assert DOC in after, "accepted upload did not persist against the case"


def test_accepted_upload_flips_document_status(case_id):
    _upload(case_id, "accepted")
    rows = document_status(store.exit_case(case_id).meta)["documents"]
    row = next(r for r in rows if r["document"] == DOC)
    assert row["status"] == "submitted"


def test_rejected_upload_is_recorded_but_not_counted(case_id):
    """A rejected scan must still leave an audit trail — you need to know
    someone tried — without counting as a submitted document."""
    _upload(case_id, "rejected")

    assert DOC not in store.exit_case(case_id).meta["documents_submitted"]
    with session_scope() as s:
        row = s.query(CaseDocument).filter(
            CaseDocument.case_id == case_id, CaseDocument.document_name == DOC).one()
        assert row.verdict == "rejected"
        assert row.ocr_text == "No dues outstanding."
        assert row.image_bytes == len(b"\x89PNG-not-a-real-image")


def test_upload_survives_a_new_store_instance(case_id):
    """The old `_EXECUTED`-style failure mode: state that lives only in this
    process. A fresh DataStore must see it."""
    _upload(case_id, "accepted")
    from backend.data_store import DataStore
    assert DOC in DataStore().exit_case(case_id).meta["documents_submitted"]
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_document_persistence.py -v`

Expected: `ImportError: cannot import name 'record_upload'`

- [ ] **Step 3: Implement persistence**

In `backend/agents/document_collection.py`, add to the imports near the top:

```python
from ..db import session_scope
from ..db.models import CaseDocument
```

Add `record_upload` immediately above `validate_upload`:

```python
def record_upload(*, case_id: str, document_name: str, filename: str,
                  content_type: str, image_bytes: bytes,
                  extracted: dict[str, Any], judgement: dict[str, Any]) -> int:
    """Persist a validated upload against the case and return its row id.

    Stored whatever the verdict: a rejected scan is evidence that someone
    tried, which matters in an audit. Only `accepted` rows are surfaced as
    `documents_submitted` (see `ExitCase.to_meta`), so a rejection does not
    silently satisfy a requirement."""
    verdict = str(judgement.get("verdict") or "needs_review").lower()
    with session_scope() as s:
        row = CaseDocument(
            case_id=case_id,
            document_name=document_name,
            filename=filename or "",
            content_type=content_type or "",
            ocr_engine=extracted.get("engine"),
            ocr_text=extracted.get("text") or "",
            ocr_confidence=extracted.get("confidence"),
            ocr_seconds=extracted.get("seconds"),
            verdict=verdict,
            reason=str(judgement.get("reason") or ""),
            validation=judgement,
            image=image_bytes or None,
            image_bytes=len(image_bytes) if image_bytes else None,
        )
        s.add(row)
        s.flush()
        return row.id
```

Change the signature of `validate_upload` (line 130) to take the case id, and
replace its final `return` (lines 154-156):

```python
def validate_upload(case_meta: dict[str, Any], document_name: str, image_bytes: bytes,
                    filename: str = "", content_type: str = "") -> dict[str, Any]:
```

```python
    judgement = llm.complete_json(VALIDATE_SYSTEM, user, max_tokens=800)
    ocr_summary = {k: extracted[k] for k in ("engine", "seconds", "confidence", "image_size", "text")}

    row_id = record_upload(
        case_id=str(case_meta.get("case_id") or ""),
        document_name=document_name, filename=filename, content_type=content_type,
        image_bytes=image_bytes, extracted=extracted, judgement=judgement,
    )
    return {"document": document_name, "filename": filename, "ocr": ocr_summary,
            "validation": judgement, "persisted": {"id": row_id, "case_id": case_meta.get("case_id")}}
```

- [ ] **Step 4: Find and update the caller**

Run: `grep -n "validate_upload" backend/agents/document_collection.py`

In `DocumentCollectionAgent.run`, the call site passes the upload dict from
`main.py` (`{"filename", "content_type", "bytes"}`). Pass the content type
through, and — importantly — **re-read the case after the upload** so the
response reflects the document that was just accepted:

```python
        upload = inputs.get("upload")
        if isinstance(upload, dict) and upload.get("bytes"):
            out["upload"] = validate_upload(
                meta, (inputs.get("document_name") or "").strip(),
                upload["bytes"], upload.get("filename", ""),
                upload.get("content_type", ""),
            )
            # The upload may have satisfied a requirement; recompute from the
            # persisted state rather than the snapshot taken before the write.
            refreshed = store.exit_case(case_id)
            if refreshed:
                meta = refreshed.meta
                status = document_status(meta)
                out["status"] = status
```

Adjust the surrounding variable names to match what `run` actually uses — read lines 183-230 first and keep its existing structure.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_document_persistence.py -v`

Expected: all four PASS.

- [ ] **Step 6: Verify in the real app**

Run: `python -m uvicorn backend.main:app --reload`

In the browser: open Document Collection, pick a case, choose a document name, upload a scan. Confirm the results section shows it as submitted, then **reload the page and run the agent again** — the document must still be there. That reload is the actual bug report; a passing test is not the same as seeing it hold.

- [ ] **Step 7: Commit**

```bash
git add backend/agents/document_collection.py tests/test_document_persistence.py
git commit -m "fix: persist uploaded documents against the exit case

validate_upload OCR'd the scan, judged it, and discarded the verdict.
Uploads now land in case_documents; accepted ones surface as
documents_submitted, rejected ones stay as audit evidence.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Move the deprovisioning audit trail into Postgres

**Files:**
- Modify: `backend/systems.py:34,37-51,222-240`
- Test: `tests/test_deprovisioning_audit.py`

**Interfaces:**
- Consumes: `DeprovisioningAction`, `session_scope`.
- Produces: `systems.executed_actions(employee_id) -> list[dict]` (unchanged signature and record shape), `systems.execute_deprovisioning_action(action, employee_id="", case_id="", approved_by="", note="") -> dict`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_deprovisioning_audit.py`:

```python
"""Deprovisioning records say which privileged accounts were revoked, when,
and on whose approval. They lived in a module-level dict and died with the
process."""
from __future__ import annotations

import importlib

import pytest

from backend import systems
from backend.db import session_scope
from backend.db.models import DeprovisioningAction

EMP = "emp-test-audit"


@pytest.fixture(autouse=True)
def clean():
    yield
    with session_scope() as s:
        s.query(DeprovisioningAction).filter(
            DeprovisioningAction.employee_id == EMP).delete()


def test_executed_action_is_recorded():
    rec = systems.execute_deprovisioning_action(
        {"type": "revoke_access", "target": "AWS (IAM user + keys)"},
        employee_id=EMP, approved_by="hr-operator", note="approved in UI")
    assert rec["result"] == "done"
    assert rec["connector"] == "aws-iam"
    assert systems.executed_actions(EMP)[0]["target"] == "AWS (IAM user + keys)"


def test_audit_survives_module_reload():
    """Reimporting the module is the closest in-process stand-in for a server
    restart. Under the old dict this assertion was impossible to satisfy."""
    systems.execute_deprovisioning_action(
        {"type": "revoke_access", "target": "Okta"}, employee_id=EMP)
    importlib.reload(systems)
    assert any(r["target"] == "Okta" for r in systems.executed_actions(EMP))


def test_approval_metadata_is_stored():
    systems.execute_deprovisioning_action(
        {"type": "revoke_access", "target": "GitHub"},
        employee_id=EMP, case_id="case-001",
        approved_by="alice@perficient.com", note="ticket OPS-4412")
    with session_scope() as s:
        row = s.query(DeprovisioningAction).filter(
            DeprovisioningAction.employee_id == EMP,
            DeprovisioningAction.target == "GitHub").one()
        assert row.approved_by == "alice@perficient.com"
        assert row.note == "ticket OPS-4412"
        assert row.case_id == "case-001"


def test_asset_recovery_does_not_count_as_revocation():
    """`_revoked` skips recover_asset records: returning a laptop is not the
    same as killing an account, and conflating them would make compliance
    report access revoked when it is still live."""
    systems.execute_deprovisioning_action(
        {"type": "recover_asset", "target": "MacBook Pro"}, employee_id=EMP)
    assert systems._revoked(EMP, "MacBook Pro") is None
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_deprovisioning_audit.py -v`

Expected: `test_audit_survives_module_reload` and `test_approval_metadata_is_stored` FAIL — the first because the reload clears the dict, the second because `approved_by` is not stored anywhere today.

- [ ] **Step 3: Replace the dict with the table**

In `backend/systems.py`, add to the imports:

```python
from .db import session_scope
from .db.models import DeprovisioningAction
```

Delete the `_EXECUTED` declaration (lines 31-34, including its comment) and replace `executed_actions` (lines 37-38):

```python
def executed_actions(employee_id: str) -> list[dict[str, Any]]:
    """Post-approval deprovisioning records for an employee, oldest first.

    Persisted, not in-memory: these state which privileged accounts were
    revoked and when, which is compliance evidence and must survive a
    restart."""
    with session_scope() as s:
        rows = (s.query(DeprovisioningAction)
                 .filter(DeprovisioningAction.employee_id == str(employee_id))
                 .order_by(DeprovisioningAction.executed_at)
                 .all())
        return [r.to_record() for r in rows]
```

Replace the loop body in `_revoked` (lines 45-50) so it reads from the same source:

```python
    for rec in executed_actions(employee_id):
```

Replace the tail of `execute_deprovisioning_action` (lines 231-240):

```python
    record = {
        "type": kind,
        "target": target,
        "result": "done",
        "executed_at": _now(),
        "connector": _connector_for(target),
    }
    if employee_id:
        with session_scope() as s:
            s.add(DeprovisioningAction(
                employee_id=str(employee_id), case_id=case_id or None,
                action_type=kind, target=target, result="done",
                connector=record["connector"],
                approved_by=approved_by or None, note=note or "",
            ))
    return record
```

And widen its signature (line 222):

```python
def execute_deprovisioning_action(action: dict[str, Any], employee_id: str = "",
                                  case_id: str = "", approved_by: str = "",
                                  note: str = "") -> dict[str, Any]:
```

Update the module docstring's "In-memory: resets with the process, like every other mock here" claim — it is no longer true and a stale comment is worse than none.

- [ ] **Step 4: Pass the approval metadata through**

Run: `grep -rn "execute_deprovisioning_action" backend/`

In `backend/agents/it_deprovisioning.py`, `execute_plan` already receives `approved_by` and `note` from `main.py:190-194`. Forward both, plus the case id if the plan carries one, to each call.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_deprovisioning_audit.py -v`

Expected: all four PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/systems.py backend/agents/it_deprovisioning.py tests/test_deprovisioning_audit.py
git commit -m "fix: persist the deprovisioning audit trail

_EXECUTED was a module-level dict, so every record of which privileged
accounts were revoked died on restart and access_status then reported
those accounts active again. Now a table, with approver and note.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Durable LangGraph checkpoints and a pending-approvals route

**Files:**
- Modify: `backend/orchestration/graph.py:336`
- Modify: `backend/main.py`
- Test: `tests/test_checkpointer.py`

**Interfaces:**
- Consumes: `backend.config.DATABASE_URL`.
- Produces: `graph.checkpointer` (a `PostgresSaver`), `graph.pending_approvals() -> list[dict]` with keys `thread_id`, `case_id`, `employee`, `paused_at`; and `GET /api/approvals`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_checkpointer.py`:

```python
"""A run paused at the human approval gate must be recoverable.

With MemorySaver the checkpoint lived in the process and the thread_id lived
only in the browser, so a restart stranded the run: resume returned 409 and
every parallel agent's work was lost. There was also no way to ask what was
waiting.
"""
from __future__ import annotations

from langgraph.checkpoint.postgres import PostgresSaver

from backend.orchestration import graph


def test_checkpointer_is_durable():
    assert isinstance(graph.checkpointer, PostgresSaver), (
        "MemorySaver loses paused approval runs on restart")


def test_pending_approvals_is_enumerable():
    """Shape contract. An empty list is a valid answer; the route must exist
    and must not raise."""
    pending = graph.pending_approvals()
    assert isinstance(pending, list)
    for item in pending:
        assert {"thread_id", "case_id"} <= set(item)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_checkpointer.py -v`

Expected: FAIL — `assert isinstance(MemorySaver(...), PostgresSaver)`.

- [ ] **Step 3: Swap the checkpointer**

In `backend/orchestration/graph.py`, replace the `MemorySaver` import and its use at line 336:

```python
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool

from ..config import DATABASE_URL

# PostgresSaver wants a libpq URI, not SQLAlchemy's "+psycopg" dialect form.
_PG_URI = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")

# A paused human-approval run may sit for days. The checkpoint has to outlive
# the process that created it, and pending approvals have to be listable —
# neither is possible with MemorySaver.
_pool = ConnectionPool(conninfo=_PG_URI, max_size=10, kwargs={"autocommit": True})
checkpointer = PostgresSaver(_pool)
checkpointer.setup()   # idempotent; creates the checkpoint tables on first run
```

Then use `checkpointer` wherever `MemorySaver()` was passed to `.compile(...)`.

Add `pending_approvals` near `pending_approval`:

```python
def pending_approvals() -> list[dict[str, Any]]:
    """Every run currently paused at the approval gate.

    Without this the only handle on a paused run is the thread_id held in one
    browser tab; close the tab and the run is unreachable."""
    out: list[dict[str, Any]] = []
    for state in graph.get_state_history(None) if False else []:
        pass  # placeholder removed below
    seen: set[str] = set()
    for cp in checkpointer.list(None):
        thread_id = cp.config.get("configurable", {}).get("thread_id")
        if not thread_id or thread_id in seen:
            continue
        seen.add(thread_id)
        snapshot = GRAPH.get_state({"configurable": {"thread_id": thread_id}})
        if not snapshot.next:            # not paused — finished or errored
            continue
        values = snapshot.values or {}
        out.append({
            "thread_id": thread_id,
            "case_id": values.get("case_id"),
            "employee": values.get("employee"),
            "paused_at": cp.checkpoint.get("ts"),
        })
    return out
```

Delete the two placeholder lines (`for state in ... if False else []` and its
`pass`) — they are written here only to make the structure obvious; the real
loop is the `checkpointer.list(None)` one. Replace `GRAPH` with whatever the
compiled graph is actually named in this module.

- [ ] **Step 4: Add the route**

In `backend/main.py`, import `pending_approvals` alongside the existing orchestration imports and add:

```python
@app.get("/api/approvals")
def list_pending_approvals() -> dict[str, Any]:
    """Runs paused at the human approval gate, across all sessions."""
    return {"approvals": pending_approvals()}
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_checkpointer.py -v`

Expected: both PASS.

- [ ] **Step 6: Verify durability end to end — this is the real test**

1. Start the server: `python -m uvicorn backend.main:app`
2. In the UI, start an orchestration and let it reach the approval gate.
3. `curl http://127.0.0.1:8000/api/approvals` — the run appears, with its `thread_id`.
4. **Stop the server** (Ctrl-C) and start it again.
5. `curl http://127.0.0.1:8000/api/approvals` — the run is **still there**.
6. Resume it: `curl "http://127.0.0.1:8000/api/orchestrate/<case_id>/stream?thread_id=<id>&decision=approve"`
7. It streams the remaining stages to completion rather than returning 409.

- [ ] **Step 7: Commit**

```bash
git add backend/orchestration/graph.py backend/main.py tests/test_checkpointer.py
git commit -m "feat: durable approval checkpoints and GET /api/approvals

MemorySaver stranded every paused run on restart, and a thread_id living
only in a browser tab was the sole handle on one. Checkpoints are now in
Postgres and pending approvals are enumerable.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Documentation and full verification

**Files:**
- Modify: `README.md`
- Create: `docs/database.md`

**Interfaces:**
- Consumes: everything.
- Produces: no code.

- [ ] **Step 1: Run the whole suite**

Run: `python -m pytest tests/ -v`

Expected: every test passes. Record the exact count — that number goes in the README.

- [ ] **Step 2: Smoke-test every agent against Postgres**

The contract tests cover the store, not the agents that read through it. Run:

```bash
python -c "
from backend.agents import REGISTRY
from backend.data_store import store
case = store.exit_cases()[0].meta['case_id']
emp  = store.employees()[0].meta['id']
for aid in sorted(REGISTRY):
    print(f'{aid:28}', end=' ')
    try:
        REGISTRY[aid].run({'case_id': case, 'employee_id': emp})
        print('ok')
    except Exception as e:
        print(f'FAIL {type(e).__name__}: {str(e)[:70]}')
"
```

Agents needing inputs this does not supply (`kt_id`, `interview_id`) will fail on a missing field — that is expected and fine. What must **not** appear is any `AttributeError`, `KeyError`, or `TypeError` coming from `data_store.py` or a `to_meta()` call: those mean the `Doc` contract broke somewhere the tests missed. Note that this burns Portkey quota, which has been hitting `412 Usage Limits Policy` — run it once, not in a loop.

- [ ] **Step 3: Write the database doc**

Create `docs/database.md`:

```markdown
# Database

PostgreSQL 17 is the system of record for employees, exit cases, clearance
stages, submitted documents, the deprovisioning audit trail, agent runs, and
LangGraph's approval checkpoints.

## Running it

    docker compose up -d db
    alembic upgrade head
    python -m backend.db.seed

`data/employees/`, `data/exit-cases/`, `data/kt-docs/` and `data/interviews/`
are **seed data**, not live records. Editing those files changes nothing until
you re-run the seeder, which is idempotent.

`data/policies/` and `data/process-docs/` are still read from disk at runtime:
they are the RAG pipeline's source documents, chunked and embedded rather than
queried, so there is nothing to gain from a table.

## Schema notes

**Stages are rows, not JSONB.** `case_stages` has one row per clearance stage
with `(case_id, ordinal)` unique. `ExitCase.to_meta()` reassembles them into
the `meta["stages"]` list agents expect, so agent code is unaware — but "which
stages breached SLA" is an indexed query rather than a scan over every case in
Python.

**`extra` JSONB on every document table** holds frontmatter keys without a
typed column, so `Doc.meta` round-trips losslessly and a new field in the
source data does not require a migration.

**`slug`** is the original Markdown filename stem. Collections order by it,
which reproduces the old directory-scan order exactly — `tests/test_store_contract.py`
depends on that.

## Migrations

    alembic revision --autogenerate -m "what changed"
    alembic upgrade head

Always read an autogenerated migration before applying it. Autogenerate misses
server defaults, does not always get `ARRAY`/`JSONB` right, and will happily
propose dropping a table it cannot see.

## Tests

The test suite needs the throwaway database on port 5433:

    docker compose up -d db-test
    python -m pytest tests/

`tests/test_store_contract.py` is the migration's safety net: it pins the
`DataStore` public surface against a golden snapshot captured from the original
Markdown implementation. If you change the store, it must still pass unmodified.
```

- [ ] **Step 4: Update the README**

Replace any Bedrock references with `@dsvertex` / `gemini-2.5-flash`, and add a
Postgres prerequisite to the setup section:

```markdown
### Prerequisites

- Python 3.11+
- Docker (for PostgreSQL)

### Setup

    pip install -r requirements.txt
    cp .env.example .env          # then fill in PORTKEY_API_KEY
    docker compose up -d db
    alembic upgrade head
    python -m backend.db.seed
    python -m uvicorn backend.main:app --reload

See [docs/database.md](docs/database.md) for schema and migration details.
```

- [ ] **Step 5: Commit**

```bash
git add README.md docs/database.md
git commit -m "docs: document the PostgreSQL setup, schema, and migrations

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**

| Requirement | Task |
|---|---|
| 1. PostgreSQL only, no SQLite | Tasks 2, 3 — `DATABASE_URL` has no fallback engine; `ARRAY`/`JSONB` used directly |
| 2. Fix the upload bug | Task 6 |
| 3. Fix the audit trail | Task 7 |
| 4. Fix approval durability | Task 8 |
| 5. Agents unchanged | Task 5, gated by Task 1's contract suite; verified in Task 9 Step 2 |
| 6. Safety net first | Task 1, which precedes every change |

**Known risks carried by this plan:**

- **Task 5 Step 3 is where this plan is most likely to fail.** `Doc.meta` must
  reproduce parsed YAML types exactly — `Decimal` vs `float`, `date` vs `str`.
  The contract suite catches it; the fix belongs in `to_meta()`/`seed.py`,
  never in the test.
- **Task 8 Step 3's `pending_approvals` uses `checkpointer.list(None)`**, whose
  signature varies across LangGraph versions. Check the installed version's API
  if it raises; the shape the route returns is what matters, not the traversal.
- **`CaseDocument.image` is `BYTEA`.** Correct at this volume, and it keeps the
  scan transactional with its verdict. Revisit if rows grow past a few MB.
- **The Portkey key is still in git history** (commits `b283a1d` through
  `07157d1`) and the gateway is returning `412 Usage Limits Policy`. Neither is
  in scope here, but the key must be rotated before this branch is pushed
  anywhere.
