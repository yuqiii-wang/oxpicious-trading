---
name: signals-live-vs-history
description: >
  How the oxpicious-trading repo differentiates HISTORY signals from LIVE
  signals: analysis_signals computes its OWN RSI percentile (rsi pct)
  buckets in-engine (the analysis_forecasts layer was REMOVED 2026-10-05)
  over analysis.mov_ave_rsi + prices; the plain gate (sign-aligned blended
  mean reversal > 0.75% at the per-side sample floor; the delay ladder /
  optimal-entry selection / SignalQuality gate were REMOVED 2026-10-05)
  turns gate-passing buckets into SIGNAL STRATEGIES
  (analysis_signals.signal_strategies — one row per bucket over its
  window period, delay 0, with the live breach bar) whose snapshot-owned
  trigger days (each qualifying streak's FIRST day) are the HISTORY
  signals (analysis_signals.history_signals); live signals are ONLY
  breaches of the strategies' active bars recorded in live.live_signals.
  Use for ANY work touching analysis_signals, live_signals, the emit
  gate, signal thresholds, or the mov_rsi family, even when the user
  does not mention signals explicitly.
---

# Live signals vs history signals

The repo has TWO signal tiers with a strict one-way dependency. Never blur
them: the live tier NEVER re-detects or re-computes anything — it only
breaches the bars the strategy tier already wrote.

2026-10-05 removals (three legs, same day):
1. The forecast layer is GONE — `analyze/analysis_forecasts` (package,
   SQL files, UI Forecast table + API endpoints), the
   `analysis_forecasts` schema (DROP CASCADE,
   temp_scripts/_drop_forecast_schema.py) and the ohlcv boost/selection
   derived populations. The signals tier computes its own rsi-pct
   buckets in-engine; strategies are the plain rsi-pct population only.
2. The SignalQuality claim-profile gate (engines/_quality.py) is gone —
   the plain gate at the per-side sample floor is the whole gate.
3. The DELAY LADDER is gone — no anchors at days 1..5, no
   optimal-entry argmax, no ladder-stability floor. A strategy IS its
   delay-0 profile: each qualifying streak's FIRST day is the trigger;
   `signal_delay_days` stays in the schema, written 0.

## Study scope for analysis & strategy (MANDATORY)

To do study in analysis and strategy, only check by industries and
indices (no etf and stocks unless explicitly said), only these 10 indices
(000001, 931743, 931160, 931071, 000015, 930708, 931994, 931594, 000683,
000991), and only write the 10 indices to DB table, unless otherwise
explicitly instructed then run all indices.

```
analysis.mov_ave_rsi (the RSI value source — Wilder RSI per window,
  owned by analyze.mov_ave_spread's rsi step) + prices (the
  mov_ave_spread price convention) + stats.market_regimes (day labels)
        │
        ▼  in-engine, per (sec_type, snapshot), on cudf.pandas frames
analyze.analysis_signals (THE SIGNALS TIER — python -m analyze.analysis_signals)
  ONE family: mov_rsi (SignalEngine ABC in engines/_base.py; the family
  package engines/signal_families/mov_rsi/; dispatched via the
  engines.ENGINES registry — never if/else). All computation is
  vectorized cudf.pandas; all frames are NATIVE-dtype only (no object
  columns, no datetime64[D] casts, no pd.Timestamp compares — they
  poison cudf frames; typed EMPTY frames matter too — object-dtype
  empties turn every numeric op into a [cudf fallback]). NO CASE/WHEN
  in any SQL: side is a STORED column, every conditional lives in the
  engine.
  Snapshot grid (config/grid.py — ported from the retired forecast
  config): the last 10 completed year-ends PLUS the ROLLING LATEST
  snapshot keyed at the sec_type's LATEST AVAILABLE RSI DATA DATE
  (analysis.mov_ave_rsi max(date)), each a trailing 10-year window
  (S − 10y, S].
  Per run (engines/signal_families/mov_rsi/_buckets.py): fetch the
  input frame ONCE (price + rsi_{W} columns + first dates + regime
  labels, bounded to the earliest target window start, no upper bound
  — forward changes need post-snapshot prices; fetch/inputs.py), then
  per snapshot: the linearly-interpolated percentile bar per (code,
  rsi_window) over the window's non-NULL values → nested per-side tail
  tests (top/bottom pct% {1,2,3}) → streak-merge (consecutive
  qualifying union-calendar days = ONE signal, triggered at its FIRST
  day, delay 0) → per-bucket blended stats (the FIXED 5d 0.65 / next
  0.25 / 20d 0.10 mix over the triggers' valid anchors, renormalized
  over the horizons with stats; occurrence = the MIN positive leg
  count = the window's qualifying-streak count) + the bucket's
  window-end trigger (bar recovery).
  THE GATE (engines/_frame.py detect): the sign-aligned blended mean
  forward change (dir_ave — top negated) > 0.75%
  (GATE_DIR_AVE_MIN) on occurrence_count >= 4 buys / 3 sells
  (BUY/SELL_SAMPLE_FLOOR — a thinner bucket measures selection noise;
  sells get the looser bar, their evidence is structurally thinner
  under positive drift). That is the WHOLE gate.
  The write is CHUNKED BY PARTITION KEY (engines/_store.py — the
  bulk-write rule): records grouped by code into ~100K-row chunks,
  each chunk ONE transaction (purge that code set's snapshot scope
  from BOTH tables + COPY its records), replica-lag throttle between
  chunks:
    signal_strategies ← ONE row per gate-passing bucket over its
    window period (start_date = window_lower(stat_date) ..
    end_date = stat_date). confidence = the bucket's sign-aligned
    dir_ave (FLOAT; INTEGER ROUND(10000 x) basis points on
    history/live rows). signal_threshold in the RSI's own space (the
    window's top/bottom-pct% percentile bar, recovered as
    rsi(window-end trigger) − trigger_excess). side stored
    (top/bottom), action sell/buy, signal_delay_days = 0. PK includes
    side AND regime_state. The parameter rides IN the sub_type —
    rsi{W}_{pct}pct ("1pct"/"2pct"/"3pct") — so each (window, pct,
    side, regime) is its own strategy.
    history_signals ← the buckets' trigger days (the qualifying
    streaks' first days) INSIDE the snapshot key's CALENDAR YEAR (the
    1-year-stride ownership rule: a year-end snapshot owns year Y;
    the rolling latest owns [Jan 1, its key]; one snapshot owns each
    date → no cross-snapshot PK conflicts). Structural clone of
    live.live_signals; time 15:00:00, is_day_close_trigger = TRUE.
  Incremental (config.mutable_dates): snapshots MISSING from
  signal_strategies plus the MUTABLE SCOPE re-emitted every run — the
  ROLLING LATEST snapshot (always; its forward legs complete late)
  and the newest completed year-end while its 20-trading-day forward
  windows are unrealized (~5 weeks after New Year; then it freezes).
  Retired keys (yesterday's rolling-latest date) are SWEPT from both
  tables before the resolution (SnapshotSelection.
  sweep_stale_snapshots — chunked deletes). (The forecast-identities
  snapshot gating + the snapshot_emissions partial-backfill attempts
  registry went with the forecast layer; coverage resolves at
  snapshot granularity, and the 04 table stays in the schema,
  unwritten.)
  --force purges the sec_type's family rows first; --metrics rsi
  scopes the family. Post-run: 02_is_active.sql (is_active = each
  (code, sec_type, type, sub_type)'s LATEST end_date — the live
  threshold set) + run/rank.py (the GPU confidence-DESC signal_order
  pass; 03_signal_order_rank.sql is its guarded write-back).
        │
        │  is_active = TRUE rows
        ▼  = THE CURRENT THRESHOLD SET
live.live_signals (LIVE signals = breaches)
  python -m live.live_signals (intraday): latest stats.*_intraday_5min
  close + the current rsi_{W} value vs every active strategy bar —
  a breach IS the live signal; one row per (code, sec_type,
  signal_type, signal_sub_type, date, time), PK upsert. THE GENERIC
  RULE (evaluator.py): the row's OWN action decides the direction
  (sell → value > threshold, buy → value <), the record carries the
  row's OWN action + confidence and signal_excess = signal −
  signal_threshold. The current value comes from the declarative
  SIGNAL_VALUE_SOURCE map (analysis/fetch/_values.py: rsi only; the
  bar is the STORED percentile threshold). Missing current value ⇒
  skipped, never invented. is_day_close_trigger = TRUE rows are the
  on-demand --date daily-close fallback (the old day-close mirror
  writer is RETIRED — history_signals is the historical record).
  (The confidence_boost / confidence_selection cohort add-on columns
  went with the forecast layer — both stay in the schema, unwritten.)
```

## The invariants (break none of them)

1. **mov_rsi's live bar is the STORED percentile threshold** (constant
   per bucket). mov_rsi is a STATE family that keeps flagging on every
   qualifying observation while the breach persists. (When both sides
   of one config breach the same bar, the record keeps the STRONGER
   breach — the live PK has no side.)
