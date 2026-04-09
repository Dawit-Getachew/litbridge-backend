# Search API Update (Frontend)

## New Endpoint

`GET /api/v1/search/history`

- Auth: **required** (Bearer token)
- Purpose: return the authenticated user's search history for infinite scroll
- Sort order: `updated_at DESC`, then `created_at DESC`, then `id DESC`
  - This means older searches moved/updated later appear at the top

## Query Params

- `limit` (optional, default `20`, max `100`)
- `cursor` (optional, opaque string from previous response)

## Response Shape

```json
{
  "searches": [
    {
      "id": "uuid",
      "query": "string",
      "query_type": "free|structured|boolean|abstract",
      "search_mode": "quick|light_thinking|deep_research|deep_analyze|deep_thinking",
      "sources": ["pubmed", "openalex"],
      "status": "processing|completed|failed",
      "total_after_dedup": 42,
      "created_at": "2026-03-20T10:00:00+00:00",
      "updated_at": "2026-03-20T11:00:00+00:00"
    }
  ],
  "total": 123,
  "next_cursor": "opaque-or-null"
}
```

## Infinite Scroll Contract

- First request: call without `cursor`
- Next page: call again with returned `next_cursor`
- Stop when `next_cursor` is `null`

## Compatibility Notes

- Existing `GET /api/v1/library/searches` remains unchanged.
- This is a **new** endpoint (non-breaking for existing frontend flows).
- MeSH quality improvements were internal behavior changes only (no request/response contract changes).
