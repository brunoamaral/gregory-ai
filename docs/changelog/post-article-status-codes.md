# `POST /articles/post/` — standardised HTTP status codes

**Context**: PR 638 introduced org-privacy enforcement on the ingest endpoint. As part of that work the error-handling paths in `post_article()` were updated to return semantically correct HTTP status codes. This is a breaking change for any client that inspected the previous (non-standard) codes.

---

## Status code changes

| Condition | Old code | New code |
|:----------|:---------|:---------|
| Missing or invalid field (`FieldNotFoundError`) | `200` | `400` |
| Source not found / source has no team (`SourceNotFoundError`) | non-standard | `404` |
| Could not save article/trial (`ArticleNotSavedError`) | `204` | `500` |
| Cross-org source in payload (`CrossOrgPayloadError`) | n/a (new) | `400` |
| API key has no organisation (`APIAccessDeniedError`) | n/a (new) | `403` |

## Unchanged codes

| Condition | Code |
|:----------|:-----|
| Article/trial already exists (dedup) | `200` |
| No API key | `401` |
| Invalid API key | `401` |
| IP not in allowlist | `401` |
| Success (item created) | `200` |
| Unexpected error | `500` |

---

## Migration guide

If your client checks the response status code from `POST /articles/post/`:

- Replace checks for `200` on error paths with the appropriate new codes (`400`, `404`, `500`).
- A `200` response now unambiguously means success or a benign duplicate.
- Add handling for `400` (bad payload or cross-org source), `403` (key lacks an org), and `404` (unknown source).

The full response body still contains a `code` and `error_msg` field for programmatic error identification regardless of status code.
