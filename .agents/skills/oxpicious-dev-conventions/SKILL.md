---
name: oxpicious-dev-conventions
description: Repo conventions for the oxpicious-trading quant trading repo — Python analysis/build pipelines, incremental --force DB regeneration, SQL-first DB changes, cudf GPU-first pandas layout, and the data_viz React UI. Use for ANY code change, DB work, script execution, verification, or UI work in this repo, even when the user does not ask for conventions explicitly.
---

# oxpicious-trading dev conventions

Layout: `builds/` and `analyze/` are Python pipelines run as `python -m <pkg>`;
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

Throwaway scripts and their outputs (console output, screenshots) go in
`temp_scripts/`.

## Verification

After implementing a change, verify it by checking API responses and the UI
console logs — not just by reading the code. The Vite dev server runs at
http://localhost:5173.

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
