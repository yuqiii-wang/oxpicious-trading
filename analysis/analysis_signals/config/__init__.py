"""analysis.analysis_signals.config — the RULE-TIER constants.

The redesigned tier (2026-10-14): signal_rules (the engine entry +
the SELF-ADAPTIVE per-cell rule_params) populates history_signals by
replay. The constants here are the shared
evaluation contract (gates, floors, the forward blend, the episode
merge) and the family parameter grids the engines measure candidate
cells against; per-family trigger semantics live in each family's
engine module, the family knowledge in static.analysis_signal_docs.

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

# The PORTFOLIO TIER's gate + CONFIDENCE CASCADE (2026-10-20): a
# portfolio group's own verdict axis is the BENEFIT it took (the hedge
# type's claim: max drawdown reduction) — the group's gate is the
# audited drawdown reduction vs the MEAN leg clearing the bar (the
# worst-leg comparison is nearly automatic for any two-asset blend;
# the mean is the honest bar). A group passes when the portfolio gate
# AND the entry rules (at least one orchestrating composite cell's own
# is_valid — the dir_ave verdict on the coordination itself) hold;
# only then do the group's MEMBER cells see the cascade — their
# confidence INCREASED by conf_scale = 1 + PF_CONF_FACTOR x
# min(benefit_pp / PF_CONF_UNIT_PP, PF_CONF_CAP), the cell depth-scale
# formula applied to the benefit depth (never a decrease: a failed
# gate stamps scale 1.0). The scale rides the portfolio snapshots'
# gate digest (history_portfolio_signal_performance); the live tier
# reads it for the is_in_portfolio member cells of is_valid groups,
# beside the starvation sample_scale.
PF_GATE_METRIC = "max_dd_reduction_vs_mean"  # the portfolio gate's metric (the history_portfolio_signal_performance.gate digest mirror)
PF_GATE_MIN_PP = 0.0      # the bar: reduction vs the MEAN leg, percentage points
PF_CONF_UNIT_PP = 5.0     # the benefit depth normalizer: 5pp of drawdown reduction = 1 unit
PF_CONF_FACTOR = 0.25     # the cascade confidence factor per unit (max x1.5 at the cap)
PF_CONF_CAP = 2.0         # the cascade depth cap (the cell depth_cap precedent)

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
# hedge_pairs — the long–long anti-correlated pair family (2026-10-18;
# REWORKED same day — the market-decoupling pass)
# ---------------------------------------------------------------------------
# The online hedge-portfolio study's pair-native discoveries over THREE
# source curves: the two industries' composite closes
# (stats.industry_basic_stats mean_close, pool 'all') and the broad-
# market benchmark close (stats.index_basic_stats — the OFFSET-
# CORRELATION market leg, never a directional gate). The pair universe
# is DATA-DRIVEN — the top offset-opposite industry pairs by the
# trailing 255d opposite score of analysis_composites.
# industry_corr_benchmark_offsets (each industry in at most one pair)
# plus the DIV_GENERAL~TECH_GENERAL showcase pair; the code is the
# composite 'A~B' (lexicographic, mirroring the offsets table's
# dedup). The long–long portfolio is INVERSE-VOL weighted (the
# study's robust choice when the rho estimate is noisy): w_A =
# sigma_B / (sigma_A + sigma_B) over the 60d window, weights lagged
# one row, portfolio value = 100 * exp(cumsum(log1p(w_A*a + w_B*b))).
#
# THE REWORK (2026-10-18, same day): the first cut gated entry on the
# broad-market drawdown with both legs at a same-day MA20 σ-depth —
# every pair fired the SAME action on the SAME crash dates (the family
# traded the market, not the pairs) and the signal count was noise.
# The legs' cheap/expensive gauge is now the SHORT-TERM-TECH signal
# itself (the mov_rsi family's Wilder RSI + per-leg adaptive
# percentile tails — the exact kernel analysis.mov_ave_rsi runs,
# reused verbatim), and the two legs' capitulation signals only need
# to be ROUGHLY NEARBY — within HP_ENTRY_WITNESS_WINDOW traded days of
# each other, never the exact date. No market-drawdown gates remain.
HP_MARKET_CODE = "000300"        # the offset-corr market leg (rho_off only)
# the universe covers ALL industries (2026-10-18 all-industries study):
# greedy disjoint selection over EVERY pair whose trailing 255d offset
# corr is negative (score > 0.5) — no pair-count cap. Industries whose
# every negative-corr partner is already paired stay out (logged); the
# hp_stop_regime exit covers pairs whose rho drifts later.
HP_MIN_PAIR_SCORE = 0.5          # floor: trailing opposite score (corr < 0)
HP_SHOWCASE_PAIR = ("DIV_GENERAL", "TECH_GENERAL")
HP_OFFSETS_TABLE = "analysis_composites.industry_corr_benchmark_offsets"
HP_OFFSETS_POOL = 3              # pool_size 'all' (static.label_dim)
HP_OFFSETS_INTERVAL = 20
HP_OPPOSITE_SCORE_COL = "opposite_score_ma255_255d"
HP_RATIO_Z_WINDOW = 60           # the pair ratio ln(A/B) z window
HP_VOL_WINDOW = 60               # inverse-vol weights + offset-corr window
HP_HARVEST_ZS = (1.5, 2.0)       # |ratio z| bars (sell the high leg)
# the MOMENTUM TILT's divergence grid (2026-10-20 spread-rotation
# study, docs/hedge_pairs_spread_rotation_study_2026-10-20.md): the
# harvest's OPPOSITE pair — at |ratio z| >= k the measured drift is
# CONTINUATION (the strong leg's fwd +0.55/+0.37/+0.39% across the
# grid; the toward-strong tilt the only one that improved BOTH lenses,
# dd +0.16..+0.90pp on 62-67% of events). k = the run_cells candidate
# set (the per-cell winner picks the bar); the mean-reversion
# direction measured negative per-open under every stop rule.
HP_MOM_ZS = (1.5, 2.0, 2.5)      # |ratio z| bars (buy strong / sell lag)
HP_HEDGEABLE_RHO = -0.30         # offset corr at/below = hedgeable
HP_REGIME_BROKE_RHO = 0.0        # offset corr at/above = regime broke
HP_REGIME_WITNESS_WINDOW = 60    # days back a hedgeable reading witnesses a break
# the legs' SHORT-TERM-TECH gauge (the mov_rsi family's conventions,
# verbatim): Wilder RSI window, the nested percentile tail grid (the
# run_cells candidate loop picks the per-cell winner), the per-leg
# adaptive bar window (trailing 10y, one-row-shifted — no look-ahead)
HP_LEG_RSI_WINDOW = 14
HP_LEG_TAIL_PCTS = (1, 2, 3)
HP_LEG_BAR_WINDOW_DAYS = 2550
HP_LEG_BAR_MIN_PERIODS = 255
# the entry's PAIRING window: the two legs' capitulation signals count
# as a pair when they sit within this many of the leg's OWN traded
# days of each other (the trigger fires the day the pair completes)
HP_ENTRY_WITNESS_WINDOW = 5

# The 2026-10-19 more-triggers graduates (docs/
# hedge_pairs_more_triggers_study_2026-10-19.md — the measured study
# behind every bar): the rf washout entry's W-window candidate grid
# (both legs' W-day Donchian-floor breakdown witnesses alive within
# the pairing window; each window its OWN population — the rf_break
# no-carve precedent, carving starves the mid windows; ONE sub since
# the 2026-10-21 dedup — the window is a run_cells candidate, the
# per-cell winner picks it), the market-witness tail (the RSI
# lo-tail whose witness augments wash120 into the trio form: the
# mkt-augmented wash measured the strongest graduate +2.58% t=8.94,
# forward worst-trough -1.79% vs the -3.44% all-days baseline), the
# deep hedgeable rho bar (the grid measured -0.40 flat / -0.50 the
# bar / -0.60 weaker — regime_flip's deep TRADING anchor; buying the
# pair when the covariance cancellation works hardest is the purest
# negative-corr exploitation), and the catch-up reclaim's MA window
# (MA20 measured worse on BOTH lenses — 4/7 years and a trough deeper
# than baseline; the |ratio_z| >= 1 conditioning adds nothing, the
# dip-in-uptrend precedent)
HP_WASH_WINDOWS = (60, 120, 255)
HP_WASH_MKT_WINDOW = 120
HP_WASH_MKT_TAIL_PCT = 3
HP_RHO_DEEP = -0.50
HP_RECLAIM_MA_WINDOW = 60

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
# estimator). The study indices carry no options data. The hedge_pairs
# universe is likewise data-driven — the top offset-opposite industry
# pairs from the offsets table (fetch/inputs._hedge_pairs.
# resolve_hedge_pair_universe; --codes is ignored for the family).
