# Offboard — Exit Automation Platform

Ten focused AI agents for employee offboarding, behind one unified frontend.
The LLM layer routes every model call through the **Portkey AI gateway**
(AWS Bedrock provider) configured in Claude's `settings.json`.

## The 10 agents

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

## Architecture

```
frontend/            Vanilla HTML/CSS/JS SPA (no build step), served static
  index.html         Sidebar + main layout
  styles.css         Design system (warm paper, clay accent, serif display)
  app.js             Data-driven UI + recursive result renderer

backend/             FastAPI app
  main.py            API + serves the frontend
  config.py          Portkey/env settings
  llm.py             LLMClient — thin wrapper over the Portkey SDK (all LLM calls)
  data_store.py      Loads the Markdown corpus (frontmatter + body)
  agents/            One module per agent, self-registering into a REGISTRY
    base.py          BaseAgent, Field, AgentMeta, @register
    _context.py      Doc -> LLM context helpers
    <agent>.py       The 10 agents
  rag/               FAQ chatbot retrieval pipeline
    chunker.py       Heading-aware, overlapping Markdown chunker
    embedder.py      Local Model2Vec static embeddings (no PyTorch)
    store.py         Persistent ChromaDB vector store wrapper
    index.py         Ingestion: chunk -> embed -> persist  (python -m backend.rag.index)
    retriever.py     Hybrid semantic + BM25 retrieval, fused with RRF

data/                Sample corpus (Markdown): employees, exit-cases, policies,
                     process-docs, kt-docs, interviews
  .chroma/           Generated vector DB (not source)
```

**Design:** every agent declares an input schema (`fields`); the frontend renders
its form automatically and `main.py` auto-fills employee/case/KT/interview dropdowns
from the data store. Agent results are arbitrary JSON, rendered by one recursive
renderer with badges, score meters, and email/source styling — so all ten agents
share one consistent, minimal look.

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
  -> embed  (Model2Vec static embeddings, local, ~30MB, no PyTorch)
  -> persist (ChromaDB vector DB on disk under data/.chroma)
  -> retrieve (hybrid: semantic vector search + BM25, fused with RRF)
  -> generate (Portkey gateway) with cited sources
```

Why local embeddings: the Portkey `@aws-bedrock-use2` integration **cannot do
embeddings** (`embed is not supported by bedrock`), so only the retrieval/embedding
step runs locally — generation still goes through the gateway. Hybrid retrieval
(semantic + BM25 via Reciprocal Rank Fusion) catches both paraphrases
("when do I get my money" -> Full-and-Final Settlement) and exact terms.

The index builds automatically on first server startup. To rebuild it manually
(e.g. after editing docs):

```bash
python -m backend.rag.index
```

If Hugging Face is unreachable, pre-download the embedding model and set
`MODEL2VEC_PATH` to its local directory (see `.env.example`).

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env          # adjust PORTKEY_MODEL / keys if needed
uvicorn backend.main:app --reload
# open http://127.0.0.1:8000  (first boot downloads the ~30MB embedding model + builds the index)
```

### API

- `GET  /api/health` — gateway/config + data counts
- `GET  /api/agents` — agent catalog with auto-filled select options
- `POST /api/agents/{id}/run` — run an agent; body is a JSON object of inputs

## Sample data

12 employees (5 exiting), 5 exit cases (2 with overdue stages: `case-001`,
`case-003`), 3 KT docs (each with a deliberate gap), 4 exit interviews (2 negative
Sales interviews for the trend demo), 4 policies, 3 process docs.
See `data/README.md` for the cross-reference tables.
