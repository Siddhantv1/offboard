# Offboard — Exit Automation Platform

Fifteen focused AI agents for employee offboarding, behind one unified frontend,
plus a LangGraph orchestrator that runs eight of them as one graph with a
human-in-the-loop approval gate. The LLM layer routes every model call through
the **Portkey AI gateway** configured in Claude's `settings.json`, and
**PostgreSQL** is the system of record for employees, cases, documents and
audit trails (see [docs/database.md](docs/database.md)).

## The 19 agents

| # | Agent | What it does |
|---|-------|--------------|
| 1 | **Exit Checklist Generator** | Role/department-specific checklist: assets to return, access to revoke, KT topics |
| 2 | **Email Drafting Agent** | Stage-specific emails (KT reminder / overdue warning / completion) |
| 3 | **Exit Process FAQ Chatbot** | RAG over process docs + policies, answers with cited sources |
| 4 | **Exit Interview Summarizer** | Structured summary with sentiment, themes, recommendations |
| 5 | **SLA Escalation Agent** | Scans all cases for stages past the 5-day SLA, drafts escalations |
| 6 | **KT Document Reviewer** | Scores KT docs against role-critical topics, flags gaps |
| 7 | **Smart Routing Agent** | Picks the best approver (availability / workload / delegation, OOO-aware) |
| 8 | **Exit Risk Assessment** | Continuity risk score + mitigations from tenure/criticality/dependencies |
| 9 | **Compliance Verification** | Verifies asset return, NDA ack, access revocation → go/no-go |
| 10 | **Dashboard Insights** | Weekly narrative report from computed cross-case metrics |
| 11 | **Multi-System Clearance** | *Tool use:* the model calls IT-asset, IAM, HRMS and finance connectors, then consolidates status per system |
| 12 | **Document Collection** | Required vs submitted documents per stage, reminder drafts, and **local-OCR** validation of an uploaded scan |
| 13 | **Exit Workflow Optimizer** | Mines all cases for bottlenecks (slow stages, delaying departments, owner load) and proposes workflow changes |
| 14 | **Automated IT Deprovisioning** | Generates the deprovisioning plan, then **executes only after human approval** (audit log with timestamps) |
| 15 | **Exit Interview Trend Analyst** | *Multi-turn:* longitudinal patterns across all interviews by department/quarter, with alerts; follow-up questions keep history |

Agents 11, 12 and 14 also run inside the orchestrator (below); 13 and 15 analyze
the whole dataset rather than one case, so they stay standalone.

## Architecture

```
frontend/            React SPA (Vite), built to frontend/dist and served by FastAPI
  index.html         Vite entry
  src/main.jsx       Root render + BrowserRouter
  src/App.jsx        Layout, routes, breadcrumb derived from the URL
  src/pages/         Home · AgentPage · Orchestrator · AddEmployee · NotFound
  src/components/    Sidebar, Field, Chat, viz (bars/tables/pipeline),
                     orchestrator (live SVG graph, approval gate),
                     results (three purpose-built views + recursive fallback)
  src/lib/           api, agent catalog context, markdown, formatting, constants
  src/styles/        Design system (teal on white; proportion rules at the top)

backend/             FastAPI app
  main.py            API + serves the frontend
  config.py          Portkey/env settings
  llm.py             LLMClient — thin wrapper over the Portkey SDK (all LLM calls,
                     incl. the tool-calling loop used by agent 11)
  data_store.py      PostgreSQL-backed store; returns Doc(.meta/.body) so agents
                     are unaware of the storage behind it
  db/                Persistence layer
    models.py        SQLAlchemy models + DateAwareJSONB column type
    seed.py          Imports data/ into PostgreSQL (idempotent)
    migrations/      Alembic revisions
  systems.py         Mock enterprise connectors (IT assets, IAM, HRMS, finance) used as
                     tools by agent 11 and as the execution target of agent 14
  ocr.py             Local OCR (Windows built-in engine, ONNX fallback) for agent 12
  agents/            One module per agent, self-registering into a REGISTRY
    base.py          BaseAgent, Field, AgentMeta, @register
    _context.py      Doc -> LLM context helpers
    <agent>.py       The 15 agents
  rag/               FAQ chatbot retrieval pipeline
    chunker.py       Heading-aware, overlapping Markdown chunker
    embedder.py      Embeddings via Portkey -> Azure OpenAI integration
    store.py         Persistent ChromaDB vector store wrapper
    index.py         Ingestion: chunk -> embed -> persist  (python -m backend.rag.index)
    retriever.py     Hybrid semantic + BM25 retrieval, fused with RRF
  orchestration/     LangGraph multi-agent orchestration (see below)
    graph.py         Exit Process Orchestrator: fan-out/join + retry cycle

data/                Seed corpus (Markdown). employees, exit-cases, kt-docs and
                     interviews are imported into PostgreSQL by backend.db.seed;
                     policies and process-docs are read at runtime as the RAG
                     pipeline's source documents
  .chroma/           Generated vector DB (not source)

scripts/
  init_database.py   Creates the application role and databases

tests/               pytest suite; test_store_contract.py pins the DataStore
                     surface against a snapshot of the pre-migration behaviour
```

