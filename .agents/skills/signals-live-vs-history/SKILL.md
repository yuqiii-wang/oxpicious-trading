---
name: signals-live-vs-history
description: >
  How the oxpicious-trading repo differentiates HISTORY signals from LIVE
  signals: analysis_forecasts computes per-bucket forward profiles; the
  plain gate (sign-aligned blended mean reversal > 0.75% — the blended
  reverse_prob leg was REMOVED 2026-09-25 with
  forecast_results.reverse_prob) turns gate-passing buckets into
  SIGNAL STRATEGIES
  (analysis_signals.signal_strategies — one row per bucket over its
  forecast period, with the live breach bar) whose month-owned trigger
  days are the HISTORY signals (analysis_signals.history_signals); live
  signals are ONLY breaches of the strategies' active bars recorded in
  live.live_signals. Use for ANY work touching analysis_signals,
  live_signals, the emit gate, signal thresholds, or signal families
  (mov_rsi / mov_std, then mov_pairs / mov_pairs_ema
  / margin_ratio / high_low_streaks as engines land), even when the user
  does not mention signals explicitly.
---

# Live signals vs history signals

The repo has TWO signal tiers with a strict one-way dependency. Never blur
them: the live tier NEVER re-detects or re-computes anything — it only
breaches the bars the strategy tier already wrote.

```
analysis_forecasts (HISTORY study)
  forecast buckets per (code, snapshot stat_date, config) on the
  ANNUAL grid — the last 10 completed year-ends PLUS the ROLLING
  LATEST snapshot keyed at the sec_type's LATEST AVAILABLE DATA DATE
  (not the year-end; it rolls forward with the data) — each a
  trailing 10-year window (S − 10y, S]; forward profiles at
  next/5d/20d → forecast_results rows (the 60d horizon was retired
        │  2026-09-20)
        │
        │  MIXED row: the FIXED-weight blend of the three horizons
        │  (5d 0.65 / next 0.25 / 20d 0.10 — config
        │  MIXED_HORIZON_WEIGHTS, materialized on every bucket)
        ▼
analyze.analysis_signals (THE SIGNALS TIER — python -m analyze.analysis_signals)
  per family ENGINE (SignalEngine ABC in engines/_base.py; family
  packages under engines/signal_families/ — mov_rsi, mov_std, mov_pairs,
  mov_pairs_ema; dispatched via the engines.ENGINES registry — never
  if/else). All computation is vectorized cudf.pandas; all frames are
  NATIVE-dtype only (no object columns, no datetime64[D] casts, no
  pd.Timestamp compares — they poison cudf frames). NO CASE/WHEN in any
  SQL: side is a STORED column, every conditional lives in the engines.
  Per (sec_type, snapshot stat_date), the write is CHUNKED BY PARTITION
  KEY (_store.py — the bulk-write rule): records grouped by code into
  ~100K-row chunks, each chunk ONE transaction (purge that code set
  from BOTH tables + COPY its records; a code is all-or-nothing per
  commit), replica-lag throttle between chunks:
    signal_strategies ← one row per bucket whose mixed-row DELAY LADDER
    (rung d = conditioned on the signal having persisted d days,
    0..TRIGGER_DELAY_MAX=5) has a rung passing THE GATE (the plain
    forecast-results rule, EVERY RUNG, every family):
      dir_ave > 0.75%  (sign-aligned blended mean forward change:
                     top/upper → −ave_change, bottom/lower → +;
                     the sign flip happens in the engine)
    The registered rung is the OPTIMAL ENTRY DELAY — the balance rule's
    argmax occurrence_count × sign-aligned dir_ave over the gate-passing
    rungs (the opportunity cost of waiting — occurrence_count decays
    with the rung — against the persistence-conditioned return
    deepening; ties → the smallest delay; value-based groupby-agg
    argmax in detect(), never frame order) — stored as
    signal_delay_days. confidence = the CHOSEN rung's sign-aligned
    dir_ave (the expected favorable blended move; FLOAT on
    strategies, INTEGER ROUND(10000 x dir_ave) basis points on
    history/live rows);
    signal_threshold in the VALUE'S OWN space (mov_rsi: the window's
    top/bottom-1% RSI percentile bar, recovered as rsi(d*) −
    trigger_excess(d*) at the CHOSEN rung's window-end anchor; mov_std:
    the band level, price(d*) − trigger_excess(d*), price space). side
    stored (top/bottom/upper/lower), action sell/buy. PK includes side
    AND regime_state; the SignalQuality gate (engines/_quality) still
    reads the DELAY-0 forward profiles (5d/20d/next) — it decides
    whether the bucket registers at all.
    history_signals ← the CHOSEN RUNG's anchor days INSIDE the snapshot
    key's CALENDAR YEAR (the 1-year-stride ownership rule: a year-end
    snapshot owns year Y; the rolling latest snapshot owns [Jan 1, its
    key] — a delay-d strategy's history rows are its streaks'
    day-d anchors — the day the delayed entry fires; one snapshot owns
    each date → no cross-snapshot PK conflicts; dedup on bucket keys +
    date — the upstream arrays can repeat a date). Structural clone of
    live.live_signals; time 15:00:00, is_day_close_trigger = TRUE,
    confidence = ROUND(10000 x dir_ave) integer basis points,
    signal_delay_days denormalized on both tables.
  Emission slices (current build): mov_rsi pct = 1 (top/bottom),
  mov_std MA/σ windows >= 20d at k >= 2.0σ (upper/lower), and the two
  pair-cross families mov_pairs / mov_pairs_ema (both fast legs, slow
  legs >= 60d, BOTH cross sides — bottom cross-down → buy AND top
  cross-up → sell, so every sign flip of the daily spread flags
  exactly once; widened from bottom-only 2026-09) — one-day signals,
  rung 0 only. The strategy parameter rides IN the sub_type as a
  literal suffix —
  rsi{W}_{pct}pct ("1pct") and std{W}_{k}std (k %g-formatted — "2",
  "2.5" — matching the API tick join's float8::text, plus the "std"
  literal) — so each (window, parameter, side) is its own strategy.
  Incremental (the mutable-scope rule, config.mutable_dates — shared
  with analysis_forecasts): snapshots present in analysis_forecasts'
  identities minus snapshots already in signal_strategies, PLUS the
  MUTABLE SCOPE re-emitted every run — the ROLLING LATEST snapshot
  (always; its long-horizon mixed legs complete late) and the newest
  completed year-end while its 20-trading-day forward windows are
  unrealized (~5 weeks after New Year; then it freezes). Retired keys
  (yesterday's rolling-latest date) are SWEPT from both tables before
  the resolution (SnapshotSelection.sweep_stale_snapshots).
  --force purges the sec_type's family rows first; --metrics rsi,std,
  pairs,epairs scopes the families. Post-run: 02_is_active.sql
  (is_active = each (code, sec_type, type, sub_type)'s LATEST end_date
  — the live threshold set) + 03_signal_order_rank.sql (confidence
  DESC rank within (sec_type, end_date), nothing trimmed).
        │
        │  is_active = TRUE rows
        ▼  = THE CURRENT THRESHOLD SET
live.live_signals (LIVE signals = breaches)
  python -m live.live_signals (intraday): latest stats.*_intraday_5min
  close + current indicator values vs every active strategy bar —
  a breach IS the live signal; one row per (code, sec_type,
  signal_type, signal_sub_type, date, time), PK upsert. THE GENERIC
  RULE (evaluator.py): the row's OWN action decides the direction
  (sell → value > threshold, buy → value <), the record carries the
  row's OWN action + confidence and signal_excess = signal −
  signal_threshold. The current value comes from the declarative
  SIGNAL_VALUE_SOURCE map in analysis/fetch/ (_values.py: rsi /
  close / cross / margin_z). Missing current value ⇒ skipped, never
  invented. (The old day-close mirror --live writer is RETIRED —
  history_signals is the historical record; is_day_close_trigger =
  TRUE rows in live.live_signals are legacy.)
```