2. **The buckets + the gate are ENGINE logic in cudf, NOT SQL.** No
   CASE/WHEN anywhere in the signals SQL — side is a stored column on
   signal_strategies, and the fetch SQL is plain SELECTs only. Values
   are read at exactly the strategy/window trigger dates
   (value_points), never whole windows.
3. **A strategy covers exactly its snapshot window**
   (start_date .. end_date = stat_date, the trailing 10y). No
   strategy row → no history events → no live breaches for that
   config.
4. **Thresholds are denormalized into the breach record** so each
   live.live_signals row is self-contained; the source of truth stays
   analysis_signals.signal_strategies. confidence rides the row:
   strategies store the fractional dir_ave; live rows ROUND(10000 x).
5. **Snapshots are write-once per run.** A snapshot's strategy +
   history rows are written in whole-code chunked transactions
   (delete snapshot scope + COPY both tables), only when the snapshot
   is missing or inside the refresh window (or after --force).
6. **After every run:** refresh is_active + re-rank signal_order
   (02 SQL + run/rank.py, executed by the pipeline with $1 =
   sec_type).
7. **No UI component changes for signals.** The live page's config
   menu reads signal_strategies WHERE is_active; the history/expansion
   panels read live.live_signals + history_signals. React components
   render whatever the API returns. (The Recent Movements Forecast
   table + the forecast endpoints were REMOVED 2026-10-05 — that page
   is the trend chart alone now.)

