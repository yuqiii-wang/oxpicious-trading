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
  the gate/confidence constants; the measured evidence lives on the
  dated snapshots), populates
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
  cells on each code's own history, writes `signal_rules` (the cells —
  CONSTANT rule definitions only + the is_in_portfolio membership
  flag), `history_signal_performance` (the cell's OWN MEASURED
  evidence — n / dir_ave / std / t / hit + the membership flag, as
  dated daily snapshots, NEVER purged; the 2026-10-19 split, slimmed
  2026-10-20 — the combined tiers moved to the portfolio tier),
  `history_signals` (the cells' triggers replayed) + `trend_days`
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
entry-window addition; 2026-10-21 the TRAVEL CLAIM trend redesign
(a traveling gap-extreme claims rise/fall directly; stationed
extremes keep top/bottom and yield to live opposite episodes — the
trend re-`--force`d at index scope the same day). The financial semantics of the six families
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

- **signal_rules is CURRENT-STATE and CONSTANT-ONLY** (the
  2026-10-19 split): the family's scope
  (sec_type, signal_type) is DELETEd then freshly inserted every run —
  cells that stopped passing the gate must not linger; rule_params
  carry only the constant rule (axis, K, reentry flags, bar_def /
  family params, gate bar constants, confidence scale depth_factor +
  depth_cap) + the is_in_portfolio membership flag (2026-10-20: the
  cell's id rides a portfolio_signal_rules ids array). The MEASURED
  items ride each row's `_performance` payload, peeled into
  **history_signal_performance** (PK = the cell key + `date` = the
  prepared frame's data horizon): the cell's OWN measure block (n /
  dir_ave / std / t / hit) + the membership flag — one dated snapshot
  per emitted cell per run (trading AND wait), upserted, NEVER purged
  (not even --force — past snapshots are the point; a cell's evidence
  evolution, graduation and death survive the rules tier's per-run
  wipes). The pooled tier / gate digest / boost conf / dd lens /
  cascade NEVER ride the cell snapshot since 2026-10-20.
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
  + t-stat, hit) → gate (`CellGate`: buy ≥ 0.25%; sell ≥ 0 — the
  equity asymmetry IS the design; LOOSENED 2026-10-16 from
  0.75% @ 4 / 0 @ 3, the moderate user-directed step; 2026-10-19 the
  SAMPLE FLOOR STOPPED BLOCKING — the dir_ave bar is the only
  verdict, a cell under its floor trades at the starvation
  confidence scale min(n/floor, 1)) → per cell
  the WINNING candidate (gate-passing, best dir_ave; ties → the
  first/widest bar) → the POOLED EVIDENCE TIER (2026-10-16: the
  (sub_type, action) pooled population of the winning cells'
  measurable triggers, judged by pooled t-stat ≥ 1 @ n ≥ 10 +
  expanding-year sign majority; 2026-10-19 the POOLED STARVATION
  RETREAT — the statistics legs (bar / t / year majority) stay the
  verdict, n in [POOLED_TRADE_FLOOR=3, POOLED_MIN_N=10) TRADES at
  the pooled completeness scale min(n/10, 1) folded into every
  row's confidence and the snapshot's gate.conf_scale, below 3
  demoted to measured-out: a BUY side that fails emits no rules
  rows and no history replay that run; SELL
  records the block ungated, exit timing — the verdict is computed
  BEFORE row materialization so the scale can ride it) → the cell's
  signal_rules
  row + history rows for the winning cells' triggers. `rule_params`
  carry the CONSTANT rule only: axis, episode_merge_days, reentry
  flags, the gate BAR constants (min_pct / sample_floor (the
  confidence reference) / the observational `ungated` designation),
  the confidence SCALE (depth_factor, depth_cap 2) plus the
  candidate's own bar_def/params. The MEASURED half rides the row's
  `_performance` payload — the history_signal_performance snapshot:
  measure (n/dir_ave_pct/dir_std_pct/t_stat/hit_rate), `pooled`
  (n/t/hit + the per-horizon decay curve + the expanding-year legs
  + starved/conf_scale, stamped on trading rows), the gate VERDICT
  digest (passed/starved/
  conf_scale), confidence base (= measure.dir_ave_pct) and
  `ohlcv_boost_conf` (boost finalize stamps it). `ungated=True` marks
  observational twins (options_wall's VRP-fail `_rej`, action
  wait 2 — never a trading row). STARVED cells (n below the sample
  floor) TRADE since 2026-10-19 at the reduced confidence (the
  2026-10-16 wait replay + the selective wait watchlist are
  RETIRED): the extreme-event monotone guarantee is kept as a
  REDUCED-CONFIDENCE trading record — the deepest-claim breach at an
  extreme corner records its real action with gate.starved +
  gate.conf_scale on the snapshot. Measured-out cells (dir_ave below
  the bar, any n) still emit NOTHING — that is the gate rejecting on
  evidence.
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
  ship gates G1 improv / G2 expanding-year OOS / G3 top-quintile).
  Its ONLY product was the per-cell `ohlcv_boost_conf` snapshot stamp
  (2026-10-16..2026-10-20) — RETIRED with the column in the
  2026-10-20 slimming; the collaborator now leaves the per-run
  DERIVATION AUDIT (the report run_family logs). NEVER a gate;
  history carries ONE population per event (the boost-twin rows were
  retired 2026-10-16). Opt-ins:
  RsiTailEntry / BollBandEntry / MomentumTailEntry / RoofFloorEntry.

## The consolidated trends component (`analysis/trends/`)

Package-level, imported by the signals tier AND by
mov_ave_spread's rsi_typical step (so it cannot live under
engines/). Family-agnostic:

