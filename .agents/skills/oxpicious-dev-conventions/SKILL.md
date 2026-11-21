---
name: oxpicious-dev-conventions
description: Repo conventions for the oxpicious-trading quant trading repo — Python analysis/build pipelines, incremental --force DB regeneration, SQL-first DB changes, cudf GPU-first pandas layout, and the data_viz React UI. Use for ANY code change, DB work, script execution, verification, or UI work in this repo, even when the user does not ask for conventions explicitly.
---

# oxpicious-trading dev conventions

Layout: `builds/` and `analysis/` are Python pipelines run as `python -m <pkg>`;
`_common/` is the shared library (never duplicate its helpers);
`database/sql/` holds SQL; `data_viz/` is the React UI; `temp_scripts/` holds
throwaway scripts and their outputs.

## Skill routing (load the matching skill BEFORE that kind of work)

- ANY dataframe / pandas / numpy / cudf code → `cudf-dtypes` (layout + dtypes)
- ANY DB write / insert / upsert / purge / DDL apply → `db-sql-write`
- ANY chart component / option builder / tooltip under `data_viz/src` →
  `ui-charts`; ANY verification of `data_viz/` work → `ui-test`
- analysis_forecasts signal semantics (history vs live) →
  `signals-live-vs-history`

## Study scope for analysis & strategy (MANDATORY)

To do study in analysis and strategy, only check by industries and
indices (no etf and stocks unless explicitly said), only these 10 indices
(000001, 931743, 931160, 931071, 000015, 930708, 931994, 931594, 000683,
000991), and only write the 10 indices to DB table, unless otherwise
explicitly instructed then run all indices.

## Financial semantics gate

Before implementing a statement involving finance or trading semantics
(indicators, signal rules, return/period math), check whether it is obviously
wrong or has a clearly better formulation. If so, STOP — make no changes and
ask the user to double-check the proposed fix first.

## Python

### Bytecode caches (`__pycache__`) — clear on source edit

After editing any `.py` source file, delete the stale bytecode for it
before the next run — remove the module's `__pycache__` directory (or the
whole package's caches in one go):

```
rm -rf analysis/market_beta/__pycache__      # one package
find analysis _common builds -name __pycache__ -type d \
  -exec rm -rf {} +                          # tree-wide sweep
```

This repo is edited on Windows but run through WSL conda (`/mnt/e/...`).
Python reuses a cached `.pyc` when its recorded source mtime matches, and
cross-boundary edits can defeat that check — so a run can silently execute
old code, and a module renamed or deleted on disk keeps importing from its
orphaned `.pyc`. Clearing the cache forces a recompile from the real
source; it is cheap and safe (caches regenerate on the next run).

### Entry points (`__main__.py`)

Every new `__main__.py` starts with the cudf.pandas activation, before the
first pandas import anywhere in the module graph:

```py
# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()
```

Every `__main__` accepts `--force`:

- Without `--force`: query the target table's primary keys (typically
  `(date, code[, ...])`) and compute which dates/codes are MISSING, then
  generate only the missing data. Incremental runs are the default because
  these pipelines run daily over large histories.
- With `--force`: purge the old rows for the scope, then regenerate.

### Dataframes — cudf GPU-first preference

cudf.pandas is active in every pipeline (`activate()` above), so the
LAYOUT decides what runs on GPU. Load the `cudf-dtypes` skill BEFORE
writing or refactoring any dataframe code — its compute-layout table
and dtype rules are mandatory, not advisory. Short form:

- Whole-frame pandas is the default: grouped rolling / shift / diff via
  the shared `_common.df_utils` helpers (`grouped_rolling_agg`,
  `grouped_shift`) and elementwise `where`-chains run on GPU; prefer ONE
  whole-frame grouped pass over per-group compute loops.
- Plain host numpy is allowed ONLY as one per-group boundary core for
  gather/index algorithms (positional gathers, sparse tables, 2D grids,
  DB array-literal building) — unwrap once via `host_array`, materialize
  via the REAL DataFrame ctor, and document the boundary.
- NEVER build mid-flow hybrids (`pd.Series(host_ndarray, index=...)`,
  `Series.take(host_positions)` between GPU ops) — cudf.pandas returns
  garbage-length Series for that pattern (2026-10-02 probes).
- Avoid Python `for` loops and `if/else` branches in dataframe code. Use
  vectorized pandas/cudf operations (`where`, `mask`, `np.select`, `cut`,
  vectorized string ops, etc.). Loop only when no vectorized form exists.
