# Streaming CSV Response Implementation

## Overview

This document explains how the Gregory API streams large CSV exports without loading the whole dataset into memory or holding up the first byte of the response. It replaces an earlier design note that described a middleware-based approach — that code no longer exists.

## Key Components

- **`api.direct_streaming.stream_csv`** — a generator that yields CSV text chunks: the header row first, then one chunk per batch of serialized rows.
- **`api.direct_streaming.csv_header_fields`** — computes the static CSV header (column order) from a serializer's field names, applying the same per-org field rule the rows use, so header and rows always agree.
- **`api.direct_streaming.process_item` / `order_columns`** — shared per-row cleanup (line-break stripping, JSON-encoding of list/dict values) and column-ordering logic, used identically by the streaming path and the legacy buffered renderer (`DirectStreamingCSVRenderer`, still used for single-object CSV responses).
- **`api.views.CSVStreamingMixin`** — the viewset mixin that intercepts `list()` for `?format=csv` requests and returns a `StreamingHttpResponse` wrapping `stream_csv`.
- **Django's `StreamingHttpResponse`** — streams the response to the client as the generator produces chunks.

## How It Works

1. `CSVStreamingMixin.list()` checks `request.query_params["format"]`. Non-CSV requests fall through to the normal DRF `list()`.
2. For CSV requests, it builds the filtered queryset (`filter_queryset(get_queryset())`) and calls `paginate_queryset()`. If pagination applies (a `page_size` was given, or the request isn't exempted), the source is that page (a Python list, ≤100 rows). Otherwise (`all_results=true`) the source is the full queryset.
3. The CSV header is computed once, up front, from `self.get_serializer().fields` — it's static per serializer, so there's no need to scan rows to discover columns.
4. `stream_csv()` yields the header chunk immediately, then iterates the source in batches of `csv_stream_chunk_size` (2000 by default). For a queryset source it uses `.iterator(chunk_size=...)`, which Django batches `prefetch_related` calls against per chunk — so memory stays bounded by one batch's worth of prefetched rows, not the whole corpus. Each batch is serialized (`get_serializer(batch, many=True).data`), cleaned via `process_item`, and written into a small in-memory `csv.writer` buffer that is drained and yielded after each batch.
5. `X-Accel-Buffering: no` is set on the response so nginx doesn't buffer the whole stream before forwarding it — without this header the time-to-first-byte win is lost in production even though Django itself streams correctly.

## For API Users

Any endpoint using `CSVStreamingMixin` (currently `ArticleViewSet`, `TrialViewSet`, `ArticleSearchView`, `TrialSearchView`) streams CSV responses transparently:

- `/articles/?format=csv&all_results=true` — stream all articles as CSV
- `/trials/?format=csv&all_results=true` — stream all clinical trials as CSV
- `/articles/search/?team_id=1&subject_id=1&format=csv&all_results=true` — stream search results as CSV

Paginated CSV (`?format=csv&page_size=50`, no `all_results`) still returns exactly one page's rows plus the header — behavior here is unchanged from before this design.

These endpoints support all the usual filtering parameters, including `site_id` (see `docs/csv-export.md`).

## For Developers

No special setup is required for a viewset that already inherits `CSVStreamingMixin` and DRF's `list()`/`get_queryset()`/`get_serializer()` conventions. `csv_stream_chunk_size` can be overridden per-viewset if a different batch size is needed.

Detail-route CSV responses (single object, not a list) are not streamed this way — `CSVStreamingMixin.finalize_response()` falls back to rendering the response normally and wrapping the resulting bytes in a single-chunk `StreamingHttpResponse`, since those responses are small.

## Benefits

- **Bounded memory**: batches, not the whole queryset, are held in memory at any point.
- **Fast time-to-first-byte**: the header (and the first data batch) is sent as soon as it's ready, instead of after the entire dataset has been rendered.
- **Same filtering and column semantics**: column order, JSON-encoding of nested fields, and text cleaning are identical to the legacy renderer (`process_item`/`order_columns` are shared between both paths).

## Response Headers

- `Content-Type: text/csv; charset=utf-8`
- `Content-Disposition: attachment; filename="gregory-ai-{object_type}-{current_date}.csv"`
- `X-Accel-Buffering: no`

## Troubleshooting

### High memory or slow first byte

- Confirm the endpoint's viewset actually inherits `CSVStreamingMixin` and that `format=csv` is present in the query string — `list()` only takes the streaming path for CSV requests.
- Check `csv_stream_chunk_size` hasn't been set unreasonably high.
- In production, confirm `X-Accel-Buffering: no` is reaching nginx unmodified — a proxy that strips or ignores it will buffer the whole response regardless of what Django does.

### Truncated CSV body

Exceptions raised mid-stream (e.g. a DB hiccup partway through) cannot become an HTTP 500 once the header has already been sent — the client sees a truncated body instead. This is inherent to HTTP streaming, not a bug to "fix" with pre-buffering.
