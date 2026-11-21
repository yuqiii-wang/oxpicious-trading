---
name: debt-market
description: China debt / money-market domain map for the oxpicious-trading repo — the stats.debt_* tables + v_debt_daily (PBoC OMO reverse repo, outright repo 买断式逆回购, MLF, repo lifecycle, SHIBOR, ChinaBond/CGB yield curve, LPR) plus stats.pboc_oma policy notices, the builds/bond pipeline that fills them, GET /api/debt, and the data_viz Debt page panels. Use whenever working on anything debt/bond/liquidity related — 债券, 国债, 逆回购, 买断式逆回购, MLF, SHIBOR, LPR, 央行公开市场操作, 公开市场业务公告, yield curve, the Debt Market page, /api/debt, or a new debt_*/pboc_oma metric — even if the user just says "debt", "bond", "债", "rates", or names one instrument.
---

# Debt market (PBoC liquidity + rates) conventions

Verified against `database/sql/stats/debt_*.sql` DDL, `builds/bond/`, and the
`data_viz` debt stack (2026-11-18). "Debt" in this repo = the money-market /
rates domain — PBoC liquidity operations, benchmark rates (SHIBOR / LPR /
OMO), and the China treasury bond (中债国债, CGB) yield curve — NOT
corporate/convertible bond securities (those live in
`database/sec_statics/sec_classification/` as DEBT_* security classes, a
different thing).

## 1. The three tiers and their (deliberately inconsistent) names

The same domain carries three names — don't "fix" them, they are the
established vocabulary:

- **Pipeline** `builds/bond` (`python -m builds.bond`) — named "bond"
- **DB tables** `stats.debt_*` + `stats.pboc_oma` — named "debt"
- **UI page** "Debt Market" at `/dataviz/debt` + API `/api/debt`

Chain: download scripts produce source CSVs (`instruments_combined.csv`,
`lpr_combined.csv`, `oma_combined.csv`) → `builds/bond` parses + upserts the
9 tables → `stats.v_debt_daily` reconstructs one row per trading day (JOIN on
date, `database/sql/stats/99_reconstruct_views.sql`) → `GET /api/debt` →
`DebtPage.tsx` panels.

### Table inventory

| Table | Content | Notes |
|---|---|---|
| `stats.debt_identity` | PK core, one row per trading day | every other debt_* table FKs to it |
| `stats.debt_omo` | daily 7d reverse-repo ops + overnight variety | `omo_all_rates/tenors/quantities` text cols for multi-tender days |
| `stats.debt_outright_repo` | 买断式逆回购 bond-buying tenders | `*_marker` 0/1 + quantity + tenor_days + serial |
| `stats.debt_mlf` | MLF tenders | same marker shape |
| `stats.debt_repo` | reverse-repo lifecycle stock | start/end/net_injection/cumulative, computed in the pipeline |
| `stats.debt_shibor` | SHIBOR fixings, 8 tenors O/N…1Y | view+API narrow to 6 (skip 2w, 9m) |
| `stats.debt_treasury` | CGB yield curve, cb_0d…cb_50y (17 tenors) | view+API narrow to 1y/5y/10y/30y |
| `stats.debt_lpr` | monthly LPR 1Y + 5Y+ | one row per announcement date, sparse by design |
| `stats.pboc_oma` | PBoC policy notices 公开市场业务公告 | PK `(date, title)`, **no FK to debt_identity** (announcements can fall on non-trading days) |

## 2. Financial semantics (units, cadence, markers)

- **Units**: quantities are 亿 (100M yuan) straight from the source bulletins;
  rates are all in %. Charts annotate `fmtNum(v) + "亿"` and `fmtPct(v)` —
  keep that pairing for any new series.
- **`omo_rate` is the 7-day reverse-repo POLICY rate** (the auction price, now
  a fixed rate) — not a market-determined fixing. SHIBOR/CGB yields are the
  market side; OMO/LPR are the policy side. Keep the two groups visually
  distinct (policy = colored axis name, market = muted axis name).