**Design:** every agent declares an input schema (`fields`); the frontend renders
its form automatically and `main.py` auto-fills employee/case/KT/interview dropdowns
from the data store. Agent results are arbitrary JSON, rendered by one recursive
renderer with badges, score meters, and email/source styling — so all fifteen agents
share one consistent, minimal look. Two agents (FAQ chatbot, Interview Trend
Analyst) opt into a chat UI instead; Document Collection adds a `file` field
(multipart via `POST /api/agents/{id}/upload`); IT Deprovisioning adds an
Approve-and-execute step under its result.

## Tool use, OCR, and human-in-the-loop (agents 11, 12, 14)

- **Tool calling (11).** `LLMClient.complete_with_tools()` runs a real
  function-calling loop through Portkey (OpenAI-style `tools`; the gateway
  reports tool calls the same way on both Bedrock and Vertex, verified before
  this agent was built around it). The tools are the connectors in
  `backend/systems.py`; they are deterministic mocks derived from the employee and
  case records, so every agent sees a consistent world. Swap in real clients to
  go to production — the agent only depends on the return shapes.
- **Local OCR (12).** Uploaded scans never go to the LLM. `backend/ocr.py`
  extracts the text on the machine (Windows' built-in OCR engine, ~0.4 s/page;
  `rapidocr` on ONNX Runtime as the portable fallback) and one short text call
  judges whether it is the expected form for the expected employee, signed and
  dated. Same principle as the RAG pipeline: cheap local work first, the model
  only sees compact text.
- **Human-in-the-loop (14).** The plan and its execution are separate
  functions. Standalone, the result page shows the plan with an Approve button
  that calls `POST /api/agents/it_deprovisioning/execute`. In the orchestrator the
  same pair straddles a LangGraph `interrupt()` (below). Execution is LLM-free, so
  exactly what was approved is what runs.
- **Truncation guard.** Agents that receive a lot of evidence (compliance in the
  orchestrator, the optimizer) can hit the output cap mid-JSON. `complete_json()`
  detects this by walking the received text for unbalanced delimiters rather
  than matching a provider's `finish_reason` vocabulary — Gemini reports
  `MAX_TOKENS` where Anthropic reports `max_tokens`, so a hardcoded list fails
  silently on the next provider — then retries at a budget measured from the
  reasoning tokens actually consumed, and salvages a repaired object if the
  retry still overflows.
- **Persisted runs.** Every agent invocation is recorded in `agent_runs` by
  `BaseAgent.execute()`, with inputs (upload bytes redacted), result, duration
  and any exception. Agents implement `run()`; they inherit the rest.

## LLM layer (Portkey)

`backend/llm.py` uses the **Portkey SDK** (not the OpenAI SDK). Config comes from
`.env` (see `.env.example`), defaulting to the values in Claude's `settings.json`:

- Gateway: `https://portkeygateway.perficient.com/v1`
- Provider: `@dsvertex`
- Model: `gemini-2.5-flash`

Notes discovered while wiring this up:
- Every integration enforces a **model allowlist**. The previous
  `@aws-bedrock-use2` provider now returns `400 bedrock error: Operation not
  allowed` and is no longer usable on this gateway.
