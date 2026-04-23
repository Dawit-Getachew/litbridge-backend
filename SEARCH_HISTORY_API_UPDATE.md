# Search API Update (Frontend)

## Auth Changes

Authentication now uses a **Bearer token** scheme (not OAuth2 password flow).

### How to authenticate

All protected endpoints expect this header:

```
Authorization: Bearer <access_token>
```

### Swagger UI (`/docs`)

Click **Authorize**, paste your access token in the **Value** field, and click **Authorize**. It no longer asks for username/password/client credentials.

### Important: search history requires the same user

History only returns searches that were created **by the same authenticated user**. If a search was created without an `Authorization` header (or with a different token), it will **not** appear in that user's history.

Make sure `POST /api/v1/search` and `POST /api/v1/search/stream` include the same `Authorization: Bearer <token>` header that you later use on `GET /api/v1/search/history`. Otherwise the search is saved as anonymous and history returns empty.

---

## Endpoint: Search History

`GET /api/v1/search/history`

- Auth: **required** (Bearer token)
- Purpose: return the authenticated user's search history for infinite scroll
- Sort order: `updated_at DESC`, then `created_at DESC`, then `id DESC`
  - This means older searches moved/updated later appear at the top

### Query Params

| Param    | Type   | Default | Description                        |
|----------|--------|---------|------------------------------------|
| `limit`  | int    | `20`    | Page size (min 1, max 100)         |
| `cursor` | string | `null`  | Opaque cursor from previous page   |

### Response Shape

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

### Infinite Scroll Contract

1. First request: call without `cursor`
2. Next page: call again with the returned `next_cursor`
3. Stop when `next_cursor` is `null`

### Example (cURL)

```bash
# First page
curl -X GET "https://your-api.com/api/v1/search/history?limit=20" \
  -H "Authorization: Bearer <access_token>"

# Next page
curl -X GET "https://your-api.com/api/v1/search/history?limit=20&cursor=<next_cursor>" \
  -H "Authorization: Bearer <access_token>"
```

---

## Troubleshooting: Empty History

If `GET /api/v1/search/history` returns `{"searches":[],"total":0,"next_cursor":null}`:

1. **Check that searches are created with the same token.** The `Authorization` header must be present on `POST /api/v1/search` (or `/search/stream`). Without it, the search is saved as anonymous.
2. **Check that the token is valid.** An expired or malformed token on the search endpoint is silently ignored (the search still runs, but without a user link).
3. **Old searches won't appear.** Searches created before this update (or without auth) have no user association and cannot be retroactively linked.

---

## Compatibility Notes

- Existing `GET /api/v1/library/searches` remains unchanged.
- This is a **new** endpoint (non-breaking for existing frontend flows).
- MeSH/keyword quality improvements were internal behavior changes only (no request/response contract changes).
- The workflow endpoints (`/api/v1/workflow/*`) are unaffected.