## The invariants (break none of them)

1. **The daily-moving families' live bars are DERIVED FRESH, never
   the stored ones.** Bollinger bands and cross legs move daily — the
   live tier derives mov_std's bar (ma_{W} ± k·std_{W}) and the cross
   families' bar (the day's SLOW-LEG value ma_{W} / ema_{W}; the
   compared signal is the day's FAST leg — the price for pxpair/
   pxemapair, ma5 for pair, ema6 for emapair — so the breach record
   carries the day's values in ABSOLUTE value space, not spread
   space) per check (fetch.resolve_threshold) and the breach record
   pins the bar it compared against. The strategies' stored
   signal_threshold is only the emission-time snapshot (for the cross
   families literally the zero line) and is never compared for them.
   Only families with a genuinely static bar (mov_rsi's percentile,
   margin_ratio's signed z-bar) check against the stored threshold.
   (When both sides of one config breach the same day, the record
   keeps the STRONGER breach — the live PK has no side.)
3. **The gate + emits are ENGINE logic in cudf, NOT SQL.** No
   CASE/WHEN anywhere in the signals SQL — side is a stored column on
   signal_strategies, and the fetch SQL is plain SELECTs only (the
   bucket reads fan triggers out via LATERAL unnest with OFFSET 0 to
   keep the planner on the per-bucket pkey probe — without it the
   planner hash-scans all 16 forecast_results partitions, ~100s per
   month). Values are fetched ONLY at the window-end + month-owned
   dates (value_points), never whole windows.
2. **A signal exists only where its forecast bucket exists.** Months
   are month-gated to analysis_forecasts' identities registry; a
   strategy covers exactly its bucket's forecast period
   (start_date .. end_date = stat_month). No strategy row → no history
   events → no tick → no live breaches for that config.
4. **Thresholds are denormalized into the breach record** so each
   live.live_signals row is self-contained; the source of truth stays
   analysis_signals.signal_strategies. confidence rides the row:
   strategies store the fractional dir_ave; live rows ROUND(10000 x).
5. **Months are write-once per run.** A month's strategy + history
   rows are written in ONE transaction (delete month scope + COPY both
   tables), only when the month is missing or inside the refresh
   window (or after --force).
6. **After every run:** refresh is_active + re-rank signal_order
   (02/03 SQL, executed by the pipeline with $1 = sec_type).
7. **No UI component changes for signals.** The Recent Movements
   forecast table's ✓ tick is the API's in_signals EXISTS into
   signal_strategies (side = side, end_date = stat_month,
   regime_state matched to the forecast row's own regime split —
   a ● row ticks iff that regime split's strategy registered)
   — see data_viz/api/services/analysis/analysis-forecasts.ts
   inSignals(); the live page's config menu reads signal_strategies
   WHERE is_active. React components render whatever the API returns.

## Where things live

- Forecast buckets + mixed row: `analyze/analysis_forecasts/`
  (weights: `config/horizons.py` — also the reverse_prob removal
  note).
- Strategy/history pipeline: `analyze/analysis_signals/` —
  `__main__.py` (cudf activate, --sec-type/--metrics/--months/--force),
  `config/` (tables, gate constant 0.0075, the delay-ladder
  SIGNAL_DELAY_MAX + balance rule, per-family slice packages
  mov_rsi/mov_std/mov_pairs, STRATEGY/HISTORY column layouts),
  `engines/_base.py` (SignalEngine ABC: identity + fetch/build phases +
  emit_snapshot template + the stale-key sweep + run entry),
  `engines/_frame.py` (detect — the
  per-rung gate + the balance-rule argmax — / snapshot_triggers /
  value_points / regime_label), `engines/_quality.py` (the final
  SignalQuality gate), `engines/_store.py` (snapshot write —
  partition-key-chunked transactions, calendar-year history ownership —
  + chunked force purge), `engines/_months.py` (SnapshotSelection:
  incremental snapshot resolution via config.mutable_dates +
  chunked stale-key sweep),
  `engines/_primitives.py` (action_of / frame_records),
  `engines/signal_families/` (mov_rsi / mov_std / mov_pairs — one
  package per family: _engine identity + phases, _strategies, _history),
  `fetch/` (CASE-free SQL loaders; `fetch/<family>/_sql.py` templates —
  the bucket read fans the mixed delay ladder + per-rung triggers out
  via LATERAL unnest with OFFSET 0),
  `run/pipeline.py` (per-sec_type pipeline + post-run 02/03 SQL).
- Tables: `database/sql/analysis/analysis_signals/00_schema.sql`
  (schema), `01_signals.sql` (signal_strategies + history_signals,
  16 hash partitions each), `02_is_active.sql`, `03_signal_order_rank.sql`.
- Live tier: `live/live_signals/` — `analysis/evaluator.py` (the ONE
  generic breach check), `analysis/fetch/` (`_values.py`
  SIGNAL_VALUE_SOURCE map + resolve_value/resolve_threshold,
  `_sources.py` current-value fetchers, `_bars.py` as-of bar
  resolution, `_active.py` active-set fetchers on signal_strategies),
  `config/` (`tables.py` SIGNALS_TABLE, `runtime.py` intraday/daily
  sources, `breach.py` scales). Schema
  `database/sql/live/03_live_signals.sql`.

## Conventions for this area

- Every `__main__.py` starts with the cudf.pandas activation
  (`from _common.df_utils._activate import activate; activate()`)
  before the first pandas import, and configures logging via
  `_common.log_setup.setup_logging`.
- All DB reads land in cudf.pandas frames (::float8 casts at the SQL
  source, epoch→datetime64 at the boundary like
  analysis_forecasts.fetch.inputs). Frames stay native-dtype: the
  python-date conversions (dates_to_python / value_points /
  frame_records) are the sanctioned scalar materializations. The bar
  is zero `[cudf fallback]` lines outside those boundary helpers.
- New signal families = a new engine module + fetch loaders + slice
  constants + a SIGNAL_VALUE_SOURCE entry (if live-wired). Dispatch
  through the ENGINES registry — no if/else ladders. A family without
  strategies naturally has no ticks and no breaches.
- Testing: `--sec-type index` first (margin families: etf), then etf /
  stock. Verify by row counts per signal_type/side, the API tick
  (`/api/analysis/mov-ave-spread/forecast` in_signals) and the UI, not
  just by reading code.
- Verification: analysis_signals row counts per signal_type +
  is_active/signal_order stamps; live.live_signals breach rows; the
  Recent Movements ✓ ticks; the live page config menu.
