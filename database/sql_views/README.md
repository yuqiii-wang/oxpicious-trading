# database/sql_views — SQL SELECT view templates

Read-side SELECT implementations live HERE, not inline in service code.
Every app query (UI API TypeScript and Python services) renders its SQL
from a template file in this tree at request time; the app code keeps only
the arg names and row mapping.

## Layout — mirror the service dir

A view file's directory mirrors the repo-relative directory of the service
file it serves; one `.sql` file per query, under a subdirectory named after
the service file's stem:

```
data_viz/api/services/analysis/market-beta/chart.ts
  → database/sql_views/data_viz/api/services/analysis/market-beta/chart/<query_name>.sql

analysis/market_beta/attributions/queries.py
  → database/sql_views/analysis/market_beta/attributions/queries/<query_name>.sql
```

The view name passed to the loaders is the path relative to this
directory, without the `.sql` extension:

```
data_viz/api/services/analysis/market-beta/chart/industry_series
```

## Template format

A `.sql` file is ONE query with `{{arg_name}}` slots and a leading
`-- args:` block documenting every slot:

```sql
-- Member-index close series for ONE industry (+ latest stock_num).
-- args:
--   industry_id: value text — the industry to list member indices for
--   pool_size_decode: fragment — labelDecodeSql("pool_size", "pool_size")
WITH stock_counts AS (...)
SELECT ... WHERE sc.industry_id = {{industry_id}}::text
```

Two slot kinds:

- **value** — a scalar or array bound through pg positional params. The
  loader replaces the slot with `$n` (first-use order; the same name
  reused twice maps to the same `$n`) and appends the arg to `params`.
  Casts stay in the template (`{{industry_id}}::text`,
  `ANY({{codes}}::text[])`) so the SQL site owns the types.
- **fragment** — raw SQL text substituted verbatim (CTE blocks built by
  code, decode subqueries, conditional clauses). Pass it wrapped: TS
  `sqlFrag(...)`, Python `frag(...)`.

Loaders are strict: every slot must get an arg and every arg must appear
as a slot, else render throws with the slot list.

### Loader mechanics to know

- Slots are substituted in **comment lines too**. Never write `{{name}}`
  in a template's comment header unless you mean it: a value-slot mention
  in a comment participates in first-use `$n` numbering and binds a param.
  Describe args in the header WITHOUT braces.
- Value slots are numbered by **first textual use**. When the original SQL
  deliberately numbered params out of text order (`$2` appears before
  `$1`), either pass the out-of-order placeholders as `sqlFrag("$n::type")`
  fragments (params stay assembled in the caller), or pin the numbering by
  mentioning the slot once in a comment line before the body (the comment
  line is inert SQL; params are appended in that same pinned order).
- Fragments carry **no params**: a `sqlFrag(...)` fragment may embed
  literal `$n` text, but the loader never binds values inside fragments —
  the caller assembles those params itself (see `renderSqlView` +
  `queryRows` call sites). When a fragment needs a bound value slot, pass
  the value as a separate template value slot instead.
- Composing a rendered view into a wrapper statement that already binds
  earlier params (Python): `render_sql_view(..., param_offset=N)` shifts
  the `$n` numbering; `view.params` extends the wrapper's params.

## Loaders

- UI: `data_viz/api/lib/sql-views.ts` — `renderSqlView(name, args)`
  returns `{ sql, params }`; `querySqlView<T>(name, args)` runs it
  through the shared `queryRows`.
- Python: `_common/sql_views.py` — `render_sql_view(name, **args)`
  returns `(sql, params)`; `frag(text)` wraps a fragment. Feed the pair
  to the existing `_common.db_commons` read helpers.

Files are re-read on mtime change, so editing a template takes effect
without restarting the dev server.

## What migrates vs what stays in code

Migrate: every read-side SELECT that implements app/analysis logic.

Stay in code:

- SQL **fragment builders** (`labelDecodeSql`, `chartCtesSql`,
  `ohlcPivotCteSql`, `priceExpr` …) — composition glue passed as
  fragment args, not queries.
- Pipeline **incremental mechanics** in Python builds/analysis `__main__`
  paths: primary-key / missing-date existence checks against the
  pipeline's own target table, and write-path SQL (INSERT/UPDATE/DELETE —
  views are the read side only).
- `_common/db_commons` internals (generic machinery, not service queries).

`serves:` a UI endpoint? mention it in the args block so the ui-test
sweep can map template → endpoint.

### Param-bearing fragments — the filter-composed pattern

Runtime-composed clauses (filter builders, per-type predicates) ride
**fragment slots**, and their `$n` placeholders stay **caller-assembled**:
the template keeps only `{{where_clause}}`-style slots, the caller renders
with `sqlFrag(whereText)` and passes its own params array to
`queryRows(rendered.sql, params)` (the pattern of
`data_viz/api/services/options/oi_cells.sql`). Consequences:

- A template that binds ANY params through fragments must not also use
  value slots (the loader numbers value slots from `$1`, colliding with
  the fragment's numbering) — page limits/offsets ride clamped-literal
  fragments instead.
- Fragments that need a bound value use the caller's positional params;
  out-of-order `$n` texts are fine inside fragments.

### What still stays in code (with the reason)

- SQL **fragment builders** (`labelDecodeSql`, `buildFilterSql`,
  `chartCtesSql`, `ohlcPivotCteSql`, `priceExpr` …) — composition glue
  passed as fragment args, not queries.
- Pipeline **incremental mechanics** in Python builds/analysis `__main__`
  paths: primary-key / missing-date existence checks against the
  pipeline's own target table, and write-path SQL (INSERT/UPDATE/DELETE —
  views are the read side only; UI-action writes are `database/sql_writes/`
  action files).
- `_common/db_commons` internals and the db.service replica-lag probe
  (generic machinery, not service queries).
- `database/sql/llm/08_llm_summary_caps.sql` — a one-time idempotent
  cleanup script, not a read path.

## Verification rule

A migration is correct only when the rendered SQL is equivalent to the
SQL it replaced (same text modulo whitespace) — the query text is not
being redesigned here, only relocated. After migrating a service, hit its
endpoints and compare payloads against the pre-migration baseline
(`ui-test` skill), or A/B the Python fetch output (md5) when it feeds a
pipeline.
