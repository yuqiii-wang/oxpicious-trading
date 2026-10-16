---
name: signals-biz-logic
description: >
  The FINANCIAL/BUSINESS semantics of the SEVEN signal families in
  oxpicious-trading — mov_rsi (RSI percentile tails, entry windows
  14/10/6), bollinger (fixed-σ band breach, window 20), ma_cross
  (price-vs-MA events, NOT a golden cross, + the market-level
  breadth thrust / 9:1 days), options_wall (VRP-gated
  OI-wall breach), options_vix (vol spike inside a deep-fear corr
  regime + the vol-ATH panic peak + the ATR volatility-expansion
  breakout), momentum (log-return tail breaches
  over vol-adaptive lookbacks), rf_break (the roof/floor Donchian
  breakout — the prior day's window-extreme level, a STRUCTURAL
  bar): what each signal MEANS as a trade
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
  sell evidence is structurally thinner under positive drift.
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
  depth (capped 2×) — never a fitted curve; the deeper the pierce,
  the stronger the claim, up to the family's depth normalizer.
- STARVED CELLS RECORD (2026-10-16; label renamed no_action → WAIT
  and the rules row made SELECTIVE the same day): a cell with
  episodes below the sample floor replays ALL its triggers into
  history as wait events and graduates to the real action in place
  once n clears the floor and the gate passes; the signal_rules
  wait watchlist row carries only floor-blocked imminent cells
  (n = floor−1 with dir_ave at the side's bar — one confirming
  episode from trading; the intended action rides
  gate.intended_action). Rationale — the monotone guarantee for extreme
  events: nested carving assigns a deep breach to its DEEPEST claim,
  and those deepest-claim cells starve exactly at the extreme corners
  where the events land (the 2024-09-30 / 2026-10-15 studies); without
  the record, a MORE extreme fluctuation produced LESS signal than a
  milder one. Measured-out cells (n ≥ floor, dir_ave below the bar)
  still emit nothing — that is rejection on evidence, not starvation.

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
episodes; pooled cells; confidence FLAT):

- **ow_put_025pct** (buy): close ≤ put_wall_low × (1 − 0.25%),
  VRP-GATED — vrp_ratio (implied vol30 vs rolling-20d realized)
  beyond its own trailing-255d 80th percentile bar. Current cell:
  +5.83% @n=5; the VRP-fail twin (`_rej`, wait) records the
  falling-knife population.
- **ow_call_025pct** (sell, ungated): close ≥ call_wall_high × (1 +
  0.25%) — exit timing.
- PROPOSED (2026-10-16): `ow_call_1pct` sell (n=14 pooled +1.31%
  @78.6 — reproduces the threshold-era n=15 @80.0% +1.41); implement
  as a second width candidate on the call side.
- MEASURED OUT 2026-10-16: the put-wall CENTER approach
  (`ow_put_center_vrp`) — its threshold-era edge (n=68 @72.1% +1.31)
  is GONE on the current wall population (+0.09% @44.7; the ungated
  twin −0.01%). The call approach rally needs etf scope to evaluate
  (its threshold-era population was 84% one ETF, 159919).

## options_vix — the vol-spike-in-fear-regime family

BUY THE PANIC, conditionally. Axes: corr20 (the PUBLISHED 20-session
corr of spot log-return vs vol log-change, linearly interpolated
per code onto every session IN-ENGINE — signal and UI read ONE
series), slope5 (5-day vol slope), z252 (vol level z), on the repo's
own 30d implied-vol index. The premise: corr alone ≈ baseline, spike
alone weak — the INTERACTION is the edge.

- **ovix_spike** (buy): corr20 < −0.8 & slope5 > +10% — the crises
  (2020-02 COVID, 2022-03/10, 2024-04, 2025-04, 2026-03). Cell:
  +3.94% n=5.
- **ovix_spike_hivol** (buy): spike & z252 > 1 — the interaction at
  its strongest (threshold era: +3.74% @83% fwd20). Cell: +4.12% n=5.
- **ovix_panic_peak** (buy, 2026-10-15): the vol30 VALUE SPACE — vol
  > prior 120-session max × 1.05 & slope5 > +10% & spot below its
  5-session-ago close. The diagnosis: in V-spike crises the corr axis
  stays moderate (000852 Feb-2024 interp corr bottomed −0.74), and
  the K=8 absorb fires level conditions at crisis ONSET — the PEAK
  never gets its own marker; the escalation margin re-arms at the NEW
  extreme, and the downside leg separates fear from upside euphoria
  (the 2024-10-08 stimulus top excluded). Cells: 000852 n=5 +4.85%
  @80, 000300 n=4 +2.32% @75.
- **ovix_fear_unwind** (sell): corr20 crosses back above −0.5 with a
  40-session deep-fear witness — exit timing (+0.05% @n=7, thin but
  honest).
