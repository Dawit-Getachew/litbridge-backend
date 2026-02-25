# LitBridge Backend — Agent Instructions

## What This Project Is

LitBridge is a federated biomedical literature search platform. It solves three problems researchers face daily:
1. **Data silos** — publications scattered across PubMed, Europe PMC, OpenAlex, ClinicalTrials.gov
2. **Duplicate overload** — same paper appearing from multiple sources
3. **Paywall barriers** — legal free versions exist but are hard to find

The backend accepts PICO or Boolean search queries, federates them across 4+ APIs simultaneously, deduplicates results into canonical "Golden Records," enriches with AI summaries and citation metrics, and resolves Open Access full-text links. All while enforcing PRISMA systematic review methodology.

## Before You Write Code

1. **Read the rules**: Check `.cursor/rules/` for domain-specific patterns
2. **Plan first**: Outline files to create/modify, ask "Approve plan?"
3. **Check existing code**: Search for similar patterns before creating new ones
4. **One responsibility per file**: Never mix architectural layers

## Multi-Agent Workflow (Agents A through E)

| Agent | Name | Role | Path | Priority |
|-------|------|------|---------|----------|
| A | Syntax Translator | Translates PubMed Boolean → source-specific query dialects | Fast path | Blocking |
| B | Federated Fetcher | Parallel async calls to PubMed, EPMC, OpenAlex, ClinicalTrials | Fast path | Blocking |
| C | Dedup Engine | DOI/PMID hard match + fuzzy title → Golden Records | Fast path | Blocking |
| D | Semantic Enricher | TLDR summaries (Semantic Scholar → LLM fallback) + citations | Slow path | Background |
| E | OA Resolver | Free PDF links via OpenAlex → Unpaywall → Europe PMC cascade | Slow path | Background |

**Critical**: Agents A→B→C are the **fast path** (must complete in <2s for first paint). Agents D and E are **slow path** (background, never block the response).

## Architecture

```
src/
├── api/v1/              → Thin routers (validate, delegate, respond)
│   ├── search.py        → POST /search, GET /results/{id}
│   ├── enrichment.py    → GET /enrichment/{search_id}/{record_id}
│   └── prisma.py        → GET /prisma/{search_id}
├── services/
│   ├── search_service.py     → Orchestrates Agents A→B→C (fast path)
│   ├── enrichment_service.py → Orchestrates Agent D (background)
│   ├── oa_service.py         → Orchestrates Agent E (background)
│   ├── dedup_service.py      → Agent C logic
│   └── prisma_service.py     → PRISMA counter computation
├── repositories/
│   ├── pubmed_repo.py        → PubMed E-Utilities (XML parsing)
│   ├── openalex_repo.py      → OpenAlex REST API (cursor pagination)
│   ├── clinicaltrials_repo.py → ClinicalTrials.gov V2
│   ├── europepmc_repo.py     → Europe PMC REST API
│   ├── semantic_scholar_repo.py → TLDR + citation counts
│   ├── unpaywall_repo.py     → OA resolution
│   └── search_repo.py        → PostgreSQL search persistence
├── schemas/
│   ├── records.py       → UnifiedRecord, RawRecord, SourceType, OAStatus
│   ├── search.py        → SearchRequest, SearchResponse, PaginatedResults
│   ├── enrichment.py    → EnrichmentResponse
│   ├── prisma.py        → PrismaCounts, PrismaFilters
│   └── pico.py          → PICOInput
├── models/
│   ├── search.py        → Search session DB model
│   └── screening.py     → PRISMA screening decisions
├── ai/
│   ├── graphs/research_graph.py → LangGraph workflow (A→B→C + background D,E)
│   ├── nodes/           → Individual node functions
│   ├── adapters/        → Query translation adapters (Agent A)
│   └── llm_client.py    → OpenRouter wrapper for TLDR fallback
├── core/
│   ├── config.py        → pydantic-settings (all env vars)
│   ├── deps.py          → FastAPI dependency providers (DB, Redis, httpx clients)
│   ├── exceptions.py    → Domain exceptions
│   ├── security.py      → Auth utilities
│   └── middleware.py     → CORS, request ID, HTTP caching (ETag/304)
└── main.py              → FastAPI app + lifespan (init pools, clients)
```

## Tech Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Framework | FastAPI | 0.115+ |
| Package Manager | uv | 0.5+ |
| Python | CPython | 3.12+ |
| Agent Workflow | LangGraph | 0.3+ |
| Database | PostgreSQL | 16+ |
| ORM | SQLAlchemy (async) | 2.0+ |
| Migrations | Alembic | 1.13+ |
| Cache | Redis | 7+ |
| HTTP Client | httpx | 0.27+ |
| Fuzzy Match | rapidfuzz | 3.0+ |
| Logging | structlog | 24.0+ |
| Settings | pydantic-settings | 2.0+ |
| Testing | pytest + pytest-asyncio | latest |

## External APIs

| API | Purpose | Auth | Rate Limit |
|-----|---------|------|-----------|
| PubMed E-Utilities | Journal articles | API key + email | 10/s |
| OpenAlex | Works metadata + OA status | Email (polite pool) | 10/s |
| ClinicalTrials.gov V2 | Clinical trials | None | ~5/s |
| Europe PMC | Articles + full-text XML | None | 10/s |
| Semantic Scholar | TLDR + citations | Optional API key | 100/s |
| Unpaywall | OA resolution | Email | 100K/day |
| Crossref | Citation counts + JATS | Email (polite pool) | 50/s |
| OpenCitations | ID bridging (DOI↔PMID) | None | Open |
| OpenRouter | LLM for TLDR fallback | API key | Per plan |

## Common Tasks

| Task | What to Do |
|------|-----------|
| Add new data source | New adapter in `ai/adapters/` + repo in `repositories/` + wire into graph |
| New API endpoint | Router in `api/v1/` + service + Pydantic schemas + test |
| New LangGraph node | Async function in `ai/nodes/` + wire into `ai/graphs/` |
| Add dependency | `uv add <package>` |
| Run migrations | `alembic revision --autogenerate -m "msg"` then `alembic upgrade head` |
| Run tests | `uv run pytest tests/ -v --asyncio-mode=auto` |
| Run dev server | `uv run uvicorn src.main:app --reload --port 8000` |

## Quality Checklist (Every Change)

- [ ] Full type hints on all function signatures and returns
- [ ] Pydantic models for all API input/output
- [ ] No `print()` — use structlog
- [ ] No hardcoded secrets — everything from `.env` via `Settings`
- [ ] Tests written alongside the feature
- [ ] Async for all I/O operations
- [ ] No debug artifacts left in code
- [ ] First paint target: < 2 seconds
- [ ] Partial failure handled: one source down doesn't crash the search
- [ ] Cache key documented if adding new Redis keys

## Boundaries

- **NEVER** edit files outside the scope of the current task
- **NEVER** auto-apply improvements not explicitly requested
- **NEVER** commit `.env` files or API keys
- **ALWAYS** generate commit-ready code: type hints, no TODOs
- **ALWAYS** follow existing patterns in the codebase
- **ALWAYS** handle partial failures gracefully in federated operations
