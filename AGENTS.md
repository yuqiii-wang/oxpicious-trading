# oxpicious-trading — agent instructions

Always-loaded workspace instructions. The skills under `.agents/skills/`
carry the details; this file routes to them and pins the preferences that
must apply even when a skill is not invoked.

## Skill routing — load the matching skill BEFORE that kind of work

Do not wait for the user to name a skill; invoke it via the Skill tool as
the first step of the work:

- ANY code change, DB work, script run, or verification in this repo →
  `oxpicious-dev-conventions`
- ANY dataframe / pandas / numpy / cudf code — new compute, rolling /
  groupby / shift, dtypes, DB fetch or bulk-write paths, or a run logging
  `[cudf fallback]` lines → `cudf-dtypes`
- ANY DB write / insert / upsert / purge / DDL apply → `db-sql-write`
- ANY table creation/alter with a type/kind/state/family column, or any
  label-vocabulary domain change → `db-smallint-type-codes`
- ANY chart component, ECharts option builder, or chart tooltip/colors
  under `data_viz/src` → `ui-charts`
- ANY verification of `data_viz/` work → `ui-test`
- analysis_signals semantics (rule cells, history vs live, the seven
  families) → `signals-live-vs-history`
- financial/biz semantics of the mov_rsi / bollinger / ma_cross /
  options_wall / options_vix / momentum / rf_break signal families
  (trigger meaning, parameters, sides, evidence) → `signals-biz-logic`

## cudf preference (short form — the `cudf-dtypes` skill is authoritative)

- Whole-frame pandas is the default: grouped rolling / shift / diff via
  the shared `_common.df_utils` helpers and elementwise `where`-chains run
  on GPU under cudf.pandas; prefer ONE whole-frame grouped pass over
  per-group compute loops.
- Plain host numpy only as ONE per-group boundary core for gather/index
  algorithms (positional gathers, sparse tables, row×day 2D grids, DB
  array-literal building) — unwrap once via `host_array`, materialize via
  the REAL DataFrame ctor, document the boundary in the module docstring.
- NEVER mid-flow hybrids: `pd.Series(host_ndarray, index=...)` and
  `Series.take(host_positions)` return garbage-length Series under
  cudf.pandas 26.08 (2026-10-02 probes). No scalar-`pd.NaT` column init
  (infers str). No `np.searchsorted` on proxy positions — use prefix-rank
  arithmetic.
- A dataframe refactor is done only when the output is bit-identical
  (per-code md5 A/B) AND the `[cudf fallback]` signature mix does not
  grow.

## Standing conventions (details in `oxpicious-dev-conventions`)

- SQL-first DB changes (`database/sql/`), then bridge to Python.
- Pipelines are incremental by default (`--force` purges + regenerates);
  reads filter on the target table's primary key.
- Run Python through WSL conda (command in the conventions skill); scope
  test runs with `--sec-type index`.
- Study scope: only the 10 named indices unless explicitly instructed.
- Throwaway scripts and outputs go in `temp_scripts/`.
- Verify changes by checking API responses and the UI, not just the code.
