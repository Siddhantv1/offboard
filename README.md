# Offboard — Exit Automation Platform

Fifteen focused AI agents for employee offboarding, behind one unified frontend,
plus a LangGraph orchestrator that runs eight of them as one graph with a
human-in-the-loop approval gate. The LLM layer routes every model call through
the **Portkey AI gateway** (AWS Bedrock provider) configured in Claude's
`settings.json`.

## The 15 agents

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
frontend/            Vanilla HTML/CSS/JS SPA (no build step), served static
  index.html         Sidebar + main layout
  styles.css         Design system (warm paper, clay accent, serif display)
  app.js             Data-driven UI + recursive result renderer

backend/             FastAPI app
  main.py            API + serves the frontend
  config.py          Portkey/env settings
  llm.py             LLMClient — thin wrapper over the Portkey SDK (all LLM calls,
                     incl. the tool-calling loop used by agent 11)
  data_store.py      Loads the Markdown corpus (frontmatter + body)
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

data/                Sample corpus (Markdown): employees, exit-cases, policies,
                     process-docs, kt-docs, interviews
  .chroma/           Generated vector DB (not source)
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
  function-calling loop through Portkey -> Bedrock Sonnet 5 (`finish_reason`
  `tool_use`, OpenAI-style `tools`). The tools are the connectors in
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
  orchestrator, the optimizer) can hit the output cap mid-JSON; `complete_json()`
  detects `finish_reason` `max_tokens` / `length` and retries once with more room.

## LLM layer (Portkey)

`backend/llm.py` uses the **Portkey SDK** (not the OpenAI SDK). Config comes from
`.env` (see `.env.example`), defaulting to the values in Claude's `settings.json`:

- Gateway: `https://portkeygateway.perficient.com/v1`
- Provider: `@aws-bedrock-use2`
- Model: `us.anthropic.claude-sonnet-5`

Notes discovered while wiring this up:
- The `@aws-bedrock-use2` integration enforces a **model allowlist**; use the
  `us.`-prefixed inference-profile ID (`us.anthropic.claude-sonnet-5`).
- The Sonnet-5 Bedrock model **rejects the `temperature` parameter**, so the client
  never forwards it.

## FAQ chatbot RAG pipeline

The FAQ chatbot is a real retrieval-augmented pipeline, not keyword matching:

```
chunk (heading-aware, overlapping)
  -> embed  (Portkey -> Azure OpenAI text-embedding-3-small)
  -> persist (ChromaDB vector DB on disk under data/.chroma)
  -> retrieve (hybrid: semantic vector search + BM25, fused with RRF)
  -> generate (Portkey -> Bedrock Claude Sonnet 5) with cited sources
```

Embeddings and generation both go through the Portkey gateway now, just via
different registered integrations: the `@aws-bedrock-use2` integration used for
chat **cannot do embeddings** (`embed is not supported by bedrock`), but
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
run is checkpointed (`MemorySaver`, one `thread_id` per run) until a human
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
`/#orchestrator`. `POST /api/orchestrate/{case_id}` is the non-interactive
variant (the gate is answered with `decision` from the body, default approve).

Agents 16-20 (supervisor-coordinated stage agents, rehire assessment, policy
auditor, predictive attrition, end-to-end automation — see `New Text
Document.txt`) are unbuilt; this graph is the pattern they'll extend.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env          # adjust PORTKEY_MODEL / keys if needed
uvicorn backend.main:app --reload
# open http://127.0.0.1:8000  (first boot builds the FAQ index via the gateway and warms the OCR engine)
```

### API

- `GET  /api/health` — gateway/config + data counts
- `GET  /api/agents` — agent catalog with auto-filled select options
- `POST /api/agents/{id}/run` — run an agent; body is a JSON object of inputs
- `POST /api/agents/{id}/upload` — same, as a multipart form (for `file` fields;
  the Document Collection scan)
- `POST /api/agents/it_deprovisioning/execute` — execute an approved plan
  (`{plan, approved_by, note}`), returns the audit log
- `GET  /api/cases` — exit case options (used by the orchestrator picker)
- `POST /api/employees` — add a new employee (writes a Markdown file under
  `data/employees/`, refreshes the in-memory cache immediately — no restart)
- `POST /api/orchestrate/{case_id}` — run the orchestrator non-interactively
  (body `{decision: approve|reject}` answers the gate)
- `GET  /api/orchestrate/{case_id}/stream` — the live run as Server-Sent Events
  (`run_start`, `node_start`, `node_end`, `awaiting_approval`, `done`, `error`);
  add `?thread_id=…&decision=approve|reject&note=…` to resume a paused run

## Sample data

13 employees (6 exiting), 6 exit cases (2 with overdue stages: `case-001`,
`case-003`; each case also lists `documents_submitted` for the Document
Collection agent), 3 KT docs (each with a deliberate gap), 4 exit interviews (2
negative Sales interviews for the trend demo), 4 policies, 3 process docs.
See `data/README.md` for the cross-reference tables.