- MEASURED OUT 2026-10-16: `ovix_rollover` (+0.48% @n=12 vs the
  threshold-era +2.32% @73 — the published-corr interpolation changed
  this population's character; 0 passing cells). Borderline:
  ovix_spike_wide (corr −0.75; n=21 pooled +1.17%, 1 cell — the
  coverage play), ovix_deep_hivol (n=10 +0.48%, 1 cell).
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

## rf_break — the roof/floor breakout family (2026-10-16)

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

- **rf{W}_roof BUY** — the first close above the prior day's W-day
  roof: the significant-high continuation. The no-carve rebuild
  (2026-10-16): 78 cells / 628 events; 2024-09-30 alone fires
  rf60/rf120/rf255 roof buys on SIX study codes (931594 rf60 at
  +9.9% beyond the roof).
- **rf{W}_floor SELL** — the first close below the prior floor: exit
  timing. The donchian floor cells ALL MEASURED OUT (breakdowns
  CONTINUE over the 5d-heavy blend — the falling knife); only the
  MEDIAN floor carries thin exit-timing cells (9-10 events, the
  consensus-level breakdowns). Do not re-add a floor BUY without new
  evidence.
- **NO cross-window carving** — the carve-out variant (deepest
  window claims the day) was measured FIRST and STARVED the mid
  windows: the 255d cells lost their strongest days to the 500d
  claim and fell below the gate floor, and the 2024-09-30 events
  VANISHED with them. The windows are different claim strengths (a
  new 60d high and the simultaneous new 255d high are different
  facts); each fires its own sub type.
- The trigger day's own level never sets its own bar (shift 1);
  regime-free with a trend split (the ma_cross precedent — and both
  studies' starving corners were regime artifacts).

## The 2026-10-16 event-study additions to the existing families

- **ovix_atr_break** (options_vix, BUY): the spot day-return in
  close-to-close ATR units past the self-adaptive {2, 2.5, 3} bar
  while vol slope5 fires — the 2026-10-15 class (fast repricing from
  a LOW base: corr −0.60..−0.72 vs the −0.80 bar, vol −17..−28%
  below its 120d max vs the +5% bar, slope5 +28%). Fired 2026-10-15
  on 000852 (−2.51 ATR); crisis-leg history 2024-10-09, 2025-04-07,
  2026-02-02, 2026-03-23. 3 cells (000016/000300 k=2, 000852 k=2.5),
  45 events.
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
  years); mov_rsi/momentum/rf sides honestly fail gates.
- **the twins retirement (2026-10-16)**: the boost's only product is
  confidence, so the boost-twin history rows were replaced by the
  per-cell `signal_rules.ohlcv_boost_conf` column (the trust-weighted
  base sum(w·y)/sum(w), pct, NULL where the side boosted nothing) —
  shown next to the plain base in the rules panel. First stamping at
  index scope: bollinger top 26 cells (−0.94%..+3.40%); mov_rsi,
  momentum, rf_break all-NULL (gates/pool).

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
5. gdepth and deep are BUY-ONLY; momentum/bollinger/rsi sells are
   exit timing, never short entries.
6. ma_cross conditioning uses trigger-time-observable state only.
7. No curve without measured monotonicity (the curve tier died
   2026-10-14 for measured weakness).
8. Regimes are SPLITS, never weights; live records regime as context,
   never a gate.
9. options_wall put-side breaches are NEVER emitted ungated; sparse
   options families pool at the gate; the VRP-fail twin is wait.
10. options_vix: corr alone and spike alone are baseline — never
    productize a single-axis variant; exits ride corr normalization,
    never tight stops.
11. boll5 is structurally untriggerable at k ≥ 2 (max |z| =
    (n−1)/√n); rsi3-sell, EMA variants, the wall-center approach,
    ovix_rollover and the squeeze breakout are MEASURED OUT (2026-10-
    16) — re-adding any requires new measurements.
12. The boost is a trust-weighted measurement, never a gate: it stamps
    the per-cell `signal_rules.ohlcv_boost_conf` (NULL where the side
    boosted nothing) and never adds/reweights history rows — the plain
    rows are the population of record (the twin era ended 2026-10-16).
13. STARVED cells (episodes below the floor) RECORD as WAIT rows
    (renamed from no_action 2026-10-16) and graduate in place — the
    extreme-event monotone guarantee (a deeper fluctuation never
    yields less record than a milder one): ALL starved triggers
    replay into history; the rules watchlist row only for
    floor-blocked imminent cells; measured-out cells emit nothing;
    wait rows never trade and never fire live.

## Where the numbers live (single sources of truth)

- Gate bars, floors, blend weights, family grids:
  `analysis/analysis_signals/config/__init__.py`
- The rule engines: `engines/rule_base.py` + one engine package per
  family under `engines/signal_families/`
- The measured evidence per cell: `signal_rules.rule_params.measure`
  (n / dir_ave_pct / hit_rate) — the evidence column the UI renders
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
signal would be a red flag. The rules panel's BOOST column renders
`ohlcv_boost_conf` (null where the side boosted nothing); history
carries one population per event (the none/boost toggle is gone with
the twins).

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
