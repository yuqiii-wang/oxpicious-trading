---
name: signals-live-vs-history
description: >
  How the oxpicious-trading repo differentiates HISTORY signals from
  LIVE signals in the RULES era (2026-10-14 redesign, consolidated
  2026-10-15, trend rebuild 2026-10-16): analysis.analysis_signals
  measures NINE standalone detection families (mov_rsi / bollinger /
  ma_cross / options_wall / options_vix / momentum / rf_break /
  rebound / macd) plus the
  PORTFOLIO family hedge_pairs (fresh 2026-10-24) as SELF-ADAPTIVE per-(code,
  sub signal, trend, regime, action) RULE CELLS written to
  analysis_signals.signal_rules (rule_params = the prepared bar +
  the gate/confidence constants; the measured evidence lives on the
  dated snapshots; the is_pe_boosted PK member — 2026-10-26 — puts a
  PE-conditioned variant of a rule in a SEPARATE row from the
  unconstrained one), populates
  analysis_signals.history_signals by REPLAYING those cells (episode
  FIRST days, delay 0), and stamps the family-agnostic
  analysis.trend_days store (the consolidated analysis.trends
  component's RSI-re-entry classifier). The live tier NEVER detects:
  it breaches the rule cells' bars (mov_rsi / bollinger live-wired)
  under a day-context (trend, regime) match. Use for ANY work touching
  analysis_signals, signal_rules, history_signals, trend_days,
  live.live_signals, the emit gate, rule parameters, or the
  families' engines — even when the user does not mention signals
  explicitly.
---

# Live signals vs history signals (rules era)

The repo has TWO signal tiers with a strict one-way dependency:

- **analysis.analysis_signals** — the strategy tier: measures rule
  cells on each code's own history, writes `signal_rules` (the cells —
  CONSTANT rule definitions only + the is_in_portfolio PORTFOLIO-tier
  flag, a PK member since 2026-10-23: an existing signal rule SOURCED
  by a portfolio is a SEPARATE row from the standalone one),
  `history_signal_performance` (the cell's OWN MEASURED
  evidence — n / dir_ave / std / t / hit + the membership flag, as
  dated daily snapshots, NEVER purged; the 2026-10-19 split, slimmed
  2026-10-20 — the combined tiers moved to the portfolio tier),
  `history_signals` (the cells' triggers replayed; the SOURCED
  component events ride the is_in_portfolio TRUE tier there too — a
  PK member since 2026-10-23, the cell_signals linkage keys carry it)
  + `trend_days`
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
  (sec_type, signal_type, codes, is_in_portfolio TIER — the wipe
  scopes are (type, codes, tier|None) triples since 2026-10-23) is
  DELETEd then freshly inserted every run —
  cells that stopped passing the gate must not linger; rule_params
  carry only the constant rule (axis, K, reentry flags, bar_def /
  family params, gate bar constants, confidence scale depth_factor +
  depth_cap) + the is_in_portfolio TIER stamp (2026-10-23: TRUE on
  EVERY source-layer row a portfolio family emits — claimed by an
  orchestration or not — plus the pair-native members; the standalone
  families' runs wipe only the FALSE tier, the portfolio family's
  component scopes wipe only the TRUE tier). The MEASURED
  items ride each row's `_performance` payload, peeled into
  **history_signal_performance** (PK = the cell key + `date` = the
  prepared frame's data horizon): the cell's OWN measure block (n /
  dir_ave / std / t / hit) + the membership flag — one dated snapshot
  per emitted cell per run (trading AND wait), upserted, NEVER purged
  (not even --force — past snapshots are the point; a cell's evidence
  evolution, graduation and death survive the rules tier's per-run
  wipes). The pooled tier / gate digest / boost conf / dd lens /
  cascade NEVER ride the cell snapshot since 2026-10-20.
- **history_signals**: force purges the scope (the same
  (type, codes, tier) scopes) then COPYs;
  incremental `copy_or_upsert_split_async` past the table's max date
  (idempotent re-emission). The SOURCED component events ride the
  is_in_portfolio TRUE tier (PK member since 2026-10-23) — a
  standalone family's events and a portfolio-sourced cell's events at
  the same (code, sub, date, time) are SEPARATE rows.
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

## The families (engines/signal_families/, registry in order — nine standalone + the hedge_pairs portfolio family)

| family | entries | sub types | cells | K | live |
|---|---|---|---|---|---|
| mov_rsi | RsiTailEntry | rsi{14,10,6}_{1,2,3}pct buy/sell | code×trend×regime, trend-aware absorb | 5 | rsi |
| bollinger | BollBandEntry | boll20_{2,2.5,3}std buy/sell (window 20 ONLY) | code×trend×regime | 5 | boll |
| ma_cross | MaCrossReclaimEntry (ma20/60_cross buy, cross-excess {1,2,3}%), MaRetestEntry (ma20_retest sell), MaGdepthEntry (ma120/255_gap buy ONLY, {3,5,8}%), MaBreadthEntry (ma_breadth_thrust buy, ma_9to1_up buy / ma_9to1_dn sell — the MARKET-LEVEL breadth rules over the sec_type's own universe, joined per date onto every code) | kind×window grids | code×trend (regime 4) | 5 | no |
| options_wall | WallBreachEntry | ow_put_025pct (buy, VRP-gated), ow_put_025pct_rej (wait twin), ow_call_025pct (sell) — the breach WIDTH collapsed to the SINGLE 0.10% bar (2026-10-19 sparsity study: breach-day counts identical across widths; the sub_type NAME's 025 is historical label vocabulary only) | code (trend 4, regime 4) | 0 | no |
| options_vix | OvixSpikeEntry (ovix_spike, ovix_spike_hivol), OvixPanicPeakEntry (ovix_panic_peak buy + ovix_euphoria_peak sell — the 2026-10-19 mirror: ONE shared vol-ATH escalation bar, the spot-vs-5d-ago leg picks the direction), OvixAtrBreakEntry (ovix_atr_break buy — the spot day-return in close-to-close ATR units past the self-adaptive {1.5,2,2.5,3} bar while slope5 fires; the 2026-10-15 low-base repricing class; k=1.5 added 2026-10-19), OvixFearUnwindEntry (ovix_fear_unwind sell) | multi-axis detects | code (trend 4, regime 4) | 8 | no |
| momentum | MomentumTailEntry | mom{5,20,short,mid,long}_{1,2,3}pct buy/sell (log-return ln(1+momentum) of analysis.mov_ave_momentum; the vol-adaptive lookbacks + fixed 5/20d anchors) | code×trend×regime, plain absorb | 5 | no |
| rebound | ReboundEntry | rebound_{capit,fail,reclaim}_{60,255} buy/sell — the drop→rebound→resolution family (2026-10-26, the ma-spread rebound study's graduates): a big drop (≥ the depth candidate {10%, 5%} below the trailing-Wd running max of close, SHIFTED 1) activates the episode; the resolutions are the sub types — capit BUY (the trough-break V-bottom), fail SELL (rollover with recovery ratio R in [0.90, 1.0) — the stall just below the last high, exit timing), reclaim BUY (close above the prior high — the strongest next-leg population); the causal episode machine + the PE trailing-percentile rank are the sanctioned host boundary (engine.py); EACH sub type emits a PLAIN and a PE-CONDITIONED cell set on the is_pe_boosted PK axis (separate run_cells calls — the pe call's masks are AND-gated: capit PE extremes ≤0.4|≥0.6, fail/reclaim not-cheap ≥0.2; bar_def.pe_gate records it; the machine events feed the K=5 detector like any mask) | code×trend (regime 4) × {plain, pe} | 5 | no |
| macd | MacdCrossEntry | macd_cross_up buy / macd_cross_dn sell — the classic MACD signal-line crossover (2026-10-26, the typical-EMA study's product; the classic 12/26/9 spans, config MACD_EMA_*): the fetch READS the precomputed ema12/ema26 (stats.*_tech_stats, LEFT JOINed — rebased 2026-10-27 from the in-fetch compute_emas recompute, user-directed; the strategy algo's source verbatim) and assembles macd_line = ema12 − ema26 + the EMA9 signal line (the one inline EMA) + the histogram; the horizon branch reads the tech_stats max(date); the engine only shifts/compares — the strategy algo's cross detection, value = histogram pct-of-close vs reference 0.0, depth = |hist| in its own trailing-2550d sigma (shifted 1); one candidate per side; NOT live-wired | code×trend (regime 4) | 5 | no |
| hedge_pairs | HedgePairTrioEntry (the ONE entry, SIMPLIFIED 2026-10-24 — user-directed "simple conditions when three curves resonate; the triggered portfolio signals set the cell level signals, no sourcing from the short-term-tech tier"): ONE wipe scope (its own type at the pair codes), NO component-code rows, NO source layer (the 304-row mov_rsi TRUE tier from the first cut was purged; no portfolio_role marker) | hp_entry_trio (5828, BUY) — ALL THREE curves (both offset-opposite legs + the broad market 000300) in their own Wilder-RSI(14) capitulation tails the same day, AT-LEAST tail populations over HP_LEG_TAIL_PCTS (NO nested carve — the per-curve tail conditions are monotone, a deeper wash also satisfies a shallower bar and fires inside a shallow winner with more depth confidence) — WIDENED 2026-10-25 {1,2,3} → {1,2,3,5,10} (user-directed "tails 1/2/3% is too strict, portfolio can self pick up rsi as well") then → {1,2,3,5,10,15,20} with the FREQUENCY-FLOOR winner policy (user-directed "the signals are too rare, loosen it to at least once per year": HP_MIN_EVENTS_PER_YEAR = 1.0 — each pair's bar = the DEEPEST gate-passing tail meeting ≥1 trigger/yr over the pair's own frame horizon, the best-dir_ave winner the fallback; 25/30 measured out at pooled +0.24%/+0.14%; the winner's tail rides rule_params.bar_def.tail_pct; value = rsi_mean vs the trio bar mean, response = the pair VALUE's forward blend); the blow-off mirror hp_exit_trio (5829) is the MEASURED asymmetry check, rows discarded every run. Each event's member records are set by the TRIGGERED signal itself — one per-curve WITNESS per curve at the curve's OWN day (its deepest-alive breach run start) carrying the curve's PER-ROLE sub type since 2026-10-25 (user-directed "diff legs or roles should have diff sub types"): hp_rsi_witness_mkt 5831 / _leg_a 5832 / _leg_b 5833 (the shared hp_rsi_witness 5830 stays for old-history decode; payload vocabulary, no rule rows); cell_signals stays empty. Universe data-driven (offsets, disjoint, score > 0.5) PLUS the PINNED showcase DIV_GENERAL~TECH_GENERAL (HP_SHOWCASE_PAIR — the page's default picks; its industries are reserved for each other before the greedy pass, score/rho null in rule_params when the offsets carry no reading). Portfolio tier: the group IS its orchestrating rows (signal_rule_ids = the pair's own rows; pooled = their combined stats; rule_params.name "trio wash" since 2026-10-25), the benefit/gate/dd audit as before | pair (trend 4, regime 4 — both POOLED) | 5 | no |
| rf_break | RoofFloorEntry | rf{60,120,255,500}{,_med}_{roof,floor}_deep BUY (the depth tiers) + rf{60,120,255,500}_floor_bounce BUY (the two-method gap study's cross-method leg) — ALL-BUY since the 2026-10-26 legacy cleanup RETIRED the catch-all roof-buy / floor-sell sub types (labels 5600-5633 decode-only; the close vs the PRIOR day's donchian/median level from analysis.mov_ave_spreads_roof_floor_estimates; floor_bounce = median floor broken while the donchian wick floor holds; donchian rf255_roof_deep pooled-rejected, emits nothing) | code×trend (regime 4) | 5 | no |

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
  observational records are never a live bar; the is_in_portfolio
  tier rows are excluded too (the sourced tier fires only inside its
  portfolio coordination), and so are the SHARED VARIANT-AXIS cells
  (2026-10-26: is_pe_boosted / is_ohlcv_boosted TRUE — the live
  breach check has no PE / turnover condition machinery, so a variant
  cell would fire its bar without the confirmation; their events live
  in the history tier only).
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
  EXCLUDED from the live active set (PRIMARY: NOT is_in_portfolio —
  every sourced row rides TRUE since 2026-10-23; the rule_entry name
  list stays as the belt-and-suspenders twin. Their cell events also
  ride
  history_signals now — the portfolio events' member cells fetch them
  there — but the portfolio source layer never fires standalone;
  without the exclusion the pooled match would wake them at
  HP_MARKET_CODE 000300, a live-checked study index).
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
   CONSTANT-only rule_params + the is_in_portfolio TIER flag, a PK
   member since 2026-10-23: an existing signal rule sourced by a
   portfolio is a SEPARATE row; the wipe scopes partition on the
   flag — standalone runs own FALSE, the portfolio component scopes
   own TRUE);
   history is append/upsert (the sourced component events ride the
   TRUE tier of history_signals, also a PK member — the
   cell_signals linkage keys carry the flag); performance
   snapshots are upsert-only and NEVER purged (--force included);
   trend_days is per-code horizon-gated; portfolio_signal_rules is
   wiped + rewritten with them (one row per portfolio) while
   history_portfolio_signal_performance is upsert-only like 04;
   portfolio_history_signals (2026-10-21, the PORTFOLIO tier's EVENT
   history — one row per STAMPED history row (the orchestrating
   pair-code rows; the source layer's component history RIDES
   history_signals since the same day, user-directed) with THE EVENT
   DATE + the ``member_signals`` JSONB — the event's coordinated
   MEMBER ACTIONS under the MEMBER-ACTION INVARIANT (2026-10-21,
   tightened 2026-10-22, user-directed): AT LEAST TWO CELL SIGNALS on
   at least two codes per event, different dates allowed — since the
   2026-10-22 consolidation EVERY member IS a sourced cell event (the
   sub types whose events could not carry two were REMOVED; the sub
   inventory is THREE) — PLUS the ``cell_signals`` JSONB array: each
   member's history_signals PK, the EXPLICIT LINKAGE the portfolio
   history API fetches the cell-level signals through (from signal
   history, user-directed 2026-10-21; member_signals stays the
   event-time snapshot and the dangling-key fallback) follows the
   history_signals lifecycle (--force purges the (sec_type,
   portfolio_type) tier).
5. The live tier NEVER writes a bar the strategy tier didn't stamp.
6. Sparse options families POOL at the gate (per-code cells, trend 4
   / regime 4); the VRP-fail twin is wait, never live.
7. The boost is a TRUST-WEIGHTED MEASUREMENT, never a gate on the
   PLAIN rows: since 2026-10-20 the measurement leaves only the
   per-run DERIVATION AUDIT (the per-cell ohlcv_boost_conf stamp
   retired with its column). SINCE 2026-10-26 (user-directed "create
   shared pe and ohlcv logic ... use them where possible or
   relevant") the OHLCV boost is ALSO a VARIANT axis again — the
   turnover-climax-confirmed population on the is_ohlcv_boosted PK
   (the engines/_pe_boost + engines/_ohlcv_boost packages over
   engines/_variant_base.py, the shared PE + OHLCV variant gates;
   rf_break deep legs + bollinger opted in, rebound carries the PE
   variants) — a SEPARATE row set judged independently by the same
   measured gate (a variant below the bar emits nothing), the plain
   rows stay the population of record, and the variant cells never
   fire live (excluded from the active set).
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
   ALL member ids; the member cells carry is_in_portfolio (since
   2026-10-23 EVERY source-layer row does — claimed or not — the
   PK's tier axis) and keep
   their OWN unified dir_ave test; history_portfolio_signal_performance
   audits the members' COMBINED stats (pooled) + the structure
   benefit + the combined dd lens, and its gate.conf_scale is the
   member BENEFIT BOOST the live tier applies beside the
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
  01_signal_rules.sql (is_in_portfolio a PK member since 2026-10-23 —
  the PORTFOLIO-tier axis),
  02_history_signals.sql,
  03_portfolio_signal_rules.sql (ONE ROW PER PORTFOLIO — no sub_type
  axis; its rule_params JSONB in the
  signal_rules GRAMMAR: the shared top-level axis / universe /
  pair_weighting / episode_merge_days / gate bars / the member
  BENEFIT BOOST scale / the portfolio {legs, market, aim} block + the
  PER-SUB coordination specs under subs; is_valid = the audited
  benefit cleared the portfolio gate AND at least one orchestrating
  cell's own verdict — the GATING conjunction for the member BENEFIT
  BOOST),
  04_history_signal_performance.sql (the cell's OWN measure + the
  membership flag — slimmed 2026-10-20),
  05_history_portfolio_signal_performance.sql (the portfolios' dated
  AUDIT snapshots — what the portfolio took; hedge: max
  drawdown reduction; `pooled` = the COMBINED STATS of the
  individual in-portfolio signals; `dd` = the orchestrating rows'
  combined lens; `gate` carries the verdict digest with conf_scale =
  the member BENEFIT BOOST; headers/COMMENTs are the tables' documentation;
  keep them current),
  06_portfolio_history_signals.sql (the PORTFOLIO tier's EVENT
  history — one row per coordinated event, THE EVENT DATE +
  `member_signals` + `cell_signals` (the sourced members'
  history_signals PK array — the keys carry is_in_portfolio; the API
  fetches the cell-level signals from signal history through it); the
  portfolio history panel's expandable rows — ONE ROW PER PORTFOLIO
  SIGNAL with ITS coordinated cell signals nested under it since
  2026-10-23, served by the
  `/api/live-data/trading-signals/portfolio-history`
  route). The knowledge docs:
  `database/sql/static/signal_rules/` (static.signal_rules_docs —
  the families; renamed from static.analysis_signal_docs + pruned
  2026-10-27) and `database/sql/static/portfolio_rules/`
  (static.portfolio_rules_docs — the portfolio tier; re-set up
  2026-10-27, NO consumer yet).
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
- 2026-10-23: the SOURCED-ROW PK SEPARATION (user-directed): an
  existing signal rule sourced by a portfolio is a SEPARATE row —
  is_in_portfolio joined the signal_rules PK (and history_signals
  gained the column + PK member; the cell_signals linkage keys carry
  it). The flag's meaning widened from claimed-membership to the
  TIER axis: EVERY source-layer row rides TRUE (claimed or not; the
  pre-change unclaimed rows read FALSE), so the wipe/purge scopes
  became (type, codes, tier) triples — standalone runs own FALSE,
  the hedge component scopes own TRUE, the hedge composite scopes
  own both; the live active set/codes exclude the TRUE tier (the
  rule_entry list stays as the twin); family_is_current counts the
  FALSE tier. The in-place migration (temp_scripts/
  apply_signals_portfolio_pk.py) also purged the stale rows: the
  unclaimed sourced rules, the sourced events at component codes,
  and the composite orphans at retired pair codes. The portfolio
  history panel became ONE ROW PER PORTFOLIO SIGNAL with its OWN
  cell signals nested under it (no more day-grouped member merging).
- 2026-10-23 (later): the MORE-TRIGGERS GRADUATES II (user-directed
  "portfolio signal rules have very few triggers"; docs/
  portfolio_more_triggers_study_2026-10-23.md): the 2026-10-19 levers
  measured as DEAD ENDS (no single trio conjunct binds — leg A, leg B
  and the market are comparably rare; witness-window widening 8/10
  rescues only 1-11% of the missing days) — the lever is NEW
  coordination subs: hp_entry_rsi_legs (5825, both legs' RSI
  capitulation market-free), hp_entry_wash_rsi (5826, one leg's floor
  + the other leg's rsi3; witness-precise members via the extended
  _pair_member_index), hp_entry_wash_mkt1 (5827, one leg's floor +
  the market's rsi3). Fetch gained floor_gap{W}_{leg}, the merged
  xwashdepth/washdepth1 single-leg gauges and rsi_mean_legs; the
  _COMP_EVENTS_CACHE gained comp_buy for the pair-native entry's
  group joins. Rebuild (index, --force): 1,189 portfolio events
  (was 434 — the winner-sim prediction exact), the member invariant
  holds at 0 violations, 12 sanctioned fallback lines (the rsi
  kernel's ewm/rolling-quantile, pre-existing).
- 2026-10-24: the FRESH TYPICAL TRIO (user-directed "most typical
  negative correlation two curve and one broadmarket solution, typical
  and simple; do not reference old demised logic, but do check
  asymmetry of buy sell actions"): the whole prior hedge_pairs /
  hp_rsi engine pair (the wash/cross-wash/coordination-widening sub
  inventory, 5812-5827) was DELETED and re-implemented from scratch as
  ONE entry (HedgePairTrioEntry) — hp_entry_trio/hp_exit_trio only,
  the co-wash + its blow-off mirror, every member a REAL mov_rsi
  source cell at the market + legs (TRUE tier), the portfolio tier's
  three tables rewritten by a slimmed run_family portfolio block
  (fragments peeled off the orchestrating rows; entry0 now RUNS on the
  instance that fetched the frame so fetch-time side state survives).
  Rebuild at index scope: 48 pairs (data-driven disjoint universe),
  379 rules (75 orchestrating + 304 sourced), 3,081 history rows, 48
  portfolios (47 gate-valid, drawdown reduction +5.5..+12.9pp vs the
  mean leg), 162 events, member invariant 0 violations, 11 sanctioned
  fallback lines. The buy/sell asymmetry record: buy 48 cells / 120
  episodes / avg dir_ave +4.82% @94.7% hit vs the sell mirror 27
  cells / 33 episodes / +1.90% (exit-timing only — tops continue; the
  mirror demoted to a logged check the same day, user-directed "just
  one as a start"). LAST (still 2026-10-24): the SIMPLIFICATION
  (user-directed) — the mov_rsi SOURCE layer and the portfolio_role
  marker REMOVED (the 304 TRUE-tier rules / 2,919 history rows purged
  one-time); the triggered portfolio signal sets its own member
  records (hp_rsi_witness, payload-only); the group ids = the pair's
  own orchestrating rows; rebuild: 48 rules / 129 events / 48
  portfolios, member invariant 0 violations, 11 sanctioned fallback
  lines).
- 2026-10-25: the rules-panel CELL DEFINITION correction (user-directed
  "cell means signals under portfolio, not portfolio itself; the
  portfolio has no action"): in the portfolio rules UI the word CELL
  names ONLY the signals UNDER a portfolio — the per-curve
  hp_rsi_witness rules/records (market + both legs) and the legacy
  sourced component rows — NEVER the portfolio itself. The pair's own
  orchestrating signal_rules rows are the PORTFOLIO RULE and render
  with NO action chip (the portfolio has no action; only its cell
  signals carry actions). CellRulesSurface's "Performance audit"
  matrix — which had rendered the PAIR row as a cell (its only
  population after the 2026-10-24 simplification) — was REMOVED; the
  portfolio's OWN audit (entry-rule pooled stats + gate verdict +
  member cascade) moved to PortfolioSurface beside the max-DD benefit.
  DB/pipeline unchanged — pure UI semantics.
- 2026-10-25 (later): the TRIO TAIL WIDENING + the RULE PARAMS NAME
  (user-directed "tails 1/2/3% is too strict, update that portfolio
  can self pick up rsi as well" + "rule param json should have a
  name, e.g., trio wash"): HP_LEG_TAIL_PCTS {1,2,3} → {1,2,3,5,10} —
  the wider tails join the deepest-first candidate carving and
  run_cells' measured gate self-picks each pair's tail, now RECORDED
  per cell on rule_params.bar_def.tail_pct (the per-tail bar_def
  moved into the Candidate's own params — extra_params' shared
  bar_def would have overwritten it; the UI gained the trio_rsi_tail
  /trio_rsi_tail_mirror bar decoders). Rebuild (index, --force): 48
  cells / 128 events; the winner-tail distribution 26× tail1, 12×
  tail2, 7× tail3, 3× tail5 (AERO_AVIATION~TOURISM, COAL~MIL_DEFENSE,
  IND_GENERAL~LIVESTOCK — the widening's graduates); NO cell picked
  the 10% tail (its dilute wash fails/beats nothing — the honest
  negative stands until re-measured); the mirror check measured 37
  cells / 98 events (wider tails densify it too — still discarded).
  The portfolio tier's rule_params gained the top-level `name`
  ("trio wash", config HP_PORTFOLIO_NAME; documented in
  03_portfolio_signal_rules.sql, rendered by PortfolioRuleParams'
  Construction rows), and CellRulesSurface's Cell-signals table shows
  the raw `signal_type · signal_sub_type` line under each Signal chip
  (user-directed). ONE-TIME cleanup (temp_scripts/
  cleanup_hedge_pair_orphans.py): the code-scoped wipe misses rows at
  pair codes the data-driven universe DROPPED — the 2026-10-24 build
  left 2 orphan rules + 5 history rows at DIV_LOW_VOL~TECH_GENERAL /
  CONSUMER_ELEC~DIV_GENERAL (deleted; the recurrence risk at every
  universe shrink is flagged to the user). LAST (still 2026-10-25):
  the TYPE/SUB COLUMNS + the PER-ROLE WITNESS SUBS (user-directed
  "in Cell signals, not seen signal type and sub type as in col, and
  diff legs or roles should have diff sub types, fix it"): the
  Cell-signals table gained a SUB TYPE column (the muted under-chip
  line replaced; the SIGNAL TYPE and ACTION columns were dropped an
  hour later, user-directed "remove cols if the data is purely from
  UI" — the signal type was the constant family word on this
  hedge-pairs-only surface and the action was pure UI semantics: the
  portfolio's deliberate no-action em dash + hardcoded BUY chips on
  the witness condition rows; the group headers carry both), and the
  witness records split by role —
  hp_rsi_witness_mkt 5831 / _leg_a 5832 / _leg_b 5833 replace 5830 on
  new member_signals rows (label vocabulary added in all three
  mirrors, applied via temp_scripts/apply_hp_witness_labels.py +
  check_label_dim.py OK; the hedge_pairs knowledge doc re-seeded via
  apply_hedge_pairs_doc.py; the families' UI registry gained the
  rsi·witness mkt / legA / legB words). Rebuild identical (48 cells /
  128 events) — the member subs now decode per role. LAST (still
  2026-10-25): the PORTFOLIO-SIGNAL COLUMN WORDS (user-directed "if
  the signal means portfolio signal, explicitly in UI to do col
  renaming, also check other display to align to sql table
  definitions"): CellRulesSurface's table column reads "Portfolio
  signal / cell signal", its first group header "Portfolio signal · N
  — the portfolio's own rule (portfolio_signal_rules): never a cell,
  no action", the section header "Portfolio signal & cell signals",
  and the history panel's event column "Portfolio signal" — the UI
  words now name the SQL tiers (portfolio_signal_rules vs the cell
  signals under it) instead of a bare "Signal"/"Portfolio". LAST
  (still 2026-10-25): the CASCADE RENAME (user-directed "rename
  cascade to a more meaningful name"): the portfolio tier's
  confidence CASCADE is now the member BENEFIT BOOST everywhere —
  the audited benefit (the hedge type's max-DD reduction vs the mean
  leg) BOOSTS its member cells' confidence, 1 + factor ×
  min(benefit_pp / unit_pp, cap), increase-only — renamed in the
  config/engine comments, the 01/03/05 SQL COMMENTs, the
  knowledge-doc principles key ("cascade" → "benefit_boost",
  re-seeded), the rule-params UI label ("cascade" → "benefit
  boost"), cellSurface's pf-chip tooltip and portfolioSurface's gate
  line ("member cascade ×N" → "member benefit boost ×N"). The
  gate-digest FIELD keeps its name (conf_scale); older dated entries
  and docs/ studies still say "cascade" — same mechanism.
- 2026-10-25 (latest): the TRIO FREQUENCY FLOOR (user-directed "the
  signals are too rare, loosen it to at least once per year"; study
  temp_scripts/trio_freq_study.py): the trio's best-dir_ave winner
  (always the deepest tail — deeper washes rebound harder) left 41/48
  pairs under 1 event/yr (the showcase DIV_GENERAL~TECH_GENERAL: 2
  events in 6.8y). The loosening, three coupled pieces: (1) the
  candidate grid gained 15/20 (pooled evidence clears the bar there;
  25/30 measured out at +0.24%/+0.14% — the honest negative stands);
  (2) the candidates became AT-LEAST populations (the nested carve
  would have dropped a pair's DEEPEST washes from its own
  shallow-winner cell — a tail1 day is a stronger instance of the
  same tail10 condition, and under a shallow winner the carve meant
  the deep days never fired; monotone per-curve bars make the
  at-least semantics the honest one); (3) run_cells gained the
  optional ``freq_floor_per_year`` winner policy (config
  HP_MIN_EVENTS_PER_YEAR = 1.0, the hedge trio's only user so far):
  among the gate-passing candidates (deepest-first order) the cell
  picks the FIRST whose episode-first trigger frequency over its
  code's own frame horizon meets the floor — loosen just enough —
  with the best-dir_ave winner as the fallback (one pair,
  COMPUTE~STRAT_LARGE, falls back: its wider tails measured out).
  The measured population is the winner's own (the emitted evidence
  describes exactly what fires). Rebuild (index, --force): 48 cells /
  503 events (was 128), winner tails 3× t1 / 3× t2 / 6× t3 / 11× t5 /
  22× t10 / 2× t15 / 1× t20; the pooled BUY verdict passes over the
  new winners (study prediction: n=501, +1.50%, t=10.5, 6/6
  measurable years positive); the showcase pair: tail10, 11 events
  (1.62/yr), dir_ave +2.47% hit 91% t=3.63; the mirror check (same
  at-least basis, no floor) 22 cells / 25 events — still thin,
  discarded. The live tier is untouched (hedge_pairs is not
  live-wired). LAST (still 2026-10-25): the STORED RULE CONDITIONS
  (user-directed "add Rule condition in Rule params ... all UI data
  must be sourced from DB"): the portfolio rule_params.subs.<sub>
  gained the engine-written `condition` line (the plain-language
  trigger sentence) + the per-witness `cell_signals` records
  [{sub_type, code, role, condition, bar}] (payload conditions, no
  rule rows); the UI's roster trigger line and the cell-signals table
  parse them verbatim (the composed-prose fallback in
  portfolioTriggerLines stays only for rows predating the field; the
  witness synthesis in CellRulesSurface was deleted; grammar
  documented in the 03 SQL header). LAST (still 2026-10-25): the RSI
  WINDOW GRID (user-directed "also add rsi10 as trigger as well"):
  the trio's candidates span HP_LEG_RSI_WINDOWS (14, 10 — the
  classic slower RSI(14) preferred, window-major order; the faster
  RSI(10) with its own per-curve adaptive bars loosens further where
  RSI(14) cannot meet the frequency floor) x HP_LEG_TAIL_PCTS,
  at-least populations per window; Candidate gained a value_col
  override (the recorded signal = the winning window's rsi_mean);
  the witnesses + the stored cell-signal condition records read the
  pair's winner window (bar_def.window). LAST (still 2026-10-25):
  the LOOSE GATE + the 10-FIRST preference (user-directed "loosen
  the gate, only obviously bad signals should get rejected, ensure
  rsi10 be shown up"; study temp_scripts/trio_window_study.py):
  run_cells gained gate_min / pooled_dir_ave_min overrides and
  pooled_evidence a dir_ave_min kwarg — the hedge family's own bars
  HP_GATE_DIR_AVE_MIN / HP_POOLED_DIR_AVE_MIN = 0 reject a candidate
  ONLY on a negative measured mean (the pooled verdict keeps t >= 1
  + the year majority; the global 0.25% bars stay the standalone
  families' discipline), and HP_LEG_RSI_WINDOWS reordered (10, 14)
  makes the faster RSI(10) the primary trigger — 14-first left rsi10
  winners at 0 even under the loose gate (the classic met the floor
  everywhere). Rebuild (index, --force): all 48 winners RSI(10)
  ({1:2, 2:6, 3:8, 5:10, 10:21, 15:1}), 494 events, the pooled
  verdict passing; the showcase pair RSI(10) tail10 n=13 +1.42% hit
  69% (1.91/yr, latest event 2026-03-23 — the faster RSI catches
  newer washes). LAST (still 2026-10-25): the BOTH-WINDOWS UNION
  CELL (user-directed "both rsi10 and rsi14 are under the same trio
  wash rule sub type ... rsi14 have three cell signals, but also
  rsi10 another three cell signals"): the winner-takes-all across
  windows was replaced by the per-window tail pre-selection
  (_pick_window_tails — the deepest floor-meeting at-least tail per
  window under the loose gate, best-dir_ave fallback) with the
  cell's population the UNION of both windows' picks (either
  window's trio resonates; primary-window precedence on overlap;
  K-absorb merges cross-window same-wash episodes); the emitted
  bar_def carries BOTH picks (bar_def.windows — the trio bar decoder
  renders both), the recorded signal the triggering window's own
  rsi_mean, the event witnesses DETECT their window (payload
  window), and the stored cell_signals carry THREE witness curves
  per window (six records KEYED rsi10/rsi14 in the subs spec —
  user-directed "rsi10 and rsi14 should be part of key field ...
  for better search" — the UI groups them "Cell signals ·
  RSI(10) · 3" / "· RSI(14) · 3"). Rebuild (index, --force): 48 trio
  cells / 584 events (up from 494 — the rsi14-only days), the
  showcase pair's bar_def.windows [{10, tail10}, {14, tail10}], 15
  coordinated events (was 11), the pooled verdict passing. LAST
  (still 2026-10-25): the SOURCED LAYER RE-INTRODUCED (user-directed
  "the signals must register and retrieve under signal rules, not
  directly from analysis, so make sure register for is in portfolio
  signals"): the trio's run() measures the mov_rsi family's own
  window x tail vocabulary (HP_STANDALONE_RSI_WINDOWS (14, 10, 6) x
  HP_STANDALONE_TAIL_PCTS (1, 2, 3)) on the COMPONENT curves (the
  market + both legs — the stashed comp frame, its own forward blend,
  pooled both axes) and registers the winners as REAL mov_rsi rows
  (signal_type mov_rsi, the rsi{w}_{p}pct sub types, is_in_portfolio
  TRUE — run_cells gained signal_type_code / portfolio_tier
  overrides); the wipe/purge scopes gained (mov_rsi, component codes,
  TRUE); the shared market cells ride EVERY pair's signal_rule_ids, a
  leg cell its own pair's (_sourced_member markers peeled in
  run_family, the ids linked in _write_portfolio_tables); the config-
  stamped `standalone` sub-type list was REMOVED (the registered rows
  replace it). Rebuild: 871 sourced mov_rsi rows / 11,853 events, the
  showcase pair carrying 27 of them in its 30 rule ids, rendered in
  the panel's "Non-portfolio signals · mov_rsi (registered)" group
  with their own measured evidence and the member benefit boost.
- 2026-10-25 (latest): the PORTFOLIO_SUB_TYPE SPLIT + the RSI MAPPING
  (user-directed "add portfolio_sub_type in portfolio_signal_rules,
  then migrate trio wash and spread to as portfolio_sub_type" + "rsi
  signals themselves should not sit in portfolio signals, by portfolio
  signal_rule_ids to map them, in other words rsi14/rsi10 themselves
  should not just sit in parallel to other portfolio cell signals ...
  onclick to expand"): the NEW label domain portfolio_sub_type
  (0 trio_wash, 1 spread; all three mirrors, applied by temp_scripts/
  apply_portfolio_sub_type.py — an in-place ALTER + PK rebuild on 03/
  05/06, the merged per-pair rules rows deleted, the audit snapshots
  backfilled 0, the events backfilled EXACT from their orchestrating
  sub) made portfolio_signal_rules ONE ROW PER (code, portfolio_type,
  portfolio_sub_type) — the pair's TWO portfolio rules are SEPARATE
  rows, each with its OWN ids array, per-sub coordination spec
  (rule_params.subs ONE key per row: hp_entry_trio on trio_wash;
  hp_entry_spread + hp_exit_spread both on spread), is_valid and
  audit; 05/06 gained the column + PK member. The engine stamps each
  orchestrating row's rule (_portfolio_sub_type marker + the per-sub
  rule_params.name "trio wash"/"spread", config
  HP_PORTFOLIO_SUB_TYPES); run_family groups by (code, sub). The
  REGISTERED mov_rsi cells NEVER sit in the portfolio signals: each
  rule MAPS them through its signal_rule_ids only (the engine's
  _sourced_member markers name the targets — the shared market cells
  ride every pair's trio_wash row ONLY, the spread disregards the
  benchmark; a leg cell rides its own pair's trio_wash AND spread
  rows), and they stay REAL signal_rules rows under the
  is_in_portfolio TRUE tier — a SEPARATE row from any standalone twin
  (the flag is a PK member there). The UI: the roster is ONE ROW PER
  PORTFOLIO SIGNAL (trio wash / spread chips), and inside the
  expansion the mapped rsi cells nest UNDER the portfolio signal row
  (user-refined "rsi6_1pct vs hp_entry_trio, one should be under
  another"): one toggle over the mapped tree, then one expandable
  SUB-TYPE group row each (rsi14_1pct, rsi10_1pct, rsi6_1pct, ...
  window-desc) over its component-code cells — never parallel
  cell-signal rows. Rebuild (index, --force): 94 portfolio rows
  (47 trio_wash + 47 spread), 2,395 events stamped per rule (772
  trio / 1,623 spread), the showcase pair's trio row 28 ids (1
  orchestrating + 27 mapped) and spread row 20 ids (2 + 18); the
  orchestrating ids DISJOINT across the two rules, the leg cells the
  legal overlap; 18 [cudf fallback] lines, one pre-existing signature.
- 2026-10-25 (last): the WITNESS MIGRATION into the rule params
  (user-directed "migrate rsi·witness mkt / hp_rsi_witness_mkt, etc,
  from DB signals to rule params json ... on portfolio level there is
  no Portfolio signal no portfolio Sub type, but in rule params json
  should have all; onclick to show the migrated Mapped rsi signals
  ... ensure mapping, ok to duplicate the mappings"): each stored
  cell-signal witness record under rule_params.subs.<sub>.cell_signals
  carries ``mapped_signals`` — the registered mov_rsi sub-type LABELS
  the rule maps at the witness's OWN curve, ACTION-SIDE aligned (the
  trio's witnesses all buy-side; the spread's legs split buy/sell by
  role; both trio windows' witnesses duplicate the same per-code
  lists — sanctioned), and the top-level rule_params gained the
  ``portfolio_sub_type`` LABEL WORD beside ``name`` (the JSON is
  self-describing). The portfolio-level CELL SURFACE dropped its
  "Portfolio signal" group, orchestrating rows and "Sub type" column
  entirely: the ONLY population is the MAPPED RSI SIGNALS tree — the
  quoted toggle row over the ids-retrieved registered cells, then one
  expandable WITNESS group per (sub, RSI window), each witness row
  (role chip, no signal vocabulary) expanding to ITS mapped cells
  (verified: every mapped name resolves in the ids array — trio 6
  witnesses / 46 names, spread 4 / 18). The EVENTS' member witness
  records (portfolio_history_signals.member_signals) are untouched —
  dated event-time records the history panel needs.
- 2026-10-26: the WINDOW CLASSIFICATION inside each witness (user-
  directed "rsi10 and rsi14 both are mixed, there should have been
  classification between rsi14 vs rsi10"): an expanded witness's
  mapped cells no longer render as one mixed list — a WINDOW GROUP
  row (RSI(14) the classic first, then RSI(10), RSI(6); window-desc
  over the registered cells' own rule_params.window) sits between the
  witness and its cells, one open at a time, each group expanding to
  its own window's cell rows. Pure UI grouping — the engine's
  mapped_signals / ids mapping unchanged.
- 2026-10-26 (later): the FLAT WITNESS SET (user-directed "there are
  RSI(10) · 3 witnesses vs RSI(14) · 3 witnesses, remove them from UI
  and py and sql json"): the trio's rule_params.subs.hp_entry_trio.
  cell_signals is ONE FLAT array of the THREE witness curves (market +
  both legs) — the per-RSI-window keyed duplication ({rsi10: [...],
  rsi14: [...]}) is REMOVED from the engine writer, the 03 SQL grammar
  and the UI parser (a legacy keyed object still flattens, window
  dropped); each witness's condition is window-free ("RSI(10/14) <
  own bottom ... tail"). The RSI(14)/RSI(10)/RSI(6) classification
  the user asked for the day before lives ON — INSIDE each witness,
  over its mapped registered cells (their own rule_params.window).
  Rebuild (index, --force): trio 3 witness records / 23 mapped names,
  spread 4 / 18, all resolving in the ids arrays.
- 2026-10-26 (last): the RF FLOOR INVERSION FIX (user-directed "buy
  actions are triggered for roof not floor ... is sell action even
  working?"): the rf_break engine's floor mask had read the un-signed
  breach ratio since the 2026-10-16 first build — the floor sub
  types fired on close ABOVE the prior floor (the roof condition,
  masked by the K=5 absorb into rare recovery episodes: 27 events in
  six years, all positive-value) instead of the documented "first
  close BELOW the prior floor". The breach pct is now DIRECTIONAL
  (positive = beyond the level in the trade's direction; the
  recorded signal reads the breakdown depth, the sell convention
  value > bar) — roof side untouched (bit-identical events, the
  2026-05-13 rf255_med_roof buys intact). Force rebuild (index): 101
  cells / 1,098 events — 35 floor SELL cells / 237 events, every
  cell trading with positive sign-aligned dir_ave in fall/unknown
  trends (the measured evidence replaces the void "floors measured
  out" record; the biz record lives in signals-biz-logic). LATER
  the same day: the DEPTH-SPLIT GRADUATES (user-directed "implement
  the discoveries to new rules as sub type") — the rf_break family
  gained the rf{W}{infix}_roof_deep BUY and rf{W}{infix}_floor_deep
  BUY legs on the directional breach depth (self-adaptive k bars
  RF_ROOF_DEEP_BARS {1,2,3} / RF_FLOOR_DEEP_BARS {2,3,5}, deepest
  bar first; labels 5640-5673 in all three mirrors, applied by
  temp_scripts/apply_rf_deep_labels.py; bar_def.min_breach_pct
  records the winning k). Rebuild (index, --force): 276 cells /
  1,955 events; the catch-all sub types bit-identical; donchian
  rf255_roof_deep honestly pooled-rejected (year majority 3/6);
  study: temp_scripts/rf_depth_split_study.py.
- 2026-10-26 (latest): the LEGACY CLEANUP + the FLOOR BOUNCE
  (user-directed "do legacy roof/floor signal clean up, remove roof
  for buy, floor for sell decisions, but totally replace with the
  above findings"): the CATCH-ALL sub types rf{W}{,_med}_{roof,floor}
  (labels 5600-5633) are RETIRED — decode-only vocabulary now, zero
  rows after the wipe (verified) — and the family is ALL-BUY: the
  deep tiers plus the NEW rf{W}_floor_bounce BUY (labels 5680-5683,
  applied by temp_scripts/apply_rf_bounce_labels.py; mirrors updated
  + check_label_dim OK) — the close breaks the prior day's MEDIAN
  floor while the DONCHIAN wick floor holds (the gap study's
  consensus-broken / wick-intact bounce; ONE cross-method population
  per window, no method infix). The breach dict moved to the run()
  top level (computed once for BOTH methods — the bounce reads both
  floors); bar_def carries the guard block (the donchian level +
  condition). The knowledge doc fully rewritten + re-seeded
  (retired_sub_signals note included). Rebuild (index, --force):
  207 cells / 1,174 events — 32 bounce cells / 317 events (every
  cell positive, all fall-trend), 175 deep cells / 857 events;
  donchian rf255_roof_deep still honestly pooled-rejected; 2
  sanctioned fallback lines (Rolling.quantile, pre-existing).
  Studies: temp_scripts/rf_solid_weak_study.py (five-method
  dispersion) + rf_gap_study.py (the donchian-vs-median gap, the
  confirmation matrix) + verify_rf_cleanup.py.
- 2026-10-26 (the PE PK AXIS + the REBOUND family, user-directed
  "similar to is ohlcv boosted as part of pk, implement is pe boosted
  and add it as new part of pk, then implement above as a new signal
  family rebound, there should be with and without pe"): the study is
  temp_scripts/ma_spread_rebound_study.py +
  docs/ma_spread_rebound_study_2026-10-26.md. The SCHEMA change:
  is_pe_boosted BOOLEAN joined all THREE tables' PKs (signal_rules /
  history_signals / history_signal_performance — applied by
  temp_scripts/apply_rebound_pe_pk.py, the apply_signals_portfolio_pk
  precedent; run.py's RULES_PK / HISTORY_PK / PERF_PK + the perf_rows
  builder carry it; the rules API's LATERAL performance join equi-keys
  on it and the history endpoint dedups per-variant) — a
  PE-conditioned variant of a rule is a SEPARATE row from the
  unconstrained one (run_cells gained the pe_boosted kwarg stamping
  both row kinds; the wipe/purge scopes stay (type, codes, tier)
  triples — no pe filter, so a family's wipe covers both its
  variants). The FAMILY: the rebound engine runs the study's causal
  state machine per (window, depth) — the host boundary
  (episode_events + trailing_pct_rank in engine.py, the study
  script's machinery moved in) — and emits the three resolutions as
  the rebound_{capit,fail,reclaim}_{60,255} sub types, each in a
  PLAIN and a PE-CONDITIONED run_cells call (the pe call's candidate
  masks AND-gated by the code's own PE-percentile condition; the
  depths {10%, 5%} are the CANDIDATES within one call — the first
  build's depths-as-calls structure collided on the PK and was
  restructured). Fetch (fetch/inputs/_rebound.py): the bollinger base
  shape + LEFT JOIN stats.index_valuation.pe (the is_pe_boosted input;
  NULL pe means the PE cells legitimately starve). Labels: signal_type
  9 + sub types 5900-5905 (all three mirrors, check_label_dim OK);
  knowledge doc database/sql/static/analysis_signal_docs/
  09_rebound.sql; UI: the families/rebound.ts registry entry + the
  rebound_resolution bar decoder (renders the PE gate word) +
  ruleCellKey carrying the flag. First force rebuild (index): 117
  cells / 598 events / 117 perf snapshots — every sub type emits in
  BOTH variants, all cells gate-valid; zero new fallback signatures
  (2 pre-existing Rolling.quantile).
- 2026-10-26 (the OHLCV-boost RULES-PK member, user-directed "for
  is_ohlcv_boosted, it exists in history_signals, but missing from
  signal_rules, add is_ohlcv_boosted to signal_rules as part of pk as
  well, and migrate related py implementation"): is_ohlcv_boosted
  joined the signal_rules PK (and history_signal_performance's — the
  variant axis of the cell key must stay joined column-for-column with
  the snapshot key, the is_pe_boosted lesson; applied by
  temp_scripts/apply_ohlcv_boosted_rules_pk.py). The flag is the
  RETIRED variant axis kept for the key's shape (the twins era ended
  2026-10-16): it reads FALSE on every row; run_cells gained the
  ohlcv_boosted kwarg (mirroring pe_boosted) stamping both row kinds
  so the two variant axes stay symmetric, run.py's RULES_PK /
  PERF_PK + the perf_rows builder carry it, and the rules API
  projects it with the LATERAL performance join equi-keyed on it.
  Regression: bollinger --force rebuilt cleanly (87 cells / 380
  events), the rebound incremental scope skips, every rule row ships
  both variant flags.
- 2026-10-26 (the SHARED VARIANT AXES — user-directed "create shared
  pe and ohlcv logic for analysis signals, in signal generation to
  use them where possible or relevant" + "on UI some signals ... no
  need for all as sometimes the boost could negatively optimize
  signals"): the variant logic went SHARED — engines/_variant_base.py + the
  engines/_pe_boost / engines/_ohlcv_boost packages (the 2026-10-27
  class OOP restructure: the VariantGate ABC + the PeBoostGate /
  OhlcvBoostGate classes, engines/boost.py moved to
  engines/_ohlcv_boost/measurement.py)
  (trailing_pe_pct the per-code host boundary moved verbatim out of
  the rebound engine; pe_gate_mask/pe_gate_def the extremes /
  not_cheap vocabulary; ohlcv_gate_mask/ohlcv_gate_def the
  turnover-climax confirmation amt_climax >= HEAVY — the OHLCV boost
  returns to GENERATION as a variant axis, twin-era key kept;
  has_pe_data the scope saver; gated_candidates the AND-gated
  candidate builder) + the shared config constants (PE_MIN_PERIODS /
  PE_GATE_EXTREMES / PE_GATE_NOT_CHEAP / OHLCV_BOOST_MIN_AMT_CODE;
  the REBOUND_PE_* names are aliases). REBOUND refactored onto it
  (A/B bit-identical: per-code md5 of history + rules rows identical,
  +4 rows only at the fresh 2026-10-26 data edge). NEW EMITTERS:
  rf_break (the fetch gained the index_valuation.pe join; floor_deep
  + floor_bounce run the PE-EXTREMES variant — the capitulation
  barbell; roof_deep + floor_deep the turnover-confirmed variant;
  375 cells / 2,122 events at index scope) and BOLLINGER (both sides'
  band breaches run the OHLCV-boosted variant — the 2026-10-16
  boost-study graduate family; 129 cells / 504 events). The bar gate
  fragments ride bar_def.pe_gate / bar_def.ohlcv_gate (NESTED keys —
  the first cut flattened them into bar_def and the UI reader missed
  them; rebuilt). The LIVE active set excludes variant-axis cells
  (the breach check has no PE / turnover machinery — their events
  are history-only). UI: ruleBarLabel renders both gate words
  generically on every family's bar word; the Short-Term Tech history
  panel's PE boosted / OHLCV boosted columns (built the same day)
  tick where generation uses the variants. mov_rsi / momentum /
  ma_cross / macd stay plain for now (the shared module leaves them a
  few-line opt-in; the negative-optimization concern is exactly why
  adoption is selective). The RULE PARAMS carry the axes too
  (user-directed "update rule params if ohlcv and pe is used to
  boost"): run_cells stamps pe_boosted / ohlcv_boosted true at the
  top level of a variant's rule_params beside the PK columns (the
  self-describing JSON convention; the conditions ride
  bar_def.pe_gate / bar_def.ohlcv_gate, the plain rows carry no
  key), rendered as the details block's boost row.
- 2026-10-26 (the CELL-SIGNALS ACTION key + the SIDE-ALIGNED MAPPING,
  user-directed "in cell signals in Portfolio signal rules, since cell
  signal carry action, should also show action on UI", refined the same
  day "the portfolio level should not have action ... if the rules say
  buy, why the registered cell signals still have sell action"): each
  stored witness record under rule_params.subs.<sub>.cell_signals
  carries ``action`` (the LABEL word, the portfolio_sub_type precedent —
  trio witnesses all buy, the spread legs split by role, the exit
  flips; fragments.py stamps it, the 03 SQL grammar documents it).
  Rendered NOWHERE at the portfolio level (the witness rows are
  rule-params condition records — the 2026-10-25 "portfolio has no
  action" semantics stands): the cell-signals table's Action column
  chips only the REGISTERED cell rows (their own signal_rules.action),
  and the stored key resolves a witness's mapped cells ACTION-SIDE
  ALIGNED — mapped_signals names the sub type only, which mov_rsi
  registers on BOTH sides, so the UI's name-only match had leaked the
  sell twins under a buy witness (19 resolved rows for 14 buy names);
  mappedFor now requires r.action === w.action (legacy records without
  the key keep the name-only match). No pipeline change beyond the
  stamping — hedge_pairs --force rebuilt (47 trio / 88 spread
  orchestrating cells, 1230 registered rows, 94 portfolios, the
  pre-existing fallback signatures only).
- 2026-10-26 (the post-write AUDIT GATE, user-directed "base on the
  mistake, in signal gate to add the check and print in logs ... study
  more what else could trigger mistakes and include them in gate so
  that mistakes can be caught when running the port and signals"):
  analysis/analysis_signals/audit.py — the in-run cross-tier
  consistency checks, wired at the end of run_family (every family
  run) + inside the portfolio write. TEN drift classes, each traced to
  an incident or a silent-failure mode: (1) witness SIDE ALIGNMENT
  (mapped name resolving only to opposite-side rows — the day's UI
  bug, now caught at the source) and dangling mapped names; (2) ids
  REACHABILITY (a mapped registered cell not riding the portfolio
  rule's signal_rule_ids is invisible to the UI's ids-array fetch);
  (3) ids INTEGRITY (dangling signal_rule_ids); (4) witness SHAPE +
  the *_buy_leg/_sell_leg ROLE/ACTION pairing (the spread-exit flip
  class); (5) witness COVERAGE vs the construction block (dropped/
  duplicated witness curve); (6) the EVENT member invariant (>=2
  members on >=2 codes + trio same-side / spread one-buy-one-sell
  semantics); (7) ORPHAN rows at codes outside the run universe — the
  2026-10-25 universe-shrink incident class, now a standing post-write
  DB query instead of a one-time cleanup script; (8) VOCABULARY
  decodability of every smallint code; (9) rules<->history LINKAGE
  (an event whose cell didn't emit vanishes from the panel); (10)
  PERFORMANCE linkage (a rule without its snapshot renders blank).
  Violations are LOUD per-line [audit] warnings + a summary count; the
  run never aborts (the log is the alarm). Verified: the negative
  harness temp_scripts/test_audit_gate.py 13/13 (every corruption
  fires its line, clean baseline silent); live hedge_pairs --force
  and bollinger --force both log "[audit] ... all consistency checks
  passed" with write counts bit-identical to the pre-audit runs.
- 2026-10-26 (the MACD FAMILY + the TYPICAL EMA, user-directed "study
  what most typical ema is, eg used 6d 12d 26d, etc, and implement it
  to replace the nearby ema period if any" + "2nd task is macd signal
  in short term tech"): the study — the most typical EMA usage is the
  classic MACD set 12/26/9 (Appel's original, the industry default);
  the repo's nearby non-standard span 10 (stats.*_tech_stats.ema10)
  was REPLACED by ema12 + ema26 (SQL-first: the three stats schema
  files + the three reconstruct views; the in-place migration +
  full-history backfill temp_scripts/apply_ema_typical_spans.py —
  832,783 index / 1,100,627 etf / 7,099,773 stock rows, spot-checked
  0.00005 vs the pandas ewm convention; the builds compute the new
  spans; the strategy-tier MacdAlgo defaults moved 10/60/6 → the
  classic 12/26/9). The FAMILY: macd (signal_type 10, sub types
  macd_cross_up 6000 / macd_cross_dn 6001 — labels in all three
  mirrors + check_label_dim OK, knowledge doc 10_macd.sql, the UI
  families/macd.ts + the macd_cross bar decoder) — the fetch computes
  macd_line/EMA9-signal/histogram IN-FETCH from the base closes
  through compute_emas (the _shared breadth precedent — the kernel
  needs the clean contiguous fetch frame; a pre-created empty column
  collision at the rename was the first build's one bug), the engine
  shifts + compares (the strategy algo's cross detection), K=5
  episodes, REGIME-FREE with a trend split, value = histogram
  pct-of-close vs reference 0.0, depth = |hist| in its own trailing-
  2550d sigma. First force rebuild (index): 35 cells / 429 events /
  35 snapshots, every cell gate-valid (the fall-trend buys +6.07%
  carry the reversal edge, rise-trend the continuation, sells exit
  timing); 2 pre-existing Rolling.quantile fallback lines, no new
  signatures; the run's [audit] all consistency checks passed.
- 2026-10-27: the CONDITION HARD HOOK (user-directed, two parts: "fix
  Portfolio signal rules — the Rule condition said 'RSI(10/14) < own
  bottom 1/2/3/5/10/15/20% tail · trailing 2550d bar · prior day' but
  the actual registered cell signals are diff, e.g., have rsi6, no 15%
  nor 20%; study to base on cell signals to correct rule conditions" +
  "study a better way to hard hook rule vs actual signals, e.g., rule
  condition text is concat from cell signal names and actions"): the
  stored portfolio-rule condition texts had been written from the
  CONFIG CANDIDATE GRIDS (HP_LEG_RSI_WINDOWS x HP_LEG_TAIL_PCTS — the
  trio's scan vocabulary) while the mapped registered cells show the
  standalone vocabulary (HP_STANDALONE_RSI_WINDOWS (14,10,6) x
  HP_STANDALONE_TAIL_PCTS (1,2,3,5,10)) — text vs cells drift by
  construction. fragments.py now BUILDS every text from the actual
  emitted rows, never the grids: a witness record's condition =
  its mapped registered cells' own (window, tail) vocabulary
  (_tail_side_text — windows desc, tails asc; CONCISE 2026-10-27,
  user-directed "collapse to rsi(14/10/6) if all these rsi follow the
  same rule conditions": the skeleton stated ONCE — equal tail sets
  share one clause "RSI(14/10/6) < own bottom 1/2/3% tail", divergent
  sets one parenthetical "RSI(14/10/6) < own bottom tail (14/10:
  1/2/3/5/10% · 6: 1/2/3/5%)" instead of repeating the skeleton per
  window; bar line from their bar_def window_days/shift), and the sub's trigger sentence = the
  row's OWN winner bar_def (_trio_sub_condition over bar_def.windows
  / _spread_sub_condition over bar_def window+tail_pct — the fired
  bar, not the scanned grid; the candidate grids stay in the
  coordination block, their documented home). The grid texts survive
  only as a malformed-row guard. Zero-mapped witnesses fall back to
  the row winner's bar facts (e.g. TECH_GENERAL sell: zero
  registered sell cells — the record states the spread winner's bar).
  The 03 SQL header documents the hook. Rebuild (index, --force):
  1,366 rule cells / 23,861 history rows / 94 portfolios / 2,335
  portfolio events; 1,232 registered mov_rsi rows / 21,526 events
  (the universe drifted since the last build — 3 pairs dropped);
  verified 449 stored texts == the row-derived composition, 0 drift
  (temp_scripts/verify_condition_hard_hook.py); the portfolio API +
  the Hedge Pairs page render the per-curve conditions (showcase
  trio: market RSI(14/10/6) 1/2/3%, leg A RSI(14/10) 1..10% +
  RSI(6) 1..5%, leg B three per-window segments, 9/14/9 mapped).
  The audit's orphan flags (the known universe-shrink recurrence —
  3 dropped pair codes + the dropped leg's mov_rsi TRUE tier) were
  purged by temp_scripts/cleanup_hedge_pair_orphans_20261027.py
  (9 + 197 + 11 + 168 rows, exactly the audit's counts).
- 2026-10-27: the DOC TIER CLEANUP (user-directed). The PORTFOLIO
  knowledge docs DROPPED — static.portfolio_signal_docs (the
  2026-10-20 analysis_signal_docs convention at the portfolio tier:
  one JSONB per portfolio_type, joined by fetchPortfolioSignals)
  was read by NOTHING (the roster trigger lines build from
  rule_params.subs; the type's claims live in
  history_portfolio_signal_performance.benefit), so the table, its
  seed directory (01_neg_corr_hedge_pair.sql was never committed —
  its content is gone with the drop), the service fetch and the
  response's document field are all removed. Same day, the
  analysis_signal_docs seeds were PRUNED to the rendered fields —
  {sub_signals{<sub>: {meaning, trigger}}} (ruleDetails' two
  strings); name/description/condition/sources/principles and the
  per-sub action/params were dead payload (64.3 KB → 17.9 KB,
  re-seeded).
- 2026-10-27 (the MACD PRECOMPUTED-SOURCE REBASE, user-directed
  "check if macd uses the newly build ema data rather than
  self-customizing re-compute for new ema"): the macd family's fetch
  had been computing its EMAs in-fetch from the base closes
  (compute_emas) while the builds' tech_stats tables now carry the
  typical ema12/ema26 — REBASED: the fetch LEFT JOINs
  stats.{etf,index,stock}_tech_stats and reads the columns (the
  strategy-tier MacdAlgo's source verbatim — ONE EMA construction in
  the repo); only the EMA9 signal line still computes inline (an EMA
  OF THE MACD LINE has no precomputed column). run.py gained the macd
  _source_horizon_sql branch (the tech_stats per-code max(date) — the
  data-keyed idempotence rule). A/B at index scope
  (temp_scripts/macd_ab_{snapshot,diff}.py): cross days preserved
  except 11 marginal flips (histograms within the NUMERIC(18,4)
  storage rounding of the built columns — the builds' warm-started
  stored EMAs vs the exact recursion, inherent to adopting the built
  data as source of truth); 35→36 cells / 429→432 events, audit all
  passed, zero new fallback signatures, incremental skip verified on
  the new horizon. Knowledge doc 10_macd.sql re-seeded under the new
  static/signal_rules/ tree (static.signal_rules_docs).
