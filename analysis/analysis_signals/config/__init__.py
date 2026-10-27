"""analysis.analysis_signals.config — the RULE-TIER constants.

The redesigned tier (2026-10-14): signal_rules (the engine entry +
the SELF-ADAPTIVE per-cell rule_params) populates history_signals by
replay. The constants here are the shared
evaluation contract (gates, floors, the forward blend, the episode
merge) and the family parameter grids the engines measure candidate
cells against; per-family trigger semantics live in each family's
engine module, the family knowledge in static.signal_rules_docs.

Fetch-layer vocabulary (the surviving fetch/ package reads these):
SEC_TYPE_IDENTITY_TABLE / UNCLASSIFIED_CLASS / RSI_WINDOWS /
BOLL_WINDOWS.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# fetch-layer vocabulary (fetch/inputs/_shared.py + family fetchers)
# ---------------------------------------------------------------------------
SEC_TYPE_IDENTITY_TABLE = {
    "etf": "stats.etf_identity",
    "index": "stats.index_identity",
    "stock": "stats.stock_identity",
}
UNCLASSIFIED_CLASS = "UNCLASSIFIED"

RSI_WINDOWS = (3, 6, 10, 14, 20, 60)
BOLL_WINDOWS = (5, 20, 60)
MA_WINDOWS = (20, 60, 120, 255)

# ---------------------------------------------------------------------------
# the shared evaluation contract (how a rule cell is judged)
# ---------------------------------------------------------------------------
# dir_ave: the FIXED forward blend — next 0.10 / 3d 0.15 / 5d 0.50 /
# 10d 0.15 / 20d 0.10 (fractions of the trading-day horizon, % space)
FORWARD_BLEND_DAYS = (0, 3, 5, 10, 20)
FORWARD_BLEND_WEIGHTS = (0.10, 0.15, 0.50, 0.15, 0.10)

# the equity asymmetry IS the design: sharp falls revert (the buy needs
# a measured edge), sharp rises continue (the sell is EXIT TIMING for
# longs — any non-negative edge qualifies); sells are structurally
# thinner under positive drift. LOOSENED 2026-10-16 (the moderate step,
# user-directed): buy bar 0.75% -> 0.25%, floors 4/3 -> 3/2. The FLOOR
# stopped being a gate 2026-10-19 (user-directed): it no longer blocks
# thin cells — it is the CONFIDENCE REFERENCE instead. A cell under its
# floor trades at sample_scale = min(n / floor, 1) of the confidence
# (the starvation wait logic retired); the dir_ave bar remains the only
# verdict — a cell below it is measured-out and emits nothing
GATE_DIR_AVE_MIN = {"buy": 0.0025, "sell": 0.0}
SAMPLE_FLOOR = {"buy": 3, "sell": 2}

# the POOLED EVIDENCE TIER (2026-10-16): the per-cell floors cannot
# reach statistical significance — a typical index's per-event blend
# sigma is ~2.6%, so n=3 gives SE ~1.5% and the 0.25% cell bar is
# ~0.16 SE (a zero-edge cell passes ~44% of the time; reaching t=2 at
# that bar needs n~450 per cell, unreachable). The BUY side's edge
# claim therefore lives at the (sub_type, action) POOLED population —
# every winning cell's measurable triggers across codes, judged by
# t-stat + expanding-year sign consistency (the graduation studies'
# discipline, formalized into the gate). SELL stays cell-gated only
# (exit timing — the equity asymmetry). A pooled FAILURE drops the
# buy side's trading rows and replayed history for that run
# (measured-out semantics) — since the 2026-10-19 starvation retreat
# there is no wait population to survive the demotion.
POOLED_DIR_AVE_MIN = 0.0025   # the pooled bar mirrors the cell bar
POOLED_T_STAT_MIN = 1.0       # one-sided ~84% — honest at small n
POOLED_MIN_N = 10             # the FULL-CONFIDENCE reference (the
                              # 2026-10-19 pooled starvation retreat: n
                              # between POOLED_TRADE_FLOOR and this
                              # trades at min(n / this, 1) of the
                              # confidence — see POOLED_TRADE_FLOOR)
POOLED_YEAR_MIN_N = 5         # a year counts toward sign consistency
# the POOLED STARVATION RETREAT (2026-10-19, user-directed — the
# per-cell starvation retreat one level up): the pooled n floor above
# stopped BLOCKING a statistically-significant side. A BUY population
# whose t-stat clears POOLED_T_STAT_MIN, whose mean clears the bar and
# whose measurable years are sign-majority positive TRADES at the
# pooled completeness scale min(n / POOLED_MIN_N, 1) of the confidence
# (the options families' 3-code universes pinned every strong crisis
# population under n=10: the 2026-10-19 rebuild measured ow_put buy
# n=7 +6.17% t=3.96 and ovix_spike n=9 +3.33% t=4.55 — both REJECTED
# on n alone). Below POOLED_TRADE_FLOOR the side still has no claim at
# all (demoted, emits nothing) — and the t/bar/year legs stay verdict
# legs; only n became a confidence reference.
POOLED_TRADE_FLOOR = 3

# one signal per EPISODE: the episode anatomy constants
# (REENTRY_EXIT_DAYS and the tail-episode parameters) moved 2026-10-15
# into the consolidated analysis.trends component
# (analysis.trends.episodes) — import them from there. The trend
# classifier's parameters likewise (analysis.trends.trend_state).

# the confidence scale's unit is PERCENT (rule_params.confidence.
# base_pct — the cell's measured dir_ave); the history/live record
# columns stay on the INTEGER basis-point scale (×100), so the pct cap
# mirrors CONFIDENCE_CAP_BP / 100
CONFIDENCE_CAP_PCT = 100.0
CONFIDENCE_CAP_BP = 10_000

# ---------------------------------------------------------------------------
# mov_rsi — the RSI percentile family
# ---------------------------------------------------------------------------
# bars are PER-CODE ADAPTIVE percentile quantiles over the trailing
# window (never fixed 30/70); the tail pcts are the nested carving grid
# (3% ⊃ 2% ⊃ 1% — a deep breach counts once, at its deepest claim)
RSI_TAIL_PCTS = (1, 2, 3)
RSI_BAR_WINDOW_DAYS = 2550   # trailing 10y
RSI_BAR_MIN_PERIODS = 255
# the rules-era ENTRY grid: 14 the Wilder classic + 10 the measured
# best buy window (2026-10-15 window study, temp_scripts/
# rsi_window_study/: rsi10_1pct +1.91% @72.2% pooled vs rsi14_1pct
# +1.68% @66.1%; positive 8/10 codes, 5/6 years) + 6 the study's
# runner-up, added 2026-10-16 after the rules-era reproduction
# (temp_scripts/subrule_candidates_study.py: rsi6_1pct pooled buy
# +1.78% @71.2 hit — the study's own number — and gate-passing buy
# cells at every tail pct; the sell side stays pooled-negative and
# the per-cell sell gate keeps it exit-timing only). The fetch
# layer's RSI_WINDOWS (3, 6, 10, 14, 20, 60) stays the data superset.
RSI_ENTRY_WINDOWS = (14, 10, 6)

# ---------------------------------------------------------------------------
# boll_std — the Bollinger fixed-σ family
# ---------------------------------------------------------------------------
# bars are FIXED ±kσ (population σ, ddof=0 — z already IS the Gaussian
# depth score); the ks are the nested carving grid (3σ ⊃ 2.5σ ⊃ 2σ — a
# deep breach counts once, at its deepest claim)
BOLL_KS = (2.0, 2.5, 3.0)

# ---------------------------------------------------------------------------
# ma_cross — the price-vs-MA event family
# ---------------------------------------------------------------------------
# cross (buy): the first close back ABOVE the MA conditioned on the
# CROSS EXCESS — the signed discount at the last below-day; the engine
# picks the per-(code, trend) bar from the candidates
MA_CROSS_EXCESS_CANDIDATES = (-0.01, -0.02, -0.03)
# gdepth (buy ONLY): the raw-gap band breach below the MA; NO top side
# (deep above the MA is momentum — the measured asymmetry)
MA_GDEPTH_BAR_CANDIDATES = (-0.03, -0.05, -0.08)
# retest (sell): an MA breakdown closing a re-test rally whose
# above-episode started within the window of the previous episode's end
MA_RETEST_WINDOW = 20

# ---------------------------------------------------------------------------
# momentum — the log-return momentum family
# ---------------------------------------------------------------------------
# Value axis: the LOG-RETURN ln(1 + momentum) of analysis.mov_ave_momentum
# (the mov_ave_spread momentum step) — the log transform maps the
# bounded-below (-100%) / unbounded-above ROC to a symmetric additive
# space where per-code percentile bars adapt cleanly across codes and
# volatility regimes. Axes: the two FIXED anchors (5/20d) + the three
# VOL-ADAPTIVE lookbacks (short/mid/long — themselves self-adjusted per
# (code, regime) by the momentum step). Bars are PER-CODE ADAPTIVE
# percentile quantiles over the trailing window (the multi-level
# threshold grid: the tail pcts are the nested carving 3% > 2% > 1% —
# a deep breach counts once, at its deepest claim); the confidence
# depth is normalized by the axis's OWN trailing sigma (self-adaptive
# across axes — a 5d and a 100d axis breach compare in sigma units).
MOMENTUM_AXES = ("5days", "20days", "short", "mid", "long")
MOMENTUM_TAIL_PCTS = (1, 2, 3)
MOMENTUM_BAR_WINDOW_DAYS = 2550   # trailing 10y
MOMENTUM_BAR_MIN_PERIODS = 255

# ---------------------------------------------------------------------------
# options_wall — the VRP-gated OI-wall family
# ---------------------------------------------------------------------------
# The breach width was a candidate grid {(0.25, 0.10), 2026-10-19} until
# the sparsity study COLLAPSED it (docs/
# options_wall_sparsity_study_2026-10-19.md): the wall-distance
# distribution is lumpy (p1 -0.30% -> p5 +4.90%) and the dominant
# wall's outer edge sits median ~12% from spot, so breach-day counts
# were IDENTICAL across widths (put 41/41/37, call 15/15/14 over
# {0.05, 0.10, 0.25}) — the width axis is not a coverage lever. The
# single shallowest measured width stands. (The sub_type NAME's 025 is
# historical label vocabulary only — the bar lives in bar_def.)
OW_BREACH_WIDTH_PCTS = (0.10,)
# the put-side VRP gate stays THE gate (put breaches never emit
# ungated — the _rej twin records the fail population). The bar
# LOOSENED twice on measured steps: 0.80 -> 0.70 (2026-10-19, the
# gated population was EMPTY at the 80th) -> 0.60 (the same-day
# sparsity study: p70 kept only 9/41 breach days — breach days' own
# vrp_ratio runs LOWER than typical days, realized reprices faster
# than implied — and p60 adds one event with the pooled edge intact
# +4.04%; p50 halves it, do not go below 0.60 without new evidence)
OW_VRP_PCTL_MIN = 0.60          # implied vol30 in its trailing top 40% vs realized
OW_VRP_REALIZED_WINDOW = 20     # the realized-vol leg (rolling σ of spot returns)
OW_VRP_PCTL_WINDOW = 255        # the VRP ratio's trailing percentile window
# the VRP bar's WARMUP (the sparsity study's main lever): the
# min_periods=255 window erased whole codes' breach history — 000016's
# 10-of-11 put breach days fell in the NaN-bar warmup and its put side
# was structurally empty. 120 and 60 measured IDENTICALLY (the rescued
# 000016 population: n=6 +0.73% hit 83% — a passing buy cell; the
# gated put pool 9 -> 15) — 120 is the more conservative warmup.
OW_VRP_PCTL_MIN_PERIODS = 120
OW_REENTRY_EXIT_DAYS = 0        # strict consecutive-day episodes

# ---------------------------------------------------------------------------
# options_vix — the vol-spike-in-fear-regime family
# ---------------------------------------------------------------------------
# The corr axis is the PUBLISHED series (analysis.
# options_vol_index_vs_spot_price_corr corr_20d — Pearson of daily spot
# log-return vs vol-index log-change over the trailing 20 joint-valid
# sessions; a 5-session stride grid through history + the daily tail),
# linearly interpolated per code onto every session IN-ENGINE — never
# stored back to the DB. The stored grid never feeds a day-over-day
# crossing directly.
OVIX_CORR_BAR = -0.75           # the deep-fear corr20 tail; LOOSENED 2026-10-19
                                # from -0.80 (user-directed) — the measured
                                # ovix_spike_wide coverage play (2026-10-16
                                # study: pooled n=21 dir_ave +1.17% positive)
                                # and the 000852 Feb-2024 V-spike crisis whose
                                # interp corr bottomed -0.74, just past the old bar
OVIX_CORR_WINDOW = 20           # the published corr_20d session window
OVIX_SPIKE_SLOPE = 0.10         # 5d vol slope > +10% (the panic spike)
OVIX_HIVOL_Z = 1.0              # the vol-level z252 leg of the interaction
OVIX_UNWIND_CORR = -0.50        # the sell's corr normalization bar
OVIX_UNWIND_WITNESS = 40        # sessions of deep-fear witness the sell requires
OVIX_Z_WINDOW = 252
OVIX_SLOPE_WINDOWS = (3, 5)
OVIX_REENTRY_EXIT_DAYS = 8      # crisis regimes fire daily; dedup at the regime scale
# ovix_panic_peak — the vol30-value-space PEAK leg (2026-10-15): vol
# escalates past its prior 120-session max by >= 5% while slope5 is
# still > +10% AND spot sits below its 5-sessions-ago close (downside
# fear, not upside euphoria). Measured (options index universe,
# EpisodeDetector K=8 + the plain gate): fires a FRESH episode at the
# crisis's new extreme — 000852/000300 2024-02-05 (the 000852 Feb-2024
# crisis the corr axis never reached: its interp corr bottomed -0.74 vs
# the -0.80 bar) — pooled n=14 dir_ave +3.55% hit 86%; per-code
# 000852 n=5 +4.85% @80, 000300 n=4 +2.32% @75.
OVIX_PEAK_ATH_WINDOW = 120      # the escalation lookback (prior max)
OVIX_PEAK_ATH_MARGIN = 0.05     # the meaningful-new-extreme margin
OVIX_PEAK_DN_WINDOW = 5         # the downside leg's slope-matched window

# ---------------------------------------------------------------------------
# rf_break — the roof/floor breakout family (2026-10-16)
# ---------------------------------------------------------------------------
# The breakout bar is STRUCTURAL (the prior day's roof/floor level from
# analysis.mov_ave_spreads_roof_floor_estimates), not a percentile
# tail: the 2024-09-30 / 2026-10-15 studies showed the per-code tail
# bars and the (trend, regime) cell grid starving exactly at the
# extreme corners where significant highs/lows land, while the
# options-wall family's FIXED 0.25% wall bar caught the same days. The
# trigger day's own level never sets its own bar (the level is shifted
# one row). NO cross-window carving — each (method, window) breach is
# that sub type's own population (the windows are different claim
# strengths; the carve-out variant starved the mid windows' cells).
# Methods: donchian (the window's raw max intraday high / min low,
# method 1) and median (the NaN-skipping median of the five estimate
# methods per (date, period) — the cross-method CONSENSUS level,
# method 5).
RF_BREAK_WINDOWS = (60, 120, 255, 500)
# (label infix, static.label_dim 'roof_floor_method' code) — the infix
# joins the sub_type label: rf{W}_{side} donchian, rf{W}_med_{side}
# median
RF_BREAK_METHODS = (("", 1), ("_med", 5))
# the DEEP-tier breach bars (2026-10-26, the depth-split study's
# graduates — temp_scripts/rf_depth_split_study.py): the deep legs as
# their own sub types, BOTH action BUY, the k bar self-adaptive per
# cell from these grids (the ma_cross gdepth precedent). roof_deep =
# the depth-gated continuation beyond the prior day's roof (the
# monotone-UP gradient: k 1-2% measures ~2x the catch-all's forward
# blend); floor_deep = the capitulation V-bottom below the prior
# day's floor (the deeper the breakdown the harder the forward
# bounce — the study REVERSED the hypothesized deep-floor sell; k 3-5%
# carries the washout population). Pct beyond the level, same unit as
# the breach depth.
RF_ROOF_DEEP_BARS = (1.0, 2.0, 3.0)
RF_FLOOR_DEEP_BARS = (2.0, 3.0, 5.0)

# ---------------------------------------------------------------------------
# SHARED VARIANT AXES — the PE-conditioning + OHLCV-boost gates (2026-10-26)
# ---------------------------------------------------------------------------
# The engines' shared boosted-variant vocabulary (the engines/_pe_boost
# and engines/_ohlcv_boost packages over engines/_variant_base.py):
# the is_pe_boosted / is_ohlcv_boosted PK axes' population gates. A
# variant is a SEPARATE row set from the plain one, judged independently
# by the same measured gate — a variant that cannot clear the bar emits
# nothing (the negative-optimization self-prune). The rebound family
# shipped the first PE variants with these exact constants (2026-10-26,
# the ma-spread rebound study's TP1/TP2 cross tables); they moved here
# verbatim when the axes went shared (rf_break floor legs / bollinger
# turnover opt-ins) — the REBOUND_PE_* names stay as aliases for the
# study scripts.
PE_MIN_PERIODS = 255
# the PE gates: the capitulation class needs a PE EXTREME (cheap OR rich
# tail — the middle-PE capitulation is the dead zone); the
# outside-cheap class (stalled rebounds / reclaims) only holds at
# not-cheap PE
PE_GATE_EXTREMES = (0.4, 0.6)   # cheap <= 0.4 or rich >= 0.6
PE_GATE_NOT_CHEAP = 0.2
# the OHLCV-boost gate: the day's turnover-climax grade (amount /
# 255d-MA bucket, fetch/inputs._shared.finalize_input_frame) at least
# HEAVY (>= 1.5x normal turnover; 2 = the 2.5x climax grade) — the
# volume-confirmation literature as a variant population (never a gate
# on the plain rows: invariant 12's trust-measurement unchanged)
OHLCV_BOOST_MIN_AMT_CODE = 1

# ---------------------------------------------------------------------------
# rebound — the drop→rebound→resolution family (2026-10-26, user-directed)
# ---------------------------------------------------------------------------
# The ma-spread rebound study's graduates
# (temp_scripts/ma_spread_rebound_study.py +
# docs/ma_spread_rebound_study_2026-10-26.md) as rule cells. A big drop
# (close >= the depth bar below the trailing-Wd running max of close,
# SHIFTED one row) activates an episode; the running trough L and the
# rebound peak P track it; the causal RESOLUTIONS are the sub types:
#   capit    BUY   close < L — the capitulation trough-break (the
#                  study's strongest fwd20 population after big drops;
#                  the PE barbell: cheap strongest, rich delayed-but-
#                  real, middle dead)
#   fail     SELL  close < P x (1 - REBOUND_ROLL) with the recovery
#                  ratio R = (P - L) / (H - L) in
#                  [REBOUND_FAIL_R_LO, 1.0) — the stall just below the
#                  last high (the refined further-drop population,
#                  exit timing for longs)
#   reclaim  BUY   close > H — the full reclaim (the next rebound
#                  measures the strongest; the study's H2 graduate)
# Each sub type emits PLAIN and PE-conditioned cells (the
# is_pe_boosted PK axis): the PE gate is the code's own trailing
# PE-PERCENTILE rank (today's PE vs the code's own prior PE history,
# min REBOUND_PE_MIN_PERIODS observations, strictly before t — no
# look-ahead; stats.index_valuation.pe joined at the fetch).
REBOUND_WINDOWS = (60, 255)
# the "big drop" activation bars as CANDIDATES (deepest first — the
# nested-carve/tie convention): the D10 study buckets sharpened without
# sign changes, so the cell gate self-picks per (code, trend)
REBOUND_DEPTHS = (0.10, 0.05)
REBOUND_ROLL = 0.03          # the rollover bar: 3% off the rebound peak
REBOUND_MIN_RB = 0.02        # a >=2% rally counts as a rebound leg
REBOUND_FAIL_R_LO = 0.90     # the failed-reclaim recovery band's floor
# the confidence depth normalizers: pct units per 1.0 depth (the rf 2%
# unit for the pct-spread kinds; the fail band's 5 R-points per unit)
REBOUND_DEPTH_UNIT_PCT = 2.0
REBOUND_DEPTH_UNIT_R = 5.0
REBOUND_PE_MIN_PERIODS = PE_MIN_PERIODS
# the PE gates (the study's TP1/TP2 cross tables — the SHARED variant
# constants above): the capitulation needs a PE EXTREME (cheap OR rich
# tail — the middle-PE capitulation is the dead zone); the fail SELL
# and the reclaim BUY only exist OUTSIDE cheap PE (stalled rebounds
# recover at cheap valuations; reclaims never fire there in the study).
REBOUND_PE_EXTREMES = PE_GATE_EXTREMES   # cheap <= 0.4 or rich >= 0.6
REBOUND_PE_NOT_CHEAP = PE_GATE_NOT_CHEAP

# ---------------------------------------------------------------------------
# macd — the classic crossover family (2026-10-26)
# ---------------------------------------------------------------------------
# The typical-EMA study's product (user-directed "study what most
# typical ema is, eg used 6d 12d 26d, etc, and implement it to replace
# the nearby ema period if any"): the most typical EMA usage is the
# classic MACD set 12/26/9 (Gerald Appel's original; the industry
# default on every charting platform), and the tech_stats tables'
# nearby non-standard span 10 became ema12 + ema26 the same day. The
# family's value source READS those precomputed columns (LEFT JOINed
# stats.{etf,index,stock}_tech_stats — the strategy-tier MacdAlgo's
# source verbatim, never a self-customized EMA recompute) and
# assembles the classic construction: MACD line = ema12 - ema26,
# signal line = EMA9 of the MACD line (the one inline EMA — an EMA of
# the MACD line has no precomputed column), histogram = MACD -
# signal. The trigger is the classic signal-line CROSSOVER
# (an edge — one episode per cross run, K-absorbed): cross-up BUY /
# cross-down SELL (exit timing — the equity asymmetry).
MACD_EMA_SHORT = 12           # the classic fast EMA
MACD_EMA_LONG = 26            # the classic slow EMA
MACD_EMA_SIGNAL = 9           # the signal line: EMA of the MACD line
# the confidence depth normalizer's trailing window — the histogram's
# own trailing sigma (pct of close), the momentum bar convention
# (trailing 10y, one-row-shifted — no look-ahead)
MACD_SIGMA_WINDOW = 2550
MACD_SIGMA_MIN_PERIODS = 255

# ---------------------------------------------------------------------------
# ohlcv boost — the turnover-climax axis (2026-10-16)
# ---------------------------------------------------------------------------
# The day-0 amount axis: trading_amount / its own trading_amt_ma255,
# bucketed into the int8 climax code (0 normal / 1 heavy / 2 climax).
# The volume/turnover-climax literature (buying climax at blow-off
# tops, capitulation volume at lows) as a TRUST-WEIGHTED boost axis —
# never a gate (the boost invariant). Source: the sec_type's raw
# amount table x analysis.mov_ave_spreads_detail.trading_amt_ma255.
AMT_CLIMAX_HEAVY = 1.5       # ratio >= 1.5x the 255d turnover average
AMT_CLIMAX_EXTREME = 2.5     # ratio >= 2.5x — the climax grade

# ---------------------------------------------------------------------------
# options_vix — the volatility-expansion (ATR) breakout leg (2026-10-16)
# ---------------------------------------------------------------------------
# ovix_atr_break BUY: the spot day-return in close-to-close ATR units
# beyond the k bar while the vol index slope5 fires — the 2026-10-15
# grind-down class (fast vol repricing from a LOW base, where the corr
# leg (-0.75) and the vol-ATH leg (+5% over the 120d max) both stay
# unmet). The ATR is close-to-close (analysis.options_spot_daily has no
# OHLC); k is SELF-ADAPTIVE per cell from the candidate grid. The
# LOOSENED 2026-10-19 grid adds k=1.5 — the shallowest candidate the
# gate + pooled tier still judge on evidence (the measured 2026-10-15
# class fired at -2.51 ATR, so 1.5 covers the milder expansions).
OVIX_ATR_WINDOW = 20         # the |ret| mean window (the ATR analog)
OVIX_ATR_KS = (1.5, 2.0, 2.5, 3.0)

# ---------------------------------------------------------------------------
# ma_cross — the market-level breadth rules (2026-10-16)
# ---------------------------------------------------------------------------
# Zweig Breadth Thrust (BUY): the 10d EMA of adv/(adv+dec) over the
# sec_type's own universe rises from below 0.40 to above 0.615 within
# 10 sessions — the rare thrust that has historically marked major
# bottoms (2024-09-24/26 class). 9:1 up/down days: adv >= 9x dec (BUY)
# / dec >= 9x adv (SELL, exit timing). Market-level states joined per
# date onto every code's frame; each code's cell measures its own
# forward blend of the same market day.
BREADTH_EMA_SPAN = 10
BREADTH_THRUST_FROM = 0.40
BREADTH_THRUST_TO = 0.615
BREADTH_THRUST_WITHIN = 10   # sessions the from->to crossing may span
BREADTH_MIN_CODES = 50       # the day's adv+dec floor for a valid state
BREADTH_9TO1_RATIO = 9.0

# ---------------------------------------------------------------------------
# hedge_pairs — the negative-correlation pair family (fresh 2026-10-24)
# ---------------------------------------------------------------------------
# THE TYPICAL SOLUTION on the trio structure: TWO negatively-correlated
# leg curves (the offset-opposite industry composites) + ONE broad
# market curve (000300). The signal is the classic co-wash: when ALL
# THREE curves sit in their own RSI capitulation tails the same day
# (the market-wide wash), BUY the inverse-vol long-long pair — the
# negative covariance is the hedge that makes the rebound's drawdown
# shallower than either leg's own (the audited max-drawdown-reduction
# benefit in history_portfolio_signal_performance). The mirror SELL
# (all three at their RSI exhaustion tails — the blow-off) is
# implemented and gate-checked as the equity-asymmetry control: sells
# are exit timing (bar 0), and the recorded pair-level finding says
# coordinated tops CONTINUE — expect the mirror to measure thin.
#
# THE SPREAD SUB (hp_entry_spread, 2026-10-25, user-directed "one buy
# one sell by the spread between two negative corr curves,
# disregarding the benchmark curve"; study temp_scripts/
# spread_pair_study.py): the pair's long-short SPREAD curve
# (sr = w_a*r_a - w_b*r_b compounded over the same lagged inverse-vol
# weights, leg A long / leg B short, NO market curve) in its own RSI
# bottom tail vs its own adaptive percentile bar — the classic pairs
# CONVERGENCE trade: BUY the laggard leg A, SELL the leader leg B.
# The response axis is the spread value's forward blend. It shares
# the grid (HP_LEG_RSI_WINDOWS x HP_LEG_TAIL_PCTS), the frequency
# floor and the loose gate with the trio; its measured record: the
# raw population is NEGATIVE (the 2026-10-20 ratio-z negative
# reproduces on the RSI axis), the edge lives in the per-pair winners
# (pooled +0.55% t=4.70 hit 61% 5/6 years over the study's 25/48
# winner pairs at the 0.25% bar). The top-tail CONTINUATION (buy the
# leader, +0.46% t=6.15 6/6 yrs over its winners) is the honest
# positive recorded for a future sub; the top-tail convergence MIRROR
# (hp_exit_spread) is the engine's measured asymmetry check, rows
# discarded.
#
# The pair universe is DATA-DRIVEN (the options families' precedent):
# each pair's FRESHEST offsets reading (pool 'all', interval 20) with
# trailing opposite score above HP_MIN_PAIR_SCORE, greedy-disjoint by
# score (each industry in at most one pair), pair code 'A~B'
# lexicographic.
HP_MARKET_CODE = "000300"        # the broad-market curve (witness only)
HP_OFFSETS_TABLE = "analysis_composites.industry_corr_benchmark_offsets"
HP_OFFSETS_POOL = 3              # pool_size 'all' (static.label_dim)
HP_OFFSETS_INTERVAL = 20
HP_MIN_PAIR_SCORE = 0.5          # floor: trailing opposite score (corr < 0)
# the PINNED showcase pair — the Hedge Pairs page's default picks
# (红利 vs 科技): always in the universe even when the pair's freshest
# offsets reading carries no opposite score (its legs' closes and the
# trio gauges are computed the same as every other pair; the
# score/rho ride rule_params as nulls when the offsets have none)
HP_SHOWCASE_PAIR = ("DIV_GENERAL", "TECH_GENERAL")
# the legs' gauge — the mov_rsi family's conventions verbatim: Wilder
# RSI window 14 (the classic), per-curve adaptive percentile bars
# (trailing 10y, one-row-shifted — no look-ahead), the tail grid.
# The GRID is the trio's candidate set the measured gate self-picks
# from per pair (the per-cell winner's tail rides rule_params.
# bar_def.tail_pct): WIDENED 2026-10-25 (user-directed "tails 1/2/3%
# is too strict, portfolio can self pick up rsi as well"), then again
# the same day (user-directed "the signals are too rare, loosen it to
# at least once per year") — 15/20 joined (their pooled evidence
# clears the bar; 25/30 measured out at +0.24%/+0.14% — the honest
# negative stands), the candidates became AT-LEAST populations (a
# deeper wash also satisfies a shallower bar, so a shallow winner
# fires its deeper days too — the carve would have dropped a pair's
# deepest washes from its own cell), and the per-cell winner is the
# FREQUENCY-FLOOR policy below.
HP_LEG_RSI_WINDOW = 14
# the trio's RSI WINDOW GRID (2026-10-25, user-directed "also add rsi10
# as trigger as well" then "ensure rsi10 be shown up"): the candidates
# span (window x tail) — Wilder RSI(w) per curve vs its own adaptive
# tail bars — and the measured gate + frequency floor self-pick the
# winner per pair. The window-major ORDER IS THE PREFERENCE: (10, 14)
# puts the FASTER RSI(10) first — the primary trigger wherever its
# candidate meets the floor (its pooled evidence measures as strong as
# the classic's: tail3 +2.38% t=12.5, 6/6 measurable years positive;
# temp_scripts/trio_window_study.py), RSI(14) the fallback for pairs
# RSI(10) cannot carry. The winner's window rides
# rule_params.bar_def.window (the UI's trio bar decoder renders it).
HP_LEG_RSI_WINDOWS = (10, 14)
HP_LEG_TAIL_PCTS = (1, 2, 3, 5, 10, 15, 20)
# the REGISTERED STANDALONE cells' grid (2026-10-25, user-directed "the
# signals must register and retrieve under signal rules, not directly
# from analysis, so make sure register for is in portfolio signals"):
# the mov_rsi family's own window x tail vocabulary, measured on the
# trio's COMPONENT curves (the market + both legs, pooled both axes)
# and registered as REAL mov_rsi rule rows with is_in_portfolio TRUE —
# the portfolio's signal_rule_ids carry them and the expanded panel
# retrieves them from signal_rules (the sourced layer, re-introduced).
# WIDENED 2026-10-26 (user-directed "the more pct signals are
# dedicated to hedge pair portfolio, write to generate the hedge pair
# dedicated rsi"), then TRIMMED the same day (user-directed "remove
# 15% and 20% rsi, only keep to up to 10%"): the dedicated tier spans
# 1/2/3/5/10 — the 15/20 labels came back out of all three mirrors
# and the live label_dim, the comp frame keeps building its bars over
# the trio's full grid (the portfolio candidates' own vocabulary)
HP_STANDALONE_RSI_WINDOWS = (14, 10, 6)
HP_STANDALONE_TAIL_PCTS = (1, 2, 3, 5, 10)
HP_LEG_BAR_WINDOW_DAYS = 2550
HP_LEG_BAR_MIN_PERIODS = 255
# the hedge cells' FREQUENCY FLOOR (2026-10-25, user-directed "the
# signals are too rare, loosen it to at least once per year", then the
# same day EVENING "portfolio event should be at least once per year"
# — the PER-YEAR COVERAGE semantics): the per-cell winner is the
# DEEPEST gate-passing tail whose episode-first triggers cover EVERY
# ELIGIBLE CALENDAR YEAR (>= this floor in each year past the
# trailing-bar warmup — the year containing the code's first ~400
# calendar days can never qualify; COMPLETE years only, the running
# year exempt until it ends). The prior horizon-AVERAGE semantics
# (n/yr over the whole span) retired the same day: it let a pair's
# events cluster in the wash years while whole calendar years stayed
# empty — 21/44 hp_entry_spread winners carried an empty year, and
# mid-April 2024's market-wide spread washes (t15/t20 depth) fired on
# almost no pair (diag_spread_20240418.py). The best-dir_ave winner
# stays the fallback when no tail meets the floor (honest: its
# shallower tails measured out). Measured studies:
# temp_scripts/trio_freq_study.py (the original average floor),
# temp_scripts/spread_peryear_study.py (the per-year floor — all
# three hedge subs' pooled verdicts PASS: entry +0.20% t=3.06 5/6y,
# exit +0.20% t=3.41 6/6y, trio +0.94% t=8.43).
HP_MIN_EVENTS_PER_YEAR = 1.0
# the trio cell's LOOSE GATE (2026-10-25, user-directed "loosen the
# gate, only obviously bad signals should get rejected"): the family's
# OWN evidence bars — a candidate is measured-out ONLY when its
# measured forward mean is NEGATIVE (any non-negative edge trades).
# The global GATE_DIR_AVE_MIN / POOLED_DIR_AVE_MIN 0.25% bars stay for
# the six standalone families; the trio's bar rides
# rule_params.gate.min_pct, and the pooled verdict keeps its
# STATISTICS legs (t >= 1 + the expanding-year sign majority) with the
# mean bar loosened to "not negative" — an obviously bad POOL is still
# rejected outright
HP_GATE_DIR_AVE_MIN = {"buy": 0.0, "sell": 0.0}
HP_POOLED_DIR_AVE_MIN = 0.0
# the portfolio rules' NAMES in portfolio_signal_rules.rule_params
# (user-directed "rule param json should have a name, e.g., trio wash")
HP_PORTFOLIO_NAME = "trio wash"
HP_PORTFOLIO_NAME_SPREAD = "spread"
# WHICH PORTFOLIO RULE each orchestrating sub writes (2026-10-25,
# user-directed "add portfolio_sub_type in portfolio_signal_rules, then
# migrate trio wash and spread to as portfolio_sub_type"): sub label ->
# (portfolio_sub_type label, its rule_params name). The split's identity
# axis — one portfolio_signal_rules row per (pair, portfolio sub); the
# registered mov_rsi cells (the is_in_portfolio TRUE tier) map INTO
# these rows through signal_rule_ids ONLY, never sitting in the
# portfolio signals themselves
HP_PORTFOLIO_SUB_TYPES: dict[str, tuple[str, str]] = {
    "hp_entry_trio": ("trio_wash", HP_PORTFOLIO_NAME),
    "hp_entry_spread": ("spread", HP_PORTFOLIO_NAME_SPREAD),
    "hp_exit_spread": ("spread", HP_PORTFOLIO_NAME_SPREAD),
}
# the pair construction: inverse-vol weights over the 60d window,
# lagged one row (the rho estimate is noisy; inverse-vol is the robust
# weighting) — portfolio value = 100*exp(cumsum(log1p(w_a*r_a + w_b*r_b)))
HP_VOL_WINDOW = 60
# the PORTFOLIO TIER's gate + MEMBER BENEFIT BOOST: the portfolio's own
# verdict axis is the BENEFIT it took — the hedge type's claim is max
# drawdown reduction vs the MEAN leg (the worst-leg comparison is
# nearly automatic for any two-asset blend; the mean is the honest
# bar). A group passes when the audited benefit clears the bar AND at
# least one orchestrating cell's own is_valid holds; the passing
# group's member confidence INCREASES by 1 + PF_CONF_FACTOR x
# min(benefit_pp / PF_CONF_UNIT_PP, PF_CONF_CAP) (never a decrease —
# a failed gate stamps scale 1.0), recorded on the
# history_portfolio_signal_performance gate digest.
PF_GATE_METRIC = "max_dd_reduction_vs_mean"
PF_GATE_MIN_PP = 0.0      # the bar: reduction vs the MEAN leg, pp
PF_CONF_UNIT_PP = 5.0     # the benefit depth normalizer: 5pp = 1 unit
PF_CONF_FACTOR = 0.25     # the benefit-boost confidence factor per unit
PF_CONF_CAP = 2.0         # the benefit-boost depth cap (max x1.5)

# ---------------------------------------------------------------------------
# population universes
# ---------------------------------------------------------------------------
# the mandatory study scope (analysis/strategy: industries + these 10
# indices only)
STUDY_INDEX_CODES = (
    "000001", "931743", "931160", "931071", "000015", "930708",
    "931994", "931594", "000683", "000991",
)
# the options families' universe is DATA-DRIVEN — the sec_type's option
# underlyings from stats.options_terms, resolved per family at run time
# (fetch/inputs._shared.resolve_options_universe; options_wall drops the
# SSE ETF underlyings, whose chain OI is an accumulated-volume
# estimator). The study indices carry no options data.
