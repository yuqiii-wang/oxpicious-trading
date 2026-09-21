---
name: signals-live-vs-history
description: >
  How the oxpicious-trading repo differentiates HISTORY signals from LIVE
  signals: analysis_forecasts computes per-bucket forward profiles; the
  plain gate (sign-aligned blended mean reversal > 0.75% AND blended
  reverse_prob > 1%) turns gate-passing buckets into SIGNAL STRATEGIES
  (analysis_signals.signal_strategies — one row per bucket over its
  forecast period, with the live breach bar) whose month-owned trigger
  days are the HISTORY signals (analysis_signals.history_signals); live
  signals are ONLY breaches of the strategies' active bars recorded in
  live.live_signals. Use for ANY work touching analysis_signals,
  live_signals, the emit gate, signal thresholds, or signal families
  (mov_rsi / mov_std, then mov_pairs / mov_pairs_ema / px_vol
  / margin_ratio / high_low_streaks as engines land), even when the user
  does not mention signals explicitly.
---

# Live signals vs history signals

The repo has TWO signal tiers with a strict one-way dependency. Never blur
them: the live tier NEVER re-detects or re-computes anything — it only
breaches the bars the strategy tier already wrote.

```
analysis_forecasts (HISTORY study)
  forecast buckets per (code, stat_month snapshot M, config)
  trailing 5-year window (M − 5y, M]; forward profiles at
  next/5d/20d → forecast_results rows (the 60d horizon was retired
        │  2026-09-20)
        │
        │  MIXED row: the FIXED-weight blend of the three horizons
        │  (5d 0.65 / next 0.25 / 20d 0.10 — config
        │  MIXED_HORIZON_WEIGHTS, materialized on every bucket)
        ▼
analyze.analysis_signals (THE SIGNALS TIER — python -m analyze.analysis_signals)
  per family ENGINE (SignalEngine ABC in _dfengine.py; engines/mov_rsi.py,
  engines/mov_std.py; dispatched via the engines.ENGINES registry — never
  if/else). All computation is vectorized cudf.pandas; all frames are
  NATIVE-dtype only (no object columns, no datetime64[D] casts, no
  pd.Timestamp compares — they poison cudf frames). NO CASE/WHEN in any
  SQL: side is a STORED column, every conditional lives in the engines.
  Per (sec_type, stat_month), ONE transaction per month (writer.py):
    signal_strategies ← one row per bucket whose MIXED row passes
    THE GATE (the plain forecast-results rule, every family):
      dir_ave > 0.75%  (sign-aligned blended mean forward change:
                     top/upper → −ave_change, bottom/lower → +;
                     the sign flip happens in the engine)
      AND reverse_prob > 1%
    confidence = reverse_prob; signal_threshold in the VALUE'S OWN
    space (mov_rsi: the window's top/bottom-1% RSI percentile bar,
    recovered as rsi(d*) − trigger_excess(d*) at the bucket's
    WINDOW-END trigger; mov_std: the band level, price(d*) −
    trigger_excess(d*), price space). side stored (top/bottom/upper/
    lower), action sell/buy. PK includes side AND is_market_hyped.
    history_signals ← the strategy's trigger days INSIDE the snapshot
    month M only (one snapshot owns each date → no cross-month PK
    conflicts; dedup on bucket keys + date — the upstream arrays can
    repeat a date). Structural clone of live.live_signals; time
    15:00:00, is_day_close_trigger = TRUE, confidence = ROUND(100 ×
    reverse_prob).
  Emission slices (current build): mov_rsi pct = 1 (top/bottom) and
  mov_std MA/σ windows >= 20d at k >= 2.0σ (upper/lower), both sides,
  BOTH hype splits. The strategy parameter rides IN
  the sub_type as a literal suffix — rsi{W}_{pct}pct ("1pct") and
  std{W}_{k}std (k %g-formatted — "2", "2.5" — matching the API tick
  join's float8::text, plus the "std" literal) — so each (window,
  parameter, side) is its own strategy.
  Incremental: months present in analysis_forecasts' identities minus
  months already in signal_strategies, PLUS the newest REFRESH_MONTHS
  (4) re-emitted every run (long-horizon mixed legs complete late).
  --force purges the sec_type's family rows first; --metrics rsi,std
  scopes the families. Post-run: 02_is_active.sql (is_active = each
  (code, sec_type, type, sub_type)'s LATEST end_date — the live
  threshold set) + 03_signal_order_rank.sql (confidence DESC rank
  within (sec_type, end_date), nothing trimmed).
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
  SIGNAL_VALUE_SOURCE map in analysis/fetch.py (rsi / gap / close /
  spreads / px_t / margin_z). Missing current value ⇒ skipped, never
  invented. (The old day-close mirror --live writer is RETIRED —
  history_signals is the historical record; is_day_close_trigger =
  TRUE rows in live.live_signals are legacy.)
```

## The invariants (break none of them)

1. **mov_std's live bar is the DAY'S band, never the stored one.**
   Bollinger bands move daily — the live tier derives
   ma_{W} ± k·std_{W} fresh (fetch.resolve_threshold; the month
   replay: the day's own ma/σ) and the breach record pins the band it
   compared against. The strategy's stored signal_threshold is only
   the emission-time snapshot and is never compared. Only families
   with a genuinely static bar (mov_rsi's percentile) check against
   the stored threshold. (When both sides of one config breach the
   same day, the record keeps the STRONGER breach — the live PK has
   no side.)
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
   strategies store the [0,1] reverse_prob; live rows ROUND(100 ×).
5. **Months are write-once per run.** A month's strategy + history
   rows are written in ONE transaction (delete month scope + COPY both
   tables), only when the month is missing or inside the refresh
   window (or after --force).
6. **After every run:** refresh is_active + re-rank signal_order
   (02/03 SQL, executed by the pipeline with $1 = sec_type).
7. **No UI component changes for signals.** The Recent Movements
   forecast table's ✓ tick is the API's in_signals EXISTS into
   signal_strategies (side = side, end_date = stat_month,
   is_market_hyped matched to the forecast row's own hype split —
   a ● row ticks iff its hyped strategy registered)
   — see data_viz/api/services/analysis/analysis-forecasts.ts
   inSignals(); the live page's config menu reads signal_strategies
   WHERE is_active. React components render whatever the API returns.

## Where things live

- Forecast buckets + mixed row: `analyze/analysis_forecasts/`
  (weights: `config/horizons.py`; reverse_prob at the FIXED 1% bar:
  `database/sql/analysis/analysis_forecasts/01_forecast_results.sql`).
- Strategy/history pipeline: `analyze/analysis_signals/` —
  `__main__.py` (cudf activate, --sec-type/--metrics/--months/--force),
  `config.py` (tables, gate constants 0.01/0.01, slices RSI_PCT / STD_MA_WINDOW_MIN / STD_K_MIN, REFRESH_MONTHS),
  `_dfengine.py` (SignalEngine ABC + the concrete gate/detect/
  month_triggers/value_points/frame_records machinery),
  `engines/` (mov_rsi.py, mov_std.py — one family per module),
  `fetch/` (CASE-free SQL loaders; `fetch/_sql.py` templates),
  `run/` (months resolution + per-sec_type pipeline),
  `writer.py` (month transaction + force purge).
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
