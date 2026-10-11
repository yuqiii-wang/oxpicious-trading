---
name: signals-live-vs-history
description: >
  How the oxpicious-trading repo differentiates HISTORY signals from LIVE
  signals: analysis_signals computes its OWN RSI percentile (rsi pct)
  buckets in-engine (the analysis_forecasts layer was REMOVED 2026-10-05)
  over analysis.mov_ave_rsi + prices; the plain gate (sign-aligned blended
  mean reversal > its side's bar (buy 0.75% / sell 0%) at the per-side
  sample floor; the delay ladder /
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
1. The forecast layer is GONE — `analysis/analysis_forecasts` (package,
   SQL files, UI Forecast table + API endpoints), the
   `analysis_forecasts` schema (DROP CASCADE,
   temp_scripts/_drop_forecast_schema.py) and the ohlcv boost/selection
   derived populations. The signals tier computes its own rsi-pct
   buckets in-engine; strategies are the plain rsi-pct population only.
   (2026-10-06: the boost returned as a MARKER, not a population —
   see the ohlcv-boost block below.)
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
  owned by analysis.mov_ave_spread's rsi step) + prices (the
  mov_ave_spread price convention) + stats.market_regimes (day labels)
  + the day-level tail-episode flags is_typical_{W}days + episode
  sizes typical_ep_size_{W}days (SAME table; THE re-entry semantics
  live at that layer — mov_ave_spread steps/rsi_typical.py computes
  them on every ranks rebuild from mov_ave_rsi_ranks' full-history
  ranks: NULL out-of-band / TRUE the TYPICAL pass's days —
  SIDE-MIRRORED (2026-10-07 isolated split + same-day sell mirror:
  the amplified tail event differs by side; equity asymmetry — sharp
  falls revert, sharp rises continue): BUY = the ISOLATED first
  touches (an episode starting > RSI_TYPICAL_ISOLATED_DAYS (20)
  trading days after the previous same-side band day — the
  capitulation; buy-side rebounds rise monotonically with absence
  length, -0.55% blended at gap 7-10 vs +2.36% at 31-60), SELL = the
  RE-TESTS (a fresh episode whose previous overbought episode ended
  within the 20-day window — the double top, the amplified sell;
  isolated overbought breakouts after calm are momentum ignitions
  where the sell backfires) / FALSE the re-entry passes' days (flicker
  re-qualifications within K=5 exit days, buy-side continuation
  starts, sell-side isolated breakouts + their continuations — the
  noisy population) at the 25% band; the two-state flags cover every
  in-band day (continuations inherit their pass's registration flag —
  the UI's typical/re-entry populations draw the deep days); both
  signal tiers' population rides the EPISODE SIZES — non-NULL =
  in-band, and the deep days the percentile bars select are exactly
  the gap-1 continuations)
        │
        ▼  in-engine, per (sec_type, snapshot), on cudf.pandas frames