- A dataframe refactor must A/B-verify: identical per-code md5 of the
  output tables AND no new `[cudf fallback]` signatures.

### Class-first Python — pydantic classes over static functions

Implement backend Python object-oriented: classes are the preferred
shape, loose module-level static functions the fallback (tiny pure
helpers, throwaway `temp_scripts/` study scripts). State and behavior
live on the class; pydantic (v2, in the conda env) is the preferred
base for typed structures:

- Pipeline steps / stages / engines: a class with typed fields for its
  params and a `run()`-style method — the established patterns are the
  signal family engines (`engines/rule_base.py` ABC), the calibration
  derivations (`BoostDerivationBase`), and the deep-agent `Tool` /
  `SkillBook` objects. A new capability is a subclass, never a branch
  in shared code.
- Typed payloads crossing module boundaries — configs, rule/label
  carriers, DB row contracts, API/service request+response bodies — are
  pydantic `BaseModel`s (validate with `model_validate`, emit with
  `model_dump()`), not loose dicts.
- Keep it cudf-compatible: classes CONFIGURE and HOLD frames; pydantic
  models never ride inside frame compute (no per-row model objects) —
  row materialization stays at the write boundary
  (`sanitize_for_db_insert` / `model_dump()`).
- Existing function-only modules follow the preference when next
  touched; don't mass-refactor working pipelines for the sake of it
  (any refactor still passes its skill's A/B bar).

### Typing and naming

- Give every variable a concrete type — never settle for `Any`. When a type
  is not determined, study the data flow; if needed split the function into
  smaller functions so each variable's type is deterministic. Do not write
  union-of-options types as an escape hatch.
- Names in English, `snake_case`.

## Database

- Before writing ANY DB write code, check `_common/db_commons/` for an
  existing helper (connections, upsert/copy, sync/async ops) and use it.
  Helper choice, write-size discipline (no bulk machinery for simple
  study), and the <10-sample study budget follow the `db-sql-write` skill.
- For DB schema/query changes, implement SQL first (files under
  `database/sql/`), then bridge to Python only if needed.
- ALL DB SQL lives in SQL files, never inline in logic — Python
  pipelines and the UI API alike. Reads are `database/sql_views/` SELECT
  templates rendered by `_common.sql_views.render_sql_view` (feed the
  returned `(sql, params)` pair to the db_commons read path); custom
  parameterized writes are `database/sql_writes/` action files (Python:
  `_common.sql_writes.render_sql_write` / `load_sql_write`; the TS API:
  `loadSqlWrite`). Logic
  keeps only arg names and row mapping. Layout and slot conventions:
  `database/sql_views/README.md` and `database/sql_writes/README.md`.
- No `CASE` / `CASE WHEN` in a SELECT — value mapping and decoding
  always come from the index: the `static.label_dim` scalar subquery /
  join on `(domain, code)` (TS renders it with
  `labelDecodeSql(domain, col)` as a `sqlFrag` fragment arg; Python
  decodes host-side via `_common.labels.decode_codes`). When a value
  that needs a label is missing from the index, fix it in SQL — add the
  row to its domain's INSERT file under
  `database/sql/static/label_dim/` plus the Python/TS mirrors
  (`db-smallint-type-codes`), never inline a CASE map in the query.
- One-time SQL (migration ALTERs, backfills, renames, legacy-row DELETEs,
  temp-table rewrites) runs ONCE from a `temp_scripts/` script and is
  never parked in `database/sql/` — fold its effect into the original DDL
  definitions so the schema files stay canonical and re-appliable. Seed
  INSERT files are the exception: they are the initial population / source
  of record, so they stay under `database/sql/` (details in the
  `db-sql-write` skill).
- Reads for incremental work filter on the table's primary key
  (typically dates plus codes) so tasks only process missing data.

## Running and testing

Run Python tasks through WSL:

```
wsl -d Ubuntu-22.04 -- bash -lc "source ~/miniconda3/etc/profile.d/conda.sh && conda activate base && cd /mnt/e/oxpicious-trading && python -m <task>"
```

For a new feature that needs testing, add `--sec-type index` so only index
data is processed; if the feature involves margin, use `--sec-type etf`
instead. Skip the scoping only for full data rebuilds.

A pipeline run produces or refreshes the data a change needs — it is not
the test itself. To test a change, send the request from the UI and check
the logs (see Verification); never write an ad-hoc Python script that
imports and calls the changed code directly.

Throwaway scripts and their outputs (console output, screenshots) go in
`temp_scripts/`.

Pending bugs found while running anything manually get a CAUTION row
(`monitor.caution_log`, the Monitor → Cautions tab) — never a loose note:

```
wsl -d Ubuntu-22.04 -- bash -lc "source ~/miniconda3/etc/profile.d/conda.sh && conda activate base && cd /mnt/e/oxpicious-trading && python -m _common.caution_log --service builds.index.daily --severity warning --title 'one-line issue summary' --detail-json '{\"key\": \"context\"}'"
```

(source=manual; resolve it in the Cautions tab once fixed. Service errors
and AI-found suspicions are recorded there automatically — abnormal
pipeline exits via `_common/service_heartbeat.py`, orchestration step
failures, maintainer job crashes, the data_viz API's own unhandled
errors + never-started py-runner runs, and the deep-agent suspicion
seams — all through the fire-and-forget `_common/caution_log` /
`data_viz/api/services/caution-log.service.ts` writers; recurring issues
dedupe onto one open row.)

LLM-touching tests and probes (deep agents, langgraph) run on the DUMMY
LLM double — `test/dummy_llm` at the project root (`DummyLlmProvider`
in-process: matched request → the feature dataset's canned reply; the
kg/jev cucumber assertion datasets live in the sibling
`test/dummy_langgraph`), or `--provider dummy` for
HTTP-sim against the resident local-model host's dummy-1 model
(default endpoint http://127.0.0.1:8767/v1/chat/completions, rate-limit
error injection via its POST /control) — zero provider spend,
deterministic; details in `deep-agent-blueprint`.

## Verification — send the request from the UI, check the logs

After implementing a change, test it by sending the request from the UI
and checking the logs — NOT by directly testing in Python. Every UI-test
request is sent from the browser into the real stack and lands on the
RESPECTIVE SERVER — the Express API (via the Vite proxy) for `data_viz`
pages, which itself delegates Python work to the respective
`server_cluster/` area server (ports in `server_cluster/cluster_config.json`:
llm 8767, downloader 8769, builder 8770, analyzer 8771, strategist 8772,
monitor 8773, config 8774, maintainer 8775) — the same no-cmd rule the
py-runner already enforces server-side. Never send a test request by cmd:
no `curl` / `node fetch` / ad-hoc Python HTTP one-liner from the shell;
if a scope has no UI entry point, send it from the open page's own
context through the browser tooling (the `ui-test` fallback). Exercise the
feature in the browser (Vite at http://localhost:5173; Express API at
http://localhost:3001 via `npm run server:dev`, or `npm run dev` for
both) so the request flows the real path (Vite proxy → API route → SQL →
pipeline-produced rows), then check the logs that request produced: the
Express server's stdout — the terminal / background task running
`npm run server:dev` (`console.log`/`console.error`; route hit, SQL
errors, stack traces) — and the browser console. Never verify by
importing/calling the changed Python code from an ad-hoc script, and
never substitute a `python -m` run for the UI path — pipeline runs
produce data (see "Running and testing"), they are not the test. Reading
the code alone is not verification either. The `data_viz/` tiers (UI
request + payload/log assertions, then one screenshot pass) are in the
`ui-test` skill.

## React UI (`data_viz/`)

Standard React + Vite project — write idiomatic React, not ad-hoc DOM code.

- Before writing a new component, check the shared ones and reuse them:
  `data_viz/src/components/` (charts, filters, tables, buttons) and
  `data_viz/src/shared/`. If a piece you wrote would serve multiple pages,
  migrate it into a shared directory instead of duplicating it.
- ANY chart work (new chart component, option builder, chart tooltip/colors)
  follows the `ui-charts` skill: inherit the base-chart kit in
  `data_viz/src/shared/charts/base-chart/` (BaseChart props/chrome/states,
  baseChartOption preamble, useChartThemeMode theme) — never hand-roll chart
  chrome, option preambles, or theme reads.
- If a page file has grown too large, refactor it into a directory of smaller
  files (page dir with subcomponents/hooks), reusing shared components as you go.
- ANY control that loads new data on click (refresh, window / order / sort /
  filter toggles) shows a spinner while the fetch is in flight — also during a
  refetch where stale rows stay on screen (never a silent reload): surface
  `useChartData`'s `loading` as a MUI `CircularProgress` beside the control or
  in the panel header, or pass `loading` through to `BaseChart` for chart
  panels (its mounted-chart corner spinner handles the overlay).
