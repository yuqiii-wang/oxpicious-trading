---
name: signals-biz-logic
description: >
  The FINANCIAL/BUSINESS semantics of the NINE standalone signal
  families plus the hedge_pairs PORTFOLIO family (the fresh typical
  trio, 2026-10-24) in oxpicious-trading — mov_rsi (RSI percentile
  tails, entry windows 14/10/6), bollinger (fixed-σ band breach,
  window 20), ma_cross
  (price-vs-MA events, NOT a golden cross, + the market-level
  breadth thrust / 9:1 days), options_wall (VRP-gated
  OI-wall breach), options_vix (vol spike inside a deep-fear corr
  regime + the vol-ATH panic peak + the ATR volatility-expansion
  breakout), momentum (log-return tail breaches
  over vol-adaptive lookbacks), rf_break (the roof/floor Donchian
  breakout — the prior day's window-extreme level, a STRUCTURAL
  bar), rebound (the drop→rebound→resolution family: the
  capitulation trough-break BUY, the failed-reclaim exit-timing SELL,
  the full-reclaim continuation BUY — each in a plain and a
  PE-conditioned variant), macd (the
  classic 12/26/9 signal-line crossover, 2026-10-26),
  MEANS as a trade
  (side, population, trigger, exit timing), the measured discoveries
  behind every parameter (equity asymmetry, band walks, re-entry
  artifacts, VRP gating, spike-regime interaction, the 2026-10-16
  sub-rule study's graduates and honest negatives), and the
  best-practice audit of each design. Use for ANY work that
  interprets, tunes, or changes the trigger logic, thresholds, sides,
  or populations of these families, or answers "what does this signal
  mean" / "is this parameter right" — even when the user does not
  mention signals explicitly. Architecture (rule cells, pipeline,
  live wiring) is the sibling skill signals-live-vs-history.
---

# Signal families — business logic and evidence (rules era)

The BIZ side of the signals: what each family means as a trade, and
the measured evidence behind every rule. Architecture — the rule-cell
tier, pipeline, trend/regime splits, live wiring — is the sibling
skill `signals-live-vs-history`; load that one for tier/pipeline work,
this one before touching trigger logic, parameters, sides, or
populations.

Implementation shape follows the repo's class-first preference
(`oxpicious-dev-conventions`): each family's semantics live in its
`rule_base` engine subclass, and new evidence/config carriers are
pydantic models — the biz rules below are never re-expressed as loose
module-level static functions.

## The one principle everything descends from: equity asymmetry

Measured repeatedly on the 10 study indices: **sharp falls revert;
sharp rises continue.** The two sides of every family are NOT mirror
images:

- The sell gate is 0 (any measured reversal edge) vs the buy 0.25%
  (`config/__init__.py` GATE_DIR_AVE_MIN; LOOSENED 2026-10-16 from
  0.75% — the moderate, user-directed step; measured graduation 350 →
  613 passing cells at index scope) — overbought extremes are
  MOMENTUM for drift-up names; a SELL is EXIT TIMING for longs, never
  a short entry.
- Sample floors 2 sells / 3 buys (loosened 2026-10-16 from 3/4) —
  sell evidence is structurally thinner under positive drift. Since
  2026-10-19 the floors are the CONFIDENCE REFERENCE, not a gate
  (user-directed): a cell under its floor trades at the starvation
  scale min(n / floor, 1) of the confidence — thin evidence
  discounts the claim instead of silencing it.
- ma_cross gdepth has NO top side: deep gaps ABOVE the MA measured
  uniformly negative.

This matches the published record: short-term reversal after sharp
drops (Dunis et al. 2011, Da 2011), continuation near highs (George &
Hwang 2004), short-sale constraints (Zhu et al.). Any proposal to
"symmetrize" the sides must beat these numbers, not an aesthetic.

## The shared evaluation contract (how "does it work" is decided)

Every family is judged identically, so parameter debates end in
measurements, not taste:

- Per-cell measurement on each code's own full history: a cell is one
  (code, sub signal, trend[, regime], action) tuple; a strategy IS
  its delay-0 profile (the trigger is each qualifying episode's FIRST
  day).
- The profitability metric is the FIXED forward blend `dir_ave` (next
  0.10 / 3d 0.15 / 5d 0.50 / 10d 0.15 / 20d 0.10); the gate is the
  sign-aligned mean clearing the side's bar at the sample floor.
- One signal per EPISODE, never per day: qualifying runs merge, and a
  run only ends after MORE THAN 5 consecutive trading days out of band
  (K=5, `analysis.trends.episodes.REENTRY_EXIT_DAYS`) on the code's
  OWN traded days. The K discovery: the deep-tail band's mean
  INVERTED (+1.47% → −1.39%) without absorbing, because 18.1% of
  inter-day gaps were holiday closures — the "reversion" was a
  re-entry artifact. K swept over {0,1,2,3,5,8,10}; 5 is past the
  knee.
- TREND-AWARE absorb (mov_rsi only, 2026-10-15): the K window merges
  only SAME-trend re-entries. Measured A/B (index force rebuild): +6
  episodes all in extreme-trend cells; 4 of 5 changed buy cells
  IMPROVED dir_ave (000683 1pct bottom 2.58→3.11%, 000991
  1.20→1.67%); the canonical rescued event: 000001's 2026-01
  exhaustion absorbed into a rise-trend cell that fails the sell gate
  — trend-aware it fires 2026-01-13 in the top cell. bollinger /
  momentum keep the plain absorb until their own A/B is measured.
- Confidence = the cell's measured dir_ave (base) scaled by breach
  depth (capped 2×) and by the STARVATION SCALE min(n / floor, 1)
  (2026-10-19 — under-floor cells trade at reduced confidence) —
  never a fitted curve; the deeper the pierce, the stronger the
  claim, up to the family's depth normalizer.
- STARVED CELLS TRADE (2026-10-19; retiring the 2026-10-16 wait
  record): a cell with episodes below the sample floor emits its REAL
  action at the starvation scale min(n / floor, 1) of the confidence
  (gate.starved + gate.conf_scale on the performance snapshot). The
  monotone guarantee for extreme events survives in a trading form:
  nested carving assigns a deep breach to its DEEPEST claim, and
  those deepest-claim cells starve exactly at the extreme corners
  where the events land (the 2024-09-30 / 2026-10-15 studies, and
  000001's 2025-04-08 tariff-crash wait rows that prompted the
  change) — the record is now a REDUCED-CONFIDENCE trading row, not a
  not-trading-yet wait. Measured-out cells (dir_ave below the bar,
  any n) still emit nothing — that is rejection on evidence, not
  starvation.
- THE POOLED STARVATION RETREAT (2026-10-19, later the same day —
  the per-cell retreat one level up): the pooled tier's n ≥ 10 floor
  stopped BLOCKING too. The statistics legs (the 0.25% bar, t ≥ 1,
  the measurable-year sign majority) stay VERDICT legs; n between
  POOLED_TRADE_FLOOR (3) and POOLED_MIN_N (10) TRADES at the pooled
  completeness scale min(n / 10, 1), folded into the same
  gate.conf_scale the live tier already applies (pooled.starved +
  pooled.conf_scale on the snapshot). What prompted it: the
  2026-10-19 loosening rebuild measured ow_put buy n=7 +6.17% t=3.96
  and ovix_spike n=9 +3.33% t=4.55 — both REJECTED on n alone while
  statistically significant. Below the trade floor the side still
  emits nothing (the etf ovix_spike_hivol n=1 is the honest example).

## The market-regime layer (stats.market_regimes)

Four per-code states from the code's OWN trailing history: return-based
EWMA(span=20) vol z-score and log-amount z-score over trailing 255
days, SHIFTED one row (no look-ahead), bars 1.0/1.0: calm 0 (~71%),
hot 1 (vol AND amount — stress), panic 2 (vol without volume — the
unwind), quiet 3 (volume without movement — accumulation); 4 'all' is
the POOLED stamp, never a regime row.

The premise (`docs/market_regimes_study.md`): sign-aligned reversal
after tail breaches is 5-9× stronger in hot/panic than calm/quiet.
Regimes are SPLITS, never weights (regime-weighted orderings measured
WORSE than equal — double-counting). Usage: mov_rsi, bollinger and
momentum keep the regime split; ma_cross went regime-free 2026-10-12
(per-regime cells thin and sign-flippy); the options families pool
(trend 4 / regime 4) for sparsity. Days with no regime row default
calm — a stale build silently reads as calm. (The 2026-10-15
regime-join double-conversion bug stamped every cell calm for the
whole early rules era; fixed + rebuilt — mov_rsi 58 → 25 cells and
non-calm populations finally measured separately.)

## mov_rsi — the RSI percentile family

A BUY fires when Wilder RSI(W) falls below the code's OWN bottom
1/2/3% tail quantile over the trailing 10y — the capitulation; a SELL
when it exceeds the top tail — the exhaustion exit. Bars are
PER-CODE ADAPTIVE percentile quantiles (never fixed 30/70 — the
2026-10-05 fixed-band attempt collapsed occurrence 35,868 → 3,370).
The entry grid `RSI_ENTRY_WINDOWS = (14, 10, 6)` (2026-10-16):

- **14** the Wilder classic — convention, kept.
- **10** the measured best buy window (2026-10-15 window study,
  temp_scripts/rsi_window_study/: rsi10_1pct +1.91% @72.2% pooled,
  positive 8/10 codes, 5/6 years).
- **6** the study's runner-up, added 2026-10-16 after the rules-era
  reproduction (temp_scripts/subrule_candidates_study.py: rsi6_1pct
  buy pooled +1.78% @71.2 — the study's own number — with
  gate-passing buy cells at every tail pct; mov_rsi 25 → 37 cells,
  113 → 167 events).
- rsi3 was measured for the SELL side (the threshold era's "broadest
  sell coverage") and MEASURED OUT 2026-10-16: pooled −1.05% @50.7 —
  the passing cells ride a −5.46% top cell. Do not re-add without new
  evidence.
- Sell side pooled NEGATIVE for every window (the asymmetry again) —
  sells survive only as per-cell exit-timing populations.

The population is the whole tail EPISODE (a code enters shallow and
burrows deep). Live: a STATE family — keeps flagging while the breach
persists, against the stored adaptive bar. An ohlcv-boost opt-in (the
day-0 anatomy trust score); its sides fail the ship gates honestly —
the 2026-10-16 run: bottom FAIL gates, top FAIL gates, `ohlcv_boost_
conf` all NULL.

## boll_std — the Bollinger fixed-σ family

A BUY fires when the close's σ-depth z = (price − SMA_W)/σ_W pierces
−k; a SELL at +k. Window 20 ONLY in the rules era (sub types
boll20_{2,2.5,3}std, `BOLL_KS`); population σ (ddof=0) matches
charting convention; z already IS the Gaussian depth score
(Φ⁻¹(1−q) == |z|), so no percentile layer.

- STRUCTURAL FACT (measured 2026-10-16): a W=5 band can NEVER breach
  2σ with population σ — max |z| over an n-point window is
  (n−1)/√n = 1.79 for n=5. boll5 is untriggerable at k ≥ 2 by
  construction; do not re-add without changing the σ convention.
- Squeeze → breakout (the audit's candidate future rule) was MEASURED
  OUT 2026-10-16: downside breakout after bandwidth compression
  pooled −0.15% (compression → breakdown CONTINUES here, not a
  reversal).
- PROPOSED (2026-10-16, pending): `boll60_2p5std` buy — pooled
  +1.96% @67.1% — John Bollinger's own "scale k with the window"
  rule (2σ at 20, 2.5σ at 60) made systematic; sell side at W60
  pooled negative. See docs/sub_rules_proposal_2026-10-16.md.
- Confidence scales 0.25 per σ of breach depth. Entry-style
  (SUDDEN/RE-ENTER) semantics ride the episode layer; no curve tier
  since 2026-10-14 (measured monotonicity was weak — re-adding
  requires new measurements, not a design argument).

## ma_cross — the price-vs-MA event family

NOT a golden cross / death cross (published MA-over-MA backtests run
mixed-to-negative). "Cross" = PRICE crossing back over a SINGLE MA,
conditioned and gated. Three entries (regime-free, trend split):

- **ma20_cross / ma60_cross** (buy): the first close back ABOVE the
  MA conditioned on the CROSS EXCESS — the signed discount at the
  last below-day; the engine picks the per-(code, trend) bar from
  {1, 2, 3}% (self-adaptive). Current evidence: cross20 pooled
  +2.06%, cross60 +1.92%.
- **ma20_retest** (sell): an MA breakdown closing a re-test rally
  whose above-episode started within 20 traded days of the previous
  episode's end — the failed rally / double top. 25 cells, pooled
  +0.08% (cells pass individually; exit-timing).
- **ma120_gap / ma255_gap** (buy ONLY): the raw-gap band breach below
  the MA at {3, 5, 8}% (gdepth). NO top side — deep above the MA is
  momentum. Pooled +1.97% / +2.28%.
- EMA twins (ex/eretest/ed/eg of the threshold era) were re-measured
  2026-10-16 and MEASURED OUT: SMA dominates at every kind under the
  rules-era cells (EMA cross pooled +0.32/+0.33% vs SMA +2.06/+1.92;
  EMA gdepth +0.52/+0.75 vs SMA +1.97/+2.28). The fetched egap
  columns may ride the frame, but no EMA rule graduates.
- PROPOSED (2026-10-16): `ma60_retest` sell (+0.40% @59.3%, 19 cells
  — the threshold-era dn60 +0.91% directionally reproduces) and
  `ma120/255_deep_turn` buy (the first up-day after a −8/−12% gap
  below the MA — the dropped deep-discount turn; −12% at MA120 pooled
  +1.37% @57.8%, 9 cells; complements gdepth's breach with the
  reclaim timing).
- is_typical = the trigger's excursion is an UNINTERRUPTED run so far
  — the whipsaw split; ALL conditioning is trigger-time-observable
  (episode FINAL sizes are hindsight and never condition anything).

## options_wall — the VRP-gated OI-wall family

"Dominant OI walls repel spot": the chain-dominant wall per side from
`analysis.options_walls`; a close MEANINGFULLY past a wall is a
reversion trade back toward it. Universe: the options underlyings
(000300/000016/000852 + SZSE ETFs; SSE ETFs excluded — accumulated-
volume OI). Rules era: breach-only (K=0 strict consecutive-day
episodes; pooled cells; confidence FLAT).

The SPARSITY FACT (2026-10-19 study, docs/
options_wall_sparsity_study_2026-10-19.md): the bar is the dominant
OI zone's OUTER edge, which sits median ~12% from spot (the zones
chain sparse deep strikes; the OI mass/center sits ~3% away) — only
~1.2% of days breach it at all, and that rare population IS the
edge. Density attempts measured OUT the same day: the nearest-
expiry dominant pick (1 gated pass), zone-entry at the near edge
(89% of days, +0.03%), and walls-as-STRIKES (max-OI and gamma×OI
picks from stats.options_greeks — 229/341 gated events at
+0.16%/+0.04%, far below the bar; SpotGamma-style strike walls are
proximate and EDGELESS here — the deep zone-edge breach is the
reversion event, matching the dealer-hedging story where a raw wall
breach otherwise ACCELERATES).

- **ow_put_025pct** (buy): close ≤ put_wall_low × (1 − width),
  VRP-GATED — vrp_ratio (implied vol30 vs rolling-20d realized)
  beyond its own trailing-255d percentile bar (the bar: 0.80 → 0.70
  → 0.60 across 2026-10-19's two steps; p50 halves the edge — do
  not go lower). The width collapsed to the SINGLE 0.10% (the
  {0.25, 0.10} grid measured a no-op — breach-day counts identical;
  the sub_type NAME's 025 is historical label vocabulary). The
  bar's warmup is the SHORT min_periods=120, not the 255d window —
  the full-window warmup erased whole codes (000016's entire put
  population sat in it). Current rebuild (2026-10-19, index): TWO
  trading cells — 000852 n=8 +5.44% hit 100% (the 2024-07/09
  window), 000016 n=8 +0.56% hit 75% (2023-12/2024-01) — pooled
  n=16 t=3.07 at FULL pooled confidence (the starvation scale is
  gone, n ≥ 10). The VRP-fail twin (`_rej`, wait) records the
  falling-knife population (+0.74% @40% — no edge ungated).
- **ow_call_025pct** (sell, ungated): close ≥ call_wall_high ×
  (1 + width) — exit timing. Index n=13 pooled +1.29% t=1.29
  (000300 n=10 + 000016 n=3).
- MEASURED OUT 2026-10-16: the put-wall CENTER approach
  (`ow_put_center_vrp`) — its threshold-era edge (n=68 @72.1% +1.31)
  is GONE on the current wall population (+0.09% @44.7; the ungated
  twin −0.01%). The call approach rally needs etf scope to evaluate
  (its threshold-era population was 84% one ETF, 159919).
- UNIVERSE GROWTH (P2) is source-blocked: the SZSE trend service
  only serves from 2025-07-01 (the 4 ETF codes' whole history);
  backfilling needs SZSE's historical-archive endpoint or a vendor.

## options_vix — the vol-spike-in-fear-regime family

BUY THE PANIC, conditionally. Axes: corr20 (the PUBLISHED 20-session
corr of spot log-return vs vol log-change, linearly interpolated
per code onto every session IN-ENGINE — signal and UI read ONE
series), slope5 (5-day vol slope), z252 (vol level z), on the repo's
own 30d implied-vol index. The premise: corr alone ≈ baseline, spike
alone weak — the INTERACTION is the edge.

- **ovix_spike** (buy): corr20 < −0.75 & slope5 > +10% — the bar
  LOOSENED 2026-10-19 from −0.80 (the measured ovix_spike_wide
  coverage play graduated into the main bar: pooled n=21 +1.17% @
  the 2026-10-16 study; the 000852 Feb-2024 crisis bottomed −0.74,
  just past the old bar). Cell (000852, index): n=6 +4.26% hit 100%
  at the pooled starvation ×0.9 (pooled n=9 t=4.55).
- **ovix_spike_hivol** (buy): spike & z252 > 1 — the interaction at
  its strongest (threshold era: +3.74% @83% fwd20). Cell: n=5
  +4.12% hit 100% at ×0.6 (pooled n=6 t=4.17).
- **ovix_panic_peak** (buy, 2026-10-15): the vol30 VALUE SPACE — vol
  > prior 120-session max × 1.05 & slope5 > +10% & spot below its
  5-session-ago close. The diagnosis: in V-spike crises the corr axis
  stays moderate (000852 Feb-2024 interp corr bottomed −0.74), and
  the K=8 absorb fires level conditions at crisis ONSET — the PEAK
  never gets its own marker; the escalation margin re-arms at the NEW
  extreme, and the downside leg separates fear from upside euphoria
  (the 2024-10-08 stimulus top excluded). Cells: 000852 n=5 +4.85%
  @80, 000300 n=4 +2.32% @75.
- **ovix_euphoria_peak** (sell, 2026-10-19): the panic-peak MIRROR —
  the same escalation bar with spot ABOVE its 5-sessions-ago close,
  the blow-off-top exit the buy legs deliberately exclude. The
  2026-10-19 off-spike study (temp_scripts/ovix_off_spike_study.py +
  ovix_grid_study.py) measured the mirror honestly: euphoria tops
  mostly CONTINUE (000016/000300 euphoria cells measured out — the
  asymmetry holding on the mirror side), and the leg trades only
  where the blow-off actually rolled over — 000852 2024-10-08 n=1
  +8.15% hit 100% at the starvation ×0.5 (sells never pooled-gated).
  The same study MEASURED OUT the candidate bar grids: corr
  {−0.75,−0.70} × slope-window {5,3} on spike/hivol/peak added ZERO
  events (the missed spikes fail both legs or have no measured
  edge), and the atr slope3 grid LOST 9 net events on
  precision-noise winner flips — do not re-add without new
  measurements.
- **ovix_atr_break** (buy, 2026-10-16): the spot day-return in
  close-to-close ATR units past the self-adaptive {1.5, 2, 2.5, 3}
  bar (k=1.5 added 2026-10-19 — the gate + pooled tier still judge
  it; the measured 2026-10-15 class fired at −2.51 ATR) while vol
  slope5 fires — the fast repricing from a LOW base (corr −0.60..
  −0.72 vs the −0.75 bar, vol −17..−28% below its 120d max, slope5
  +28%). Fired 2026-10-15 on 000852 (−2.51 ATR); crisis-leg history
  2024-10-09, 2025-04-07, 2026-02-02, 2026-03-23. Index: 45 events /
  3 cells (the winners stayed deep — the K=8 absorb merges the
  shallower runs).
- **ovix_fear_unwind** (sell): corr20 crosses back above −0.5 with a
  40-session deep-fear witness (corr min < the −0.75 bar) — exit
  timing. The 2026-10-19 index rebuild MEASURED IT OUT at the cell
  gate (the eased witness re-populated it below the 0 bar); the etf
  cells carry it (2 cells / 7 events).
- MEASURED OUT 2026-10-16: `ovix_rollover` (+0.48% @n=12 vs the
  threshold-era +2.32% @73 — the published-corr interpolation changed
  this population's character; 0 passing cells). ovix_spike_wide is
  RETIRED AS A NAME (2026-10-19): its −0.75 widening IS the main bar.
- Exits ride every rule doc: primary exit corr20 ≥ −0.5; a −8% stop
  halves the win rate — tight stops are HARMFUL here; max hold 60.
- OVIX_REENTRY_EXIT_DAYS = 8 (crisis regimes fire daily). Pooled,
  no curve, confidence FLAT, NOT live-wired.

## momentum — the log-return momentum family (2026-10-15)

A BUY fires when a lookback axis's LOG-RETURN ln(1 + momentum) falls
below the code's OWN bottom 1/2/3% tail quantile over the trailing
10y — the cumulative-crash capitulation; a SELL when it exceeds the
top tail — the run-up exhaustion exit. Self-adaptive at TWO levels:

- The LOOKBACKS are vol-adaptive upstream (`analysis.mov_ave_momentum`
  picks short/mid/long n per (code, regime) by equal-expected-move
  targeting) alongside the fixed 5/20d anchors — five axes:
  mom5 / mom20 / momshort / mommid / momlong.
- The BARS are per-code trailing percentile quantiles of the axis's
  OWN log-return; the confidence depth is normalized by the axis's
  OWN trailing sigma (a 5d and a 100d breach compare in sigma units).

The log transform is the enabler: raw ROC is bounded below (−100%) /
unbounded above, so percentile tails in raw space are dominated by
that asymmetry; ln(1 + momentum) is the symmetric additive space
where a trailing quantile tracks the code's own volatility level.
Plain K=5 absorb (trend-aware splitting is a measured A/B away, the
boll_std precedent). First builds (2026-10-15/16, index scope): 59
cells / 238 events; bottom-trend buys pooled +2.01% (16 cells),
fall-trend buys +1.35% (7 cells), and the strongest sells are
top-trend exits (pooled +2.94%, 19 cells). Two honest flags: `momshort` ≈ `mom5` (the vol-adaptive short
lookback sits at 5 days for the study indices — the two sub signal
types emit near-identical cells; upstream decides); the classic
cross-axis dip-in-uptrend (short-axis tail conditioned on long-axis
sign) measured +3.05% BUT its falling-knife control measured +2.61% —
the conditioning adds almost nothing and cells starve below the
floor. Revisit when the population doubles.

## hedge_pairs — the fresh typical trio (2026-10-24)

The PORTFOLIO family, re-implemented from scratch (user-directed:
"most typical negative correlation two curve and one broadmarket
solution, typical and simple; do not reference old demised logic, but
do check asymmetry of buy sell actions"). The prior sub inventory
(5812-5827 — washes, cross-washes, coordination widenings, harvest,
tilts, stops) is DEMISED; its labels stay in static.label_dim for
old-history decode only, and its honest negatives STAND (ratio-z mean
reversion measured negative per-open under every stop rule; leg
Donchian ROOF breakouts, naked Donchian-on-ratio, portfolio gdepth,
the knife state all measured out — do not re-add without new
evidence).

THE TYPICAL SOLUTION on the trio structure: TWO negatively-correlated
leg curves (the offsets table's disjoint offset-opposite industry
pairs, trailing 255d opposite score > 0.5, greedy-disjoint, PLUS the
PINNED showcase DIV_GENERAL~TECH_GENERAL — the page's default 红利 vs
科技, reserved before the greedy pass) + ONE broad-market curve
(000300). The construction is the classic:
inverse-vol weights over the 60d return window, lagged one row,
long-long; pair value = 100·exp(cumsum(log1p(w_a·r_a + w_b·r_b))) —
the response axis AND the audited benefit gauge.

- **hp_entry_trio** (BUY, 5828): all THREE curves in their own
  Wilder-RSI(14) capitulation tails the SAME day (each vs its own
  per-code adaptive trailing-2550d percentile bar — the mov_rsi
  convention verbatim; the candidate grid HP_LEG_TAIL_PCTS holds
  AT-LEAST populations, the per-cell winner picks the tail and records
  it on rule_params.bar_def.tail_pct). The grid WIDENED 2026-10-25
  (user-directed "tails 1/2/3% is too strict, update that portfolio
  can self pick up rsi as well"): {1,2,3} → {1,2,3,5,10}. The same
  day, LOOSENED TO A FREQUENCY FLOOR (user-directed "the signals are
  too rare, loosen it to at least once per year", study
  temp_scripts/trio_freq_study.py): the grid gained 15/20 (25/30
  measured out at pooled +0.24%/+0.14% — the honest negative stands),
  the candidates became AT-LEAST populations (the per-curve tail
  conditions are MONOTONE — a deeper wash also satisfies a shallower
  bar — so the cell's bar is its winner's tail percentile and the
  deeper days fire inside it with more confidence; the old nested
  carve would have dropped a pair's deepest washes from its own
  shallow-winner cell), and run_cells gained the FREQUENCY-FLOOR
  winner policy (config HP_MIN_EVENTS_PER_YEAR = 1.0): each pair's
  bar = the DEEPEST gate-passing tail meeting >=1 trigger/yr over the
  pair's own horizon — the best-dir_ave winner (always the deepest
  tail) left 41/48 pairs under 1 event/yr, the floor policy puts all
  48 at >=1/yr with the pooled verdict passing (n=501, +1.50%,
  t=10.5, 6/6 measurable years positive); the best-dir_ave winner
  stays the fallback when no tail reaches the floor. The same day,
  the RSI WINDOW GRID, then the BOTH-WINDOWS UNION (user-directed
  "also add rsi10 as trigger as well" → "ensure rsi10 be shown up" →
  "both rsi10 and rsi14 are under the same trio wash rule sub type
  ... rsi14 have three cell signals, but also rsi10 another three
  cell signals"): the candidates span WINDOW x TAIL —
  HP_LEG_RSI_WINDOWS (10, 14) — and the winner-takes-all across
  windows was REPLACED by the per-window tail pre-selection
  (_pick_window_tails: the deepest at-least tail meeting the
  frequency floor under the loose gate per window, the best-dir_ave
  fallback) with the CELL's population the UNION of both windows'
  picks — a day fires when EITHER window's trio resonates (the
  primary RSI(10) takes precedence on overlap; the K-absorb merges
  same-wash episodes across windows); the emitted bar_def carries
  BOTH windows' picks (bar_def.windows [{window, tail_pct}, ...] —
  the UI's trio bar decoder renders both; the recorded signal is the
  triggering window's own rsi_mean; the event witnesses DETECT the
  event's own window and ride it on the payload). The same day, the
  LOOSE GATE (user-directed "loosen the gate, only obviously bad
  signals should get rejected"): the family's OWN evidence bars
  HP_GATE_DIR_AVE_MIN / HP_POOLED_DIR_AVE_MIN = 0 — a candidate is
  measured out ONLY when its measured forward mean is negative, and
  the pooled verdict keeps its STATISTICS legs (t >= 1 + the
  expanding-year sign majority) with the mean bar at "not negative";
  the global 0.25% bars stay the standalone families' discipline
  (run_cells gained gate_min / pooled_dir_ave_min overrides). The
  market-wide
  wash bought THROUGH the pair — the negative covariance is the hedge.
  Recorded value = rsi_mean vs the trio bar mean; depth in RSI points
  /10. The portfolio rule_params also carry a top-level NAME
  ("trio wash", config HP_PORTFOLIO_NAME — user-directed the same
  day). First index rebuild: 48 cells / 129 events / 120 measurable
  episodes, avg dir_ave +4.82% @94.7% hit — the pooled member stats
  t-stat 2.2-4.1 per pair. The widening's own rebuild (2026-10-25,
  index --force): 48 cells / 128 events; winners 26× tail1 / 12×
  tail2 / 7× tail3 / 3× tail5, NO tail10 (the dilute wash never beats
  a deeper tail's dir_ave — the self-pick kept the edge where it
  lives); the blow-off mirror (check-only) densified 27→37 cells /
  33→98 events, still discarded.
- **the blow-off mirror** (hp_exit_trio, 5829 — CHECK-ONLY, never
  shipped; user-directed "just one as a start"): all three curves at
  their RSI exhaustion tails, measured through the same candidate
  machinery every run with the rows DISCARDED (one logged asymmetry
  record). The measurement: 27 cells / 33 events / +1.90% —
  STRUCTURALLY THINNER than the buy (a quarter of the episodes), the
  pair-level continuation finding reproduced ("tops continue"). Do
  not source coordinated sells without new evidence — twice measured
  now.
- MEMBERS (SIMPLIFIED 2026-10-24, user-directed): the TRIGGERED
  portfolio signal sets its own cell-level member records — one
  WITNESS per curve at the curve's OWN day (its deepest-alive RSI-tail
  breach run start, RSI vs its own bar, the tail it sits at), each
  carrying the curve's PER-ROLE sub type since 2026-10-25
  (user-directed "diff legs or roles should have diff sub types"):
  hp_rsi_witness_mkt 5831 / _leg_a 5832 / _leg_b 5833 — payload-only
  labels, no rule rows behind them (the shared hp_rsi_witness 5830
  decodes old rows). NO sourcing from the mov_rsi tier,
  no component-code rule rows, cell_signals empty (every member is a
  pair-frame record); the group ids = the pair's own orchestrating
  rows. The same day, the STORED RULE CONDITIONS (user-directed "add
  Rule condition in Rule params ... all UI data must be sourced from
  DB"): the portfolio rule_params.subs.hp_entry_trio carries the sub's
  plain-language `condition` line + the per-witness `cell_signals`
  records KEYED BY RSI WINDOW ({rsi10: [...], rsi14: [...]} — each
  record {sub_type, code, role, condition, bar}; user-directed "rsi10
  and rsi14 should be part of key field rather than merged inside a
  lengthy description for better search") — engine-written,
  the UI parses them verbatim (the cell-signals table and the roster
  trigger line no longer compose prose from the fragments; documented
  in the 03_portfolio_signal_rules.sql header grammar).
- THE BENEFIT AUDIT (history_portfolio_signal_performance): the
  pair value's own max drawdown vs each leg's standalone — the first
  rebuild's gate-passing pairs sit +5.5..+12.9pp shallower than the
  MEAN leg (47/48 groups is_valid; the benefit-boost scale 1 + 0.25 ×
  min(benefit/5, 2) recorded, never a discount). The dd lens
  (forward worst-trough within 20 pair-days, clamped at 0) rides
  beside it.

## rf_break — the roof/floor breakout family (2026-10-16; ALL-BUY since 2026-10-26)

The turtle breakout on the repo's own roof/floor machinery: the
trigger is the close crossing the PRIOR day's level
(`analysis.mov_ave_spreads_roof_floor_estimates`) per the (METHOD,
WINDOW) grid — donchian (method 1, the window's max intraday high /
min low) and median (method 5, the NaN-skipping median of the five
estimate methods — the cross-method CONSENSUS level, smoother than
the raw wick extreme) x windows {60, 120, 255, 500}. WHY IT EXISTS
(the two event studies): 2024-09-30 and 2026-10-15 breached every
percentile bar massively but produced no signals — the (top, hot)
sell cells and (bottom, calm) buy cells starve at exactly the extreme
corners where these events land, while options_wall's FIXED
0.25%-past-the-wall bar caught the same days. A STRUCTURAL bar cannot
thin out the way a trailing percentile does.

**THE 2026-10-26 LEGACY CLEANUP (user-directed "remove roof for buy,
floor for sell decisions, but totally replace with the above
findings")**: the CATCH-ALL sub types are RETIRED (labels 5600-5633
decode-only now) — the any-breach roof BUY (diluted ~2x by the
shallow band; 78 cells / 628 events in the last catch-all build) and
the any-breach floor SELL (measured sign-flipping by level
agreement — see the gap study below; 35 cells / 237 events after the
inversion fix). The family is ALL-BUY (the gdepth/deep precedent —
no sell rides it). The inventory:

- **rf{W}{infix}_roof_deep BUY** — the depth-gated continuation past
  the self-adaptive k bar from RF_ROOF_DEEP_BARS {1,2,3}% (the
  depth-split study's monotone-UP gradient; rf255_med catch-all
  +0.90% vs deep2 +2.00% @65.6 hit). The depth-split graduates
  shipped 2026-10-26 morning.
- **rf{W}{infix}_floor_deep BUY** — the capitulation V-bottom past
  RF_FLOOR_DEEP_BARS {2,3,5}% (the deeper below the consensus floor
  the harder the forward bounce; rf500_med deep5 forward +4.66% @85%
  up-rate; the crash-era events 2024-02-02/05, 2025-04-07,
  2026-10-15). Measured GAP-AGNOSTIC (twice — flat across both
  solidity axes; the washout works regardless of level agreement).
- **rf{W}_floor_bounce BUY** (5680-5683) — the two-method gap
  study's graduate: the close breaks the prior day's MEDIAN
  consensus floor while the DONCHIAN wick floor still holds below —
  the consensus-broken / wick-intact dip, wick-bounded by
  construction. Measured at ALL FOUR windows (t 3.0-4.5, hit 57-67%:
  W60 +1.51% n=164, W120 +1.54% n=104, W255 +2.50% n=66, W500
  +2.64% n=42); the mirrored sell measures −1.5..−2.6 everywhere.
  The controlled-dip twin of floor_deep's washout — the two floor
  buys tile the whole breach range, and the structural
  wick-intact condition is what the depth study's numeric shallow
  band could not express. Rebuild: 32 cells / 317 events, every
  cell positive (+0.43..+7.59%), all in fall-trend cells.
- **NO cross-window carving** — the carve-out variant (deepest
  window claims the day) was measured FIRST and STARVED the mid
  windows: the 255d cells lost their strongest days to the 500d
  claim and fell below the gate floor, and the 2024-09-30 events
  VANISHED with them. The windows are different claim strengths (a
  new 60d high and the simultaneous new 255d high are different
  facts); each fires its own sub type.
- **THE DEPTH-SPLIT STUDY (2026-10-26, user-hypothesized
  "shallow touch of the roof sells the valuation top, deep breach buys
  the regime change; floor mirrored" — temp_scripts/
  rf_depth_split_study.py): REFUTED in both shallow legs, CONFIRMED
  and SHARPENED in both deep legs — with the floor's deep leg
  REVERSED.** Measured on the directional breach depth per
  (method, window), production contract, all 10 indices:
  (1) roof SHALLOW (≤0.5..3% beyond the level) is NOT a sell —
  pool% negative for the sell at every k (forward +0.5..+1.4%:
  shallow roof breaches drift UP; the roof is anchoring support, not
  resistance — George-Hwang 52-week-high underreaction); roof
  approach-without-crossing measures the same (no sell edge).
  (2) roof DEEP (>1-2%) IS the stronger buy — the depth gradient is
  monotone UP (rf255_med: catch-all +0.90% vs deep2 +2.00% @65.6
  hit; consistent across all 8 method×window sections) — SHIPPED
  the same day as the rf{W}{infix}_roof_deep sub types (user-
  directed): BUY past the self-adaptive k bar from
  RF_ROOF_DEEP_BARS {1,2,3}%, the ma_cross gdepth precedent; the
  catch-all cells stay (different claim strengths).
  (3) floor SHALLOW (≤k below the level) has NO bounce edge
  (pool% −0.83..+1.32, ~50% hit — the hypothesized floor-buy is
  flat).
  (4) floor DEEP measures EXACTLY BACKWARDS vs the hypothesis: the
  deeper below the prior floor, the harder the forward bounce
  (rf500_med_floor_deep5 sell pool −4.66% = forward +4.66% @85%
  up-rate; the gradient worsens monotonically with depth in all 8
  sections) — the deep consensus-floor breakdown is the CAPITULATION
  V-BOTTOM (the falling knife reverses at depth; the fall-trend
  mid-band floor SELL cells stood as exit timing until the same
  day's legacy cleanup retired them).
  SHIPPED the same day as the rf{W}{infix}_floor_deep sub types
  (user-directed): the capitulation BUY past the self-adaptive k
  bar from RF_FLOOR_DEEP_BARS {2,3,5}%. First deep rebuild (index,
  --force): 276 cells / 1,955 events (the deep legs added 175 cells
  / 857 events); 15 of the 16 deep sub types emit — donchian
  rf255_roof_deep POOLED-REJECTED honestly (n=95, +1.71%, t=2.83,
  but 3/6 years positive — the year-majority leg), the floor_deep
  cells carry the washout class (best cells ~+5.23%; the crash-era
  events land 2024-02-02/05, 2025-04-07 — 931160 fired five
  floor_deep buys on the tariff-crash day — and 2026-10-15).
  The shallow legs of the hypothesis (roof-touch sell,
  floor-touch buy) MEASURED OUT — roof breaches drift up at
  every depth, floor touches carry no bounce edge (the numeric
  shallow band, that is — the gap study's structural wick-intact
  condition below IS the bounce). The prompt case
  000001@2026-05-13 (+0.264%) sits in the
  shallow tier at every grid k — right call that day, systematic
  loser across the population.
- **THE SOLID/WEAK STUDIES (2026-10-26, user-directed; temp_scripts/
  rf_solid_weak_study.py + rf_gap_study.py)**: whether the breached
  level's AGREEMENT carries information. First axis — the five
  methods' dispersion (relative terciles; the absolute spread scales
  with the window, medians 7-25%): floor sells split cleanly (tight
  tier +0.65% sell-aligned vs wide tier −1.31% POOLED, wide tier
  negative in 8/8 method×window sections — the unconditioned floor
  sell mixes exit edge and bounce) and roof_deep dilutes on wide
  gaps (2.26 vs 1.48 pooled); roof catch-alls and floor_deep FLAT.
  Second axis (user-directed "only need to care about donchian and
  median") — the |donchian − median| gap on the engine's own frame:
  the floor findings REPRODUCE (med floor sell tight −0.07 vs wide
  −2.06 @35.9; 8/8 again), and the CONFIRMATION MATRIX found the
  floor_bounce graduate above (medonly: consensus broken / wick
  intact bounces, t>3 at all windows; dononly — wick broken /
  consensus intact — too thin, n=4-42, honest negative). These
  findings are WHY the catch-alls were retired rather than
  re-conditioned: the pooled verdicts flip by level agreement, and
  the buy legs own the reversion. THE ROOF-SELL FOLLOW-UP (same day,
  user-directed "also study for sell signal as well for roof to
  sell, on weak roof no action"; temp_scripts/
  rf_roof_sell_study.py): MEASURED OUT COMPLETELY — every sell
  variant (any breach / shallow ≤1,2% / approach-within-1,2% below)
  x every tier x both methods pools NEGATIVE sell-aligned
  (t −0.8..−6.0), and the SOLID tier — the hypothesized sellable
  roof — is the STRONGEST up-drift (don_any_tight forward +1.13%
  pooled, t=−5.36): a tight-agreement roof is anchoring SUPPORT,
  not resistance; the roof side is purely the buy side. The wide
  tier's rare positive pockets (W120/W255 donchian any_wide +1.2/
  +1.1 @n≈40) pool to t=0.2 — noise, and the weak tier is no-action
  by the hypothesis's own rule. Do not re-add a roof sell without
  new evidence.
- The trigger day's own level never sets its own bar (shift 1);
  regime-free with a trend split (the ma_cross precedent — and both
  studies' starving corners were regime artifacts).

## The 2026-10-16 event-study additions to the existing families

- **ovix_atr_break** (options_vix, BUY): the spot day-return in
  close-to-close ATR units past the self-adaptive bar (now
  {1.5, 2, 2.5, 3} — k=1.5 added 2026-10-19) while vol slope5 fires —
  the 2026-10-15 class (fast repricing from a LOW base: corr −0.60..−0.72 vs
  the fear bar, vol −17..−28%
  below its 120d max vs the +5% bar, slope5 +28%). Fired 2026-10-15
  on 000852 (−2.51 ATR); crisis-leg history 2024-10-09, 2025-04-07,
  2026-02-02, 2026-03-23. 3 cells / 45 events at index scope (the
  winners stayed deep — see the options_vix section).
- **ma_breadth_thrust** (ma_cross, BUY): the Zweig thrust — the 10d
  EMA of adv/(adv+dec) over the sec_type's OWN universe from below
  0.40 to above 0.615 within 10 sessions. Fired 2024-09-24 (0.616 vs
  0.615 — the policy-rally day 1). 33 events 2020-2024.
- **ma_9to1_up / ma_9to1_dn** (ma_cross, BUY / SELL): the 9:1
  advance-decline days at the ≥50-code floor — up = the thrust
  companion, dn = the capitulation washout exit (fired 2026-09-24 at
  10.2:1 as the breakdown started, and the 2024-08-05/14 lows).
  362/589 events.
- **ohlcv boost turnover-climax axis**: the day's trading_amount /
  its 255d MA bucketed {0 normal, 1 ≥1.5x heavy, 2 ≥2.5x climax} as
  the 4th trust axis (beta_amt); bollinger + momentum + rf_break opt
  in. First build: bollinger's TOP side SHIPS (g1 +0.3bp, g2 2/3
  years); mov_rsi/momentum/rf sides honestly fail gates. SINCE
  2026-10-26 (user-directed "create shared pe and ohlcv logic") the
  same axis is ALSO the shared OHLCV-boost VARIANT gate
  (engines/_ohlcv_boost.OhlcvBoostGate, amt_climax >= HEAVY) — bollinger (both sides)
  and rf_break's deep legs emit turnover-confirmed variant rows on
  the is_ohlcv_boosted PK beside their plain rows, each judged
  independently by the measured gate; the PE-EXTREMES variant joined
  rf_break's floor legs (the rebound barbell) the same day.
- **the twins retirement (2026-10-16)**: the boost's only product is
  confidence, so the boost-twin history rows were replaced by the
  per-cell `signal_rules.ohlcv_boost_conf` column (the trust-weighted
  base sum(w·y)/sum(w), pct, NULL where the side boosted nothing) —
  shown next to the plain base in the rules panel. First stamping at
  index scope: bollinger top 26 cells (−0.94%..+3.40%); mov_rsi,
  momentum, rf_break all-NULL (gates/pool).

## rebound — the drop→rebound→resolution family (2026-10-26)

The ma-spread rebound study's graduates
(temp_scripts/ma_spread_rebound_study.py,
docs/ma_spread_rebound_study_2026-10-26.md) as rule cells, user-
directed ("implement above as a new signal family rebound, there
should be with and without pe"). A BIG DROP (close >= the depth
candidate below the trailing-Wd running max of close, shifted 1)
activates an episode; the trough-break, the stall just below the last
high, and the full reclaim are the three causal RESOLUTIONS (sub
types per window {60, 255}; the depths {10%, 5%} are the candidates
the gate self-picks):

- **rebound_capit_{W}** (BUY): the trough-break — the CAPITULATION
  V-bottom. The study's strongest fwd20 population after big drops
  (+3.42% pooled, +4.95% after >20% crashes) — the H1 claim measured
  BACKWARDS at the bottom: the weak-rebound bucket is dominated by
  new-lows and carries the strongest positive forwards, the repo's
  standing asymmetry again (the rf floor_deep capitulation).
  First rebuild: 28 cells (16 at W60 / 12 at W255), all gate-valid.
- **rebound_fail_{W}** (SELL, exit timing): the FAILED RECLAIM — the
  rebound stalls within 10% of the last high (recovery ratio R in
  [0.90, 1.0)) and rolls over. THE REFINED H1: the further-drop
  population lives at the TOP of the rebound range, not the bottom —
  the last high is a CLIFF, not a gradient (clearing it flips the
  forward sign +4.32% vs landing just short −1.5..−1.9% dir_ave).
  18 cells.
- **rebound_reclaim_{W}** (BUY): the FULL RECLAIM (R ≥ 1, close above
  the prior high) — the study's H2 graduate, the strongest next-leg
  population anywhere in the study (de-overlapped fwd20 +4.32% /
  fwd60 +7.91%, the new-low rate 19% vs 77% base, fmax60 20.4% vs
  13.0% — the NEXT rebound really is bigger). 13 cells.

THE PE CONDITIONING (the is_pe_boosted PK axis — every sub type
emits a PLAIN and a PE-gated cell set, judged independently): the
capitulation bounce is BARBELL-shaped in the code's own PE percentile
(cheap20 +6.64% fwd20 — the valuation floor; rich20 +2.18% but
+6.46% fwd60 — the knife takes a month to stop, the snapback real;
middle-PE ±1% with NO 60d edge — the dead zone) → the PE-gated capit
requires the EXTREMES (pe_pct ≤ 0.4 or ≥ 0.6). The failed-reclaim
further-drop claim and the reclaim continuation only exist OUTSIDE
cheap PE (stalled rebounds recover at cheap valuations +2.04%;
zero cheap-PE reclaims in the study — expensive markets V-bottom,
cheap markets bottom slowly) → their PE gate is pe_pct ≥ 0.2
(pe_pct = today's PE among the code's own PRIOR PE readings, min 255
obs — stats.index_valuation.pe). The gate definition rides
rule_params.bar_def.pe_gate (the UI bar word renders it).

First force rebuild (index): 117 cells / 598 events — every sub type
trades in BOTH variants, all gate-valid; the study's per-window /
per-depth candidate structure follows the rf_break precedent (no
cross-window carving; the trigger day never sets its own bar).

## macd — the classic MACD crossover family (2026-10-26)

The typical-EMA study's product (user-directed "study what most
typical ema is, eg used 6d 12d 26d, etc, and implement it to replace
the nearby ema period if any" + "2nd task is macd signal in short
term tech"). THE STUDY: the most typical EMA usage is the classic
MACD set **12/26/9** — Gerald Appel's original, the industry default
on every charting platform (Investopedia / Interactive Brokers);
standalone EMAs cluster at 9/20/50/200. The repo's `ema10` was the
nearby non-standard period standing in for the typical 12 (the
strategy-tier MacdAlgo literally snapped 12→10 for lack of the
column) — REPLACED the same day: `stats.{index,etf,stock}_tech_stats`
now carry ema12 + ema26 (the backfill: temp_scripts/
apply_ema_typical_spans.py, ~9M rows across the three sec_types,
spot-checked against the pandas ewm convention), and MacdAlgo's
defaults are the classic 12/26/9.

- **macd_cross_up** (BUY, 6000): the MACD line (EMA12 − EMA26)
  crosses ABOVE its EMA9 signal line — the classic momentum turn.
  One episode per cross run, K=5 absorb (a re-cross within 5 traded
  days is the same whipsaw episode, first day fired, delay 0).
- **macd_cross_dn** (SELL, 6001): the bearish crossover — EXIT
  TIMING for longs, never a short (the equity asymmetry).
- Value source (REBASED 2026-10-27, user-directed "check if macd
  uses the newly build ema data rather than self-customizing
  re-compute"): the fetch READS the builds' precomputed ema12/ema26
  columns (stats.{etf,index,stock}_tech_stats, LEFT JOINed — the
  strategy-tier MacdAlgo's source verbatim, ONE EMA construction in
  the repo) and only the EMA9 signal line computes inline (an EMA of
  the MACD line has no precomputed column); the family's horizon is
  the tech_stats max(date). A/B vs the self-computed build: cross
  days preserved except 11 marginal flips (values within the
  NUMERIC(18,4) storage rounding), 35→36 cells / 429→432 events, all
  audited.
- The recorded value = the histogram as a PCT OF CLOSE (cross-
  security comparable, the strategy algo's normalization); reference
  0.0; confidence depth = the cross day's |histogram| in its own
  trailing-10y sigma (the momentum depth convention, factor 0.5).
- REGIME-FREE with a trend split (the ma_cross cross-event
  precedent). NOT live-wired.
- First force rebuild (index, 2026-10-26): 35 cells / 429 events,
  EVERY cell gate-valid — buys strongest in the fall-trend cells
  (931743 fall +6.07% @77.8% hit, 000001 fall +1.51%, 931160 fall
  +1.31% @69.2%), rise-trend cells the continuation population
  (+0.67..+0.73%), sells exit timing across top/unknown trends
  (000683 unknown +1.54%, 931160 unknown +1.43%). The fall-trend
  buy edge + the rise-trend continuation is exactly the measured
  asymmetry shape.

## Does it hold against published best practice? (audited 2026-10-14; momentum + the 2026-10-16 study folded in)

**RSI — HOLDS, stricter than the standard.** Per-code percentile bars
(the "adjust per market" rule, made systematic), the window grid
measured rather than assumed, the sell-side exit-only policy IS the
overbought-persistence warning encoded as a gate. The unimplemented
RSI-price DIVERGENCE confirmation remains a new axis to study, not a
missing fix.

**Bollinger — HOLDS.** 20/2σ default; the (20) × (2, 2.5, 3) grid
lets the measured gate pick per k; the band-walk warning is the
gated sell side. The squeeze/BandWidth breakout was measured
(2026-10-16) and does NOT hold as a reversal here — recorded as an
honest negative. The scale-k-with-window rule (boll60 at 2.5σ) is
the graduating extension.

**MA cross — HOLDS by divergence from the criticized design.**
Whipsaw → the uninterrupted-run split + K=5 absorb; lag → the trigger
is the price-vs-MA event itself; no blind following → every cell
clears the measured gate. EMA superiority claims did NOT survive the
rules-era measurement — SMA stands.

**MACD — HOLDS as the textbook construction, made self-adaptive by
the cell machinery.** The classic 12/26/9 spans ship verbatim (the
typical-setting study, not a tuned grid); the whipsaw criticism is
answered by the K=5 episode absorb (one signal per cross run); the
zero-line/lag criticisms are the reason the cells split by trend and
the gate judges each population on evidence; no blind following — a
cell whose cross-up population doesn't clear the bar emits nothing.
The EMA basis here is NOT in tension with ma_cross's SMA verdict:
MACD is DEFINED on EMAs (the "moving average convergence/divergence"
of two EMAs is the indicator), while ma_cross's price-vs-MA reclaim
is a structural event where SMA measured better.

**options_wall / options_vix — HOLD as the VRP/interaction
literature.** The put side never trades ungated (the variance risk
premium is the load-bearing piece — Carr & Wu 2009;
Bollerslev-Tauchen-Zhou); the vix family's edge is the corr×spike
INTERACTION, never a single axis. Tiny samples are handled honestly:
pooled cells, floors, no curve, flat scale, explicit rejection twin.

**momentum — the log-return tails ARE the short-term-reversal
literature** (the same premise as RSI/bollinger, measured on the
cumulative-return axis); intermediate-momentum CONTINUATION is
deliberately not productized — the top-side sells are exit timing,
consistent with the asymmetry. The classic dip-in-uptrend construct
was measured and did NOT separate from its control.

**Episodes — textbook event-study hygiene** (Kothari & Warner 2006;
Brown & Warner 1985): one-signal-per-episode at the first day, K=5
hysteresis, trigger-time-observable conditioning only.

## Biz rules that must not silently regress

1. Sell gate 0 / buy 0.25%, floors 2/3 (LOOSENED 2026-10-16 from
   0.75% / 3/4 — the moderate user-directed step; the asymmetry IS
   still the design, at a lower bar).
2. One signal per episode, first day, delay 0; K=5 on the code's OWN
   traded days (mov_rsi's absorb is TREND-AWARE; bollinger/momentum
   keep plain absorb until their own A/B).
3. mov_rsi / momentum bars are per-code percentile quantiles — never
   fixed levels; bollinger bars are fixed ±kσ — never percentiles;
   momentum values are LOG-RETURN ln(1+momentum) — never raw ROC.
4. Nested widest-first carving — a deep breach counts once, at its
   deepest claim.
5. gdepth, deep and the rf_break legs (roof_deep / floor_deep /
   floor_bounce — the family is ALL-BUY since the 2026-10-26 legacy
   cleanup) are BUY-ONLY; momentum/bollinger/rsi sells are
   exit timing, never short entries.
6. ma_cross conditioning uses trigger-time-observable state only.
7. No curve without measured monotonicity (the curve tier died
   2026-10-14 for measured weakness).
8. Regimes are SPLITS, never weights; in the live tier the
   day-context match makes the cell's OWN regime a firing gate (a
   cell fires only on the days of the regime population it measured;
   a pooled 'all' 4 cell matches any day state on that axis) while
   the breach row records the day regime as CONTEXT (re-worded
   2026-10-21 — the old "context, never a gate" line predated the
   rules-era day-context match).
9. options_wall put-side breaches are NEVER emitted ungated; sparse
   options families pool at the gate; the VRP-fail twin is wait.
10. options_vix: corr alone and spike alone are baseline — never
    productize a single-axis variant; exits ride corr normalization
    (fear_unwind) or the euphoria-top mirror (vol-ATH escalation with
    spot above its 5-session-ago close) — never tight stops. The
    peak legs' direction split (spot below 5d-ago buys, above sells)
    is the family's fear-vs-euphoria separator — measured out on
    2026-10-19: the corr/slope bar grids add zero events, the atr
    slope3 grid loses net events.
11. boll5 is structurally untriggerable at k ≥ 2 (max |z| =
    (n−1)/√n); rsi3-sell, EMA variants, the wall-center approach,
    ovix_rollover and the squeeze breakout are MEASURED OUT (2026-10-
    16) — re-adding any requires new measurements.
12. The boost is a trust-weighted measurement, never a gate on the
    PLAIN rows: the per-cell `ohlcv_boost_conf` stamp retired with
    the 2026-10-20 slimming (the derivation audit remains) and the
    measurement never adds/reweights the plain history rows — they
    are the population of record. SINCE 2026-10-26 (user-directed)
    the boost is ALSO a VARIANT axis again: the turnover-confirmed
    population emits SEPARATE rows on the is_ohlcv_boosted PK
    (bollinger, rf_break deep legs) — each variant judged
    independently by the same gate, so a negatively-optimizing
    variant emits nothing; the PE-conditioned variants (rebound all
    subs, rf_break floor legs) share the machinery
    (engines/_pe_boost / engines/_ohlcv_boost) and never fire live (excluded from the
    live active set).
13. STARVED cells (episodes below the floor) TRADE at the starvation
    confidence scale min(n / floor, 1) (2026-10-19, retiring the
    2026-10-16 wait rows; readers RECOMPUTE the scale since 2026-10-20
    from the snapshot's n_episodes + the rule's gate.sample_floor —
    the digest left the slimmed cell snapshot) — the extreme-event
    monotone guarantee (a
    deeper fluctuation never yields less record than a milder one)
    kept as a reduced-confidence trading record; the dir_ave bar is
    the only verdict, so measured-out cells (any n) emit nothing and
    the only wait rows left are the ungated VRP twins (never trade,
    never fire live).
14. The POOLED floor is a CONFIDENCE reference too (2026-10-19): the
    statistics legs (bar / t / year majority) are the BUY side's
    verdict; n in [POOLED_TRADE_FLOOR, POOLED_MIN_N) trades at
    min(n / POOLED_MIN_N, 1), below the trade floor emits nothing.
    The options families' bars as of the 2026-10-19 sparsity study:
    wall width SINGLE 0.10% (the grid measured a no-op), VRP gate
    0.60 with the SHORT 120-row warmup (the full-window warmup
    erased whole codes), corr bar −0.75 (spike_wide graduated into
    it), ATR ks include 1.5. options_wall put breaches never emit
    ungated; the deep zone-edge breach is the family's only
    edge-carrying population (strike walls / near-edge / center /
    nearest-expiry all measured out).

## Where the numbers live (single sources of truth)

- Gate bars, floors, blend weights, family grids:
  `analysis/analysis_signals/config/__init__.py`
- The rule engines: `engines/rule_base.py` + one engine package per
  family under `engines/signal_families/`
- The measured evidence per cell: the LATEST
  `analysis_signals.history_signal_performance` snapshot (n /
  dir_ave_pct / std / t / hit + the is_in_portfolio flag — the
  2026-10-19 split slimmed 2026-10-20; the rules API
  re-merges it into its rule_params response as `measure`) — the
  evidence the UI renders, one dated snapshot per daily run; the
  PORTFOLIO tier's combined audit (the members' pooled stats, the
  drawdown benefit, the benefit-boost scale) lives in
  `analysis_signals.history_portfolio_signal_performance`
- The 2026-10-16 sub-rule study (graduates + honest negatives):
  `docs/sub_rules_proposal_2026-10-16.md`; the harness:
  `temp_scripts/subrule_candidates_study.py`
- The rule-cells API: `GET /api/live-data/trading-signals/rules`;
  history via `/history` (the live 20d slice UNIONed)

## What the UI displays (rules-era, 2026-10-15)

Short-Term Tech (`/analysis/signals/recent-movements`) and the
Options page (`/analysis/signals/options`) render the two surfaces:
**SignalRulesPanel** (one row per rule cell: action chip, the TREND
chip — the per-PK asymmetry axis, the regime word, the BAR decoded
from rule_params.bar_def, the EVIDENCE n · dir_ave ▲/▼ · hit%, the
confidence base; expanding shows the knowledge doc + gate digest) and
**SignalHistoryPanel** (one row per signal date: value, REFERENCE
(signal_reference), EXCESS, confidence bp, regime as recorded
context). One signal-family pick drives rules + chart markers +
history. Display rules that must not regress: regimes are CONTEXT
columns never gates; the evidence column makes the gate visible; no
depth-response curves; identical bars across all trends of one sub
signal would be a red flag. Variant-axes display (2026-10-26): the
rules row's bar word renders the PE / AMT gate suffixes
(bar_def.pe_gate / .ohlcv_gate — the ruleBarLabel generic suffix;
the old BOOST column died with the ohlcv_boost_conf snapshot in the
2026-10-20 slimming), and the Short-Term Tech history panel carries
the PE boosted / OHLCV boosted columns (✓ where the event replays
the boosted variant — i.e. generation actually used the boost; – on
the plain population) in the main table AND the per-date expansion.

## References (the best-practice audit's sources)

- RSI: OANDA / Investopedia / thinkorswim RSI references.
- Bollinger: Investopedia, Fidelity; John Bollinger's own rules
  (bollingerbands.com — scale k with window; band walks vs squeezes).
- MA crossover: Quantified Strategies / Cabot SPY 50/200 tests.
- Asymmetry: Dunis et al. 2011; Da, NY Fed SR 513; George & Hwang,
  J. Finance 2004; Zhu et al. on short selling.
- Regimes: Daniel & Moskowitz, "Momentum Crashes" (JFE 2016); the
  Markov regime-switching tradition.
- Episodes: Kothari & Warner 2006; Brown & Warner 1985.
- VRP / contrarian vol: Carr & Wu, RFS 2009; Bollerslev-Tauchen-Zhou;
  Fassas 2019; ECB WP1675.

## The portfolio_sub_type split (2026-10-25, latest)

User-directed: "add portfolio_sub_type in portfolio_signal_rules, then
migrate trio wash and spread to as portfolio_sub_type" + "rsi signals
themselves should not sit in portfolio signals, by portfolio
signal_rule_ids to map them". A pair now carries TWO portfolio RULES —
**trio wash** (0: the hp_entry_trio orchestration, all three curves in
their own RSI capitulation tails) and **spread** (1: the
hp_entry_spread + hp_exit_spread orchestrations, the long-short
spread's RSI bottom/top tail) — each its own portfolio_signal_rules
row with its own ids, coordination spec, is_valid and audit. The
REGISTERED mov_rsi cells (the is_in_portfolio TRUE tier) are NEVER
portfolio rows: each rule MAPS them through signal_rule_ids only —
the shared market cells (000300) map to trio wash alone (the spread
disregards the benchmark curve BY CONSTRUCTION — its condition never
reads the market), a leg cell maps to its pair's BOTH rules (the legs
witness both). The mapping mirrors each rule's witness semantics
verbatim; the rsi rows keep their own unified evidence and remain
duplicated from any standalone twin purely by the PK's tier axis.

### The witness mapping record (2026-10-25, last)

The stored witness records (rule_params.subs.<sub>.cell_signals) now
carry `mapped_signals` — the registered mov_rsi sub types the rule
maps at that witness's own curve, action-side aligned (the trio's
three witnesses are all buy-side washes; the spread entry's leg A
maps buy cells / leg B sell cells, the exit flips). Duplicating the
lists across the trio's two windows (and the legs across both
portfolio rules) is deliberate: every witness states its own mapping,
and the per-witness mapping is exactly what its condition claims —
the market/leg curve's own RSI wash — nothing more.
