# Database

PostgreSQL is the system of record for employees, exit cases, clearance
stages, submitted documents, the deprovisioning audit trail, agent run history,
and LangGraph's approval checkpoints.

There is deliberately no SQLite fallback. A silent downgrade to a single-writer
engine would hide concurrency bugs until production, and the schema uses
`JSONB` and `TEXT[]` that SQLite cannot index.

## First-time setup

PostgreSQL 17 installed natively on Windows — no Docker in the loop:

```
winget install --id PostgreSQL.PostgreSQL.17 --exact ^
  --accept-package-agreements --accept-source-agreements ^
  --custom "--superpassword postgres --serverport 5432 --enable-components server,commandlinetools"
```

That installs the server and `psql` only. It needs an elevated prompt (UAC),
because it writes to Program Files and registers the `postgresql-x64-17`
service.

Then, from the repository root:

```
pip install -r requirements.txt
copy .env.example .env          # fill in PORTKEY_API_KEY and the DATABASE_URL password
python -m scripts.init_database
alembic upgrade head
python -m backend.db.seed
python -m uvicorn backend.main:app --reload
```

`init_database.py` creates the `offboard` role and the `offboard` and
`offboard_test` databases. It reads the role name and password out of
`DATABASE_URL`, so that file is the single source of truth. It connects as a
superuser only for those statements — the application role is **not** a
superuser and owns nothing but its two databases, so a stray migration or an
injection cannot reach the rest of the cluster.

Superuser credentials are read from `PGSUPERUSER` / `PGSUPERPASSWORD`, or
prompted for. They are never written to disk.

`psql` is not reliably added to PATH by the installer. It lives at
`C:\Program Files\PostgreSQL\17\bin\psql.exe`.

## Seed data vs. system of record

`data/employees/`, `data/exit-cases/`, `data/kt-docs/` and `data/interviews/`
are **seed data**. Editing those files changes nothing until you re-run
`python -m backend.db.seed`, which is idempotent.

`data/policies/` and `data/process-docs/` are still read from disk at runtime.
They are the RAG pipeline's source documents — chunked and embedded rather than
queried — so there is nothing to gain from putting them in a table.

## Schema notes

**Stages are rows, not JSONB.** `case_stages` holds one row per clearance
stage, unique on `(case_id, ordinal)`. `ExitCase.to_meta()` reassembles them
into the `meta["stages"]` list agents expect, so agent code is unaware — but
"which stages breached SLA" is an indexed query rather than a scan over every
case in Python, which is what `dashboard_insights` and `workflow_optimizer`
do today.

**`extra` is `DateAwareJSONB`, not plain `JSONB`.** Plain JSONB stringifies a
`date` silently, so a field that went in as a date comes back as a string.
That is exactly the defect that made `join_date` a date on hand-authored
employees and a string on UI-created ones. The custom type tags dates on the
way in and restores them on the way out.

**`slug`** is the record's stable human-readable key (originally the Markdown
filename stem). Collections order by it, which reproduces the old directory
scan order that `tests/test_store_contract.py` pins.

**`documents_submitted` is derived, not stored.** It comes from the latest
upload per document name in `case_documents`, counting only `accepted` ones.
Re-submitting a rejected form clears it; a later rejection un-clears it. Rows
seeded from a case's original `documents_submitted` list are tagged
`uploaded_by = 'seed:exit-case-record'`, so a historical assertion stays
distinguishable from a real scan — an auditor asking to see the signed form
will find nothing attached to a seeded row.

**The exit case → employee foreign key is `RESTRICT`, not `CASCADE`.** An
employee with exit history cannot be silently erased; the case is the
compliance record. Delete the case first if you really mean it.

## Adding an agent

Agents get persistence for free. Subclass `BaseAgent`, set `meta`, implement
`run()`, decorate with `@register`, and import the module in
`backend/agents/__init__.py`. That is the whole contract.

`BaseAgent.execute()` — which is what routes and the orchestrator call, never
`run()` — opens an `agent_runs` row before the agent starts and closes it
after, recording inputs, result, duration and any exception. Raw upload bytes
and oversized strings are redacted on the way in (a 14 KB scan becomes
`{"__bytes__": 14765}`). Recording is best-effort: if the database is
unreachable the agent still runs, because an observability failure must not
become an application outage.

`register()` rejects a duplicate agent id rather than overwriting, which would
silently make one agent unreachable from the UI and unattributable in the
history.

If a new agent needs its own table, follow the conventions at the top of
`backend/db/models.py`: a `slug` for the stable key, typed columns for whatever
agents filter or aggregate on, and `DateAwareJSONB` `extra` for the rest. Then:

```
alembic revision --autogenerate -m "add <table> for <agent>"
alembic upgrade head
```

`env.py` renders `DateAwareJSONB` as plain `postgresql.JSONB` in generated
migrations. Without that hook, autogenerate emits
`backend.db.models.DateAwareJSONB(...)` and never adds the import, so the
migration fails on first run — and baking an application class into a
migration breaks every historical revision the day it is renamed.

## Migrations

```
alembic revision --autogenerate -m "what changed"
alembic upgrade head
alembic downgrade -1
alembic history
```

Always read an autogenerated migration before applying it. Autogenerate is a
draft, not an oracle: it misses some server defaults, and will happily propose
dropping a table it cannot see.

`alembic.ini` ships with a blank `sqlalchemy.url` on purpose — `env.py` sets it
from `backend.config`, so no credential is ever committed.

## Tests

The suite needs the `offboard_test` database migrated:

```
set DATABASE_URL=postgresql+psycopg://offboard:<password>@localhost:5432/offboard_test
alembic upgrade head
```

Then:

```
python -m pytest tests/
```

`tests/test_store_contract.py` is the migration's safety net. It pins the
`DataStore` public surface against a golden snapshot captured from the original
Markdown implementation, before any of this existed. If you change the store,
**it must still pass unmodified** — a test edited to match a regression is
worse than no test, because it certifies the regression.

Regenerating the snapshot (`python -m tests.capture_golden`) records whatever
the store does *now*, so only do it deliberately, when you have decided the
change is correct.

## Inspecting the database

```
psql -U offboard -d offboard -h localhost

\dt                list tables
\d exit_cases      one table: columns, indexes, foreign keys
\d+ case_stages    plus storage detail
\di                all indexes
```

Useful queries:

```sql
-- what is every case waiting on?
select c.case_id, s.name, s.status, s.days_open, s.owner
from case_stages s join exit_cases c using (case_id)
where s.status <> 'completed' and s.days_open > 5
order by s.days_open desc;

-- agent run history, slowest first
select agent_id, case_id, duration_ms, error is null as ok, started_at
from agent_runs order by duration_ms desc nulls last limit 20;

-- who was deprovisioned, when, on whose approval
select employee_id, target, connector, approved_by, executed_at
from deprovisioning_actions order by executed_at desc;

-- uploads that a human still needs to look at
select case_id, document_name, verdict, reason, uploaded_at
from case_documents where verdict = 'needs_review' order by uploaded_at desc;
```

A GUI is optional and can be added at any time without touching the server:

```
winget install DBeaver.DBeaver.Community     # lighter than pgAdmin, good JSONB viewer
winget install PostgreSQL.pgAdmin
```
