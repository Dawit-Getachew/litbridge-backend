# LitBridge API Reference

> **Base URL:** `/api/v1`
> **Content-Type:** `application/json` (all non-streaming endpoints)
> **Streaming Content-Type:** `text/event-stream` (SSE endpoints)

---

## Table of Contents

1. [General Conventions](#general-conventions)
2. [Enums](#enums)
3. [Search Endpoints](#search-endpoints)
4. [Enrichment Endpoints](#enrichment-endpoints)
5. [PRISMA Endpoints](#prisma-endpoints)
6. [Chat Endpoints](#chat-endpoints)
7. [System Endpoints](#system-endpoints)
8. [SSE Event Reference](#sse-event-reference)
9. [Search Mode Behavior Matrix](#search-mode-behavior-matrix)
10. [Error Codes](#error-codes)
11. [Schemas Reference](#schemas-reference)

---

## General Conventions

| Aspect | Detail |
|---|---|
| Request bodies | JSON (`application/json`) |
| Responses | JSON for REST endpoints, `text/event-stream` for SSE endpoints |
| IDs | UUIDs (string) |
| Dates | ISO 8601 datetime strings |
| Pagination | Cursor-based — pass `?cursor=` from `next_cursor` |
| Errors | `{ "detail": "Human-readable message" }` |
| SSE wire format | `event: <type>\ndata: <json>\n\n` |
| SSE keepalive | `: keepalive\n\n` every 15 seconds of inactivity |
| Null vs absent | Fields with `null` default may be omitted or set to `null` |

---

## Enums

### SourceType

External database sources for federated search.

| Value | Description |
|---|---|
| `pubmed` | PubMed (NCBI E-Utilities) |
| `europepmc` | Europe PMC |
| `openalex` | OpenAlex |
| `clinicaltrials` | ClinicalTrials.gov (V2 API) |

### OAStatus

Open-access availability status.

| Value | Description |
|---|---|
| `open` | Full text is freely available |
| `closed` | Behind a paywall |
| `unknown` | OA status has not been resolved yet |

### QueryType

How the query string is interpreted.

| Value | Description |
|---|---|
| `free` | Natural language — system translates for each source |
| `structured` | PICO format — requires `pico` field |
| `boolean` | PubMed-style Boolean — passed to all sources |
| `abstract` | Full abstract text (min 50 chars) — used for semantic matching |

### SearchMode

Controls search depth and AI processing.

| Value | Max Results | Enrichment | AI Thinking | Description |
|---|---|---|---|---|
| `quick` | 100 | No | No | Fast search, no AI |
| `deep_research` | 5000 | No | No | Full-depth search, no AI |
| `deep_analyze` | 5000 | Yes | No | Enriches each record with TLDR + citations |
| `deep_thinking` | 5000 | Yes | Yes (comprehensive) | Enrichment + streamed AI synthesis |
| `light_thinking` | 100 | No | Yes (concise) | Quick search + short streamed AI paragraph |

---

## Search Endpoints

### POST `/api/v1/search`

Execute a non-streaming search. Returns immediately with a `search_id`; poll status and fetch results separately.

**Request Body:** `SearchRequest`

```json
{
  "query": "metformin cardiovascular outcomes",
  "query_type": "free",
  "search_mode": "quick",
  "sources": ["pubmed", "openalex"],
  "max_results": 100
}
```

**Response:** `SearchResponse`

```json
{
  "search_id": "a3f8c9d1-2e45-4b7a-9c3d-1234567890ab"
}
```

**Errors:** `404`, `422`, `429`, `502`

---

### POST `/api/v1/search/preview`

Preview how the query will be translated for each source database without executing a search.

**Request Body:** `SearchRequest` (same as above)

**Response:**

```json
{
  "translations": {
    "pubmed": "(metformin[Title/Abstract]) AND (cardiovascular[Title/Abstract])",
    "openalex": "metformin cardiovascular",
    "europepmc": "(metformin) AND (cardiovascular)",
    "clinicaltrials": "metformin cardiovascular"
  }
}
```

---

### POST `/api/v1/search/stream`

Execute a search with real-time SSE streaming. Events narrate the entire lifecycle: source-by-source progress, deduplication, per-record enrichment, and token-by-token AI synthesis.

**Request Body:** `SearchRequest`

**Response:** `text/event-stream`

```
event: search_started
data: {"search_id": "abc-123", "sources": ["pubmed", "openalex"], "search_mode": "deep_thinking"}

event: status
data: {"message": "Translating query for each database..."}

event: source_searching
data: {"source": "pubmed", "message": "Searching PubMed..."}

event: source_completed
data: {"source": "pubmed", "count": 42, "duration_ms": 890}

event: dedup_completed
data: {"total_before": 180, "total_after": 150, "duplicates_removed": 30}

event: record_enriched
data: {"id": "rec-1", "tldr": "This study found...", "citation_count": 47, "progress": "1/150"}

event: thinking
data: {"chunk": "Based on the "}

event: thinking
data: {"chunk": "evidence from these "}

event: thinking
data: {"chunk": "150 studies..."}

event: search_completed
data: {"search_id": "abc-123", "total_count": 150}
```

See [SSE Event Reference](#sse-event-reference) for all event types.

---

### GET `/api/v1/search/{search_id}/status`

Check the status of a previously started search.

**Path Parameters:**

| Param | Type | Description |
|---|---|---|
| `search_id` | string | UUID from POST /search |

**Response:** `SearchStatusResponse`

```json
{
  "search_id": "abc-123",
  "status": "completed",
  "total_count": 150,
  "sources_completed": ["pubmed", "openalex", "europepmc"],
  "sources_failed": ["clinicaltrials"],
  "progress_pct": 100
}
```

**status values:** `"processing"`, `"completed"`, `"failed"`

**Errors:** `404`

---

### GET `/api/v1/results/{search_id}`

Retrieve one page of deduplicated search results.

**Path Parameters:**

| Param | Type | Description |
|---|---|---|
| `search_id` | string | UUID from search |

**Query Parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `cursor` | string \| null | null | Pagination cursor from `next_cursor` |

**Response:** `PaginatedResults`

```json
{
  "search_id": "abc-123",
  "total_count": 150,
  "records": [
    {
      "id": "rec-1",
      "title": "Metformin and Cardiovascular Risk Reduction",
      "authors": ["Zhang L", "Wang H"],
      "journal": "The Lancet",
      "year": 2024,
      "doi": "10.1016/j.lancet.2024.01234",
      "pmid": "39876543",
      "source": "pubmed",
      "sources_found_in": ["pubmed", "openalex"],
      "tldr": "This meta-analysis of 12 RCTs demonstrates...",
      "citation_count": 47,
      "oa_status": "open",
      "pdf_url": "https://europepmc.org/articles/PMC1234567?pdf=render",
      "abstract": "Background: Metformin has shown...",
      "duplicate_cluster_id": "clust-abc",
      "prisma_stage": "screened"
    }
  ],
  "next_cursor": "eyJvZmZzZXQiOiA1MH0="
}
```

**Errors:** `404`

---

## Enrichment Endpoints

### GET `/api/v1/enrichment/{search_id}/{record_id}`

Get AI enrichment for a single record: TLDR summary, citation count, OA status, and PDF link.

**Path Parameters:**

| Param | Type | Description |
|---|---|---|
| `search_id` | string | Search session UUID |
| `record_id` | string | `UnifiedRecord.id` |

**Response:** `EnrichmentResponse`

```json
{
  "id": "rec-1",
  "tldr": "This meta-analysis demonstrates a 22% reduction in cardiovascular events...",
  "citation_count": 47,
  "oa_status": "open",
  "pdf_url": "https://europepmc.org/articles/PMC1234567?pdf=render"
}
```

**Errors:** `404` (search not found or record not in search)

---

## PRISMA Endpoints

### GET `/api/v1/prisma/{search_id}`

Compute PRISMA systematic review flow diagram counts for a completed search.

**Path Parameters:**

| Param | Type | Description |
|---|---|---|
| `search_id` | string | Search session UUID |

**Query Parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `year_from` | integer \| null | null | Minimum publication year |
| `year_to` | integer \| null | null | Maximum publication year |
| `sources` | string \| null | null | Comma-separated source names, e.g. `pubmed,openalex` |
| `open_access_only` | boolean | false | Only count open-access records |

**Response:** `PrismaCounts`

```json
{
  "identified": 180,
  "after_deduplication": 150,
  "screened": 130,
  "excluded": 20,
  "oa_retrieved": 95
}
```

**Errors:** `404`, `422` (invalid source name)

---

## Chat Endpoints

### POST `/api/v1/chat/stream`

Send a natural-language follow-up message about search results. The AI response streams token-by-token via SSE. Reference papers by name or position — no IDs required.

**Request Body:** `ChatRequest`

```json
{
  "search_id": "abc-123",
  "message": "Explain the Zhang 2024 paper in simple terms",
  "conversation_id": null
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `search_id` | string | Yes | Search session whose results you're discussing |
| `message` | string (1-4000 chars) | Yes | Natural language message |
| `conversation_id` | string \| null | No | Omit to start new conversation; provide to continue existing one |

**Example messages the user might send:**

- "Explain the Zhang 2024 paper in simple terms"
- "Deep dive into the RECOVERY trial's methodology"
- "Compare the first paper with the WHO solidarity trial"
- "What are the limitations across all these studies?"

**Response:** `text/event-stream`

```
event: chat_started
data: {"conversation_id": "conv-456", "resolved_records": [{"id": "rec-1", "title": "Remdesivir for Treatment of COVID-19", "first_author": "Zhang L", "year": 2024}]}

event: thinking
data: {"chunk": "The Zhang 2024 "}

event: thinking
data: {"chunk": "paper is a randomized "}

event: thinking
data: {"chunk": "controlled trial that..."}

event: chat_completed
data: {"conversation_id": "conv-456", "message_id": "msg-789"}
```

See [Chat SSE Events](#chat-stream-events) for details.

---

### GET `/api/v1/chat/{conversation_id}/history`

Retrieve the full message history for a conversation.

**Path Parameters:**

| Param | Type | Description |
|---|---|---|
| `conversation_id` | string | UUID from `chat_started` event |

**Response:** `ConversationHistoryResponse`

```json
{
  "conversation": {
    "id": "conv-456",
    "search_id": "abc-123",
    "title": "Explain the Zhang 2024 paper in simple terms",
    "message_count": 4,
    "created_at": "2026-03-05T10:30:00Z",
    "updated_at": "2026-03-05T10:32:00Z"
  },
  "messages": [
    {
      "id": "msg-1",
      "role": "user",
      "content": "Explain the Zhang 2024 paper in simple terms",
      "record_ids": ["rec-1"],
      "created_at": "2026-03-05T10:30:00Z"
    },
    {
      "id": "msg-2",
      "role": "assistant",
      "content": "The Zhang 2024 paper is a randomized controlled trial...",
      "record_ids": ["rec-1"],
      "created_at": "2026-03-05T10:30:05Z"
    }
  ]
}
```

**Errors:** `404`

---

### GET `/api/v1/chat/conversations/{search_id}`

List all conversation threads for a search session.

**Path Parameters:**

| Param | Type | Description |
|---|---|---|
| `search_id` | string | Search session UUID |

**Response:** `ConversationResponse[]`

```json
[
  {
    "id": "conv-456",
    "search_id": "abc-123",
    "title": "Explain the Zhang 2024 paper in simple terms",
    "message_count": 4,
    "created_at": "2026-03-05T10:30:00Z",
    "updated_at": "2026-03-05T10:32:00Z"
  },
  {
    "id": "conv-789",
    "search_id": "abc-123",
    "title": "Compare metformin studies",
    "message_count": 2,
    "created_at": "2026-03-05T11:00:00Z",
    "updated_at": "2026-03-05T11:01:00Z"
  }
]
```

---

## System Endpoints

### GET `/`

API root. Returns service information.

```json
{
  "name": "LitBridge API",
  "version": "1.0.0",
  "status": "ok"
}
```

### GET `/health`

Health check. Probes database and Redis connectivity.

```json
{
  "status": "ok",
  "database": "connected",
  "redis": "connected"
}
```

---

## SSE Event Reference

### Wire Format

All SSE endpoints use the standard `text/event-stream` format:

```
event: <event_type>
data: <json_payload>

```

A keepalive comment is sent every 15 seconds of inactivity:

```
: keepalive

```

### Search Stream Events

Emitted by `POST /api/v1/search/stream`.

| Event | When | Data Fields | Description |
|---|---|---|---|
| `search_started` | Start | `search_id`, `sources`, `search_mode` | Search session created |
| `status` | Throughout | `message` | Human-readable progress narration (display in UI) |
| `source_searching` | Before each source fetch | `source`, `message` | A specific source is being queried |
| `source_completed` | After each source | `source`, `count`, `duration_ms` | Source returned results |
| `source_failed` | On source error | `source`, `error` | Source failed (search continues with others) |
| `dedup_completed` | After dedup | `total_before`, `total_after`, `duplicates_removed` | Deduplication stats |
| `record_enriched` | Per record (deep modes) | `id`, `tldr`, `citation_count`, `progress` | One record was enriched |
| `thinking` | Token-by-token (thinking modes) | `chunk` | Fragment of AI synthesis text |
| `search_completed` | End | `search_id`, `total_count` | Search finished |
| `error` | On failure | `error` | Error message |

**Event order by search mode:**

| Mode | Events Emitted |
|---|---|
| `quick` | search_started → status → source_searching/completed → dedup_completed → search_completed |
| `deep_research` | Same as quick (more results) |
| `deep_analyze` | Same as quick + record_enriched (one per record) |
| `deep_thinking` | Same as deep_analyze + thinking (token-by-token AI synthesis) |
| `light_thinking` | Same as quick + thinking (token-by-token short summary) |

### Chat Stream Events

Emitted by `POST /api/v1/chat/stream`.

| Event | When | Data Fields | Description |
|---|---|---|---|
| `chat_started` | Start | `conversation_id`, `resolved_records` | Conversation identified; papers resolved from natural language |
| `thinking` | Token-by-token | `chunk` | Fragment of AI response text |
| `chat_completed` | End | `conversation_id`, `message_id` | Response finished and saved |
| `error` | On failure | `error` | Error message |

**`resolved_records`** is an array of `ResolvedRecord` objects indicating which papers the system thinks the user is referring to. Use these to highlight papers in the UI.

---

## Search Mode Behavior Matrix

| Capability | `quick` | `deep_research` | `deep_analyze` | `deep_thinking` | `light_thinking` |
|---|---|---|---|---|---|
| Max results | 100 | 5000 | 5000 | 5000 | 100 |
| All 4 sources queried | Yes | Yes | Yes | Yes | Yes |
| Deduplication | Yes | Yes | Yes | Yes | Yes |
| TLDR enrichment | No | No | Yes | Yes | No |
| Citation counts | No | No | Yes | Yes | No |
| OA resolution | No | No | Yes | Yes | No |
| AI synthesis (streamed) | No | No | No | Yes (comprehensive) | Yes (concise) |
| `record_enriched` events | No | No | Yes | Yes | No |
| `thinking` events | No | No | No | Yes | Yes |
| Typical latency | < 2s | 2-5s | 5-15s | 10-30s | 2-5s |

---

## Error Codes

| Code | Meaning | When |
|---|---|---|
| `404` | Not Found | Search session, record, or conversation doesn't exist |
| `422` | Validation Error | Bad request body, invalid enum value, constraint violation |
| `429` | Rate Limited | Too many requests to upstream APIs. Check `Retry-After` header. |
| `502` | Bad Gateway | An upstream source API (PubMed, OpenAlex, etc.) failed |

**Error response format:**

```json
{
  "detail": "Search 'abc-123' was not found."
}
```

For `422` validation errors, FastAPI returns a structured error:

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "pico"],
      "msg": "pico must be provided when query_type is structured",
      "input": null
    }
  ]
}
```

---

## Schemas Reference

### SearchRequest

| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `query` | string | — | Yes | Search query text |
| `query_type` | QueryType | `"free"` | No | How to interpret the query |
| `search_mode` | SearchMode | `"quick"` | No | Controls depth and AI processing |
| `sources` | SourceType[] \| null | null (all) | No | Which databases to search |
| `pico` | PICOInput \| null | null | Conditional | Required when query_type is `"structured"` |
| `max_results` | integer (1-5000) | 100 | No | Capped to 100 for quick/light_thinking |

### PICOInput

| Field | Type | Default | Description |
|---|---|---|---|
| `population` | string \| null | null | Patient population |
| `intervention` | string \| null | null | Treatment or exposure |
| `comparison` | string \| null | null | Alternative being compared |
| `outcome` | string \| null | null | Measured outcome |

### UnifiedRecord

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | string | — | Unique record identifier |
| `title` | string | — | Publication title |
| `authors` | string[] | — | List of author names |
| `journal` | string \| null | null | Journal or source name |
| `year` | integer \| null | null | Publication year |
| `doi` | string \| null | null | Digital Object Identifier |
| `pmid` | string \| null | null | PubMed ID |
| `source` | SourceType | — | Primary source |
| `sources_found_in` | SourceType[] | [] | All sources where this appeared |
| `tldr` | string \| null | null | AI-generated one-sentence summary |
| `citation_count` | integer \| null | null | Number of citations |
| `oa_status` | OAStatus | `"unknown"` | Open-access status |
| `pdf_url` | string \| null | null | Link to free PDF |
| `abstract` | string \| null | null | Full abstract text |
| `duplicate_cluster_id` | string \| null | null | Cluster ID for dedup tracking |
| `prisma_stage` | string \| null | null | PRISMA flow stage |

### EnrichmentResponse

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | string | — | Record ID |
| `tldr` | string \| null | null | AI summary |
| `citation_count` | integer \| null | null | Citation count |
| `oa_status` | OAStatus \| null | null | OA status |
| `pdf_url` | string \| null | null | Free PDF link |

### PrismaCounts

| Field | Type | Description |
|---|---|---|
| `identified` | integer | Total records found before dedup |
| `after_deduplication` | integer | Records after duplicate removal |
| `screened` | integer | Records passing filter criteria |
| `excluded` | integer | Records excluded by filters |
| `oa_retrieved` | integer | Records with open-access full text |

### ChatRequest

| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `search_id` | string | — | Yes | Search session to discuss |
| `message` | string (1-4000) | — | Yes | Natural language message |
| `conversation_id` | string \| null | null | No | Continue existing conversation |

### ResolvedRecord

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | string | — | Record ID |
| `title` | string | — | Paper title |
| `first_author` | string \| null | null | First author name |
| `year` | integer \| null | null | Publication year |

### ConversationResponse

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | string | — | Conversation UUID |
| `search_id` | string | — | Associated search session |
| `title` | string \| null | null | Auto-generated title |
| `message_count` | integer | — | Total messages |
| `created_at` | datetime | — | ISO 8601 |
| `updated_at` | datetime | — | ISO 8601 |

### MessageResponse

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | string | — | Message UUID |
| `role` | `"user"` \| `"assistant"` | — | Who sent the message |
| `content` | string | — | Message text |
| `record_ids` | string[] \| null | null | Referenced paper IDs |
| `created_at` | datetime | — | ISO 8601 |

### ConversationHistoryResponse

| Field | Type | Description |
|---|---|---|
| `conversation` | ConversationResponse | Conversation metadata |
| `messages` | MessageResponse[] | All messages, chronological |

### SearchResponse

| Field | Type | Description |
|---|---|---|
| `search_id` | string | UUID for the search session |

### SearchStatusResponse

| Field | Type | Default | Description |
|---|---|---|---|
| `search_id` | string | — | Search session UUID |
| `status` | `"processing"` \| `"completed"` \| `"failed"` | — | Current state |
| `total_count` | integer | 0 | Records found so far |
| `sources_completed` | SourceType[] | [] | Sources that returned results |
| `sources_failed` | SourceType[] | [] | Sources that failed |
| `progress_pct` | integer (0-100) | 0 | Completion percentage |

### PaginatedResults

| Field | Type | Default | Description |
|---|---|---|---|
| `search_id` | string | — | Search session UUID |
| `total_count` | integer | — | Total deduplicated results |
| `records` | UnifiedRecord[] | — | Records for this page |
| `next_cursor` | string \| null | null | Pass as `?cursor=` for next page; null = last page |

### PrismaFilters (query params only)

| Param | Type | Default | Description |
|---|---|---|---|
| `year_from` | integer \| null | null | Minimum publication year |
| `year_to` | integer \| null | null | Maximum publication year |
| `sources` | string \| null | null | Comma-separated: `pubmed,openalex` |
| `open_access_only` | boolean | false | Filter to OA-only records |
