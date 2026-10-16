---
name: signals-live-vs-history
description: >
  How the oxpicious-trading repo differentiates HISTORY signals from
  LIVE signals in the RULES era (2026-10-14 redesign, consolidated
  2026-10-15, trend rebuild 2026-10-16): analysis.analysis_signals
  measures SEVEN detection families (mov_rsi / bollinger / ma_cross /
  options_wall / options_vix / momentum / rf_break) as SELF-ADAPTIVE per-(code,
  sub signal, trend, regime, action) RULE CELLS written to
  analysis_signals.signal_rules (rule_params = the prepared bar +
  measured evidence + gate + confidence scale), populates
  analysis_signals.history_signals by REPLAYING those cells (episode
  FIRST days, delay 0), and stamps the family-agnostic
  analysis.trend_days store (the consolidated analysis.trends
  component's RSI-re-entry classifier). The live tier NEVER detects:
  it breaches the rule cells' bars (mov_rsi / bollinger live-wired)
  under a day-context (trend, regime) match. Use for ANY work touching
  analysis_signals, signal_rules, history_signals, trend_days,
  live.live_signals, the emit gate, rule parameters, or the six
  families' engines — even when the user does not mention signals
  explicitly.
---

# Live signals vs history signals (rules era)

The repo has TWO signal tiers with a strict one-way dependency:

- **analysis.analysis_signals** — the strategy tier: measures rule
  cells on each code's own history, writes `signal_rules` (the cells)
  + `history_signals` (the cells' triggers replayed) + `trend_days`
  (the per-(code, date) trend classification every cell splits on).
- **live.live_signals** — the live tier: NEVER re-detects or
  re-computes anything. It reads the ACTIVE rule cells and records
  breaches of their bars under the day-context match.

Timeline of the era: 2026-10-14 the threshold/curve tables were
dropped and the rule-cell tier landed; 2026-10-15 the OOP
consolidation (rule_base ABC + evaluation/boost/trends siblings), the
regime-join bug fix, the rules-era live rewire, the momentum family,
and the consolidated `analysis.trends` classifier (RSI(10) re-entry
zones + Kaufman guard + deep-anchor turn); 2026-10-16 the trend-update
force rebuild (216 cells / 1,458 events at index scope) and the rsi6
entry-window addition. The financial semantics of the six families
live in the sibling skill `signals-biz-logic`; this file stays on
architecture.

## The pipeline (`python -m analysis.analysis_signals`)

CLI: `--force` (purge the sec_type's rows in both tables and
re-emit), `--sec-type {index,etf,stock}` (default index),
`--families` (comma list; `boll_std` aliases `bollinger`),
`--codes` (default the 10 study indices; ignored for the options
families — their universe resolves per sec_type from
stats.options_terms, options_wall dropping the SSE ETF underlyings
whose chain OI is an accumulated-volume estimator), `--analyze`,
`--no-boost` (the ohlcv-boost A/B switch; default = each family's
OHLCV_BOOST class flag).

`run.py` orchestrates per family:

- **signal_rules is CURRENT-STATE**: the family's scope
  (sec_type, signal_type) is DELETEd then freshly inserted every run —
  cells that stopped passing the gate must not linger.
- **history_signals**: force purges the scope then COPYs;
  incremental `copy_or_upsert_split_async` past the table's max date
  (idempotent re-emission).
- **trend_days**: the first family run whose frame outdates the store
  writes the prepared frame's per-(code, date) trend rows, per-code
  horizon check (a universe growth writes the newcomer even when the
  veterans are fresh); purge + COPY per stale code.
- **Incremental skip**: a family is skipped when every requested
  code's history already reaches the family's VALUE-SOURCE horizon
  (per-family max-date SQL in `_source_horizon_sql`) AND the rules
  scope is non-empty (its max created_at IS the build time). NOTE:
  the skip never detects a TREND-CLASSIFIER change — after editing
  `analysis.trends` you must `--force` to re-stamp cells and history.
- Post-`--analyze`: ANALYZE both tables.

## The RuleEntry architecture

- `engines/rule_base.py` — the ABC every family engine implements.
  Class flags: `FAMILY` / `PACKAGE` / `SUB_TYPES` (identity),
  `REGIME_FREE` (stamp pooled regime 4, no split), `USES_TREND_SPLIT`
  (False → pooled trend 4), `TREND_AWARE_REENTRY` (the K-absorb
  merges only SAME-trend re-entries; a re-entry in a different trend
  is a FRESH episode in that trend's cell — mov_rsi only), and
  `OHLCV_BOOST` (the trust-score collaborator, disabled by default).
  Hooks: `fetch_frame` (delegates to the fetch/ layer) and `run`
  (builds per-(sub_type, action) CANDIDATE trigger masks, then calls
  `run_cells`).
- `prepare_frame` = fetch → `attach_trend` (the code's own trend
  classification) → the regime join (`stats.market_regimes`, missing
  days default calm 0 — the fetch already returns datetime64[us]
  dates; NEVER re-convert through epoch_col_to_dt64, that was the
  2026-10-15 all-calm degenerate-split bug) → the fixed forward blend
  (`_blend` = 0.10/0.15/0.50/0.15/0.10 over 0/3/5/10/20d;
  `_blend_ok` marks rows whose full 20d future is realized — only
  those are measured, the newest triggers still replay with their
  cell's prepared confidence).
- `run_cells` — the shared SELF-ADAPTIVE cell loop: per candidate,
  episode FIRST days (`EpisodeDetector`, K-absorb on the code's own
  traded rows, trend-aware when the family opts in) → per-cell
  measurement (`cell_stats`: n episodes, sign-aligned dir_ave + std
  + t-stat, hit) → gate (`CellGate`: buy ≥ 0.25% @ n ≥ 3; sell ≥ 0 @
  n ≥ 2 — the equity asymmetry IS the design; LOOSENED 2026-10-16
  from 0.75% @ 4 / 0 @ 3, the moderate user-directed step) → per cell
  the WINNING candidate (gate-passing, best dir_ave; ties → the
  first/widest bar) → the POOLED EVIDENCE TIER (2026-10-16: the
  (sub_type, action) pooled population of the winning cells'
  measurable triggers, judged by pooled t-stat ≥ 1 @ n ≥ 10 +
  expanding-year sign majority — a BUY side whose pooled evidence
  FAILS is demoted to measured-out: no trading rules rows, no
  history replay that run, the starved wait replay untouched; SELL
  records the block ungated, exit timing) → the cell's signal_rules
  row + history rows for the winning cells' triggers. `rule_params`
  carry: axis, episode_merge_days, reentry_trend_split, gate digest,
  measure (n/dir_ave_pct/dir_std_pct/t_stat/hit_rate), `pooled`
  (n/t/hit + the per-horizon decay curve + the expanding-year legs,
  stamped on trading rows), confidence (base_pct = the measured
  dir_ave, depth_factor, cap 2), plus the candidate's own
  bar_def/params. `ungated=True` marks
  observational twins (options_wall's VRP-fail `_rej`, action
  wait 2 — never a trading row). STARVED cells (episodes but
  below the sample floor, 2026-10-16; label RENAMED no_action →
  wait the same day) are the WAIT population: their history
  triggers ALWAYS replay as wait events (the extreme-event
  monotone guarantee — a deepest-claim breach never vanishes
  because its cell is under-floored) and upsert to the real
  action once the cell graduates (action is no history PK
  member); the signal_rules WAIT row is SELECTIVE — only cells
  blocked SOLELY by the floor (n = floor−1 with dir_ave already
  at the side's bar: one confirming episode from trading; gate
  .starved + gate.intended_action; one row per rules PK, twin
  sides dedup). Measured-out cells (n ≥ floor, dir_ave below the
  bar) still emit NOTHING — that is the gate rejecting on
  evidence. Wait rows never feed the ohlcv-boost derivation.
- `engines/evaluation.py` — `CellGate` (the plain gate +
  depth-scaled confidence: base_pct × (1 + factor × min(depth, 2))),
  `cell_stats` (n / dir_ave / dir_std / hit) and `pooled_evidence`
  (the TWO-TIER gate's statistical layer: the (sub_type, action)
  pooled n / dir_ave / t-stat / hit, the per-horizon decay curve,
  the expanding-year legs with their train means — a HOST-numpy
  boundary over run_cells' staged facts). The gate constants come
  from `config.GATE_DIR_AVE_MIN` / `SAMPLE_FLOOR`; the pooled tier's
  from `config.POOLED_*` (bar 0.25%, t ≥ 1, floor 10, year n ≥ 5).
- `engines/boost.py` — `OhlcvBoost`, the merged trust-score
  collaborator: collects each fired row's day-0 anatomy facts (OHLC
  pattern + (vol_state, px_speed) cell + tail depth + the cell keys +
  the blend_ok flag), derives its coefficients SELF-ADAPTIVELY per run
  from that anchor population (host-pandas frame, one scipy boundary;
  ship gates G1 improv / G2 expanding-year OOS / G3 top-quintile), and
  stamps the per-cell TRUST-WEIGHTED CONFIDENCE BASE onto the rule
  rows — `signal_rules.ohlcv_boost_conf` = sum(w·y)/sum(w) over the
  cell's MEASURABLE triggers, w = max(1 + score/K, 0.5), NULL where
  the side boosted nothing. NEVER a gate; history carries ONE
  population per event (the boost-twin rows were retired 2026-10-16 —
  only confidence is tuned, and it lives on the rule cell). Opt-ins:
  RsiTailEntry / BollBandEntry / MomentumTailEntry / RoofFloorEntry.

## The consolidated trends component (`analysis/trends/`)

Package-level, imported by the signals tier AND by
mov_ave_spread's rsi_typical step (so it cannot live under
engines/). Family-agnostic:

- `trend_state.attach_trend` stamps each (code, date) row an int8
  `trend` (0 top / 1 bottom / 2 rise / 3 fall / 5 unknown; 4 'all' is
  the POOLED stamp of sparse families' rule rows, never a day state):
  - **top/bottom** = the price_vs_ma255 gap beyond its own TRAILING
    255d q90/q10 (shifted, no look-ahead) with a matching 20d MA
    slope — the overextended extremes.
  - **rise/fall** = RSI RE-ENTRY episodes: Wilder RSI(TREND_RSI_WINDOW
    = 10) vs the FIXED 50 ± TREND_RSI_ZONE_SPREAD (12.5) momentum
    zones (fixed, not per-code percentiles — RSI is bounded and its
    trailing quantiles collapse onto hair-trigger or unreachable
    edges), alive through ≤ REENTRY_EXIT_DAYS (5) out-of-band traded
    rows, the LATEST band touch owning the day, AND the EFFICIENCY
    GUARD agreeing — the sign of the Kaufman net change over
    TREND_GUARD_WINDOW (10) rows (the 2026-10-15 measured replacement
    of the MA20-slope guard).
  - **the DEEP-ANCHOR TURN**: a deep RSI touch (≤ 30 / ≥ 70) opens a
    40-row window in which the midline re-cross with the guard flips
    the trend — the post-crash-rebound fix (000001's Apr-2020
    recovery flipped 2020-04-07; the plain zones and the retired
    MA60 rule never rose that April).
  - unknown = neither episode alive (mid-band, dissolved momentum,
    warmup head). The RSI is computed INLINE (the mov_ave_spread
    rsi contract — Wilder ewm alpha=1/W through the fused CUDA
    kernel) because the options/ma_cross frames carry no RSI columns.
  - Consumers: every family's prepare_frame, the pipeline's
    trend_days write (the chart's trend band shading via the
    trend-spans API + the live tier's day-context match both read the
    STORE, not the classifier).
- `episodes.py` — the shared episode anatomy: REENTRY_EXIT_DAYS = 5
  (> K out-of-band TRADED rows ends an episode; 1..K re-qualifies it
  as the same episode), TYPICAL_ISOLATED_DAYS = 5, and
  `EpisodeDetector.triggers(df, mask, trend=...)` — breach-run FIRST
  days, whole-frame grouped ops, optional trend-aware splitting (the
  (pos, trend) pair packed pos*8 + trend + 1 in one cummax).

## The seven families (engines/signal_families/, registry in order)

| family | entries | sub types | cells | K | live |
|---|---|---|---|---|---|
| mov_rsi | RsiTailEntry | rsi{14,10,6}_{1,2,3}pct buy/sell | code×trend×regime, trend-aware absorb | 5 | rsi |
| bollinger | BollBandEntry | boll20_{2,2.5,3}std buy/sell (window 20 ONLY) | code×trend×regime | 5 | boll |
| ma_cross | MaCrossReclaimEntry (ma20/60_cross buy, cross-excess {1,2,3}%), MaRetestEntry (ma20_retest sell), MaGdepthEntry (ma120/255_gap buy ONLY, {3,5,8}%), MaBreadthEntry (ma_breadth_thrust buy, ma_9to1_up buy / ma_9to1_dn sell — the MARKET-LEVEL breadth rules over the sec_type's own universe, joined per date onto every code) | kind×window grids | code×trend (regime 4) | 5 | no |
| options_wall | WallBreachEntry | ow_put_025pct (buy, VRP-gated), ow_put_025pct_rej (wait twin), ow_call_025pct (sell) | code (trend 4, regime 4) | 0 | no |
| options_vix | OvixSpikeEntry (ovix_spike, ovix_spike_hivol), OvixPanicPeakEntry (ovix_panic_peak), OvixAtrBreakEntry (ovix_atr_break buy — the spot day-return in close-to-close ATR units past the self-adaptive {2,2.5,3} bar while slope5 fires; the 2026-10-15 low-base repricing class), OvixFearUnwindEntry (ovix_fear_unwind sell) | multi-axis detects | code (trend 4, regime 4) | 8 | no |
| momentum | MomentumTailEntry | mom{5,20,short,mid,long}_{1,2,3}pct buy/sell (log-return ln(1+momentum) of analysis.mov_ave_momentum; the vol-adaptive lookbacks + fixed 5/20d anchors) | code×trend×regime, plain absorb | 5 | no |
| rf_break | RoofFloorEntry | rf{60,120,255,500}_roof buy / rf{60,120,255,500}_floor sell + the rf{W}_med_{roof,floor} twins — the close vs the PRIOR day's roof/floor level per (method, window): donchian (method 1, the window extreme) and median (method 5, the five-method consensus) from analysis.mov_ave_spreads_roof_floor_estimates; a STRUCTURAL bar, not a percentile tail; NO cross-window carving (each window its own population) | code×trend (regime 4) | 5 | no |

Nested widest-first carving everywhere: the deeper tail claims first,
a deep breach counts once at its deepest claim (3σ ⊃ 2.5σ ⊃ 2σ;
3% ⊃ 2% ⊃ 1%). mov_rsi / momentum bars are PER-CODE ADAPTIVE trailing
quantiles (window 2550d, min 255, shifted one row — never fixed
levels); bollinger bars are FIXED ±kσ (population σ, ddof=0). The
confidence depth normalizer is family-local (RSI points / σ / axis
sigma / gap pct). ma_cross's is_typical split = the uninterrupted-run
whipsaw split; trigger-time-observable state only.

## The live tier (live/live_signals, intraday + as-of)

A live signal IS a breach of an active `signal_rules` CELL's bar —
never a detection, no per-family logic (the rules-era rewire of
2026-10-15; the 67k threshold-era rows were truncated then):

- The active set = the LIVE-WIRED families' cells
  (`SIGNAL_VALUE_SOURCE` in `analysis/fetch/_values.py`: mov_rsi →
  "rsi", bollinger → "boll" — the boll z computed on the fly from the
  code's last closes, no source table). A family without an entry is
  not live. wait rows are EXCLUDED from the set (the VRP twin +
  the starved cells since 2026-10-16) — not-trading-yet records are
  never a live bar.
- Each cell carries its own action, bar_def (resolved per check:
  bollinger's fixed ±kσ constant; mov_rsi's per-code ADAPTIVE
  trailing-2550d percentile tail recomputed at check time over the
  rows strictly before the value anchor — bit-parity verified against
  history_signals.signal_reference) and confidence scale (the history
  writer's identical formula).
- THE DAY-CONTEXT MATCH is the gate: only the cell whose (trend,
  regime) equals the day's own trend (`analysis.trend_days`) and
  regime (stats.market_regimes, calm default) may fire — the same
  population the cell measured.
- The evaluator compares sell value > bar / buy value < bar (the
  row's OWN action decides direction; signal_excess = value − bar);
  missing value / underivable bar / no trend context ⇒ skipped,
  never invented. mov_rsi is a STATE family — it keeps flagging
  while the breach persists.
- AS-OF MODE (--date): the last intraday bar on D when one exists
  (is_day_close_trigger FALSE), else the official daily close
  (15:00, TRUE). Live rows UNION into the history endpoint; the
  history panel shows them with engine-identical bars.

## The invariants (break none of them)

1. One signal per EPISODE, first day, delay 0 — the K-absorb runs on
   the code's OWN traded days, never a union calendar.
2. The buckets + the gate are ENGINE logic in cudf, NOT SQL. No
   CASE/WHEN anywhere in the signals SQL; side/action/trend/regime
   are stored SMALLINT label codes (static.label_dim, mirrored by
   _common.labels + data_viz label-codes.ts) end-to-end; the live
   tier and the API decode at their boundaries.
3. A cell's rule_params are SELF-ADAPTIVE per PK — the same sub
   signal measures different bars per (trend, regime); identical bars
   across all trends of one sub signal would be a red flag.
4. signal_rules is wiped + reinserted per run (current-state);
   history is append/upsert; trend_days is per-code horizon-gated.
5. The live tier NEVER writes a bar the strategy tier didn't stamp.
6. Sparse options families POOL at the gate (per-code cells, trend 4
   / regime 4); the VRP-fail twin is wait, never live.
7. The boost is a TRUST-WEIGHTED MEASUREMENT, never a gate: it stamps
   the per-cell `ohlcv_boost_conf` (NULL where the side boosted
   nothing) and never touches the history population — the plain rows
   are the population of record.
8. STARVED cells RECORD (2026-10-16, tightened + renamed to WAIT the
   same day): a cell with episodes below the sample floor replays
   ALL its triggers into history as wait events, graduating to the
   real action in place once the gate passes — an extreme event's
   deepest claim never vanishes. The signal_rules wait row is
   SELECTIVE (floor-blocked imminent cells only, n = floor−1 at the
   bar). Measured-out cells still emit nothing, and wait rows never
   fire live.
9. After any run that wrote: ANALYZE the tables; verify by row counts
   per signal_type/sub_type, the trading-signals API, and the UI.

## Where things live

- Config (grids, gates, floors, blend, K): `analysis/
  analysis_signals/config/__init__.py`
- Engines: `engines/rule_base.py` (ABC + run_cells),
  `engines/evaluation.py`, `engines/boost.py`, and one package per
  family under `engines/signal_families/` (+ the registry in
  `engines/signal_families/__init__.py`)
- Trends/episodes: `analysis/trends/` (trend_state, episodes, the rsi
  episode-days module mov_ave_spread's rsi_typical step consumes)
- Fetch: `fetch/inputs/` (one module per family + _shared: universe
  resolution, first dates, the finalize step — epoch float8 →
  datetime64[us] + boost axes as int8 codes)
- Pipeline: `run.py` + `__main__.py`; regimes: `fetch/regimes.py`
- Tables: `database/sql/analysis/analysis_signals/` — 00_schema.sql,
  01_signal_rules.sql, 02_history_signals.sql (headers/COMMENTs are
  the tables' documentation; keep them current). trend_days:
  `database/sql/analysis/trends/`
- Live: `live/live_signals/` — `analysis/evaluator.py` (the ONE
  generic breach check), `analysis/fetch/` (_values the
  SIGNAL_VALUE_SOURCE map, _sources value fetchers, _bars as-of bar
  resolution, _active the active-set fetchers), `config/` (tables,
  runtime sources, breach scales)
- Studies/proposals: `docs/sub_rules_proposal_2026-10-16.md` (the
  measured graduate/reject digest), `temp_scripts/
  subrule_candidates_study.py` (the measurement harness — reuse it
  for any new candidate)

## Conventions

- Every `__main__.py` starts with the cudf activation before any
  pandas import; frames stay native-dtype (typed empties included);
  the bar is ZERO `[cudf fallback]` lines outside the sanctioned
  boundary helpers (`CUDF_FALLBACK_TRACE=1 ... --sec-type index`
  pinpoints regressors).
- Whole-frame grouped ops only; python loops only over structural
  iterables (candidates, sub types, the small measured-cell frames,
  the row materialization).
- New family = new engine package + registry entry (+ label_dim rows
  in all three mirrors + a SIGNAL_VALUE_SOURCE entry if live-wired);
  dispatch through the registry, never if/else.
- Study scope: the 10 study indices only (config.STUDY_INDEX_CODES);
  the options families' universe is data-driven (stats.options_terms).
- Testing: `--sec-type index --families <one>` first, `--force` for
  real rebuilds; verify row counts per signal_type/sub_type, the
  rules + history API arrays, and the UI panels.

## Architecture history (what died, and why — keep it that way)

- 2026-10-05: the forecast layer (analysis_forecasts package, schema,
  UI) was dropped; the signals tier computes its own populations.
- 2026-10-07..11: snapshot-grid/threshold-era machinery — episode
  alignment on own traded days, side fairness at the gate layer,
  in-engine boll values, the shared episode classifier.
- 2026-10-14: signal_thresholds + signal_curves DROPPED (bollinger's
  monotonicity was weak, ma_cross's negative; the fitted-curve claims
  replaced by per-cell measured evidence); the rule-cell tier landed
  (signal_rules + history_signals).
- 2026-10-15: OOP consolidation (rule_base/evaluation/boost/trends),
  the regime-join double-conversion fix, the rules-era live rewire,
  the momentum family, the RSI-re-entry trend classifier.
- 2026-10-16: trend-update force rebuild; RSI_ENTRY_WINDOWS (14,10)
  → (14,10,6) after the rules-era reproduction (mov_rsi 37 cells /
  167 events at index scope). LATER the same day, the event-study
  additions: the rf_break family (the 7th), the ovix_atr_break leg,
  the ma_cross breadth rules (thrust / 9:1), and the ohlcv boost's
  TURNOVER-CLIMAX 4th axis (bollinger + momentum + rf_break opt in —
  bollinger's top side ships, +158 twins at index scope). LAST, the
  TWINS RETIREMENT: the boost's only product is confidence, so the
  twin history rows were dropped for `signal_rules.ohlcv_boost_conf`
  (the per-cell trust-weighted base; the 158 twins purged, the UI
  population toggle removed — bollinger top 26 cells stamped, the
  other families' sides honestly NULL). LAST: the STARVED-CELL
  OBSERVATIONAL EMISSION — under-floored cells record as WAIT
  (renamed from no_action the same day; every starved cell's
  history triggers replay, the rules watchlist row only for
  floor-blocked imminent cells; live excludes wait cells). LAST
  (still 2026-10-16): the TWO-TIER GATE / POOLED EVIDENCE TIER — the
  per-cell floors cannot reach statistical significance (blend sigma
  ~2.6% → n=3 SE ~1.5%, the 0.25% bar ~0.16 SE), so the BUY edge
  claim moved to the (sub_type, action) pooled population: pooled
  t-stat ≥ 1 @ n ≥ 10 + expanding-year sign majority, a failing BUY
  side demoted to measured-out (no rows that run); cell_stats gained
  dir_std/t_stat; every trading rule row carries rule_params.pooled
  (the decay curve + the expanding-year legs, the boost-G2 lift).