- **Overnight reverse repo (隔夜逆回购)**: `omo_rate_1d` / `omo_quantity_1d`.
  Quantity-only bulletins; rate is fixed = 7d OMO rate + 25bp (per 公开市场
  业务公告[2026]第2号; first operation 2026-06-29 at 1.65%). Parsed from the
  bulletin rider "同时，开展了N亿元隔夜逆回购操作".
- **Markers** (`outright_repo_marker`, `mlf_marker`) are SMALLINT 0/1 — the
  date marks "PBoC announced a tender today"; quantity/tenor live beside it.
  API coerces them to `0 | 1` literals.
- **`repo_end_quantity` is signed** (withdrawals negative); the UI takes
  `Math.abs()` for the withdrawal bars. Don't flip the sign in SQL.
- **LPR cadence**: published monthly on the 20th (next business day on
  holidays) since 2019-08-20. Rows are sparse monthly dates in a daily frame —
  the flat line between announcements is drawn with `connectNulls`, it is not
  missing data.
- **OMA vs daily bulletins**: `pboc_oma` holds the INFREQUENT policy notices
  (tool introductions, primary dealer lists); the daily transaction bulletins
  are already parsed into debt_omo / debt_outright_repo / debt_mlf. Never
  merge the two sources.
- **OMA `type` slugs** (closed set, DDL COMMENT is the source of truth):
  `overnight_reverse_repo | outright_repo | central_bank_bill | mlf |
  primary_dealer | interest_rate | tool_introduction | other`. The UI
  `OMA_TYPE_META` map colors the known ones and falls back to `other` styling —
  add both sides when adding a slug.

## 3. The repo lifecycle rule (shared pipeline ⇄ UI algorithm)

`computeOutrightRepoLifecycle` (`data_viz/src/lib/lifecycle.ts`) is a port of
`compute_outright_repo_lifecycle()` in `builds/bond/repo_lifecycle.py` — keep
the two behaviorally identical:

1. Each tender emits a **start leg** `+quantity` at the op date and an
   **end leg** `−quantity` at `date + round(tenor_days)` (calendar days).
2. Legs group by date; `cumulative` is a running sum of the daily net.
3. The cumulative is only correct over the **FULL history** — `rows` passed in
   must not be date-filtered first. The page therefore fetches everything and
   windows locally; never recompute the cumulative over a filtered range.

Two different cumulatives coexist by design: `debt_repo.repo_cumulative`
(7d reverse-repo stock, computed in the pipeline and stored) vs the
outright-repo/MLF cumulative (computed in the UI from markers, not stored).
Don't conflate or "store" the second one — it derives from tenor legs.

## 4. Sync points — adding a debt metric touches all of these

A new debt column/tenor/panel is a cross-tier change. In order:

1. **DDL** `database/sql/stats/debt_<sub>.sql` (+ the kg entity banner, per
   `kg-db-semantics`; a new type/state column → `db-smallint-type-codes`).
