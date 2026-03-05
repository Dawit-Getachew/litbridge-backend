# LitBridge Backend

Federated biomedical literature search platform. Searches PubMed, Europe PMC, OpenAlex, and ClinicalTrials.gov in parallel, deduplicates results, enriches with AI summaries and citation metrics, resolves open-access full-text, and supports conversational follow-up over search results.

## Tech Stack

- **Framework:** FastAPI (async)
- **Database:** PostgreSQL (asyncpg + SQLAlchemy 2.0)
- **Cache:** Redis
- **AI/LLM:** OpenAI / OpenRouter (GPT-4o-mini)
- **Package Manager:** uv
- **Migrations:** Alembic (async)
- **Testing:** pytest + pytest-asyncio

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (install: `curl -LsSf https://astral.sh/uv/install.sh | sh` or `pip install uv`)
- PostgreSQL 16+
- Redis 7+

## Quick Start (Local Development)

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd litbridge-backend
uv venv
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your real values:

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `NCBI_API_KEY` | PubMed API key ([get one here](https://www.ncbi.nlm.nih.gov/account/settings/)) |
| `CONTACT_EMAIL` | Required by NCBI and polite pool APIs |
| `OPENAI_API_KEY` | OpenAI API key for AI features |
| `SECRET_KEY` | Random string for signing |
| `CORS_ORIGINS` | JSON array of allowed origins, e.g. `["*"]` |

### 3. Start PostgreSQL and Redis

If you have Docker:

```bash
docker run -d --name litbridge-pg \
  -e POSTGRES_USER=litbridge \
  -e POSTGRES_PASSWORD=litbridge_dev_2026 \
  -e POSTGRES_DB=litbridge \
  -p 5432:5432 \
  postgres:16

docker run -d --name litbridge-redis \
  -p 6379:6379 \
  redis:7
```

Or use your existing local Postgres/Redis instances — just update `DATABASE_URL` and `REDIS_URL` in `.env`.

### 4. Run database migrations

```bash
uv run alembic upgrade head
```

This creates the `search_sessions`, `conversations`, and `messages` tables.

### 5. Start the dev server

```bash
uv run uvicorn src.main:app --reload --port 8000
```

The API is now running at `http://localhost:8000`.

- Interactive docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

## Alembic (Database Migrations)

All commands use `uv run` to run inside the virtual environment.

```bash
# Apply all pending migrations
uv run alembic upgrade head

# Roll back the last migration
uv run alembic downgrade -1

# Roll back all migrations
uv run alembic downgrade base

# Check current migration version
uv run alembic current

# View migration history
uv run alembic history --verbose

# Generate a new migration after changing models
uv run alembic revision --autogenerate -m "describe your change"
```

Alembic reads `DATABASE_URL` from `.env` automatically (via `alembic/env.py` → `get_settings()`).

## Running Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ -v --cov=src --cov-report=term-missing

# Run only unit tests
uv run pytest tests/ -v -m "not integration"

# Run a specific test file
uv run pytest tests/test_search_api.py -v
```

## Docker (Production)

### Build and run locally

```bash
docker build -t litbridge-backend .

docker run -d \
  --name litbridge-api \
  -p 8000:8000 \
  --env-file .env \
  litbridge-backend
```

The container automatically runs `alembic upgrade head` on startup before launching the server.

### Deploy to Coolify

1. Push the repo to GitHub/GitLab
2. In Coolify, create a new **Project**
3. Add a **PostgreSQL** service (note the internal hostname, e.g. `postgres`)
4. Add a **Redis** service (note the internal hostname, e.g. `redis`)
5. Add the API as a **Docker** resource pointing to your Git repo
6. Set these environment variables in the Coolify dashboard:

```
DATABASE_URL=postgresql+asyncpg://litbridge:YOUR_PASSWORD@postgres:5432/litbridge
REDIS_URL=redis://redis:6379/0
NCBI_API_KEY=your_key
CONTACT_EMAIL=your@email.com
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-your-key
OPENAI_MODEL=gpt-4o-mini
OPENROUTER_API_KEY=
OPENROUTER_MODEL=openai/gpt-4o-mini
CORS_ORIGINS=["*"]
SECRET_KEY=generate-a-random-string
APP_NAME=LitBridge
DEBUG=false
HOST=0.0.0.0
PORT=8000
```

7. Set the exposed port to **8000**
8. Deploy

For SSL database connections (managed Postgres), append `?ssl=require`:

```
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname?ssl=require
```

## API Overview

Full documentation in [`API_REFERENCE.md`](API_REFERENCE.md) and machine-readable spec in [`api-spec.json`](api-spec.json).

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/v1/search` | Execute search (returns search_id) |
| POST | `/api/v1/search/stream` | Stream search via SSE (real-time) |
| POST | `/api/v1/search/preview` | Preview translated queries per source |
| GET | `/api/v1/search/{id}/status` | Poll search status |
| GET | `/api/v1/results/{id}` | Paginated results |
| GET | `/api/v1/enrichment/{search_id}/{record_id}` | Per-record TLDR + citations + OA |
| GET | `/api/v1/prisma/{search_id}` | PRISMA flow counts |
| POST | `/api/v1/chat/stream` | Conversational AI follow-up (SSE) |
| GET | `/api/v1/chat/{conversation_id}/history` | Chat history |
| GET | `/api/v1/chat/conversations/{search_id}` | List conversations |
| GET | `/health` | Health check |

### Search Modes

| Mode | Enrichment | AI Thinking | Max Results |
|---|---|---|---|
| `quick` | No | No | 100 |
| `deep_research` | No | No | 5000 |
| `deep_analyze` | Yes | No | 5000 |
| `deep_thinking` | Yes | Yes (comprehensive) | 5000 |
| `light_thinking` | No | Yes (concise) | 100 |

## Project Structure

```
src/
├── api/v1/          # FastAPI routers (search, enrichment, prisma, chat)
├── services/        # Business logic (search, enrichment, chat, PRISMA)
├── repositories/    # Data access (PubMed, OpenAlex, EPMC, ClinicalTrials, DB)
├── schemas/         # Pydantic models (request/response contracts)
├── models/          # SQLAlchemy ORM models
├── ai/              # LLM client, adapters, graph nodes
├── core/            # Config, deps, middleware, exceptions, database, redis
└── main.py          # App entrypoint + lifespan
```

## License

Proprietary.
