# data_viz API — HTTP/JSON contract

The single source of truth for request/response shapes of the Express API
(`data_viz/api`). Enforcement helpers live in `api/lib/http.ts`; the envelope
types live in `data_viz/shared/types.ts` (imported by both API and UI).
Every route handler follows this contract — extend it, never fork it.

## 1. Transport rules

- **HTTP status is the single success discriminator**: 2xx = success,
  4xx/5xx = failure. The UI never inspects a body to decide whether a call
  succeeded (except command endpoints' `success` field, §4).
- Every handler returns a body on every path. Async handlers MUST be wrapped
  in `wrapAsync(...)` — Express 4 does not catch async rejections, and an
  unwrapped rejection hangs the response forever. Sync throws are forwarded
  by Express natively.
- All bodies are JSON (`application/json`), except the documented binary
  endpoint (`GET /api/ai/ask-image` — raw image bytes with `Content-Type`).

## 2. Error bodies — one shape for every non-2xx (ApiErrorBody)

```json
{
  "success": false,
  "error": "Missing 'code' parameter",
  "code": "MISSING_PARAM",
  "details": { "param": "code" }
}
```

| Field    | Status   | Notes |
|----------|----------|-------|
| `success`| mandatory, literal `false` | |
| `error`  | mandatory, string | human-readable message the UI surfaces (`formatFetchError` reads exactly this field) |
| `code`   | optional, `ApiErrorCode` enum | machine-readable class |
| `details`| optional, object | per-field context (e.g. `{ param }`) |

`ApiErrorCode` vocabulary (`shared/types.ts`):

| Code | HTTP | Meaning |
|------|------|---------|
| `MISSING_PARAM` | 400 | required query/body param absent or blank |
| `INVALID_PARAM` | 400 | param present but malformed / out of range / not allowed |
| `NOT_FOUND` | 404 | addressed resource does not exist |
| `CONFLICT` | 409 | conflicts with existing state (duplicate, stale write) |
| `UPSTREAM_FAILED` | 500 (may be 502) | python runner / LLM / daemon failure |
| `INTERNAL` | 500 | everything else (the global handler's default) |

Routes raise errors by throwing `HttpError` (from `api/lib/http.ts`) —
`HttpError.missingParam("code")`, `HttpError.invalidParam("days", "1..5000")`,
`HttpError.notFound(...)`, `HttpError.conflict(...)`, or
`new HttpError(status, code, msg)`. The app-level handler in `app.ts`
(`sendThrowable`) renders the body; it also honors the service-layer
`Object.assign(new Error(msg), { status })` convention. Never write
`res.status(...).json(...)` by hand in a route.

## 3. Success: read endpoints (GET) — payload-is-body

The 2xx body IS the payload, typed in `data_viz/shared/types.ts`.

| Class | Mandatory | Optional |
|---|---|---|
| Series/chart | identity echo (`code`, `sec_type`/`date` when queried), `rows: Row[]` (never `null`; `[]` when empty) | `name`, `dates` (only when rows are date-keyed) |
| Entity list | `items: T[]`, `total: number` | pagination block (§5) |
| Single entity | the resource object — absent resource is **404 + §2 body**, never `null` at 200 | — |
| Composite/bundle | each sub-payload keeps its own contract; freshness anchors (`snapshot_date`, `dates`, `rows`) stay top-level reachable — the UI's cache-freshness extractor (`api-client/_cache.ts: extractLatestDate`) reads top-level fields | — |

Field conventions (mandatory for new fields):

- Dates `YYYY-MM-DD` strings; times `HH:MM` local; month keys `YYYY-MM`.
- Codes: 6-digit padded strings; `.SZ`/`.SS` suffixes for stock/ETF (`.SS`,
  not Tushare's `.SH`); bare 6-digit for indices; bare code strings for
  futures. Label vocabularies are decoded server-side (`labelDecodeSql`) —
  never leak SMALLINT codes into payloads.
- Missing scalar → `null` (never omitted, never `""`, never `NaN`); empty
  collection → `[]`; `?:` only for conditionally-present fields, documented.
- Numbers are real numbers (server-side `toNum` coercion) — pg numeric
  strings never reach the wire.
- Names: `snake_case` for payload data fields.

## 4. Success: command endpoints (run-trigger / CRUD POST/PUT/DELETE)

```json
{ "success": true, "message": "started", "process_id_tag": "..." }
```

- `success` is **mandatory here** — the command outcome is the data.
- The one documented exception to §1: a python-run command may answer **200
  with `success: false`** + `stdout_tail`/`stderr_tail` — that outcome is
  DATA (the UI branches on `success` first, only for command endpoints).
- CRUD mutations: `{ success: true, entry }` / `{ success: true, rows }`;
  delete: `{ success: true }`; creation responds **201**.

## 5. Pagination — one spelling

| Field | Status |
|---|---|
| `page`, `page_size`, `total` | mandatory on any paginated list |
| `rows` (items array) | mandatory — `rows` is the collection key |
| `total_pages` | legacy, optional (derivable) |

Unify new endpoints on `total`; `total_indices` / `total_etfs` /
`total_stocks` / `total_codes` are legacy spellings pending migration.

## 6. Coverage/meta — optional flat fields, never nested

| Field | Meaning |
|---|---|
| `as_of` | single-date freshness anchor (live/snapshot endpoints) |
| `first_date`, `last_date`, `n_dates` | range coverage of a historical series |
| `available_dates` | selectable date list |
| `warnings` | non-fatal caveats (`string[]`) |

Never nest these under a `meta` object — the UI cache-freshness extractor
reads top-level fields; nesting silently breaks staleness detection.

## 7. Request params

Standard names: `code, sec_type, exchange, start_date, end_date, page,
page_size, limit, fresh`. Parse with the `api/lib/http.ts` parsers —
`strParam / intParam / dateParam / enumParam / boolParam` — which fire
`HttpError(400, MISSING_PARAM)` on absent required params. Booleans:
`"1"`/`"true"` → true, `"0"`/`"false"` → false. Never silently coerce a
malformed param where the endpoint has no documented default (silent
fallbacks must stay declared via the parser's `default` option).

## 8. Migration notes / follow-ups (not yet done)

- Pagination spellings (`total_*` → `total`) and `minDate/maxDate` →
  `first_date/last_date` are legacy-compatible; migrating them requires
  pairing each change with the UI readers (`extractLatestDate` anchors,
  pagination footers) in the same commit.
- ~20 monitor/config command bodies still use camelCase meta fields
  (`generatedAt`, `staleAfterS`); migrate to snake_case opportunistically.
- Response interfaces duplicated between route files and
  `src/lib/api-client/*` are pending centralization into `shared/types.ts`.
- Optional future hardening: zod request-schema layer; enable
  `tsconfig` `strictNullChecks`.
