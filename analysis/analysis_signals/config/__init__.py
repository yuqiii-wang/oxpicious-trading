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
# user-directed): buy bar 0.75% -> 0.25%, floors 4/3 -> 3/2 — measured
# graduation on the starved watchlist ~176 cells at index scope, plus
# the previously measured-out cells between 0.25% and 0.75% the bar cut
# re-admits; the buy still requires a positive measured edge — the
# asymmetry stands, at a lower bar
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
# (measured-out semantics); the starved wait population is unchanged.
POOLED_DIR_AVE_MIN = 0.0025   # the pooled bar mirrors the cell bar
POOLED_T_STAT_MIN = 1.0       # one-sided ~84% — honest at small n
POOLED_MIN_N = 10             # below this the side has no claim to test
POOLED_YEAR_MIN_N = 5         # a year counts toward sign consistency

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
OW_BREACH_WIDTH_PCT = 0.25      # the meaningful-breach bar (pct of the wall)
OW_VRP_PCTL_MIN = 0.80          # implied vol30 in its trailing top quintile vs realized
OW_VRP_REALIZED_WINDOW = 20     # the realized-vol leg (rolling σ of spot returns)
OW_VRP_PCTL_WINDOW = 255        # the VRP ratio's trailing percentile window
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
OVIX_CORR_BAR = -0.80           # the deep-fear corr20 tail (~5th pctile)
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
# leg (-0.80) and the vol-ATH leg (+5% over the 120d max) both stay
# unmet). The ATR is close-to-close (analysis.options_spot_daily has no
# OHLC); k is SELF-ADAPTIVE per cell from the candidate grid.
OVIX_ATR_WINDOW = 20         # the |ret| mean window (the ATR analog)
OVIX_ATR_KS = (2.0, 2.5, 3.0)

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
