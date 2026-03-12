# LitBridge API Reference

> **Base URL:** `/api/v1`
> **Content-Type:** `application/json` (all non-streaming endpoints)
> **Streaming Content-Type:** `text/event-stream` (SSE endpoints)

---

## Table of Contents

1. [General Conventions](#general-conventions)
2. [Authentication](#authentication)
   - [How Auth Works (Email OTP Flow)](#how-auth-works-email-otp-flow)
   - [Using the Access Token](#using-the-access-token)
   - [Token Refresh Flow](#token-refresh-flow)
   - [Which Endpoints Require Auth?](#which-endpoints-require-auth)
   - [Auth Endpoints](#auth-endpoints)
3. [Enums](#enums)
4. [Search Endpoints](#search-endpoints)
5. [Structured Search Workflow (Human-in-the-Loop)](#structured-search-workflow-human-in-the-loop)
   - [How the Workflow Flag Works](#how-the-workflow-flag-works)
   - [Workflow Lifecycle Overview](#workflow-lifecycle-overview)
   - [Step 1: Start Workflow](#step-1-start-workflow)
   - [Step 2: PICO Preview (Optional)](#step-2-pico-preview-optional)
   - [Step 3: Keywords & PICO Feedback](#step-3-keywords--pico-feedback)
   - [Step 4: Manual MeSH Resolution (Optional)](#step-4-manual-mesh-resolution-optional)
   - [Step 5: MeSH Feedback](#step-5-mesh-feedback)
   - [Step 6: Query Preview](#step-6-query-preview)
   - [Step 7: Edit Query (Optional)](#step-7-edit-query-optional)
   - [Step 8: Execute Search](#step-8-execute-search)
   - [After Workflow Search Completes](#after-workflow-search-completes)
6. [Enrichment Endpoints](#enrichment-endpoints)
7. [PRISMA Endpoints](#prisma-endpoints)
8. [Chat Endpoints](#chat-endpoints)
9. [Library / Collections Endpoints](#library--collections-endpoints)
   - [Library Concepts](#library-concepts)
   - [Library CRUD](#library-crud)
   - [Library Items (Adding/Removing Searches)](#library-items-addingremoving-searches)
   - [User Searches](#user-searches)
10. [System Endpoints](#system-endpoints)
11. [SSE Event Reference](#sse-event-reference)
12. [Search Mode Behavior Matrix](#search-mode-behavior-matrix)
13. [Error Codes](#error-codes)
14. [Schemas Reference](#schemas-reference)

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

## Authentication

LitBridge uses **passwordless email OTP** authentication. There are no passwords — users verify identity by entering a 6-digit code sent to their email. This produces a JWT **access token** (short-lived) and an opaque **refresh token** (long-lived).

### How Auth Works (Email OTP Flow)

```
┌───────────────┐       ┌───────────────┐       ┌───────────────┐
│   Frontend    │       │   LitBridge   │       │  Email (SMTP) │
│               │       │   Backend     │       │               │
└───────┬───────┘       └───────┬───────┘       └───────┬───────┘
        │                       │                       │
        │  POST /auth/request-otp                       │
        │  { "email": "user@example.com" }              │
        │ ─────────────────────►│                       │
        │                       │  Sends 6-digit code   │
        │                       │ ─────────────────────►│
        │                       │                       │  ──► email arrives
        │  200 OK               │                       │
        │  { "message": "..." } │                       │
        │ ◄─────────────────────│                       │
        │                       │                       │
        │  User enters code     │                       │
        │                       │                       │
        │  POST /auth/verify-otp                        │
        │  { "email": "...", "code": "482916" }         │
        │ ─────────────────────►│                       │
        │                       │                       │
        │  200 OK               │                       │
        │  { "access_token": "eyJ...",                  │
        │    "refresh_token": "rt_abc...",               │
        │    "token_type": "bearer",                    │
        │    "expires_in": 1800 }                       │
        │ ◄─────────────────────│                       │
        │                       │                       │
        │  Store both tokens    │                       │
        │  in memory/storage    │                       │
        └───────────────────────┘                       │
```

**Key points:**
- If the email doesn't have an account yet, one is **created automatically** on first OTP verification (sign-up = sign-in).
- The `access_token` is a JWT. It expires after `expires_in` seconds (typically 30 minutes).
- The `refresh_token` is an opaque string. Use it to get a new token pair without re-verifying OTP.
- The OTP code is exactly **6 digits** and expires after a short window (typically 5 minutes).

### Using the Access Token

For endpoints that accept or require authentication, send the access token as a **Bearer token** in the `Authorization` header:

```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**Example with fetch:**

```javascript
const response = await fetch('/api/v1/library', {
  headers: {
    'Authorization': `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
  },
});
```

### Token Refresh Flow

When the access token expires (you receive a `401` response), use the refresh token to get a new pair:

```
POST /api/v1/auth/refresh
{ "refresh_token": "rt_abc..." }

→ 200 OK
{
  "access_token": "eyJ...(new)...",
  "refresh_token": "rt_xyz...(new)...",
  "token_type": "bearer",
  "expires_in": 1800
}
```

**Important:** The old refresh token is revoked after use. Always store and use the **new** refresh token returned.

### Which Endpoints Require Auth?

| Category | Auth Requirement | Behavior Without Token |
|---|---|---|
| **Auth endpoints** (`/auth/*`) | No auth needed (except `/auth/logout` and `/auth/me`) | N/A |
| **Search endpoints** (`/search/*`, `/results/*`) | **Optional** | Works fine — search is anonymous |
| **Enrichment** (`/enrichment/*`) | **Optional** | Works fine |
| **PRISMA** (`/prisma/*`) | **Optional** | Works fine |
| **Chat** (`/chat/*`) | **Optional** | Works fine |
| **Workflow** (`/workflow/*`) | **Optional** | Works fine — workflow session is anonymous |
| **Library** (`/library/*`) | **Required** | Returns `401 Unauthorized` |

When auth is **optional** and a valid token is provided, the search/workflow session is **linked to the user**. This means:
- The user can see their past searches in the Library section
- Searches appear in `GET /library/searches`
- Searches can be organized into library collections

When auth is **optional** and no token is provided, the search still works but is **anonymous** — it cannot be linked to a user later.

### Auth Endpoints

#### POST `/api/v1/auth/request-otp`

Send a one-time verification code to an email address. If the user doesn't exist yet, they will be created upon verification.

**Request Body:**

```json
{
  "email": "researcher@university.edu"
}
```

| Field | Type | Required | Validation |
|---|---|---|---|
| `email` | string | Yes | Must be a valid email address |

**Response:** `200 OK`

```json
{
  "message": "Verification code sent. Check your email."
}
```

**Note:** This endpoint always returns 200 even if the email doesn't exist (to prevent email enumeration).

---

#### POST `/api/v1/auth/verify-otp`

Verify the 6-digit code and receive authentication tokens. If this is the user's first verification, their account is created automatically.

**Request Body:**

```json
{
  "email": "researcher@university.edu",
  "code": "482916"
}
```

| Field | Type | Required | Validation |
|---|---|---|---|
| `email` | string | Yes | Must match the email used in request-otp |
| `code` | string | Yes | Exactly 6 digits (`^\d{6}$`) |

**Response:** `200 OK`

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "rt_a1b2c3d4e5f6...",
  "token_type": "bearer",
  "expires_in": 1800
}
```

| Field | Type | Description |
|---|---|---|
| `access_token` | string | JWT to use in `Authorization: Bearer` header |
| `refresh_token` | string | Opaque token for refreshing the session |
| `token_type` | string | Always `"bearer"` |
| `expires_in` | integer | Seconds until the access token expires |

**Errors:** `401` (wrong code, expired code, too many attempts)

---

#### POST `/api/v1/auth/refresh`

Exchange a valid refresh token for a new token pair. The old refresh token is revoked.

**Request Body:**

```json
{
  "refresh_token": "rt_a1b2c3d4e5f6..."
}
```

**Response:** `200 OK` — same `TokenResponse` as verify-otp.

**Errors:** `401` (token revoked, expired, or invalid)

---

#### POST `/api/v1/auth/logout`

**Requires authentication.** Revoke the provided refresh token.

**Headers:** `Authorization: Bearer <access_token>`

**Request Body:**

```json
{
  "refresh_token": "rt_a1b2c3d4e5f6..."
}
```

**Response:** `200 OK`

```json
{
  "message": "Logged out successfully."
}
```

---

#### GET `/api/v1/auth/me`

**Requires authentication.** Return the authenticated user's profile.

**Headers:** `Authorization: Bearer <access_token>`

**Response:** `200 OK`

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "researcher@university.edu",
  "display_name": "Dr. Sarah Chen",
  "is_verified": true,
  "auth_provider": "email",
  "created_at": "2026-03-01T12:00:00Z"
}
```

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

### AgeGroup

Standardised age-group categories (aligned with ClinicalTrials.gov `stdAges`).

| Value | Description |
|---|---|
| `child` | Pediatric / paediatric population (typically 0–17 years) |
| `adult` | Adult population (typically 18–64 years) |
| `older_adult` | Geriatric / elderly population (typically 65+ years) |

### StudyType

Publication or trial study-design classification.

| Value | Description |
|---|---|
| `interventional` | Randomized controlled trials, clinical trials, phase studies |
| `observational` | Cohort, case-control, cross-sectional, registry studies |
| `expanded_access` | Expanded / compassionate access programs |
| `diagnostic` | Diagnostic accuracy, screening, biomarker validation studies |
| `other` | Reviews, editorials, letters, datasets, books, or unclassified |

---

## Search Endpoints

All search endpoints accept **optional authentication**. When a valid token is provided, the search session is linked to the authenticated user (visible in Library).

### POST `/api/v1/search`

Execute a non-streaming search. Returns immediately with a `search_id`; poll status and fetch results separately.

**Request Body:** `SearchRequest`

```json
{
  "query": "metformin cardiovascular outcomes",
  "query_type": "free",
  "search_mode": "quick",
  "sources": ["pubmed", "openalex"],
  "max_results": 100,
  "workflow": false
}
```

| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `query` | string | — | Yes | Search query text |
| `query_type` | QueryType | `"free"` | No | How to interpret the query |
| `search_mode` | SearchMode | `"quick"` | No | Controls depth and AI processing |
| `sources` | SourceType[] \| null | null (all 4) | No | Which databases to search |
| `pico` | PICOInput \| null | null | Conditional | Required when query_type is `"structured"` |
| `max_results` | integer (1-5000) | 100 | No | Capped to 100 for quick/light_thinking |
| `workflow` | boolean | false | No | **Set to `true` to trigger the structured search workflow** (see [Workflow section](#structured-search-workflow-human-in-the-loop)) |

> **Important:** When `workflow: true`, the frontend should **not** use this endpoint. Instead, use the dedicated `/api/v1/workflow/start` endpoint to begin the human-in-the-loop process. The `workflow` flag on `SearchRequest` is used internally by the workflow engine when it executes the final search.

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
      "prisma_stage": "screened",
      "age_groups": ["adult"],
      "age_min": 18,
      "age_max": 65,
      "study_type": "interventional"
    }
  ],
  "next_cursor": "eyJvZmZzZXQiOiA1MH0="
}
```

**Errors:** `404`

---

## Structured Search Workflow (Human-in-the-Loop)

The workflow system provides a deep, multi-step **human-in-the-loop** process for building precise structured Boolean queries. Instead of executing a search immediately, the system:

1. **Extracts PICO** elements from the user's query using AI
2. **Expands keywords** with synonyms, abbreviations, and variants
3. **Resolves MeSH** terms from NCBI for each keyword
4. **Builds a Boolean query** combining MeSH headings + text-word clauses
5. **Adapts the query** for each database (PubMed, OpenAlex, EuropePMC, ClinicalTrials.gov)
6. Lets the user **review and edit** at every stage before executing

### How the Workflow Flag Works

The frontend can offer users two modes:

1. **Normal search** — User types a query, you call `POST /search` or `POST /search/stream`, results come back immediately.
2. **Structured search** — User toggles a "Structured Search" switch or clicks a "Build precise query" button. You call `POST /workflow/start` instead, which begins the interactive multi-step process.

Both modes ultimately produce a `search_id` that works with all existing endpoints (`/results/{search_id}`, `/enrichment/{search_id}/{record_id}`, `/prisma/{search_id}`, `/chat/stream` with `search_id`, `/library/{id}/items`).

### Workflow Lifecycle Overview

```
User input (query or PICO)
        │
        ▼
┌─────────────────────────────┐
│  POST /workflow/start       │  ← AI extracts PICO + expands keywords
│  Returns: pico, keywords    │
│  awaiting: "keywords_review"│
└──────────┬──────────────────┘
           │
     User reviews/edits PICO elements
     User accepts/rejects/edits keywords
           │
           ▼
┌──────────────────────────────────────┐
│  POST /workflow/{id}/keywords/feedback│  ← Applies edits, resolves MeSH
│  Returns: mesh descriptors           │
│  awaiting: "mesh_review"             │
└──────────┬───────────────────────────┘
           │
     User reviews MeSH terms
     User selects qualifiers, toggles explode
     User optionally resolves custom MeSH terms
           │
           ▼
┌──────────────────────────────────┐
│  POST /workflow/{id}/mesh/feedback│  ← Builds Boolean query
│  Returns: structured query       │
│  awaiting: "query_review"        │
└──────────┬───────────────────────┘
           │
     User previews Boolean + per-database queries
     User optionally edits the Boolean directly
           │
           ▼
┌────────────────────────────────┐
│  GET /workflow/{id}/query      │  ← Preview final query
│  PUT /workflow/{id}/query      │  ← Optional: direct edit
└──────────┬─────────────────────┘
           │
     User approves and clicks "Search"
           │
           ▼
┌───────────────────────────────────┐
│  POST /workflow/{id}/search       │  ← Executes through existing pipeline
│  Returns: search_id, counts       │
└──────────┬────────────────────────┘
           │
           ▼
   Use search_id with /results, /enrichment, /prisma, /chat, /library
```

### Step 1: Start Workflow

#### POST `/api/v1/workflow/start`

Start a new workflow session. The AI extracts PICO elements from the user's query and expands keywords with synonyms.

**Request Body:** `WorkflowStartRequest`

You can start from a free-text query **or** from structured PICO input:

**Option A — Free-text query:**

```json
{
  "query": "Does metformin reduce cardiovascular events in type 2 diabetes patients compared to sulfonylureas?",
  "query_type": "free"
}
```

**Option B — PICO input:**

```json
{
  "query": "",
  "query_type": "structured",
  "pico": {
    "P": "type 2 diabetes patients",
    "I": "metformin",
    "C": "sulfonylureas",
    "O": "cardiovascular events"
  }
}
```

| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `query` | string | `""` | No | Natural language research question |
| `query_type` | string | `"free"` | No | `"free"` or `"structured"` |
| `pico` | object \| null | null | No | PICO input with keys `P`, `I`, `C`, `O` |

**Response:** `201 Created`

```json
{
  "workflow_session_id": "wf-550e8400-e29b-41d4-a716-446655440000",
  "awaiting": "keywords_review",
  "pico": {
    "P": [
      {
        "text": "type 2 diabetes",
        "confidence": 0.95,
        "provenance": "llm",
        "facet": null
      },
      {
        "text": "adults",
        "confidence": 0.7,
        "provenance": "llm",
        "facet": "age group"
      }
    ],
    "I": [
      {
        "text": "metformin",
        "confidence": 0.99,
        "provenance": "llm",
        "facet": null
      }
    ],
    "C": [
      {
        "text": "sulfonylureas",
        "confidence": 0.95,
        "provenance": "llm",
        "facet": null
      }
    ],
    "O": [
      {
        "text": "cardiovascular events",
        "confidence": 0.9,
        "provenance": "llm",
        "facet": null
      }
    ]
  },
  "keywords": {
    "P": {
      "type 2 diabetes": [
        {
          "term": "T2DM",
          "concept": "P",
          "base_term": "type 2 diabetes",
          "status": "suggested",
          "variant": "abbreviation",
          "confidence": 0.95
        },
        {
          "term": "type II diabetes mellitus",
          "concept": "P",
          "base_term": "type 2 diabetes",
          "status": "suggested",
          "variant": "spelling",
          "confidence": 0.9
        },
        {
          "term": "non-insulin dependent diabetes",
          "concept": "P",
          "base_term": "type 2 diabetes",
          "status": "suggested",
          "variant": "synonym",
          "confidence": 0.85
        }
      ]
    },
    "I": { ... },
    "C": { ... },
    "O": { ... }
  },
  "errors": []
}
```

**Understanding the response:**

- `workflow_session_id` — Use this in all subsequent workflow calls as `{session_id}`
- `awaiting` — Tells the frontend what the user should review next. Values: `"keywords_review"`, `"mesh_review"`, `"query_review"`, or `null`
- `pico` — Extracted PICO elements organized by concept (`P`, `I`, `C`, `O`). Each element has `text`, `confidence` (0-1), `provenance` (`"llm"` or `"user"`), and optional `facet`
- `keywords` — Synonym suggestions organized as `{ concept: { base_term: [suggestions] } }`. Each suggestion has a `variant` type: `"synonym"`, `"abbreviation"`, `"spelling"`, `"lay_term"`, or `"phrase_variant"`

**What the frontend should display:** Show the PICO elements in an editable list (let users add/remove/edit terms). Show keywords grouped under their base terms with accept/reject toggles.

---

### Step 2: PICO Preview (Optional)

#### POST `/api/v1/workflow/pico-preview`

Quick stateless PICO extraction without creating a workflow session. Useful for showing a preview before the user commits to the workflow.

**Request Body:**

```json
{
  "question": "Does metformin reduce cardiovascular events in type 2 diabetes?"
}
```

| Field | Type | Required | Validation |
|---|---|---|---|
| `question` | string | Yes | Min 5 characters |

**Response:** `200 OK`

```json
{
  "pico": {
    "P": [{"text": "type 2 diabetes", "confidence": 0.95, "provenance": "llm", "facet": null}],
    "I": [{"text": "metformin", "confidence": 0.99, "provenance": "llm", "facet": null}],
    "C": [],
    "O": [{"text": "cardiovascular events", "confidence": 0.9, "provenance": "llm", "facet": null}]
  }
}
```

---

### Step 3: Keywords & PICO Feedback

#### POST `/api/v1/workflow/{session_id}/keywords/feedback`

Submit the user's PICO edits and keyword decisions. The system then resolves MeSH terms for all accepted keywords.

**Path Parameters:**

| Param | Type | Description |
|---|---|---|
| `session_id` | string | `workflow_session_id` from Step 1 |

**Request Body:** `KeywordFeedbackRequest`

```json
{
  "pico_edits": [
    {
      "concept": "P",
      "action": "add",
      "text": "elderly patients"
    },
    {
      "concept": "P",
      "action": "remove",
      "index": 1
    },
    {
      "concept": "O",
      "action": "edit",
      "index": 0,
      "text": "major adverse cardiovascular events"
    }
  ],
  "keyword_decisions": [
    {
      "concept": "P",
      "base_term": "type 2 diabetes",
      "decisions": [
        { "action": "accept", "term": "T2DM" },
        { "action": "reject", "term": "non-insulin dependent diabetes" },
        { "action": "edit", "term": "type II diabetes mellitus", "new_term": "type 2 diabetes mellitus" },
        { "action": "add", "new_term": "diabetes mellitus type 2" }
      ]
    }
  ]
}
```

**PicoEdit actions:**

| Action | Fields | Description |
|---|---|---|
| `"add"` | `concept`, `text` | Add a new PICO element to a concept |
| `"remove"` | `concept`, `index` | Remove element at the given index |
| `"edit"` | `concept`, `index`, `text` | Replace element text at the given index |

**KeywordDecision actions:**

| Action | Fields | Description |
|---|---|---|
| `"accept"` | `term` | Accept a suggested synonym (include in query) |
| `"reject"` | `term` | Reject a suggested synonym (exclude from query) |
| `"edit"` | `term`, `new_term` | Replace a suggestion with edited text |
| `"add"` | `new_term` | Add a brand-new synonym not in the original suggestions |

**Response:** `200 OK`

```json
{
  "workflow_session_id": "wf-550e...",
  "awaiting": "mesh_review",
  "mesh": {
    "P": {
      "type 2 diabetes": [
        {
          "mesh_term": "type 2 diabetes",
          "concept": "P",
          "base_term": "type 2 diabetes",
          "status": "suggested",
          "descriptor_uid": "D003924",
          "descriptor_name": "Diabetes Mellitus, Type 2",
          "tree_numbers": ["C18.452.394.750.149", "C19.246.300"],
          "min_depth": 4,
          "entry_terms": ["Diabetes Mellitus, Noninsulin-Dependent", "Diabetes Mellitus, Adult-Onset", "NIDDM"],
          "entry_terms_selected": [],
          "qualifiers": {
            "allowed": ["complications", "drug therapy", "epidemiology", "mortality", "prevention & control", "therapy"],
            "selected": []
          },
          "explode": true,
          "scope_note": "A subclass of DIABETES MELLITUS that is not INSULIN-responsive or dependent..."
        }
      ]
    },
    "I": { ... },
    "C": { ... },
    "O": { ... }
  },
  "errors": []
}
```

**Understanding MeSH data:**

- `descriptor_uid` — NCBI MeSH unique ID (e.g. `"D003924"`)
- `descriptor_name` — Official MeSH heading name
- `tree_numbers` — Position(s) in the MeSH hierarchy tree
- `entry_terms` — Alternative names for this descriptor. The user can select which to include
- `qualifiers.allowed` — Valid MeSH subheadings (e.g. `/drug therapy`, `/complications`)
- `qualifiers.selected` — Which subheadings the user has chosen
- `explode` — If `true`, the search includes all narrower MeSH terms below this descriptor in the tree
- `scope_note` — Official definition of this MeSH term (display as a tooltip or info box)

**What the frontend should display:** For each MeSH descriptor, show the official name, scope note, and let users:
- Accept or reject each MeSH term
- Select entry terms to include
- Select qualifiers (subheadings)
- Toggle the explode flag

---

### Step 4: Manual MeSH Resolution (Optional)

#### POST `/api/v1/workflow/{session_id}/mesh/resolve`

Let the user type a custom MeSH term and resolve it against NCBI's database. Use this when the user wants to add a MeSH term that wasn't automatically found.

**Request Body:**

```json
{
  "concept": "P",
  "base_term": "type 2 diabetes",
  "mesh_term": "Insulin Resistance"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `concept` | string | Yes | PICO concept: `"P"`, `"I"`, `"C"`, or `"O"` |
| `base_term` | string | Yes | The keyword this MeSH term belongs to |
| `mesh_term` | string | Yes | The MeSH term to look up |

**Response:** `200 OK`

```json
{
  "found": true,
  "suggestion": {
    "mesh_term": "Insulin Resistance",
    "concept": "P",
    "base_term": "type 2 diabetes",
    "status": "suggested",
    "descriptor_uid": "D007333",
    "descriptor_name": "Insulin Resistance",
    "tree_numbers": ["E07.500.500", "G03.295.500"],
    "entry_terms": ["Resistance, Insulin"],
    "entry_terms_selected": [],
    "qualifiers": { "allowed": [...], "selected": [] },
    "explode": true,
    "scope_note": "Diminished effectiveness of INSULIN in lowering blood sugar levels..."
  },
  "message": null
}
```

If the term is not found:

```json
{
  "found": false,
  "suggestion": null,
  "message": "No canonical MeSH descriptor found."
}
```

---

### Step 5: MeSH Feedback

#### POST `/api/v1/workflow/{session_id}/mesh/feedback`

Submit the user's MeSH decisions. The system builds the final Boolean query.

**Request Body:** `MeshFeedbackRequest`

```json
{
  "items": [
    {
      "concept": "P",
      "base_term": "type 2 diabetes",
      "mesh_term": "Diabetes Mellitus, Type 2",
      "action": "accept",
      "entry_terms_selected": ["NIDDM", "Diabetes Mellitus, Adult-Onset"],
      "qualifiers_selected": ["drug therapy", "complications"],
      "explode": true
    },
    {
      "concept": "I",
      "base_term": "metformin",
      "mesh_term": "Metformin",
      "action": "accept",
      "entry_terms_selected": [],
      "qualifiers_selected": ["therapeutic use", "pharmacology"],
      "explode": false
    },
    {
      "concept": "C",
      "base_term": "sulfonylureas",
      "mesh_term": "Sulfonylurea Compounds",
      "action": "reject",
      "entry_terms_selected": [],
      "qualifiers_selected": [],
      "explode": true
    }
  ]
}
```

**MeshFeedbackItem fields:**

| Field | Type | Default | Description |
|---|---|---|---|
| `concept` | string | — | PICO concept |
| `base_term` | string | — | Original keyword |
| `mesh_term` | string | — | MeSH descriptor name |
| `action` | string | `"none"` | `"accept"`, `"reject"`, or `"none"` |
| `entry_terms_selected` | string[] | `[]` | Which entry terms to include in the query |
| `qualifiers_selected` | string[] | `[]` | MeSH subheadings to apply |
| `explode` | boolean | `true` | Include narrower MeSH terms |

**Response:** `200 OK`

```json
{
  "workflow_session_id": "wf-550e...",
  "awaiting": "query_review",
  "query": {
    "pubmed_query": "((\"Diabetes Mellitus, Type 2\"[MeSH Terms] OR \"type 2 diabetes\"[tiab] OR \"T2DM\"[tiab]) AND (\"Metformin\"[MeSH Terms] OR \"metformin\"[tiab]) AND (\"cardiovascular events\"[tiab] OR \"MACE\"[tiab]))",
    "adapted_queries": {
      "pubmed": "((\"Diabetes Mellitus, Type 2\"[MeSH Terms] OR ...) AND ...)",
      "openalex": "(type 2 diabetes OR T2DM) AND metformin AND cardiovascular events",
      "europepmc": "((\"type 2 diabetes\" OR \"T2DM\") AND (\"metformin\") AND ...)",
      "clinicaltrials": "(type 2 diabetes OR T2DM) AND (metformin) AND (cardiovascular events)"
    },
    "concept_blocks": [...],
    "warnings": []
  },
  "errors": []
}
```

---

### Step 6: Query Preview

#### GET `/api/v1/workflow/{session_id}/query`

Preview the built Boolean query and how it adapts for each database.

**Response:** `200 OK`

```json
{
  "pubmed_query": "((\"Diabetes Mellitus, Type 2\"[MeSH Terms] OR \"type 2 diabetes\"[tiab]) AND (\"Metformin\"[MeSH Terms] OR \"metformin\"[tiab]) AND (\"cardiovascular events\"[tiab]))",
  "adapted_queries": {
    "pubmed": "((\"Diabetes Mellitus, Type 2\"[MeSH Terms] OR ...) AND ...)",
    "openalex": "(type 2 diabetes) AND metformin AND cardiovascular events",
    "europepmc": "((\"type 2 diabetes\") AND (\"metformin\") AND ...)",
    "clinicaltrials": "(type 2 diabetes) AND (metformin) AND (cardiovascular events)"
  },
  "warnings": []
}
```

**What the frontend should display:** Show the PubMed Boolean query in an editable text area. Show the adapted queries for other databases as read-only previews (they auto-derive from the PubMed query). Display any warnings.

---

### Step 7: Edit Query (Optional)

#### PUT `/api/v1/workflow/{session_id}/query`

Let the user directly edit the PubMed Boolean query. The system re-adapts it for all other databases.

**Request Body:**

```json
{
  "pubmed_query": "((\"Diabetes Mellitus, Type 2\"[MeSH Terms]) AND (\"Metformin\"[MeSH Terms]) AND (\"Cardiovascular Diseases\"[MeSH Terms]))"
}
```

**Response:** Same as GET — returns `QueryPreviewResponse` with updated `adapted_queries`.

---

### Step 8: Execute Search

#### POST `/api/v1/workflow/{session_id}/search`

Execute the approved structured query through the existing federated search pipeline. This produces a `search_id` that works with all standard endpoints.

**Request Body:** `WorkflowSearchRequest`

```json
{
  "search_mode": "deep_analyze",
  "sources": ["pubmed", "openalex", "europepmc"],
  "max_results": 500
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `search_mode` | string | `"quick"` | Same SearchMode enum values |
| `sources` | string[] \| null | null (all) | Database sources to search |
| `max_results` | integer (1-5000) | 100 | Maximum results per source |

**Response:** `200 OK`

```json
{
  "search_id": "a3f8c9d1-2e45-4b7a-9c3d-1234567890ab",
  "workflow_session_id": "wf-550e8400-e29b-41d4-a716-446655440000",
  "total_identified": 342,
  "total_after_dedup": 289,
  "sources_completed": ["pubmed", "openalex", "europepmc"],
  "sources_failed": []
}
```

### After Workflow Search Completes

The returned `search_id` is a standard search session. Use it with **all existing endpoints**:

| What to do | Endpoint |
|---|---|
| Get paginated results | `GET /results/{search_id}` |
| Check search status | `GET /search/{search_id}/status` |
| Enrich a single record | `GET /enrichment/{search_id}/{record_id}` |
| Get PRISMA counts | `GET /prisma/{search_id}` |
| Chat about results | `POST /chat/stream` with `search_id` |
| Add to a library | `POST /library/{library_id}/items` with the `search_id` |

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

Compute PRISMA systematic review flow diagram counts for a completed search. All filters are optional and additive (AND logic) — each active filter narrows the set further.

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
| `age_group` | string \| null | null | Comma-separated age groups, e.g. `adult,older_adult`. Values: `child`, `adult`, `older_adult` (see [AgeGroup](#agegroup)) |
| `age_min` | integer \| null | null | Minimum age in years (inclusive). Records whose age range overlaps are included |
| `age_max` | integer \| null | null | Maximum age in years (inclusive). Records whose age range overlaps are included |
| `study_type` | string \| null | null | Comma-separated study types, e.g. `interventional,observational`. Values: `interventional`, `observational`, `expanded_access`, `diagnostic`, `other` (see [StudyType](#studytype)) |

**Filter behavior:**

- **`age_group`** — A record passes if **any** of its `age_groups` overlaps with the requested set. Records with no age-group data are excluded when this filter is active.
- **`age_min` / `age_max`** — Uses **inclusive range overlap**: a record passes if its age range intersects `[age_min, age_max]`. If a record has no age bounds, it is kept (not excluded) so that records without age metadata are not silently dropped.
- **`study_type`** — A record passes if its `study_type` is in the requested set. Records with no study type are excluded when this filter is active.
- All filters combine with AND logic — a record must satisfy every active filter to be counted.

**Example — no filters (baseline):**

```
GET /api/v1/prisma/abc-123
```

**Example — adults-only interventional studies from 2020+:**

```
GET /api/v1/prisma/abc-123?year_from=2020&age_group=adult&study_type=interventional
```

**Example — pediatric and adult studies with age range 5–30:**

```
GET /api/v1/prisma/abc-123?age_group=child,adult&age_min=5&age_max=30
```

**Example — multiple study types:**

```
GET /api/v1/prisma/abc-123?study_type=interventional,observational,diagnostic
```

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

**Errors:**

| Status | Cause |
|---|---|
| `404` | Search session not found |
| `422` | Invalid value in `sources`, `age_group`, or `study_type` parameter |

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
  }
]
```

---

## Library / Collections Endpoints

The Library system lets authenticated users organize their search sessions into hierarchical collections (like Zotero collections or ChatGPT projects).

**All library endpoints require authentication** — send `Authorization: Bearer <access_token>`.

### Library Concepts

- **Library** — A named folder/collection. Can have an optional parent (for nesting), icon, color, and position for ordering.
- **Library Item** — A link between a library and a search session. A search can be in **multiple** libraries (many-to-many, like playlists).
- **Hierarchy** — Libraries support 1-2 levels of nesting (`parent_id`). A root library has `parent_id: null`.
- **Unfiled searches** — Searches that belong to the user but are not in any library.
- **All Searches** — All searches linked to the user (via `GET /library/searches`).

### Library CRUD

#### GET `/api/v1/library`

**Requires authentication.** List the user's libraries as a tree structure with item counts.

**Response:** `200 OK`

```json
{
  "libraries": [
    {
      "id": "lib-001",
      "name": "Cardiovascular Research",
      "description": "All my CV-related searches",
      "parent_id": null,
      "icon": "heart",
      "color": "#e74c3c",
      "position": 0,
      "item_count": 5,
      "created_at": "2026-03-01T10:00:00Z",
      "updated_at": "2026-03-05T14:30:00Z",
      "items": [
        {
          "id": "item-001",
          "library_id": "lib-001",
          "search_session_id": "abc-123",
          "notes": "Key metformin search",
          "created_at": "2026-03-02T09:00:00Z"
        }
      ],
      "children": [
        {
          "id": "lib-002",
          "name": "Metformin Studies",
          "description": null,
          "parent_id": "lib-001",
          "icon": null,
          "color": null,
          "position": 0,
          "item_count": 3,
          "created_at": "2026-03-02T10:00:00Z",
          "updated_at": "2026-03-04T16:00:00Z"
        }
      ]
    },
    {
      "id": "lib-003",
      "name": "COVID-19 Literature",
      "description": null,
      "parent_id": null,
      "icon": "virus",
      "color": "#3498db",
      "position": 1,
      "item_count": 12,
      "created_at": "2026-03-03T08:00:00Z",
      "updated_at": "2026-03-10T11:00:00Z",
      "items": [...],
      "children": []
    }
  ]
}
```

---

#### POST `/api/v1/library`

**Requires authentication.** Create a new library.

**Request Body:**

```json
{
  "name": "Cardiovascular Research",
  "description": "All my CV-related searches",
  "parent_id": null,
  "icon": "heart",
  "color": "#e74c3c"
}
```

| Field | Type | Required | Validation | Description |
|---|---|---|---|---|
| `name` | string | Yes | 1-255 chars | Library name |
| `description` | string \| null | No | — | Optional description |
| `parent_id` | UUID \| null | No | Must be an existing library you own | Parent library for nesting (null = root) |
| `icon` | string \| null | No | Max 64 chars | Icon name or emoji |
| `color` | string \| null | No | Max 32 chars | Hex color code |

**Response:** `201 Created`

```json
{
  "id": "lib-001",
  "name": "Cardiovascular Research",
  "description": "All my CV-related searches",
  "parent_id": null,
  "icon": "heart",
  "color": "#e74c3c",
  "position": 0,
  "item_count": 0,
  "created_at": "2026-03-10T12:00:00Z",
  "updated_at": "2026-03-10T12:00:00Z"
}
```

**Errors:** `422` (nesting depth exceeded — max 2 levels), `404` (parent not found)

---

#### GET `/api/v1/library/{library_id}`

**Requires authentication.** Get a single library with its items and children.

**Response:** `200 OK` — `LibraryDetailResponse` (same structure as items in tree response, with `items` and `children` arrays)

**Errors:** `404` (not found), `403` (not your library)

---

#### PATCH `/api/v1/library/{library_id}`

**Requires authentication.** Update a library (rename, move, recolor, reorder). Only include the fields you want to change.

**Request Body:**

```json
{
  "name": "CV Research (Updated)",
  "color": "#2ecc71",
  "position": 2
}
```

| Field | Type | Description |
|---|---|---|
| `name` | string \| null | New name (1-255 chars) |
| `description` | string \| null | New description |
| `parent_id` | UUID \| null | Move to a new parent (or set null to move to root) |
| `icon` | string \| null | New icon |
| `color` | string \| null | New color |
| `position` | integer \| null | New ordering position |

**Response:** `200 OK` — `LibraryResponse`

**Errors:** `404`, `403`, `422` (nesting depth exceeded or circular reference)

---

#### DELETE `/api/v1/library/{library_id}`

**Requires authentication.** Delete a library. The search sessions inside it are **not deleted** — they are just unlinked and become "unfiled".

**Response:** `204 No Content`

**Errors:** `404`, `403`

---

### Library Items (Adding/Removing Searches)

#### POST `/api/v1/library/{library_id}/items`

**Requires authentication.** Add one or more search sessions to a library. Duplicate additions are silently skipped (idempotent).

**Request Body:**

```json
{
  "search_session_ids": [
    "a3f8c9d1-2e45-4b7a-9c3d-1234567890ab",
    "b4a9d2e3-3f56-5c8b-ad4e-2345678901bc"
  ],
  "notes": "Important metformin studies"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `search_session_ids` | UUID[] | Yes | At least 1 search session ID |
| `notes` | string \| null | No | User annotation for these items |

**Response:** `201 Created`

```json
[
  {
    "id": "item-001",
    "library_id": "lib-001",
    "search_session_id": "a3f8c9d1-2e45-4b7a-9c3d-1234567890ab",
    "notes": "Important metformin studies",
    "created_at": "2026-03-10T12:05:00Z"
  }
]
```

**Note:** Only newly added items are returned. If a search was already in the library, it won't appear in the response.

**Errors:** `404` (library not found), `403` (not your library)

---

#### DELETE `/api/v1/library/{library_id}/items/{search_id}`

**Requires authentication.** Remove a search session from a library. The search session itself is not deleted.

**Response:** `204 No Content`

**Errors:** `404` (library not found or item not in library), `403`

---

### User Searches

#### GET `/api/v1/library/searches`

**Requires authentication.** List all search sessions belonging to the authenticated user, newest first.

**Query Parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `limit` | integer | 50 | Max results (1-200) |
| `offset` | integer | 0 | Skip first N results |

**Response:** `200 OK`

```json
{
  "searches": [
    {
      "id": "a3f8c9d1-2e45-4b7a-9c3d-1234567890ab",
      "query": "metformin cardiovascular outcomes",
      "query_type": "free",
      "search_mode": "quick",
      "sources": ["pubmed", "openalex"],
      "status": "completed",
      "total_after_dedup": 150,
      "created_at": "2026-03-10T10:00:00Z"
    },
    {
      "id": "b4a9d2e3-3f56-5c8b-ad4e-2345678901bc",
      "query": "((\"Diabetes Mellitus, Type 2\"[MeSH Terms]) AND ...)",
      "query_type": "boolean",
      "search_mode": "deep_analyze",
      "sources": ["pubmed", "openalex", "europepmc", "clinicaltrials"],
      "status": "completed",
      "total_after_dedup": 289,
      "created_at": "2026-03-09T15:30:00Z"
    }
  ],
  "total": 2
}
```

---

#### GET `/api/v1/library/searches/unfiled`

**Requires authentication.** List search sessions that belong to the user but are **not in any library**.

**Response:** `200 OK` — Same `UserSearchesResponse` format as above.

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
| `401` | Unauthorized | Missing or invalid access token on a required-auth endpoint |
| `403` | Forbidden | Authenticated but accessing another user's resource (library) |
| `404` | Not Found | Search session, record, conversation, library, or workflow session doesn't exist |
| `422` | Validation Error | Bad request body, invalid enum value, constraint violation, nesting too deep |
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
| `workflow` | boolean | false | No | Internal flag — set by workflow engine |

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
| `age_groups` | [AgeGroup](#agegroup)[] | `[]` | Applicable age categories. Extracted from ClinicalTrials.gov structured data or inferred from title/abstract heuristics |
| `age_min` | integer \| null | null | Minimum eligible age in years. From ClinicalTrials.gov `eligibilityModule.minimumAge` when available |
| `age_max` | integer \| null | null | Maximum eligible age in years. From ClinicalTrials.gov `eligibilityModule.maximumAge` when available |
| `study_type` | [StudyType](#studytype) \| null | null | Study design classification. Extracted from ClinicalTrials.gov `studyType`, OpenAlex `type`, or inferred via title/abstract heuristics |

### TokenResponse

| Field | Type | Description |
|---|---|---|
| `access_token` | string | JWT for authenticated requests |
| `refresh_token` | string | Opaque token for refreshing the session |
| `token_type` | string | Always `"bearer"` |
| `expires_in` | integer | Seconds until access token expires |

### UserResponse

| Field | Type | Description |
|---|---|---|
| `id` | UUID | User ID |
| `email` | string | Email address |
| `display_name` | string \| null | Display name |
| `is_verified` | boolean | Whether email is verified |
| `auth_provider` | string | Auth method (currently `"email"`) |
| `created_at` | datetime | Account creation timestamp |

### WorkflowStartRequest

| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `query` | string | `""` | No | Free-text research question |
| `query_type` | string | `"free"` | No | `"free"` or `"structured"` |
| `pico` | object \| null | null | No | `{"P": "...", "I": "...", "C": "...", "O": "..."}` |

### WorkflowStartResponse

| Field | Type | Description |
|---|---|---|
| `workflow_session_id` | string | Session ID for subsequent workflow calls |
| `awaiting` | string \| null | Current stage: `"keywords_review"`, `"mesh_review"`, `"query_review"`, or null |
| `pico` | object | `{ "P": [PicoElement], "I": [...], "C": [...], "O": [...] }` |
| `keywords` | object | `{ concept: { base_term: [Suggestion] } }` |
| `errors` | object[] | Any errors that occurred during processing |

### PicoElement

| Field | Type | Description |
|---|---|---|
| `text` | string | The PICO term text |
| `confidence` | float \| null | AI confidence score (0-1) |
| `provenance` | string | `"llm"` (AI-extracted) or `"user"` (manually added) |
| `facet` | string \| null | Sub-classification (e.g. `"age group"`, `"condition"`) |

### Suggestion (Keyword)

| Field | Type | Description |
|---|---|---|
| `term` | string | The synonym text |
| `concept` | string | PICO concept (`P`, `I`, `C`, `O`) |
| `base_term` | string | The original keyword this synonym belongs to |
| `status` | string | `"suggested"`, `"accepted"`, or `"rejected"` |
| `variant` | string \| null | `"synonym"`, `"abbreviation"`, `"spelling"`, `"lay_term"`, or `"phrase_variant"` |
| `confidence` | float \| null | AI confidence score (0-1) |

### MeshDescriptor

| Field | Type | Description |
|---|---|---|
| `mesh_term` | string | The queried MeSH term |
| `concept` | string | PICO concept |
| `base_term` | string | Original keyword |
| `status` | string | `"suggested"`, `"accepted"`, or `"rejected"` |
| `descriptor_uid` | string \| null | NCBI MeSH unique ID (e.g. `"D003924"`) |
| `descriptor_name` | string \| null | Official MeSH heading |
| `tree_numbers` | string[] | Positions in MeSH hierarchy |
| `min_depth` | integer \| null | Minimum depth in tree |
| `entry_terms` | string[] | Alternative names for this descriptor |
| `entry_terms_selected` | string[] | User-selected entry terms to include |
| `qualifiers` | MeshQualifiers | Subheading configuration |
| `explode` | boolean | Include narrower terms in search |
| `scope_note` | string \| null | Official definition of the term |

### MeshQualifiers

| Field | Type | Description |
|---|---|---|
| `allowed` | string[] | Valid subheadings for this descriptor |
| `selected` | string[] | User-chosen subheadings |

### WorkflowSearchRequest

| Field | Type | Default | Description |
|---|---|---|---|
| `search_mode` | string | `"quick"` | SearchMode value |
| `sources` | string[] \| null | null (all) | Source databases to search |
| `max_results` | integer (1-5000) | 100 | Max results per source |

### WorkflowSearchResponse

| Field | Type | Description |
|---|---|---|
| `search_id` | string | Standard search session UUID — use with all `/results`, `/enrichment`, `/chat` endpoints |
| `workflow_session_id` | string | The workflow session that produced this search |
| `total_identified` | integer | Total raw records found across sources |
| `total_after_dedup` | integer | Records after deduplication |
| `sources_completed` | string[] | Sources that returned results |
| `sources_failed` | string[] | Sources that failed |

### QueryPreviewResponse

| Field | Type | Description |
|---|---|---|
| `pubmed_query` | string | The PubMed Boolean query string |
| `adapted_queries` | object | `{ "pubmed": "...", "openalex": "...", "europepmc": "...", "clinicaltrials": "..." }` |
| `warnings` | string[] | Any warnings about the query |

### LibraryResponse

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Library ID |
| `name` | string | Library name |
| `description` | string \| null | Optional description |
| `parent_id` | UUID \| null | Parent library ID (null = root) |
| `icon` | string \| null | Icon name or emoji |
| `color` | string \| null | Hex color code |
| `position` | integer | Sort order within parent |
| `item_count` | integer | Number of search sessions in this library |
| `created_at` | datetime | ISO 8601 |
| `updated_at` | datetime | ISO 8601 |

### LibraryDetailResponse

Extends `LibraryResponse` with:

| Field | Type | Description |
|---|---|---|
| `items` | LibraryItemResponse[] | Search sessions in this library |
| `children` | LibraryResponse[] | Child libraries (nested folders) |

### LibraryItemResponse

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Item ID (the link) |
| `library_id` | UUID | Which library |
| `search_session_id` | UUID | Which search session |
| `notes` | string \| null | User annotation |
| `created_at` | datetime | When added |

### SearchSessionBrief

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Search session ID |
| `query` | string | Original query text |
| `query_type` | string | QueryType value |
| `search_mode` | string | SearchMode value |
| `sources` | string[] | Databases searched |
| `status` | string | `"processing"`, `"completed"`, `"failed"` |
| `total_after_dedup` | integer | Result count after dedup |
| `created_at` | datetime | When the search was run |

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

---

## Complete Endpoint Summary

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/auth/request-otp` | None | Send OTP to email |
| POST | `/api/v1/auth/verify-otp` | None | Verify OTP, get tokens |
| POST | `/api/v1/auth/refresh` | None | Refresh token pair |
| POST | `/api/v1/auth/logout` | **Required** | Revoke refresh token |
| GET | `/api/v1/auth/me` | **Required** | Get current user profile |
| POST | `/api/v1/search` | Optional | Execute search |
| POST | `/api/v1/search/preview` | Optional | Preview query translations |
| POST | `/api/v1/search/stream` | Optional | Streaming search with SSE |
| GET | `/api/v1/search/{id}/status` | Optional | Check search status |
| GET | `/api/v1/results/{id}` | Optional | Get paginated results |
| GET | `/api/v1/enrichment/{search_id}/{record_id}` | Optional | Get record enrichment |
| GET | `/api/v1/prisma/{search_id}` | Optional | Get PRISMA counts |
| POST | `/api/v1/chat/stream` | Optional | Chat about results (SSE) |
| GET | `/api/v1/chat/{conversation_id}/history` | Optional | Get conversation history |
| GET | `/api/v1/chat/conversations/{search_id}` | Optional | List conversations |
| POST | `/api/v1/workflow/start` | Optional | Start structured workflow |
| POST | `/api/v1/workflow/pico-preview` | Optional | Quick PICO preview |
| POST | `/api/v1/workflow/{id}/keywords/feedback` | Optional | Submit PICO + keyword feedback |
| POST | `/api/v1/workflow/{id}/mesh/resolve` | Optional | Resolve custom MeSH term |
| POST | `/api/v1/workflow/{id}/mesh/feedback` | Optional | Submit MeSH feedback |
| GET | `/api/v1/workflow/{id}/query` | Optional | Preview Boolean query |
| PUT | `/api/v1/workflow/{id}/query` | Optional | Edit Boolean query |
| POST | `/api/v1/workflow/{id}/search` | Optional | Execute workflow search |
| GET | `/api/v1/library` | **Required** | List user's libraries (tree) |
| POST | `/api/v1/library` | **Required** | Create library |
| GET | `/api/v1/library/searches` | **Required** | List user's searches |
| GET | `/api/v1/library/searches/unfiled` | **Required** | List unfiled searches |
| GET | `/api/v1/library/{id}` | **Required** | Get library detail |
| PATCH | `/api/v1/library/{id}` | **Required** | Update library |
| DELETE | `/api/v1/library/{id}` | **Required** | Delete library |
| POST | `/api/v1/library/{id}/items` | **Required** | Add searches to library |
| DELETE | `/api/v1/library/{id}/items/{search_id}` | **Required** | Remove search from library |
| GET | `/` | None | API root |
| GET | `/health` | None | Health check |