analysis.analysis_signals (THE SIGNALS TIER — python -m analysis.analysis_signals)
  TWO families (SignalEngine ABC in engines/_base.py; one package each
  under engines/signal_families/; dispatched via the
  engines.ENGINES registry — never if/else): mov_rsi (the RSI
  percentile family) and boll_std (2026-10-07: the fixed-σ Bollinger
  band-breach family — IN-ENGINE values since 2026-10-10, signal_type
  'bollinger' in the DB: z = (price − SMA_W)/σ_W per W ∈ {5, 20, 60}
  computed on the fetched price frame by boll_std/_compute (the classic
  trailing SMA + population σ + the episode layer; the grid is the
  parent's MA_WINDOWS subset — the 10-day band was replaced by the
  5-day and the analysis.mov_ave_boll value-source table DROPPED with
  the mov_ave_spread boll step); NO percentile logic: the bars are
  the FIXED ±k constants, k ∈ {2, 2.5, 3}σ (sub_type boll{W}_{k}std,
  2σ the classic default — the rsi14 analog), the breach excess depth
  |z| − k in σ units replaces the pct axis, and the curve tier reads
  s = |z| DIRECTLY as the Gaussian depth score (the Gaussian tail of
  |z| gives Φ^-1(1-q) == |z|); the episode layer rides the SAME frame
  at the FIXED 0.6745σ band (the Gaussian 25% tail — the RSI rank-band
  analog; z is trailing so the flags never shift retroactively, no
  ranks table, computed in the same in-engine pass through the shared
  SIDE-MIRRORED classifier mov_ave_spread steps/rsi_typical.py);
  the boll curve's band_curve bins the QUARTER-σ stride (keys 0.75..3.5,
  edges aligned with the 2/2.5/3σ bars) and tail_bars carries the σ
  bars themselves; the fit core is SHARED (engines/_curve_fit.py —
  moved verbatim from mov_rsi/curve; fit_payload takes the family
  grid, boll's (0.66, 3.10) extends past its 3σ bar; the SIDE-AWARE
  fit since 2026-10-11: the SELL side keeps the rank-gated isotonic
  (pooled_iso — its measured response IS rank-monotone), the BUY side
  fits deep_arm_iso — the strict increasing PAVA (loosely monotone
  plateau → deep-rise shape) gated by the PRE-REGISTERED ≥
  BOLL_CURVE_DEEP_ARM_S (2.5σ) deep-arm vs-rest contrast at
  CURVE_SLOPE_MIN_T (day-divisor SE, permutation-null calibrated ~5%
  false-draw) — the global rank correlation read ~0-to-negative on the
  measured U-shaped buy response and stored the flat payload
  everywhere; fit_t = the arm contrast t on buy rows); the shared
  BucketContext machinery is subclassed by boll_std/_buckets.py with
  fixed-bar detection; each engine resolves its OWN rolling-latest
  key via the latest_data_date hook on SnapshotSelection — boll's is
  the sec_type's latest PRICE-data date). All
  computation is
  vectorized cudf.pandas; all frames are NATIVE-dtype only (no object
  columns, no datetime64[D] casts, no pd.Timestamp compares — they
  poison cudf frames; typed EMPTY frames matter too — object-dtype
  empties turn every numeric op into a [cudf fallback]). NO CASE/WHEN
  in any SQL: side is a STORED column, every conditional lives in the
  engine. The label columns store their stats.label_dim SMALLINT codes
  end-to-end (side / action / regime_state; since 2026-10-10 also
  sec_type 0=etf/1=index/2=stock and signal_type 0=mov_rsi/1=bollinger
  — the row dicts carry codes from the emit boundary, the SQL params
  carry codes; the live tier decodes at its active-set fetch, the API
  at the SQL boundary via labelDecodeSql).
  Snapshot grid (config/grid.py — ported from the retired forecast
  config): the last 10 completed year-ends PLUS the ROLLING LATEST
  snapshot keyed at the sec_type's LATEST AVAILABLE RSI DATA DATE
  (analysis.mov_ave_rsi max(date)), each a trailing 10-year window
  (S − 10y, S].
  Per run (engines/signal_families/mov_rsi/_buckets.py): fetch the
  input frame ONCE (price + rsi_{W} columns + first dates + regime
  labels, bounded to the earliest target window start, no upper bound
  — forward changes need post-snapshot prices; fetch/inputs.py), then
  per snapshot: the side's OWN percentile bar per (code, rsi_window,
  pct) — bottom = the window's q(p/100), top = q(1−p/100)
  interpolated quantile (the classic percentile construction the
  {pct}pct sub_type names; RESTORED 2026-10-07 — the 2026-10-05
  symmetric-in-space median-centered band landed one or both bars
  outside a bounded high-variance indicator's reach (an RSI(3) top
  bar past 100) and collapsed bucket occurrence below the sample
  floors: 35,868 → 3,370 anchor-carrying buckets on the index rolling
  snapshot; side fairness lives at the gate layer now — per-side gate
  bars + sample floors; _buckets._tail_quantile_bars) → nested
  per-side tail
  tests (top/bottom pct% {1,2,3}) → streak-merge (2026-10-07 TIER
  ALIGNMENT: qualifying runs on each code's OWN traded-day ordinals
  with the analysis layer's K=5 re-entry exit absorbed — ONE signal
  per EPISODE, triggered at its FIRST day, delay 0; the old
  union-calendar axis carried other codes' holiday dates, split runs
  at every closure the code did not trade and manufactured phantom
  re-opening triggers, and the 0-day debounce counted bar re-crossing
  wiggles as fresh occurrences — occurrence was inflated vs the curve
  tier's episode count and thin-regime buckets passed gates the
  curve floor rightly dropped) → per-bucket blended stats (the FIXED
  5d 0.50 / 3d 0.15 / 10d 0.15 / next 0.10 / 20d 0.10 mix over the triggers' valid
  anchors, renormalized
  over the horizons with stats; occurrence = the MIN positive leg
  count = the window's EPISODE count — the population the curve
  tier's mu is measured over) + the bucket's
  window-end trigger (bar recovery).
  THE GATE (engines/_frame.py detect): the sign-aligned blended mean
  forward change (dir_ave — top negated) > the side's bar
  (buy GATE_DIR_AVE_MIN 0.75% / sell GATE_DIR_AVE_MIN_SELL 0) on
  occurrence_count >= 4 buys / 3 sells
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
    side's own percentile bar — bottom q(p/100) / top q(1−p/100),
    recovered as
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
  SIGNAL_VALUE_SOURCE map (analysis/fetch/_values.py: mov_rsi → "rsi"
  (latest rsi_{W}days from analysis.mov_ave_rsi), bollinger → "boll"
  (the latest close's σ-depth z computed ON THE FLY from the code's
  last closes — 2026-10-10, no source table; sub_type
  boll{W}_{k}std, window = the first integer run); the
  bar is the STORED percentile threshold). Missing current value ⇒
  skipped, never invented. is_day_close_trigger = TRUE rows are the
  on-demand --date daily-close fallback (the old day-close mirror
  writer is RETIRED — history_signals is the historical record).
  (The confidence_boost / confidence_selection cohort add-on columns
  went with the forecast layer — both stay in the schema, unwritten.)

THE OHLCV BOOST (2026-10-06 — population VARIANTS, not cohorts): the
GENERAL formula lives in `engines/ohlcv_boost/` (family-agnostic;
score_bp = β_ohlc·ê_ohlc + β_vol·ê_vol + β_rsi·ê_rsi + γ·ê_ohlc·ê_vol/
100 over the day-0 anchor's stored axes — the ohlc_gaps `pattern`
label, the price_vs_amt (vol_state, px_speed) amount/price-speed cell,
and the tail depth past the bar — through per-side measured ladders in
`_configs.py`, reusing the retired 2026-09-27/28 fused-score
calibration verbatim). The engine fetches the axes (fetch/inputs.py
LEFT JOINs the two stores; pattern maps to an int8 code at the
boundary) and scores the day-0 anchors in `_apply_boost`. The flag is
a PK POPULATION VARIANT on history + curves — every event/cell emits
TWICE: FALSE = the plain record, TRUE = the BOOST twin (the SAME
trigger days, contributions weight-blended by w = max(1 + score/K,
floor): history confidence w-scaled, curve stats weight-blended incl.
pooled slopes — 06_boost_population_pk.sql). signal_thresholds keeps
the flag as the bucket VERDICT marker (non-PK, one row). The UI
Recent-Movements curve overlay's OHLCV none/boost toggle picks the
curve variant; the history service reads the PLAIN population only.
Never a gate, never part of is_active / the live threshold set; no
@boost child rows, no confidence_boost denormalization — the retired
cohort machinery stays retired.
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

- In-engine bucket computation: `analysis/analysis_signals/engines/
  signal_families/mov_rsi/_buckets.py` (percentile bars, streak-merge
  delay-0 triggers, forward changes, the blended per-bucket stats +
  typed empty frames).
- Strategy/history pipeline: `analysis/analysis_signals/` — `__main__.py`
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
  `engines/ohlcv_boost/` (the GENERAL boost formula — _score.py
  anchor_scores/bucket_flags, _config.py the config dataclass,
  _configs.py the per-side measured ladders, _axes.py the axis code
  vocab; imported by the rsi family, NOT an ENGINES registry entry),
  `engines/signal_families/mov_rsi/` (_engine identity + phases,
  _strategies, _history — mov_rsi is the only family),
  `fetch/` (inputs.py — the input frame + universe/first-dates/latest
  loaders; regimes.py — the daily market-regime labels),
  `run/pipeline.py` (per-sec_type pipeline + post-run refresh),
  `run/rank.py` (the GPU signal_order rank).
- Price/amount source maps (PRICE_SOURCE / AMT_SOURCE /
  OHL_EST_FILTER) live in `analysis/mov_ave_spread/config/
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
  SIGNAL_VALUE_SOURCE map (mov_rsi → rsi, bollinger → boll) +
  resolve_value/resolve_threshold,
  `_sources.py` the current-RSI fetcher (mov_ave_rsi) + the boll
  z-from-prices fetcher (the mov_ave_spread PRICE_SOURCE convention;
  windows single-sourced from the signals config's BOLL_WINDOWS),
  `_bars.py`
  as-of bar resolution,
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
  `CUDF_FALLBACK_TRACE=1 python -m analysis.analysis_signals
  --sec-type index` pinpoints any regressor's caller.
- New signal families = a new engine package + slice constants (+ a
  SIGNAL_VALUE_SOURCE entry if live-wired). Dispatch through the
  ENGINES registry — no if/else ladders. A family without strategies
  naturally has no breaches.
- Testing: `--sec-type index --snapshots 1` first (the rolling
  snapshot — fast), full grid for real runs. Verify by row counts per
  signal_type/side, `SELECT DISTINCT signal_delay_days` (= 0),
  the is_ohlcv_boosted distribution per table (history + curves ≈ half
  TRUE — the boost twins; thresholds a small verdict TRUE mix on
  index/etf), the trading-signals API + UI, not just by reading code.
- Verification: analysis_signals row counts per sec_type +
  is_active/signal_order stamps; live.live_signals breach rows; the
  live page config menu + row rendering.