- The gateway forwards each upstream provider's quirks rather than normalising
  them, so `backend/llm.py` negotiates capabilities at runtime instead of
  hardcoding any one provider's behaviour: it detects JSON-mode support,
  measures reasoning-token usage, and finds truncation by walking the received
  text for unbalanced delimiters rather than matching a `finish_reason`
  vocabulary. Switching providers needs no code change.
- Gemini's thinking tokens come out of `max_tokens` and expand to fill whatever
  budget they are given, so raising the cap on a truncated answer buys more
  thinking, not more answer.

## FAQ chatbot RAG pipeline

The FAQ chatbot is a real retrieval-augmented pipeline, not keyword matching:

```
chunk (heading-aware, overlapping)
  -> embed  (Portkey -> Azure OpenAI text-embedding-3-small)
  -> persist (ChromaDB vector DB on disk under data/.chroma)
  -> retrieve (hybrid: semantic vector search + BM25, fused with RRF)
  -> generate (Portkey -> the configured chat model) with cited sources
```

Embeddings and generation both go through the Portkey gateway now, just via
different registered integrations: the integration used for chat **cannot do
embeddings** (`embed is not supported by bedrock` on the old provider), but
Portkey also has an `@azure-openai` integration, which can. `backend/rag/embedder.py`
routes to it by prefixing the model id (`@azure-openai/text-embedding-3-small`)
rather than setting a different `provider` on the client. Hybrid retrieval
(semantic + BM25 via Reciprocal Rank Fusion) catches both paraphrases
("when do I get my money" -> Full-and-Final Settlement) and exact terms.

The index builds automatically on first server startup. To rebuild it manually
(e.g. after editing docs, or after changing `EMBED_MODEL`):

```bash
python -m backend.rag.index
```

## Multi-agent orchestration (LangGraph)

Agents 1-10 are single-shot: one form submit, one LLM call, one JSON result via
`POST /api/agents/{id}/run`. That's fine until a task is graph-shaped instead of
linear — needs to fan out to independent sub-agents, join their results, and
cycle (retry/reroute) based on an outcome. `backend/orchestration/graph.py`
adds that as a second execution mode, built with **LangGraph**, calling the
existing agents through `REGISTRY` unchanged:

```
                 ┌─ checklist ──────────────┐
                 ├─ risk_assessment ────────┤
load_case ───────┼─ smart_routing ──────────┼──> approval_gate ──> deprov_execute ──> compliance ──cleared──> END
(exit case)      ├─ multi_system_clearance ─┤   (interrupt: human    (approved: run          │
                 ├─ document_collection ────┤    approve / reject)    rejected: skip)    blocked (retries < 2)
                 └─ deprov_plan ────────────┘                                                │
                                                                                             v
                                                                                          escalate ──> back to compliance
```

Six agents fan out from `load_case` and join at the **approval gate**, where the
graph calls LangGraph's `interrupt()` with the deprovisioning plan and stops. The
run is checkpointed (`PostgresSaver`, one `thread_id` per run) until a human
resumes it with approve or reject; approve executes the plan through the
connectors, reject skips execution and compliance is told so. Compliance then
verifies with **extra evidence** handed over by the other agents: live
per-system status from 11, missing documents from 12, and the execution audit
log (or "not performed") from 14. If blocked, it escalates and re-verifies up to
twice before settling on `cleared` / `blocked` / `error`.