2. **View** `stats.v_debt_daily` in `99_reconstruct_views.sql` — DROP then
   CREATE (leaf view; CREATE OR REPLACE can't insert columns mid-list).
3. **API** `data_viz/api/services/market/debt.service.ts`: add to
   `SELECT_COLUMNS`, `DbDebtRow`, and `transformRow` together — the comment
   there says "kept in sync with transformRow", preserve that.
4. **Types** `DebtDailyRow` in `data_viz/shared/types.ts`.
5. **Pipeline** `builds/bond/<source>.py` parse + upsert (SQL-first writes per
   `oxpicious-dev-conventions`; the pipeline is incremental DB-first —
   missing dates vs `debt_identity` — so a new column needs `--force` once to
   backfill).
6. **UI**: series const in `data_viz/src/theme/chart-palette.ts`
   (`SHIBOR_SERIES` / `CHINABOND_SERIES` / `LPR_SERIES` pattern: col + label +
   MUTED_PALETTE color), then a panel in
   `data_viz/src/dataviz/features/debt/DebtPage.tsx` built on `buildBaseOption`.
7. **AI-Ask docs** (optional): a `ui.debt.<id>` row in
   `database/sql/static/docs/static/30_ui_derivatives_market.sql` (DELETE +
   INSERT seed per that file's convention), referenced via `useDocs()`.

**View-key gotcha**: the service lives at
`services/market/debt.service.ts` but its sql views are keyed
`data_viz/api/services/debt/daily` and `…/date_range` (+ `…/pboc_oma`) — no
`market` segment. The date-range filter is pushed down via fragment args
(`select_columns` / `where_clause` sqlFrag) per the `sql_views` convention.

## 5. Debt-page UI conventions (beyond the shared chart kit)

The page inherits `ui-charts` (`BaseChart`, `baseChartOption`,
`useChartThemeMode`); these are the family-specific rules:

- **One fetch for the five rate/ops panels**: `fetchDebtDaily(undefined,
  undefined)` → full history, `{dates, rows, minDate, maxDate}`; the OMA strip
  fetches `/api/debt/oma` separately (small table, always full).
- **Crosshair sync**: every panel passes `group="debt"` so ECharts links the
  axisPointer across panels (the React mirror of matplotlib `sharex=True`).
- **Quarterly x ticks**: `quarterTickFilter` in `base-option.ts` mirrors
  `format_date_axis_quarterly` in `_plot_commons.py` — first business date of
  each quarter. Don't replace it with ECharts auto interval.
- **PBoC ops as tooltip info, not markLines**: `buildMarkerMap` builds a
  date → info-strings map (outright repo, MLF, overnight ops incl. the 亿
  quantities) shown inside the tooltip; `buildMarkerMarkLines` still exists
  for dense vertical lines but the tooltip treatment is the default. Colors:
  MLF = `MUTED_PALETTE[1]` (orange), outright = `MUTED_PALETTE[3]` (red) —
  same convention as the Python side; keep both sides aligned.
- **Sparse policy rates render as step lines** (LPR, overnight OMO):
  `step: "end"` (the new rate is effective from the next day),
  `connectNulls: true`, `showSymbol: false` plus a `symbolSize` function that
  returns 6 only when the value is non-null — markers appear exactly on
  announcement/operation days.
- **Axis freeze across the OMO 1d/7d toggle**: compute min/max from the UNION
  of both rate series with a 5% pad and pin them explicitly — otherwise the
  sparse flat 1.65 overnight line rescales the axis on every switch.
- **Refresh**: `invalidateCacheForUrl("/api/debt")` + `refresh()` at page
  level (one cache key covers all five panels); the OMA strip has its own
  plot-level refresh button and cache key.
- **AI-Ask specs** use `useDocs()` with doc ids `ui.debt.oml` (outright/MLF
  panel), `ui.debt.omo-7d`, `ui.debt.omo-1d` (the toggle swaps doc ids),
  `ui.debt.shibor`, `ui.debt.cgb`, `ui.debt.lpr`.

## 6. Guardrails

- Chart work routes through `ui-charts`; verification of anything here is
  `ui-test` (send the request from the Debt page, assert `/api/debt` payloads
  and logs) — never a direct Python probe of the service.
- DB writes route through `db-sql-write`; the pipeline's `--force` /
  incremental semantics and the WSL conda run command are in
  `oxpicious-dev-conventions`.
- The pipeline imports pandas, so `builds/bond/__main__.py` defers the import
  until after cudf.pandas activation (DataBuild ABC lifecycle) — keep that
  ordering when touching the entry point; dataframe code follows `cudf-dtypes`
  (host unwrap once at the final boundary, as `pipeline.py::_host` does).
- This is a data-surface domain: no signal/alpha semantics live here (the
  rate series feed eyeballs and AI-Ask, not the signal families), so don't
  invent derived indicators on the UI side — compute in the pipeline first.