- `trend_state.attach_trend` stamps each (code, date) row an int8
  `trend` (0 top / 1 bottom / 2 rise / 3 fall / 5 unknown; 4 'all' is
  the POOLED stamp of sparse families' rule rows, never a day state):
  - **rise/fall** = RSI RE-ENTRY episodes: Wilder RSI(TREND_RSI_WINDOW
    = 10) vs the FIXED 50 ± TREND_RSI_ZONE_SPREAD (12.5) momentum
    zones (fixed, not per-code percentiles — RSI is bounded and its
    trailing quantiles collapse onto hair-trigger or unreachable
    edges), alive through ≤ REENTRY_EXIT_DAYS (5) out-of-band traded
    rows, the LATEST band touch owning the day, AND the EFFICIENCY
    GUARD agreeing — the sign of the Kaufman net change over
    TREND_GUARD_WINDOW (10) rows (the 2026-10-15 measured replacement
    of the MA20-slope guard).
  - **the TRAVEL CLAIM (2026-10-21, user-directed)**: a gap-extreme
    day whose leg has traveled ≥ TREND_TRAVEL_MIN (4.0) close-vol
    units (the Wilder ewm of |dClose| over 14 rows — the family
    frames carry no high/low) from the prior TREND_TRAVEL_WINDOW (60)-
    row high/low, with the GUARD AGREEING, claims rise/fall DIRECTLY
    — a large fluctuation in progress is a directional state, not an
    extreme state; the RSI episode's zone edge sits weeks away once a
    leg consolidates, so the old extremes-override chain masked whole
    legs (the prompt case: 000001's 2026 Sep-Oct decline read bottom
    20+ sessions; it now reads fall). The travel bar is the semantic
    floor, not the working discriminator (the below-extreme
    population's leg sits ≥ 3.7 cvol everywhere — the GUARD SIGN
    splits traveling vs stationed); T=3..6 profiles identical
    (temp_scripts/trend_study/travel60_calib.py). Regressions all
    hold: Apr-2020 flip 2020-04-07, synthetic quiet occupancy 162/200
    (retired MA60 rule's 167), V-turn +3d / crash +0d; the four state
    populations are forward-coherent (rise(travel) continues +0.6%
    hit 52%, fall(travel) +1.1% — the capitulation edge now lives in
    the fall-trend cells, stationed top/bottom revert); most old top
    days flip to rise (above-extremes with an agreeing guard are the
    measured continuation population, +1.4% at guard > 3 ATR).
  - **top/bottom** (the STATIONED extremes, 2026-10-21 reshape) = the
    price_vs_ma255 gap beyond its own TRAILING 255d q90/q10
    (shifted, no look-ahead) with a matching 20d MA slope, KEPT only
    when the day is not traveling — the small-fluctuation-parked-at-
    the-band-edge mean-reversion state — and yielding to a live
    opposite episode label.
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
| options_wall | WallBreachEntry | ow_put_025pct (buy, VRP-gated), ow_put_025pct_rej (wait twin), ow_call_025pct (sell) — the breach WIDTH collapsed to the SINGLE 0.10% bar (2026-10-19 sparsity study: breach-day counts identical across widths; the sub_type NAME's 025 is historical label vocabulary only) | code (trend 4, regime 4) | 0 | no |
| options_vix | OvixSpikeEntry (ovix_spike, ovix_spike_hivol), OvixPanicPeakEntry (ovix_panic_peak buy + ovix_euphoria_peak sell — the 2026-10-19 mirror: ONE shared vol-ATH escalation bar, the spot-vs-5d-ago leg picks the direction), OvixAtrBreakEntry (ovix_atr_break buy — the spot day-return in close-to-close ATR units past the self-adaptive {1.5,2,2.5,3} bar while slope5 fires; the 2026-10-15 low-base repricing class; k=1.5 added 2026-10-19), OvixFearUnwindEntry (ovix_fear_unwind sell) | multi-axis detects | code (trend 4, regime 4) | 8 | no |
| momentum | MomentumTailEntry | mom{5,20,short,mid,long}_{1,2,3}pct buy/sell (log-return ln(1+momentum) of analysis.mov_ave_momentum; the vol-adaptive lookbacks + fixed 5/20d anchors) | code×trend×regime, plain absorb | 5 | no |
| hedge_pairs | HedgeRsiEntry (registry entry0 — owns the ONE fetch + all three wipe scopes: hp_rsi + hedge_pairs at the pair codes, mov_rsi at the component codes; stamps the sourced type-0 component rows via the _ComponentRsiEntry shim) + HedgePairEntry (pair-native) | hp_rsi (8): hp_entry_legs_rsi / hp_exit_mkt_high / hp_catchup_rsi_hi / hp_entry_wash120_mkt (2026-10-19) — orchestration composites whose portfolio_signal_rules groups reference the sourced mov_rsi ids; hedge_pairs (7): hp_harvest (2026-10-21 dedup — the z15/z20 pair merged, the z grid the run_cells candidate set) / hp_mom_strong / hp_mom_lag (2026-10-20 tilt) / hp_stop_regime + the 2026-10-19 pair-native graduates hp_entry_wash (the same dedup merged the per-window wash{60,120,255} trio — the window grid the run_cells candidate set) / hp_entry_rho50 / hp_entry_catchup_reclaim60 (own one-id groups; the observational hp_regime_flip retired 2026-10-21 — rho50 is the hedgeable turn-on's one record). The family also stamps the DRAWDOWN LENS dd block (ddmin20_{price,px_strong,px_lag} forward-trough gauges) on every hedge performance snapshot — run.py combines the orchestrating fragments' `dd` lenses into the portfolio audit's dd column — AND (2026-10-20, re-shaped the same day's cell-level redesign) the PORTFOLIO tier's two PER-SUB fragment stamps on every orchestrating row: `_portfolio_rule_params` (portfolio_signal_rules.rule_params — the shared legs / market / aim / inverse-vol weighting blocks + the sub's own spec, merged per portfolio into rule_params.subs) and `_portfolio_performance` (history_portfolio_signal_performance — the max_drawdown_reduction audit: the pair value's own max DD vs the legs' standalone as reduction pp + the cell's dd lens fragment; run.py merges the fragments per portfolio and adds the members' COMBINED STATS as `pooled`; dated, upserted, never purged) | pair (trend 4, regime 4 — both POOLED; codes 'A~B') | 5 | no |
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
  not live. wait rows are EXCLUDED from the set (the ungated VRP twin
  only since 2026-10-19 — under-floor cells are trading rows now) —
  observational records are never a live bar.
- Each cell carries its own action, bar_def (resolved per check:
  bollinger's fixed ±kσ constant; mov_rsi's per-code ADAPTIVE
  trailing-2550d percentile tail recomputed at check time over the
  rows strictly before the value anchor — bit-parity verified against
  history_signals.signal_reference) and confidence scale (rule_params.
  confidence: depth_factor/cap) + BASE (the latest performance
  snapshot's dir_ave_pct at-or-before the checked date — the live
  fetch merges it into the confidence dict together with the
  STARVATION scale from the same snapshot's gate digest
  (gate.conf_scale → confidence.sample_scale, applied by
  rules_confidence_bp); a --date replay uses the
  measurement known that day).
- THE DAY-CONTEXT MATCH is the gate: only the cell whose (trend,
  regime) equals the day's own trend (`analysis.trend_days`) and
  regime (stats.market_regimes, calm default) may fire — the same
  population the cell measured. A POOLED axis (the 'all' 4 stamp)
  measured EVERY day state, so it matches any day context on that
  axis (strict equality could never fire it — fixed 2026-10-21); the
  hedge_pairs ORCHESTRATION stamps (`_ComponentRsiEntry` — REAL
  mov_rsi cells at the market + leg codes, pooled both axes) are
  EXCLUDED from the live active set (their history replay is
  deliberately dropped — the portfolio source layer never fires
  standalone; without the exclusion the pooled match would wake them
  at HP_MARKET_CODE 000300, a live-checked study index).
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
4. signal_rules is wiped + reinserted per run (current-state,
   CONSTANT-only rule_params + the is_in_portfolio membership flag);
   history is append/upsert; performance
   snapshots are upsert-only and NEVER purged (--force included);
   trend_days is per-code horizon-gated; portfolio_signal_rules is
   wiped + rewritten with them (one row per portfolio) while
   history_portfolio_signal_performance is upsert-only like 04;
   portfolio_history_signals (2026-10-21, the PORTFOLIO tier's EVENT
   history — one row per coordinated event with THE EVENT DATE + the
   ``member_signals`` JSONB, the sourced component CELL EVENTS that
   triggered it each with its OWN date, resolved ACROSS the tail grid
   since the raw-tail witness stays lit while the deeper claim carves
   the shallower cell's episodes) follows the history_signals
   lifecycle (--force purges the (sec_type, portfolio_type) tier).
5. The live tier NEVER writes a bar the strategy tier didn't stamp.
6. Sparse options families POOL at the gate (per-code cells, trend 4
   / regime 4); the VRP-fail twin is wait, never live.
7. The boost is a TRUST-WEIGHTED MEASUREMENT, never a gate: since
   2026-10-20 it leaves only the per-run DERIVATION AUDIT (the
   per-cell ohlcv_boost_conf stamp retired with its column) and never
   touches the history population — the plain rows are the population
   of record.
8. STARVED cells TRADE (2026-10-19, retiring the 2026-10-16 wait
   replay): the sample floor is the confidence reference, not a
   verdict — a cell under its floor emits its REAL action at the
   starvation scale min(n/floor, 1) (readers RECOMPUTE it since
   2026-10-20 from the snapshot's n_episodes + the rule's
   gate.sample_floor — the digest left the slimmed cell snapshot; the
   live tier applies the same scale). An
   extreme event's deepest claim still records — as a
   reduced-confidence trading row. Measured-out cells (dir_ave below
   the bar, any n) emit nothing, and the only wait rows left are the
   ungated VRP twins (never live). The POOLED n floor is the same
   kind of reference (same day): the statistics legs (bar / t / year
   majority) are the BUY verdict, n in [POOLED_TRADE_FLOOR,
   POOLED_MIN_N) trades at min(n/10, 1) folded into the in-run
   confidence, below the trade floor emits nothing.
9. The PORTFOLIO tier is CELL-LEVEL-SIGNALS-IN-PORTFOLIO
   (2026-10-20): one portfolio_signal_rules row per portfolio holds
   ALL member ids; the member cells carry is_in_portfolio and keep
   their OWN unified dir_ave test; history_portfolio_signal_performance
   audits the members' COMBINED stats (pooled) + the structure
   benefit + the combined dd lens, and its gate.conf_scale is the
   member confidence cascade the live tier applies beside the
   starvation scale (is_valid groups only — never a discount).
10. After any run that wrote: ANALYZE the tables; verify by row counts
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
  01_signal_rules.sql (is_in_portfolio since 2026-10-20),
  02_history_signals.sql,
  03_portfolio_signal_rules.sql (ONE ROW PER PORTFOLIO — no sub_type
  axis; its rule_params JSONB in the
  signal_rules GRAMMAR: the shared top-level axis / universe /
  pair_weighting / episode_merge_days / gate bars / confidence
  cascade scale / the portfolio {legs, market, aim} block + the
  PER-SUB coordination specs under subs; is_valid = the audited
  benefit cleared the portfolio gate AND at least one orchestrating
  cell's own verdict — the GATING conjunction for the CONFIDENCE
  CASCADE),
  04_history_signal_performance.sql (the cell's OWN measure + the
  membership flag — slimmed 2026-10-20),
  05_history_portfolio_signal_performance.sql (the portfolios' dated
  AUDIT snapshots — what the portfolio took; hedge: max
  drawdown reduction; `pooled` = the COMBINED STATS of the
  individual in-portfolio signals; `dd` = the orchestrating cells'
  combined lens; `gate` carries the verdict digest with conf_scale =
  the member cascade; headers/COMMENTs are the tables' documentation;
  keep them current),
  06_portfolio_history_signals.sql (the PORTFOLIO tier's EVENT
  history — one row per coordinated event, THE EVENT DATE +
  `member_signals`; the portfolio history panel's expandable rows,
  served by the `/api/live-data/trading-signals/portfolio-history`
  route). The PORTFOLIO type knowledge
  docs: `database/sql/static/portfolio_signal_docs/` (the
  analysis_signal_docs convention at the portfolio tier, one JSONB
  per portfolio_type, joined by the portfolio API).
  trend_days: `database/sql/analysis/trends/`
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
  (the decay curve + the expanding-year legs, the boost-G2 lift). LAST
  (2026-10-19): the CONSTANT/PERFORMANCE SPLIT — signal_rules.
  rule_params slimmed to the constant rule (bar def, K, reentry flags,
  gate bars, confidence scale) and the MEASURED items (measure /
  pooled / gate verdict / confidence base / the boost conf — its rules
  column dropped) moved to analysis_signals.history_signal_performance
  as dated daily snapshots (date = the frame's data horizon, upserted,
  never purged); the rules API re-merges the latest snapshot per cell
  and the live tier reads its confidence base as-of the checked date.
  LAST (2026-10-19, later the same day): the STARVATION RETREAT —
  the sample floor stopped blocking (user-directed, answering the
  2025-04-08 tariff-crash wait records): CellGate.passes judges the
  dir_ave bar only, under-floor cells emit their REAL action at
  conf_scale = min(n/floor, 1) (digest fields starved + conf_scale
  replace intended_action; the selective wait watchlist and the
  starved history replay are deleted; the live lateral join reads
  conf_scale into confidence.sample_scale for rules_confidence_bp;
  the pooled demotion now drops the whole side — no wait population
  survives it). LAST (2026-10-19, the OPTIONS LOOSENING — user-
  directed "more signals on the Options page"): the wall width
  became a per-cell candidate grid {0.25, 0.10}% (the ma_cross
  cross-excess precedent), the put-side VRP gate 0.80 → 0.70 (the
  gated put population was empty at 0.80), the ovix corr bar −0.80 →
  −0.75 (the spike_wide coverage play graduating into the main bar),
  and ovix_atr_break's k grid gained 1.5 — AND the POOLED
  STARVATION RETREAT one level up (the pooled verdict moved BEFORE
  row materialization; the statistics legs stay verdict legs, n in
  [3, 10) trades at min(n/10, 1) folded into gate.conf_scale): the
  first-ever ow_put BUY cells (000852 n=7 +6.17% t=3.96 at ×0.7) and
  starved-trading ovix_spike/hivol cells (×0.9/×0.6) date from this
  rebuild; the UI decoders gained the atr_multiple bar word and the
  wall_dist_pct percent-unit fix (fmtNum — it read ×100 off), and
  the vol-index corr companion's guide lines moved to −0.75/−0.5.
  LAST (2026-10-19, the EUPHORIA MIRROR — the off-spike study's
  productized leg, temp_scripts/ovix_off_spike_study.py +
  ovix_grid_study.py): OvixPanicPeakEntry owns a second sub type,
  ovix_euphoria_peak (label 4307, SELL) — the SAME vol-ATH
  escalation bar (vol > prior 120-session max × 1.05 & slope5 >
  +10%) with the spot leg ABOVE its 5-sessions-ago close (the
  blow-off-top exit the buy legs exclude; 000852 2024-10-08 n=1
  +8.15% at the starvation ×0.5, sells never pooled-gated; the other
  codes' euphoria cells measured out — tops continue). The same
  study MEASURED OUT every candidate bar grid (corr {−0.75,−0.70} ×
  slope-window {5,3} on spike/hivol/peak: +0/−0 events; the atr
  slope3 grid: −9 net events on precision-noise winner flips) — the
  production bars stand; rule_params gained the vol_ath_euphoria
  bar_def kind (the UI decoder renders "spot > 5d-ago").
  LAST (2026-10-19, the SPARSITY STUDY — docs/
  options_wall_sparsity_study_2026-10-19.md, temp_scripts/
  ow_sparsity_study.py + ow_gamma_wall_study.py): the family's low
  counts are INTRINSIC (the dominant OI zone's outer edge — the bar —
  sits median ~12% from spot; only 1.2% of days breach it) with the
  255-row VRP warmup erasing whole codes (000016's entire put
  population). The user-directed implementation: the VRP bar's
  min_periods dropped to OW_VRP_PCTL_MIN_PERIODS=120 (60 identical —
  the rescued 000016 put cell: n=8 +0.56% hit 75%), the VRP bar
  0.70 → 0.60 (p50 halves the edge — do not go lower), and the
  no-op width grid collapsed to (0.10,). Rebuilt: the put side
  trades TWO cells (000852 n=8 +5.44%, 000016 n=8 +0.56%) at FULL
  pooled confidence (pooled n=16 t=3.07 — the starvation scale is
  gone), call pooled n=13 t=1.29, index history 52 rows (29
  trading). Two honest negatives recorded the same day: the
  NEAREST-EXPIRY dominant-wall pick (denser breach days, 1 gated
  pass, weaker edge) and the walls-as-STRIKES counterfactuals
  (max-OI and gamma×OI strike picks from stats.options_greeks —
  hundreds of gated events at +0.16%/+0.04%, far below the bar;
  the deep zone-edge breach IS the edge). P2 (SZSE ETF option chain
  backfill) is SOURCE-BLOCKED: the SZSE trend service serves only
  from 2025-07-01 (downloads/options/szse/trend
  TREND_ARCHIVE_DATE); a backfill needs SZSE's historical-archive
  endpoint (undiscovered — reportdocs probes 404) or a vendor
  (Tushare opt_daily class).