The frontend's **Exit Process Orchestrator** page draws this graph live. It
subscribes to `GET /api/orchestrate/{case_id}/stream` (Server-Sent Events fed by
LangGraph's `debug` stream, one `node_start` / `node_end` per node), so each
agent's circle gets its own spinner ring while it runs and settles to a check or
cross when it finishes -- parallel branches independently. At the gate the
stream sends `awaiting_approval` and closes; the page shows the plan with
Approve / Reject, then re-opens the same route with `?thread_id=&decision=` to
resume and stream the rest. `done` carries the final state, rendered under the
chain as a verdict plus one collapsible section per agent. Deep link:
`/orchestrator` (a real route, so it survives a reload). `POST /api/orchestrate/{case_id}` is the non-interactive
variant (the gate is answered with `decision` from the body, default approve).

Agents 16-20 (supervisor-coordinated stage agents, rehire assessment, policy
auditor, predictive attrition, end-to-end automation — see `New Text
Document.txt`) are unbuilt; this graph is the pattern they'll extend.

## Run it

Prerequisites: Python 3.11+, Node 18+, and PostgreSQL 17. To install the
database on Windows (elevated prompt — it registers a service):

```
winget install --id PostgreSQL.PostgreSQL.17 --exact ^
  --accept-package-agreements --accept-source-agreements ^
  --custom "--superpassword postgres --serverport 5432 --enable-components server,commandlinetools"
```

Then:

```bash
pip install -r requirements.txt
cp .env.example .env          # set PORTKEY_API_KEY and the DATABASE_URL password
python -m scripts.init_database   # creates the offboard role and databases
alembic upgrade head              # builds the schema
python -m backend.db.seed         # imports data/ into PostgreSQL
python -m scripts.create_users    # one account per role; prints passwords ONCE
python -m scripts.link_stage_owners   # connects accounts to the stages they own
cd frontend && npm install && npm run build && cd ..   # builds the React app
uvicorn backend.main:app --reload
# open http://127.0.0.1:8000  (first boot builds the FAQ index via the gateway and warms the OCR engine)
```

The frontend must be built before the server can serve it; without
`frontend/dist` every page returns a 503 saying exactly that. For UI work run
Vite instead and let it proxy the API, which gives hot reload:

```bash
cd frontend && npm run dev     # http://127.0.0.1:5173, /api proxied to :8000
```

`python -m pytest` runs the backend suite and `cd frontend && npm test` the
frontend one. The backend tests run against `TEST_DATABASE_URL` (a separate
`offboard_test` database), which `tests/conftest.py` points the engine at
before anything imports `backend.db` -- so a test run cannot touch development
data. That database must be migrated and seeded too, or the suite refuses to
start rather than quietly skipping half of itself:

```bash
DATABASE_URL=$TEST_DATABASE_URL alembic upgrade head
DATABASE_URL=$TEST_DATABASE_URL python -m backend.db.seed
```

See [docs/database.md](docs/database.md) for schema, migrations, and how a new
agent gets persistence without writing any.

## Roles

### Signing in for a demo

`scripts/create_users.py` prints each generated password exactly once. For a
local run where you want to move between roles, put one known password on every
account instead:

```bash
python -m scripts.create_users --demo-passwords    # password: offboard-demo-2026
```

It refuses unless `COOKIE_SECURE=0`, which is the setting that already says
"this is local http". The four accounts worth using:

| Role | Account | What they see |
|---|---|---|
| HR | `hr@perficient.com` | all 6 cases, orchestrator, people & access |
| IT | `sanya.kapoor@perficient.com` | all 6 cases, 6 IT Clearance stages assigned, deprovisioning |
| Manager | `vikram.sharma@perficient.com` | 3 cases (emp-001, 006, 008), KT + sign-off |
| Employee | `aarav.mehta@perficient.com` | case-001 only, plus the FAQ chatbot |

Three of the seeded managers — `anjali.desai@`, `neha.iyer@`, `sunil.rao@` —
correctly see nothing: none of their reports are exiting. That is the scope
check working, not an empty database.


Four kinds of person, matching the five clearance stages the exit data already
models. Each one gets a scoped work queue and can actually move the stage they
own -- recorded in `case_stage_events`, because the exit case *is* the
compliance record.

| Role | Owns | Sees |
|---|---|---|
| `hr` | HR Clearance, Finance Clearance | Everything. Blanket override on any stage, recorded as an escalation |
| `it` | IT Clearance | Every case (they clear access on all of them), plus deprovisioning |
| `manager` | Knowledge Transfer, Manager Sign-off | Only cases whose employee reports to them |
| `employee` | — | Only their own exit case, plus the FAQ chatbot |

Authorisation is two independent checks. `require_roles(...)` gates the route;
`scoped_case` resolves the record already filtered to the caller, so a route
physically cannot obtain a case it has not scoped -- `tests/test_permissions.py`
walks the route table and fails if a new `{case_id}` route skips it.

Two things are deliberately split across people:

* **Deprovisioning** goes `pending -> approved -> executed`. HR authorises, IT
  runs it, and the two cannot be the same person. Before the split, whoever
  executed was recorded as the approver -- so opening execution to IT would
  have let one account draft a revocation against anyone, run it, and sign its
  own paperwork.
* **Nobody can action their own exit case**, whatever their role.

`blocked` is a `blocked_reason` column, not a stage status. Four consumers read
`status in ("in_progress", "completed")` as "started", so a fourth status would
read as *earlier* than in progress and silently regress derived facts --
including making a blocked stage's documents stop counting as due, which erases
the policy auditor's missing-document findings. A stalled stage stays
`in_progress` and carries a reason, so its SLA keeps counting.

### API

Routes live in `backend/api/`; `backend/main.py` only assembles the app, mounts
the static bundle, and declares the SPA catch-all last so it cannot shadow the
API. Every mutating `/api` request must carry the `X-CSRF-Token` header
(middleware, so a new route cannot forget it); login and logout are exempt.

**Auth** — `backend/api/auth_routes.py`
- `POST /api/auth/login` · `POST /api/auth/logout`
- `GET  /api/auth/me` — 200-with-null when signed out, so a page load can ask
  without treating "nobody" as an error
- `POST /api/auth/password` — rotate; revokes every session including this one

**Agents** — `backend/api/agents_routes.py`
- `GET  /api/agents` — the catalog, filtered to the caller's role, with select
  options scoped to the records they may name
- `POST /api/agents/{id}/run` — checks the agent *and* its arguments
- `POST /api/agents/{id}/upload` — same, as multipart (the Document Collection scan)
- `POST /api/agents/it_deprovisioning/execute` — `{plan_id, note}`. `hr` and `it`;
  refuses a plan that is unapproved, or approved by the same person

**Cases and work** — `backend/api/cases_routes.py`
- `GET  /api/me/case` — the caller's own case, resolved from the session
- `GET  /api/queue?scope=mine|all` — open stages of the types the role owns,
  worst first
- `GET  /api/cases` · `GET /api/cases/{case_id}` · `GET /api/cases/{case_id}/timeline`
- `POST /api/cases/{case_id}/stages/{stage_key}/transition` — `{to, note, claim,
  blocked_reason}`; writes `case_stage_events` and recomputes the case
- `POST /api/cases/{case_id}/stages/{stage_key}/claim`
- `GET  /api/cases/{case_id}/documents` · `POST /api/cases/{case_id}/documents/{doc_id}/review`
- `POST /api/employees`

**Orchestration** — `backend/api/orchestration_routes.py`
- `GET  /api/approvals` — runs paused at the human gate, surviving restarts
- `POST /api/deprovisioning/{plan_id}/approve` — HR authorises; IT then executes
- `POST /api/orchestrate/{case_id}` — non-interactive; `{decision}` answers the gate
- `GET  /api/orchestrate/{case_id}/stream` — the live run as Server-Sent Events
  (`run_start`, `node_start`, `node_end`, `awaiting_approval`, `done`, `error`);
  add `?thread_id=…&decision=approve|reject&note=…&csrf_token=…` to resume.
  EventSource cannot send headers, hence the token as a query parameter.

**Admin** — `backend/api/admin_routes.py`, all HR
- `GET/POST /api/admin/users` · `PATCH /api/admin/users/{id}` ·
  `POST /api/admin/users/{id}/reset-password`. Refuses to deactivate or demote
  the last active HR account.

## Sample data

13 employees (6 exiting), 6 exit cases (2 with overdue stages: `case-001`,
`case-003`), 3 KT docs (each with a deliberate gap), 4 exit interviews (2
negative Sales interviews for the trend demo), 4 policies, 3 process docs.
See `data/README.md` for the cross-reference tables.

Each case's `documents_submitted` list is seeded into `case_documents` as
accepted rows tagged `uploaded_by = 'seed:exit-case-record'`. After that the
list is **derived** from the latest upload per document, so it updates itself
as scans are validated — and a seeded assertion stays distinguishable from a
real scan, which has OCR text and the image attached.