## Where things live

- In-engine bucket computation: `analyze/analysis_signals/engines/
  signal_families/mov_rsi/_buckets.py` (percentile bars, streak-merge
  delay-0 triggers, forward changes, the blended per-bucket stats +
  typed empty frames).
- Strategy/history pipeline: `analyze/analysis_signals/` — `__main__.py`
  (cudf activate, --sec-type/--metrics/--snapshots/--force),
  `config/` (tables, gate constant 0.0075, sample floors, the snapshot
  grid in `grid.py` + horizon weights in `horizons.py`, the mov_rsi
  slice package, STRATEGY/HISTORY column layouts),
  `engines/_base.py` (SignalEngine ABC: identity + prepare/buckets/
  values_long/build phases + emit_snapshot template + the run entry),
  `engines/_frame.py` (detect — the plain gate at the sample floor — /
  snapshot_triggers / value_points), `engines/_store.py` (snapshot
  write — partition-key-chunked transactions, calendar-year history
  ownership — + chunked force purge), `engines/_snapshots.py`
  (SnapshotSelection: grid-based incremental resolution via
  config.mutable_dates + chunked stale-key sweep),
  `engines/_primitives.py` (action_of / frame_records),
  `engines/signal_families/mov_rsi/` (_engine identity + phases,
  _strategies, _history — mov_rsi is the only family),
  `fetch/` (inputs.py — the input frame + universe/first-dates/latest
  loaders; regimes.py — the daily market-regime labels),
  `run/pipeline.py` (per-sec_type pipeline + post-run refresh),
  `run/rank.py` (the GPU signal_order rank).
- Price/amount source maps (PRICE_SOURCE / AMT_SOURCE /
  OHL_EST_FILTER) live in `analyze/mov_ave_spread/config/
  price_sources.py` (re-homed from the retired forecast package).
- Tables: `database/sql/analysis/analysis_signals/00_schema.sql`
  (schema), `01_signals.sql` (signal_strategies + history_signals,
  16 hash partitions each), `02_is_active.sql`,
  `03_signal_order_rank.sql`, `04_snapshot_emissions.sql` (retired
  attempts registry — kept in the schema, unwritten). NOTE: the
  00/01 COMMENTs still describe the retired forecast-backed flow —
  the files were intentionally left untouched; treat the comments as
  historical.
- Live tier: `live/live_signals/` — `analysis/evaluator.py` (the ONE
  generic breach check), `analysis/fetch/` (`_values.py`
  SIGNAL_VALUE_SOURCE map (rsi only) + resolve_value/resolve_threshold,
  `_sources.py` the current-RSI fetcher (RSI_WINDOWS single-sourced
  from mov_ave_spread's rsi step), `_bars.py` as-of bar resolution,
  `_active.py` the active-set fetchers), `config/` (`tables.py`
  SIGNALS_TABLE + the record layout, `runtime.py` intraday/daily
  sources, `breach.py` scales). Schema
  `database/sql/live/03_live_signals.sql` +
  `05_live_signals_cohort_confidence.sql` (the retired cohort add-on
  columns — left in place, unwritten).

## Conventions for this area

- Every `__main__.py` starts with the cudf.pandas activation
  (`from _common.df_utils._activate import activate; activate()`)
  before the first pandas import, and configures logging via
  `_common.log_setup.setup_logging`.
- All DB reads land in cudf.pandas frames (::float8 casts at the SQL
  source, epoch→datetime64 at the boundary). Frames stay
  native-dtype — including EMPTY frames (typed constructors in
  _buckets; object-dtype empties turn the machinery's numeric ops
  into [cudf fallback] lines). The python-date conversions
  (value_points / frame_records) are the sanctioned scalar
  materializations. The bar is ZERO `[cudf fallback]` lines —
  `CUDF_FALLBACK_TRACE=1 python -m analyze.analysis_signals
  --sec-type index` pinpoints any regressor's caller.
- New signal families = a new engine package + slice constants (+ a
  SIGNAL_VALUE_SOURCE entry if live-wired). Dispatch through the
  ENGINES registry — no if/else ladders. A family without strategies
  naturally has no breaches.
- Testing: `--sec-type index --snapshots 1` first (the rolling
  snapshot — fast), full grid for real runs. Verify by row counts per
  signal_type/side, `SELECT DISTINCT signal_delay_days` (= 0),
  `SELECT DISTINCT ohlcv_type` (NULL), the trading-signals API + UI,
  not just by reading code.
- Verification: analysis_signals row counts per sec_type +
  is_active/signal_order stamps; live.live_signals breach rows; the
  live page config menu + row rendering.
