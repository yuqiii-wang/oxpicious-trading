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
MA_WINDOWS = (20, 60, 120, 252)

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

# the NEGATIVE-CONFIDENCE REJECT tier (2026-11-16, user-directed — the
# semantics live in engines/reject.py, a module fully SEPARATE from the
# positive-confidence path): a cell whose best candidate measures a
# sign-aligned dir_ave strictly below this bar emits its REAL action at
# NEGATIVE confidence — REJECT the action at this price (a negative SELL
# means "do not sell here", NEVER "buy"); the weak band [this bar, the
# side's gate bar) stays silent (unproven, not rejected). The magnitude
# is the measured negative dir_ave depth/starvation-scaled, mirrored
# against CONFIDENCE_CAP_PCT.
# HIGH-CONFIDENCE BAR (2026-11-18, user-directed "only reject on high
# confidence, there should be very few no action than action"): the bar
# moved OFF zero to a DEEPLY negative level — only a strongly losing
# measured mean may claim against its action (hairline negatives join
# the silent band; at −2.0% about 38% of the 2026-11-16 hairline-era
# rejects would still qualify — ~8% of the action-rule count, with
# recorded confidences ≈ −150..−600bp after the depth/starvation
# scales). 8× the buy gate bar in the adverse direction.
REJECT_DIR_AVE_MAX = -0.02

# the SIGNAL-CLUSTER PROBATION (2026-11-18, user-directed "implement
# signal cluster that if a signal is 1st appeared in past 20d, do not
# set to reject"): a rule cell whose SYSTEM BIRTH — its first-ever
# history_signal_performance snapshot date, the never-purged record of
# when the cell first MEASURED anything — falls within the past N of
# the code's own traded rows before the prepared frame's horizon is
# still FRESH: the reject tier may not claim against its action yet,
# so the cell stays silent exactly like the weak band above (both
# sides — a fresh negative SELL would render the UI's "NO SELL" chip,
# suppressed the same as a fresh "NO BUY"). TRADING rows, not calendar
# (the code's own traded rows define the window, the same convention
# as the episode absorb). NOTE the in-frame first-trigger reading is
# structurally implied, never a guard: the measurement itself needs a
# FULL 20d forward blend realized, so every reject's earliest trigger
# is already >= 20 traded rows old. The births ride one scoped SQL
# read (sql_views .../rule_base/cell_birth_dates.sql) into
# RejectEval.select (engines/reject.py); the LIVE tier inherits the
# probation (a suppressed cell emits no rule row to fire).
REJECT_FIRST_APPEARANCE_DAYS = 20

# the NO-ACTION OVERLAY TIER (2026-11-18, user-directed — the module
# engines/no_action, fully independent of the positive-confidence
# path and of engines/reject.py; docs/no_action_module_design_
# 2026-11-18.md): per-family trigger-time INVALIDATION conditions +
# the cross-family FATIGUE filter, marking FIRED POSITIVE days only
# (overlay — never a suppression), written to the tier's OWN table
# analysis_signals.no_action_signals. VERY CONSERVATIVE by contract:
# calibration-gated rules (temp_scripts/no_action_calibration_
# study.py — a rule ships only with measured separation AND rarity
# <= NO_ACTION_MAX_SHARE), run-time audit digest, and the standing
# asymmetry (never a BUY-side plunge rule). Flip False for the A/B
# isolation check (positive tables must stay bit-identical).
NO_ACTION_ENABLED = True

# the CALIBRATION VERDICT (2026-11-18, temp_scripts/
# no_action_calibration_study.py — the shipping gate): of the study
# doc's candidate rules only boll_squeeze_breakdown SEPARATES on
# the fired population (marked 1.36% vs kept 2.30% aligned blend at
# the tercile cut — the 2026-10-16 honest negative reproduced
# INSIDE the fired set); every other candidate marked populations
# that measure BETTER, not worse — the positive tier's measured
# GATES already absorbed their invalidation (a cell fires only
# where its population measured positive; the noisy corners were
# measured out), and within what still fires the conditions select
# STRONGER events, not weaker. The retired rules stay implemented
# (the vocabulary + the audit machinery) but OUT of the active set;
# re-activate only with new measurements. THE FATIGUE FILTER IS
# RETIRED THE SAME WAY: same-type recency marks the CRISIS CLUSTERS
# (marked buys 3.23% vs kept 1.75% aligned blend at N4/W20, every
# family, both actions, every cell of the N x W grid) — marking
# them no-action would advise against the system's best signals.
# THE HURT RE-STUDY (2026-11-19, full universe — temp_scripts/
# no_action_hurt_study.py + docs/no_action_hurt_study_2026-11-19.md)
# re-confirmed every retirement AT SCALE and measured the damage:
# fatigue marks the crisis clusters (bollinger fatigue marked buys
# +3.97% @ 84% hit vs kept +1.71% — the record's best cohort;
# mov_rsi +3.18% @ 75%), ma_whipsaw and boll_bandwalk_sell marked
# BETTER populations in 7/7 years each (the whipsaw-range reclaims
# bounce hardest; the band-walk sells capture the deepest rolls),
# macd_chop marks ~1/3 of the family with both actions better,
# rebound_pe_deadzone marked better 4/6 years, rsi_bandwalk_sell is
# vacuous (marks ~100% of fired sells). The shipped squeeze rule is
# the ONLY candidate whose marked population measures worse — and
# it does so in 6-7 of 7 years at every cut of the sweep.
NO_ACTION_ACTIVE_RULES = ("boll_squeeze_breakdown",)
NO_ACTION_FATIGUE_ENABLED = False

# the FATIGUE filter (implemented + calibrated, RETIRED — see the
# verdict above): >= this many DISTINCT FIRED DAYS of the
# (code, signal_type, action) within the trailing window of the
# code's own traded rows (STRICTLY BEFORE the fired day) marks the
# fired days beyond the bar; the calibration swept N {3,4,5} x
# W {10,20,40} and every grid cell measured the marked population
# better than the kept one.
NO_ACTION_FATIGUE_MIN_DAYS = 4
NO_ACTION_FATIGUE_WINDOW_ROWS = 20

# bollinger's bandwidth percentile memory (sigma20/sma20 trailing
# quantiles, shifted — the day never sets its own bar). The squeeze
# cut is the bottom NO_ACTION_BOLL_SQUEEZE_Q quantile of the code's
# own trailing bandwidth. THE CUT (2026-11-19 full-universe sweep,
# temp_scripts/no_action_hurt_study.py): every deeper cut keeps the
# separation (marked vs kept aligned blend — q0.10 0.66% vs 1.86% at
# 10.5% share, q0.05 0.74% vs 1.79% at 5.5%, q0.03 0.81% vs 1.76% at
# 3.3%, q0.02 0.76% vs 1.75% at 2.4%; separation 6-7 of 7 years at
# every cut) — the shipped cut is the DEEPEST one meeting the
# tightened < 3% rarity budget (q0.03 measured 3.29% — over).
NO_ACTION_BOLL_BW_WINDOW = 255
NO_ACTION_BOLL_BW_MIN_PERIODS = 60
NO_ACTION_BOLL_SQUEEZE_Q = 0.02

# ma_cross whipsaw: below->above MA flips in the trailing traded
# rows (per fired sub type's own window: ma20_cross counts gap20,
# ma60_cross gap60)
NO_ACTION_MA_WHIPSAW_FLIPS = 3
NO_ACTION_MA_WHIPSAW_WINDOW = 20

# macd chop: distinct cross flips in the trailing rows AND the
# trigger's |histogram| below its own trailing p-quantile (shifted)
NO_ACTION_MACD_CHOP_EPISODES = 2
NO_ACTION_MACD_CHOP_WINDOW = 10
NO_ACTION_MACD_HIST_PCTL = 0.25

# rebound's PE dead zone (the 2026-10-26 barbell): today's PE among
# the code's own PRIOR readings (stats.index_valuation, >= the min
# observations) inside [lo, hi) — not cheap enough to be the floor,
# not rich enough for the violent snapback (middle PE has NO 60d
# edge)
NO_ACTION_PE_DEADZONE = (0.4, 0.6)
NO_ACTION_PE_MIN_PERIODS = 252

# the recorded negative confidence per reason (bp) — a SMALL
# DELIBERATE magnitude: the marker is the product, the magnitude a
# severity hint. Keys are the no_action_reason label vocabulary
# (static.label_dim + _common.labels.NO_ACTION_REASON_CODE + the TS
# mirror).
NO_ACTION_CONF_BP = {
    "fatigue": -120,
    "rsi_bandwalk_sell": -150,
    "boll_squeeze_breakdown": -150,
    "boll_bandwalk_sell": -100,
    "ma_whipsaw": -100,
    "rebound_pe_deadzone": -150,
    "macd_chop": -100,
}

# the rarity budget (TIGHTENED 2026-11-19, user-directed — the hurt
# study): no-action rows stay under this share of the family's
# same-action positive fired rows — < 3%, down from 10%. The hurt
# study's full-universe re-measurement put the retired candidates'
# shares at 4-34% of their families (macd_chop ~1/3) — dilution this
# wide marked BETTER-than-kept populations every time; rarity is the
# first line of defense. Logged in the run summary (the OVER BUDGET
# flag); a rule that cannot meet it at any separating cut does not
# ship. The combined negative tier — rejects + no-action — stays
# well under the positive count.
NO_ACTION_MAX_SHARE = 0.03

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
RSI_BAR_WINDOW_DAYS = 2520   # trailing 10y
RSI_BAR_MIN_PERIODS = 252
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

# the Wilder FAILURE SWING (2026-11-19, the RSI canon study's graduate —
# temp_scripts/rsi_new_rules_study.py: rsi14 +1.78% hit 73% t=3.07,
# rsi10 +2.14% hit 75% t=3.38 pooled on the tail3 episode population;
# BUY-ONLY — the sell side unmeasured, never symmetrized): inside a
# tail3 breach episode the trough (min RSI within the first
# RSI_FSWING_TROUGH_WINDOW rows), the bounce high (max RSI in the next
# RSI_FSWING_BOUNCE_WINDOW rows), the re-dip that HOLDS above the
# trough (a new low is ordinary continuation, not a failure swing),
# and the trigger = the first RSI cross back above the bounce high
# within RSI_FSWING_HORIZON rows of the trough (a pattern segment
# shorter than RSI_FSWING_MIN_SEG is noise). The sub types ride
# RSI_FSWING_WINDOWS (rsi6 unmeasured — do not add).
RSI_FSWING_WINDOWS = (14, 10)
RSI_FSWING_TROUGH_WINDOW = 40
RSI_FSWING_BOUNCE_WINDOW = 20
RSI_FSWING_HORIZON = 60
RSI_FSWING_MIN_SEG = 5

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
MOMENTUM_BAR_WINDOW_DAYS = 2520   # trailing 10y
MOMENTUM_BAR_MIN_PERIODS = 252

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
OW_VRP_PCTL_WINDOW = 252        # the VRP ratio's trailing percentile window
# the VRP bar's WARMUP (the sparsity study's main lever): the
# min_periods=252 window erased whole codes' breach history — 000016's
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
RF_BREAK_WINDOWS = (60, 120, 252, 504)
# the PERSISTENCE + TREND confirmation (2026-11-18, user-directed
# "update the roof/floor break to as multiple days remained exceeded
# the bands and the upward/downward trend maintained"): the break
# trigger is no longer the episode-first CROSS — the close must have
# REMAINED beyond the band for RF_BREAK_PERSIST_DAYS CONSECUTIVE
# trading days (the signal day = the confirmation day the run reaches
# the Nth day; the episode K-absorb still dedupes re-qualifications),
# AND the code's own trend state must agree with the break direction
# at that day: roof legs fire only on rise/top (the upward structure
# maintained), floor legs only on fall/bottom (downward maintained).
# The candidate's own day condition (the deep k bar / the bounce's
# wick-intact guard) rides on the confirmation day as before.
RF_BREAK_PERSIST_DAYS = 3
# the direction-matched trend gates (static.label_dim 'trend' codes):
# roof = the upward states, floor = the downward states
RF_BREAK_TREND_GATE = {"roof": (0, 2), "floor": (1, 3)}
# the ROOF GAP GUARD (2026-11-18, user-directed "stop rf break signal
# if one day rise/drop is too large that the day opportunity is
# lost"; temp_scripts/rf_gap_guard_study.py): a roof_deep trigger is
# STOPPED when the max ONE-DAY rise over the confirmation window (the
# last RF_BREAK_PERSIST_DAYS traded days) reaches this bar — the move
# came from a single spent jump, the day's opportunity is gone.
# Measured on the shipped persistence+trend population: trigger-day
# rises <= 7% all measure +1.45..+2.72% blend, the >= 7% class
# measures -1.41% @32.2% hit (run-move bar 7 drops 974 such triggers,
# the kept population +2.41% vs pool +2.19%). ROOF-ONLY by evidence:
# the floor side measures the EXACT INVERSE — big-drop days are the
# capitulation washout's edge, monotone up (5-7% drops +5.22 @88.8%,
# >7% +6.84 @98.2% hit) and every exclusion bar makes the floor
# worse (kept +2.5..+3.0 vs dropped +5.1..+8.0) — excluding drops
# would amputate the floor_deep/floor_bounce class the depth-split
# study graduated. Do not symmetrize this guard without new
# measurements.
RF_BREAK_GAP_BAR_PCT = 7.0
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
# the ROOF SIDE-CONFIDENCE SCALE (2026-11-18, the floor-bias study's
# product step — user-directed "roof break signals are reduced unless
# very confident"; temp_scripts/rf_roof_scale_study.py + docs/
# rf_floor_bias_retest_squeeze_study_2026-11-18.md): the H1
# matched-depth floor bias (floor/roof 1.24-1.35x at every band, year
# majority 7/7 vs 3/6) implemented as the roof_deep legs' confidence
# reciprocal — every roof_deep trigger rides x0.78 UNLESS its breach
# is VERY CONFIDENT: inside the measured strong zone [2, 5]% beyond
# the roof (both studies' sweet band: matched-depth +1.66/+2.06 vs the
# shallow (1,2] +0.50/+1.25; the >5% blow-off is mixed across cuts —
# donchian plain pool -2.08 @22% hit — so it stays at the REDUCED
# scale too, honest to the conflict). Per-trigger on the history rows
# (the breach pct is the trigger's own value), POSITIVE trading rows
# only; the floor legs stay 1.0 — the bias lives on the roof side.
# Alignment measured: C2 lift 1.596 > the no-scale baseline 1.593,
# wasted confidence mass 0.330 -> 0.327; the rejected alternatives:
# flat-only (order-blind, lift unchanged), the 0.5 blow-off tier
# (measured WORSE than baseline on the cell winners), the cell-t leg
# (+0.008 lift — not worth the complexity).
RF_ROOF_CONF_SCALE = 0.78
RF_ROOF_CONF_ZONE = (2.0, 5.0)

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
PE_MIN_PERIODS = 252
# the PE gates: the capitulation class needs a PE EXTREME (cheap OR rich
# tail — the middle-PE capitulation is the dead zone); the
# outside-cheap class (stalled rebounds / reclaims) only holds at
# not-cheap PE
PE_GATE_EXTREMES = (0.4, 0.6)   # cheap <= 0.4 or rich >= 0.6
PE_GATE_NOT_CHEAP = 0.2
# the OHLCV-boost gate: the day's turnover-climax grade (amount /
# 252d-MA bucket, fetch/inputs._shared.finalize_input_frame) at least
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
REBOUND_WINDOWS = (60, 252)
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
MACD_SIGMA_WINDOW = 2520
MACD_SIGMA_MIN_PERIODS = 252

# ---------------------------------------------------------------------------
# crowdedness — the pool-level crowding tail family (2026-11-16)
# ---------------------------------------------------------------------------
# The market crowdedness composite as rule cells ON THE INDEX ITSELF
# (user-directed: "the buy/sell decisions are on the index/industry,
# not on individual stocks, and the crowdedness stats are for
# assistant info, so that buy low sell high on index"). TWO HOT-SET
# BASES x two score axes x two RECENT-EXTREME states (the v3
# action-logic refactor, 2026-11-16, user-directed "no long period
# percentiles, but should consider recent crowdedness and underlying
# curve ups and downs" — the v2 trailing-10y quantile-tail triggers
# measured out; studies temp_scripts/crowd_action_study{,2,3}.py):
#   drain  BUY   the crowd at its trailing-window LOW while the index
#               ROSE over its own trailing window — the crowd drained
#               while price rose: the move is not overcrowded
#               (+0.39/+0.52/+0.62/+0.63% forward blend on the four
#               bases at the 60/10 windows, all clearing the 0.25%
#               buy bar);
#   pilein SELL  the crowd at its trailing-window HIGH while the
#               index FELL — distribution churn in a decline, exit
#               timing (-0.60% raw on the raw score base; the ma5
#               bases' ungated sells sit at ~0 and their cells are
#               expected to measure out on evidence). The hot-tech
#               confirmation gate measured formulation-dependent (it
#               helped the 20-row pile-in but flipped the 60-row one)
#               — the honest negative stands, not shipped.
# roll_* reads the DAILY-ROLLING hot set (analysis.market_crowdedness
# — re-ranked every date), year_* the THIS-YEAR pinned hot set
# (analysis.market_crowdedness_year joined on hot_year = the date's
# own calendar year — each year's frozen roster chained across
# years). Within each base: _score_* the RAW composite (the
# 0.30/0.30/0.25/0.15 blend of the four pillar percentiles), _ma5_*
# its 5-pool-date moving average (the pipeline's ONLINE read). The
# confidence depth is the breach of the PRIOR window extreme in the
# axis's OWN trailing-window sigma. Data-driven universe: the codes
# with crowdedness rows at the sec_type (index only — etf/stock
# skip).
CROWD_AXES = (
    # (axis id, frame column, sub-type prefix)
    ("roll_score", "crowdedness_score", "crowd_roll_score"),
    ("roll_ma5", "crowdedness_score_ma5", "crowd_roll_ma5"),
    ("year_score", "crowdedness_score_year", "crowd_year_score"),
    ("year_ma5", "crowdedness_score_ma5_year", "crowd_year_ma5"),
)
# the recent-extreme state window: the crowd at its trailing 60-row
# low/high (the separating window in study 2 — the 20-row pile-in is
# unfavorable while the 60-row one carries the sell edge; the drain
# is positive at both, the 60d uniformly stronger per event)
CROWD_STATE_WINDOW = 60
# the state windows' min_periods: SPARSE-CROWD TOLERANCE (the v4 fix,
# 2026-11-16 — the crowdedness build leaves gap days, and a full-60
# requirement blacked out whole stretches, e.g. 2026-10); 20 non-null
# readings in the window suffice
CROWD_STATE_MIN_PERIODS = 20
# the underlying direction window: the index's own trailing 10-row
# return gates the side (drain needs it UP, pilein DOWN — ret10
# separated cleanly where ret5 did not)
CROWD_TREND_WINDOW = 10

# ---------------------------------------------------------------------------
# crowdedness v4 additions — the turning-date catch (2026-11-16,
# user-directed: 2020-05-29 / 2022-10-26 / 2025-06-03 / 2026-10-30 must
# fire BUYs and 2024-11-11 / 2026-06-24 SELLS; studies
# temp_scripts/crowd_action_study4..7 + crowd_date_diagnosis)
# ---------------------------------------------------------------------------
# drain_floor BUY (crowd_roll/year_score_drain_floor): the crowd
# composite at its trailing-250-row BOTTOM (pos <= 0.10) while the
# index ticks up over 5 rows — the year-scale crowd floor with the
# first up-tick (the 2020-05-29 / 2025-06-03 signature; 000001's own
# episodes +0.50% / the year axis +0.19%). The family-scoped LOOSE
# gate (only a negative measured mean rejected — the hedge_pairs
# precedent, run_cells gate_min/pooled_dir_ave_min overrides): the
# pooled means sit below the global 0.25% buy bar.
CROWD_FLOOR_WINDOW = 250
CROWD_FLOOR_MIN_PERIODS = 20
CROWD_FLOOR_POS_CUT = 0.10
CROWD_FLOOR_TREND_WINDOW = 5
# tech_washout BUY (crowd_tech_washout): the hot set's median
# px-vs-MA60 percentile UNDER WATER — the falling-knife capitulation
# catch, any direction (+0.92% forward blend pooled, hit 61%). Two
# arms OR'd: the fresh-depth-extreme arm (<= 0.10 at a fresh 20-row
# low of the percentile with volume picking up — the 2026-10-30
# signature) and the amount-climax arm (<= 0.20 at amt >= 0.80 — the
# 2022-10-26 signature: crowd under water + 放量 climax).
CROWD_HOT_PX60_DEEP = 0.10
CROWD_HOT_PX60_WIDE = 0.20
CROWD_HOT_AMT_MIN = 0.60
CROWD_HOT_AMT_CLIMAX = 0.80
CROWD_HOT_FRESH_WINDOW = 20
# tech_euphoria / tech_euphoria_div SELL: the crowd ma5 at its 60-row
# high or within 10% of its trailing-year high while the hot set's
# px-vs-MA20 percentile >= 0.85 — the blow-off stretch (2026-06-24);
# the _div arm adds the crowd composite ALREADY turning down
# (score_chg5 < 0 — the price/crowd divergence, the 2024-11-11
# signature; 000001's own episodes -0.70% raw = favorable exit
# timing). Sells are not pooled-gated — the per-cell gate judges.
CROWD_HOT_PX20_STRETCH = 0.85
CROWD_EUPHORIA_YEAR_TOP = 0.90

# ---------------------------------------------------------------------------
# ohlcv boost — the turnover-climax axis (2026-10-16)
# ---------------------------------------------------------------------------
# The day-0 amount axis: trading_amount / its own trading_amt_ma252,
# bucketed into the int8 climax code (0 normal / 1 heavy / 2 climax).
# The volume/turnover-climax literature (buying climax at blow-off
# tops, capitulation volume at lows) as a TRUST-WEIGHTED boost axis —
# never a gate (the boost invariant). Source: the sec_type's raw
# amount table x analysis.mov_ave_trading_amt.trading_amt_ma252 (the
# amt columns' home since the 2026-11-08 detail split).
AMT_CLIMAX_HEAVY = 1.5       # ratio >= 1.5x the 252d turnover average
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
# The pair universe is ALL INDUSTRY PAIRS (2026-11-07, user-directed
# "for hedge pairs add all industries (would have (n ** 2) / 2 pairs
# of n industries)"): every unordered pair of the pool-'all' industry
# composites (stats.industry_basic_stats, mean_close rows), n industries
# -> n(n-1)/2 pairs (106 -> 5,565). The scored-disjoint era (trailing
# opposite score above the retired HP_MIN_PAIR_SCORE floor, greedy
# one-pair-per-industry) is retired — each pair's FRESHEST offsets
# reading (pool 'all', interval 20) rides the pair as METADATA
# (score/rho, null where the offsets carry no reading), never a filter,
# and the pinned showcase pair stays the universe's FIRST entry (the
# Hedge Pairs page's default picks). The negative-correlation story
# the gate self-picks on evidence: a positively-correlated pair's
# washes measure thin and its cells measured-out on their own.
HP_MARKET_CODE = "000300"        # the broad-market curve (witness only)
HP_OFFSETS_TABLE = "analysis_composites.industry_corr_benchmark_offsets"
HP_OFFSETS_POOL = 3              # pool_size 'all' (static.label_dim)
HP_OFFSETS_INTERVAL = 20
# the PINNED showcase pair — the Hedge Pairs page's default picks
# (红利 vs 科技): always the universe's first entry (its legs' closes
# and the trio gauges are computed the same as every other pair; the
# score/rho ride rule_params as nulls when the offsets have none)
HP_SHOWCASE_PAIR = ("DIV_GENERAL", "TECH_GENERAL")
# the pair-frame CHUNK (2026-11-07, the all-industries scale): the
# universe's pairs are measured in chunks of this many (each chunk one
# self-contained mini-run of the proven per-pair machinery — build +
# value/spread/trio attaches + the subs' run_cells — so the ~5.5k-pair
# frame never materializes whole: 500 pairs x ~1.7k industry dates x
# the gauge columns is ~2-3GB of GPU, vs ~15-20GB for the whole
# universe at once). Every step is per-pair or per-(pair, date) — the
# chunk partition changes nothing per pair; the ONE cross-pair
# coupling, the pooled evidence tier, judges each chunk's own
# (sub_type, action) population (hundreds of pairs -> thousands of
# triggers per pool — statistically the full-universe verdict).
HP_PAIR_CHUNK = 500
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
HP_LEG_BAR_WINDOW_DAYS = 2520
HP_LEG_BAR_MIN_PERIODS = 252
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
# the study scope of the OTHER analysis/strategy packages (industries +
# these 10 indices only — market_crowdedness etc. still default to it).
# The signals tier's own daily scope is the FULL index universe since
# 2026-11-07 (user-directed "only a small num of codes are run daily,
# now add for all indexes"): --codes 'all' resolves the sec_type's
# codes from its base stats table at run time
# (fetch/inputs._shared.resolve_sec_type_universe).
STUDY_INDEX_CODES = (
    "000001", "931743", "931160", "931071", "000015", "930708",
    "931994", "931594", "000683", "000991",
)
# the options families' universe is DATA-DRIVEN — the sec_type's option
# underlyings from stats.options_terms, resolved per family at run time
# (fetch/inputs._shared.resolve_options_universe; options_wall drops the
# SSE ETF underlyings, whose chain OI is an accumulated-volume
# estimator). The study indices carry no options data.

# ---------------------------------------------------------------------------
# volatility — the realized-vol tail family (2026-11-18)
# ---------------------------------------------------------------------------
# The analysis.volatility panel (the daily realized-vol build) as rule
# cells: the LEVERAGE-EFFECT family — realized-vol peaks cluster at
# price TROUGHS (Black 1976; French-Schwert-Stambaugh 1987 — negative
# returns raise future vol more than positive ones; "local top
# volatility signals local low price", the user's phrasing), and vol
# spikes are EPISODES, not levels (GARCH clustering + mean reversion
# — a top-tail breach is an event). The repo's own capitulation
# populations (rf floor_deep, rebound_capit, ovix panic) measured the
# same washout class; the equity asymmetry keeps the sell side EXIT
# TIMING (tops continue).
#   spike  BUY   the vol axis pierces ABOVE its own trailing top-q
#                adaptive tail while price sits BELOW its 5-rows-ago
#                close — the fear leg (the ovix panic/euphoria
#                direction split: keeps upside euphoria like
#                2024-09-30 out of the buy);
#   crush  SELL  the vol axis below its own bottom-q adaptive tail
#                while price sits within 2% of its trailing-60-row
#                high — complacency AT THE TOP, exit timing (a naked
#                low-vol sell would fire through every calm uptrend;
#                the low-vol anomaly is cross-sectional, not a
#                time-series sell).
# Axes: hv20 = the canonical 20-row close-to-close realized vol,
# ewma = the RiskMetrics fast vol (λ=0.94). Bars are per-code
# adaptive trailing quantiles — the mov_rsi convention (shifted one
# row); nested widest-first carving (the deepest claim counts once).
# Data-driven universe: the codes with volatility rows
# at the sec_type (index only while the panel's run scope is the 10
# study indices — etf/stock skip until the panel widens).
VOL_AXES = (
    # (sub-type prefix, frame column)
    ("hv20", "hv_20"),
    ("ewma", "ewma_vol"),
)
# the tail grid — LOCAL (trailing-year) tails, widened 2026-11-18 from
# the first build's all-history {1,2,3}% (user-directed "a rough check
# on 000001 volatility should have 2 dozens local volatility spikes"):
# the 2520d all-history top-1/2/3% tails are DEGENERATE on the panel's
# ~7y history — the whole-history tail sits so high only the 2024-10
# mega-cluster ever breaches it (000001: 1-2 episodes, 4 events total).
# The LOCAL trailing-252d tails match the visible spikes (000001
# episodes with the fear leg: 5%→6, 10%→9, 15%→15, 20%→19; both axes
# union ≈ the two dozen) AND keep the edge — pooled over the 10 study
# indices every tail clears the buy bar hugely (study
# temp_scripts/vol_spike_count_study.py: +1.15..+1.88% blend,
# t=3.3..4.9, hit 57-67%).
VOL_TAIL_PCTS = (5, 10, 15, 20)
# the adaptive bar + depth-sigma windows — LOCAL (the panel's own
# pct_rank_252 convention: trailing 252 rows, >= 60 obs), shifted one
# row (no look-ahead).
VOL_BAR_WINDOW_DAYS = 252
VOL_BAR_MIN_PERIODS = 60
# the spike BUY's fear-leg window: price below its N-rows-ago close
VOL_SPIKE_LEG_WINDOW = 5
# the crush SELL's price-stretch context: within this fraction of the
# trailing VOL_CRUSH_HIGH_WINDOW-row high (both shifted 1 — no
# look-ahead)
VOL_CRUSH_HIGH_WINDOW = 60
VOL_CRUSH_HIGH_FRAC = 0.98

# ---------------------------------------------------------------------------
# signal amplification — the amplifier tier (2026-11-18; the 2026-11-20
# two-level amplifier axis + the margin type; the 2026-11-22 news type)
# ---------------------------------------------------------------------------
# The close-only signal families' intraday strengthener
# (analysis_signals.signal_amplification_rules + _history, written by
# python -m analysis.analysis_signals.signal_amplification): every
# trigger in the nine standalone families reads the CLOSE (RSI, σ-depth,
# MA gap, momentum, histogram); the intraday open/high/low and the
# volume (trading_amount) ride along unused. This tier records the
# recurring day-level anatomy (the amplifier) and BINDS it to the signal
# families it strengthens/discounts. The amplifier axis is TWO-LEVEL:
# the PARENT TYPE (ohlcv — K-line day anatomy / margin — 融资融券
# margin-balance level / news — the daily news-text MARKET state) + the
# per-type SUB TYPE vocabulary (SIGNAL_AMPLIFIER_CODE when ohlcv,
# SIGNAL_AMPLIFIER_MARGIN_CODE when margin, SIGNAL_AMPLIFIER_NEWS_CODE
# when news).
#
# Two tables:
#   signal_amplification_rules   one row per (code, sec_type, signal_type)
#                                — the binding config (amplifiers[], bars,
#                                base_bp, measured evidence).
#   signal_amplification_history the ORIGINAL RECORD KEEPER — one row per
#                                amplifier day (code, sec_type,
#                                amplifier_type, sub_type, date) with the
#                                day's anatomy facts + the per-binding
#                                amplification applied.
# Data sources: analysis.ohlcv_ext (this tier's own extended tech-stats
# build from stats.*_basic_stats — gaps/wicks/close_pos/prev-day refs/
# amt ratios) + the margin series (stats.{etf,stock}_liquidity_margin
# .rz_balance; indices read analysis.margin_index_series' weighted
# aggregate — NULL for codes without margin data, so no margin amplifier
# days fire there) + history_signals (which bound families fired).
#
# Amplifier strength = base_bp depth-scaled, the CellGate convention:
#   amplify_bp = round(base_bp * (1 + AMP_DEPTH_FACTOR * min(depth, AMP_DEPTH_CAP)))
# capped at |AMP_CAP_BP|; dry_rebound's base is NEGATIVE (a discount).
AMP_DEPTH_FACTOR = 0.5
AMP_DEPTH_CAP = 2
AMP_CAP_BP = 600
# the per-binding measured-evidence floor (rules.evidence.is_valid):
# an amplifier binding is valid when at least this many amplifier days
# had a measurable bound-family response AND the amplifier-day forward
# mean improves on the family's own base with the claimed sign
AMP_EVIDENCE_MIN_N = 3

# the anatomy bars (ohlcv_ext conventions):
AMP_WICK_WINDOW = 252          # trailing window of the wick top-decile bar
AMP_WICK_MIN_PERIODS = 120     # min trailing obs (the ohlc_gaps convention)
AMP_WICK_Q = 0.90              # the top-decile quantile
AMP_AMT_HEAVY_RATIO = 1.5      # 放量: amt_ratio_20 >= this (the AMT_CLIMAX_HEAVY grade)
AMP_AMT_DRY_RATIO = 0.8        # 缩量: amt_ratio_20 <= this (the dry_rebound bar)
AMP_GAP_OPEN_BAR = 0.005       # the up/down-open bar (±0.5% overnight gap)
AMP_BREAKOUT_CLOSE_POS = 0.7   # breakout_confirm's strong-close bar
AMP_BLOWOFF_CLOSE_POS = 0.3    # spike_blowoff's weak-close bar
# the depth-sigma window (gap/breakout depth in the code's own trailing
# return sigma, shifted one row — no look-ahead)
AMP_RET_SIGMA_WINDOW = 252
AMP_RET_SIGMA_MIN_PERIODS = 60

# the MARGIN amplifier bars (the 'margin' parent type, 2026-11-20): the
# 融资余额 (rz_balance; indices read analysis.margin_index_series'
# weighted aggregate) LEVEL vs its own trailing quantile bars — LOW
# balance = leverage washed out (low risk, lower price → BUY
# amplification), HIGH balance = leverage crowding (fragility → SELL
# amplification). Trailing window with the day's own row INCLUDED (the
# anatomy module's wick-bar convention — post-close records, same-day
# observable).
AMP_MARGIN_WINDOW = 252          # trailing window of the margin quantile bars
AMP_MARGIN_MIN_PERIODS = 120     # min trailing obs before a bar exists
AMP_MARGIN_LOW_Q = 0.10          # margin_washout fires at/below this quantile
AMP_MARGIN_HIGH_Q = 0.90         # margin_crowding fires at/above this quantile

# The NEWS amplifier bars (the 'news' parent type, 2026-11-22 —
# migrated from the deleted news_wash detection family the same day:
# the news states AMPLIFY the existing families' signals, never create
# their own). The daily news-text MARKET state panel
# (text.news_text_daily, scope market_all) merged per DATE onto the
# anatomy frame + the per-code weekly aggregates + the drop leg
# (news_anatomy.NewsAnatomy). The performance verification that
# graduated the bars (temp_scripts/
# news_state_signal_vs_amplifier_study.py + the improvement/weekly
# studies, the 10 study indices on the repo's forward-blend contract):
# the deep NEGATIVE tails carry real excess over the +0.276% drift —
# flow_neg >= 5 +1.87% (excess +1.60pp, t=6.70), lex_tone_z <= -1.5
# +1.59% (+1.31pp, t=4.65), sent_z <= -2.0 +0.77% (+0.50pp, t=3.80),
# flow_af <= -1.0 the honest marginal candidate (+0.26pp) — while the
# SELL/euphoria states measured tops-continue (the equity asymmetry),
# hence BUY-side amplifiers except the weekly tone-recovery exit. The
# nested family bar grids COLLAPSED to their primary (deepest measured)
# bars: the amplifier tier has no carve (each amplifier is an
# independent day row) and the shallow remainders measured drift-only;
# depth-scaled amplify_bp scales intensity past the bar.
AMP_NEWS_Z_BAR = -2.0            # sent_shock: sent_z at-or-below
AMP_NEWS_TONE_BAR = -1.5         # tone_panic: lex_tone_z at-or-below
AMP_NEWS_FLOWNEG_BAR = 5         # neg_wave: flow_neg at-least
AMP_NEWS_AF_CALM_BAR = -1.0      # quiet_cov: flow_af at-or-below
# the POSITIVE-extreme divergence bars (the "good news can't lift it"
# pair — euphoric state while the code's own curve falls; the study's
# ungated euphoria states measured tops-continue, the drop-gated
# divergence is the different claim the evidence block judges)
AMP_NEWS_POS_Z_BAR = 1.5         # sent_euphoria: sent_z at-or-above
AMP_NEWS_POS_TONE_BAR = 1.5      # tone_euphoria: lex_tone_z at-or-above
# the INTERACTION bars (the Heston-Sinha flow-beats-tone /
# interaction-is-the-edge finding, the options_vix corr x spike
# precedent): the sentiment shock CONFIRMED by the negative-article
# wave, confirmed again by cross-origin disagreement (disp_z), and the
# tone panic with high disagreement. Measured on the 10 study indices,
# drop-leg gated: z&flow +1.34% / excess +1.07pp / t=5.35; &disp
# +1.74% / +1.46pp / t=6.87 / hit 64%; tone&disp +1.90% / +1.63pp /
# t=7.09 / hit 73% — the measured strongest populations.
AMP_NEWS_ZFLOW_Z_BAR = -1.5      # shock_wave: the shock leg, sent_z at-or-below
AMP_NEWS_ZFLOW_NEG_BAR = 3       # shock_wave: the wave leg, flow_neg at-least
AMP_NEWS_INTER_DISP_BAR = 1.0    # wave/tone_disagree: disp_z at-least
AMP_NEWS_TONE_INTER_BAR = -1.5   # tone_disagree: the tone-panic leg
# the WEEKLY-AGGREGATION bars (the Heston-Sinha horizon finding: daily
# tone dies in <=2 days but weekly aggregation persists ~a quarter —
# the rolling-5 trading-row aggregates, measured WITHOUT the drop leg:
# the weekly aggregate already carries the persistence the leg
# confirms daily): wfn >= 10 +0.77%/t=3.21 both regimes (fwd60 +4.18%);
# wz <= -1 stress +1.85%/t=2.57 vs calm below-bar; wt <= -1 stress
# +5.90%/t=4.51 hit 78% (fwd60 +10.13% — the quarter drift's strongest
# carrier). The SELL companion: the weekly tone RECOVERY exit (wt
# crosses up through 0 after a deep wt episode) — the ONE sell side
# that cleared the bar (the euphoria/coverage sells measured
# below-bar, tops continue).
AMP_NEWS_WEEKLY_WINDOW = 5        # the trading-week rolling window
AMP_NEWS_WEEKLY_Z_BAR = -1.0      # week_shock: mean(sent_z,5) at-or-below
AMP_NEWS_WEEKLY_FLOWNEG_BAR = 10  # week_neg_wave: sum(flow_neg,5) at-least
AMP_NEWS_WEEKLY_TONE_BAR = -1.0   # week_tone: mean(lex_tone_z,5) at-or-below
AMP_NEWS_WEEKLY_TONE_REC_LOOKBACK = 10  # the recovery's deep-episode memory
# the UNDERLYING DIRECTION FILTER leg of every DAILY news amplifier:
# the code's own price below its N-rows-ago close (the volatility
# VOL_SPIKE_LEG_WINDOW convention — trigger-time observable). The
# improvement study's window grid {none, 3, 5, 10} measured FLAT (the
# leg is a per-code confirmation, not a pooled filter) — 5 stands.
AMP_NEWS_DROP_LEG_WINDOW = 5

# THE BINDING MATRIX — which amplifier strengthens which family, at
# which action side, at what base strength (bp; NEGATIVE = discount).
# NESTED per the two-level amplifier axis (2026-11-20): the outer key is
# the PARENT TYPE (decodes via SIGNAL_AMPLIFIER_TYPE_CODE — "ohlcv" /
# "margin"), the inner key the specific pattern ('signal_amplifier'
# vocabulary when ohlcv, 'signal_amplifier_margin' vocabulary when
# margin). Family labels decode via SIGNAL_TYPE_CODE. The v1 scope is
# the standalone close-price families whose signals the day anatomy can
# genuinely qualify — the options families (their own universe/derivative
# axis), the portfolio tier (hedge_pairs / hp_rsi) and the market-level
# breadth rules are out of scope. Bases are priors (the CellGate
# convention: a measured base scaled by depth), not fitted — the
# evidence block on the rules row is the honest verdict.
AMP_BINDINGS: dict[str, dict[str, tuple[tuple[str, str, int], ...]]] = {
    "ohlcv": {
        # BUY-side reversion amplifiers — capitulation/washout anatomy
        # strengthens the buy side of the reversion families
        "flush_washout": (
            ("mov_rsi", "buy", 150), ("bollinger", "buy", 120),
            ("rebound", "buy", 180), ("rf_break", "buy", 120),
            ("volatility", "buy", 100), ("crowdedness", "buy", 100),
        ),
        "gap_reclaim": (
            ("mov_rsi", "buy", 120), ("bollinger", "buy", 100),
            ("rebound", "buy", 150), ("macd", "buy", 100),
        ),
        # BUY continuation — a volume-confirmed breakout strengthens the
        # breakout/continuation families' buy
        "breakout_confirm": (
            ("rf_break", "buy", 180), ("ma_cross", "buy", 120),
            ("macd", "buy", 100), ("momentum", "buy", 100),
            ("rebound", "buy", 100),
        ),
        # SELL-side exit amplifiers — blow-off/exhaustion anatomy
        # strengthens the exit-timing sell
        "spike_blowoff": (
            ("mov_rsi", "sell", 120), ("bollinger", "sell", 100),
            ("momentum", "sell", 100), ("volatility", "sell", 100),
            ("crowdedness", "sell", 100),
        ),
        "exhaustion_gap": (
            ("ma_cross", "sell", 100), ("macd", "sell", 100),
            ("momentum", "sell", 100),
        ),
        # the BUY DISCOUNT — 缩量弱反弹 (an up close on shrinking
        # participation) is fade-prone: the bound families' buy is
        # DISCOUNTED (negative base), deeper dryness discounts harder
        "dry_rebound": (
            ("rebound", "buy", -120), ("ma_cross", "buy", -100),
            ("macd", "buy", -100),
        ),
    },
    "margin": {
        # margin_washout (融资余额低位, 2026-11-20): rz_balance at/below
        # its own trailing-252d q10 bar — leverage washed out. LOW
        # margin indicates LOW risk and a LOWER price: the de-risked
        # balance strengthens the reversion families' BUY (the
        # flush_washout binding set — the same reversion thesis on the
        # leverage axis)
        "margin_washout": (
            ("mov_rsi", "buy", 120), ("bollinger", "buy", 100),
            ("rebound", "buy", 150), ("rf_break", "buy", 120),
            ("volatility", "buy", 100), ("crowdedness", "buy", 100),
        ),
        # margin_crowding (融资余额高位, 2026-11-20): rz_balance at/above
        # its own trailing-252d q90 bar — leverage crowding, the
        # fragile side; strengthens the exit-timing families' SELL
        # (the spike_blowoff binding set's mirror)
        "margin_crowding": (
            ("mov_rsi", "sell", 100), ("bollinger", "sell", 100),
            ("momentum", "sell", 100), ("volatility", "sell", 100),
            ("crowdedness", "sell", 120),
        ),
    },
    "news": {
        # the NEWS state amplifiers (2026-11-22, migrated from the
        # deleted news_wash detection family): the daily news-text
        # MARKET state amplifies the EXISTING families' signals —
        # deep-negative states on the code's own drop leg mark the
        # capitulation washout (BUY reversion), the weekly tone
        # recovery marks the exit (SELL). BUY subs bind the reversion
        # set (the flush_washout / margin_washout convention); bases
        # scale with the studies' measured strength — priors, the
        # evidence block is the honest verdict.
        "tone_disagree": (          # tone&disp +1.90% t=7.09 — strongest
            ("mov_rsi", "buy", 170), ("bollinger", "buy", 150),
            ("rebound", "buy", 190), ("rf_break", "buy", 150),
            ("volatility", "buy", 130), ("crowdedness", "buy", 130),
        ),
        "wave_disagree": (          # z&flow&disp +1.74% t=6.87
            ("mov_rsi", "buy", 160), ("bollinger", "buy", 140),
            ("rebound", "buy", 180), ("rf_break", "buy", 140),
            ("volatility", "buy", 120), ("crowdedness", "buy", 120),
        ),
        "neg_wave": (               # flow_neg >= 5: +1.87% / excess +1.60pp
            ("mov_rsi", "buy", 150), ("bollinger", "buy", 130),
            ("rebound", "buy", 170), ("rf_break", "buy", 130),
            ("volatility", "buy", 110), ("crowdedness", "buy", 110),
        ),
        "tone_panic": (             # lex_tone_z <= -1.5: +1.59% t=4.65
            ("mov_rsi", "buy", 140), ("bollinger", "buy", 120),
            ("rebound", "buy", 160), ("rf_break", "buy", 120),
            ("volatility", "buy", 110), ("crowdedness", "buy", 110),
        ),
        "shock_wave": (             # sent_z & flow_neg: +1.34% t=5.35
            ("mov_rsi", "buy", 130), ("bollinger", "buy", 110),
            ("rebound", "buy", 150), ("rf_break", "buy", 110),
            ("volatility", "buy", 100), ("crowdedness", "buy", 100),
        ),
        "sent_shock": (             # sent_z <= -2.0: +0.77% t=3.80
            ("mov_rsi", "buy", 100), ("bollinger", "buy", 90),
            ("rebound", "buy", 120), ("rf_break", "buy", 90),
            ("volatility", "buy", 80), ("crowdedness", "buy", 80),
        ),
        "quiet_cov": (              # flow_af <= -1.0: marginal +0.26pp
            ("mov_rsi", "buy", 60), ("bollinger", "buy", 50),
            ("rebound", "buy", 70), ("rf_break", "buy", 50),
            ("volatility", "buy", 40), ("crowdedness", "buy", 40),
        ),
        # the weekly aggregates (no drop leg — the aggregate carries
        # the persistence): wt <= -1 stress +5.90% hit 78% (fwd60
        # +10.13%), wz <= -1 stress +1.85%, wfn >= 10 +0.77% both
        "week_tone": (
            ("mov_rsi", "buy", 140), ("bollinger", "buy", 120),
            ("rebound", "buy", 160), ("rf_break", "buy", 120),
            ("volatility", "buy", 110), ("crowdedness", "buy", 110),
        ),
        "week_shock": (
            ("mov_rsi", "buy", 110), ("bollinger", "buy", 100),
            ("rebound", "buy", 130), ("rf_break", "buy", 100),
            ("volatility", "buy", 90), ("crowdedness", "buy", 90),
        ),
        "week_neg_wave": (
            ("mov_rsi", "buy", 100), ("bollinger", "buy", 90),
            ("rebound", "buy", 120), ("rf_break", "buy", 90),
            ("volatility", "buy", 80), ("crowdedness", "buy", 80),
        ),
        # the positive-extreme divergence pair ("good news can't lift
        # a falling market" — euphoric state on the drop leg);
        # faithful BUY-side migration, modest priors, the evidence
        # block judges each binding
        "sent_euphoria": (
            ("mov_rsi", "buy", 80), ("bollinger", "buy", 70),
            ("rebound", "buy", 90), ("rf_break", "buy", 70),
            ("volatility", "buy", 60), ("crowdedness", "buy", 60),
        ),
        "tone_euphoria": (
            ("mov_rsi", "buy", 80), ("bollinger", "buy", 70),
            ("rebound", "buy", 90), ("rf_break", "buy", 70),
            ("volatility", "buy", 60), ("crowdedness", "buy", 60),
        ),
        # the ONE sell side (exit timing): the weekly tone recovery —
        # wt crosses up through 0 after a deep episode; binds the
        # exit-timing set (the spike_blowoff convention)
        "week_tone_recovery": (
            ("mov_rsi", "sell", 100), ("bollinger", "sell", 100),
            ("momentum", "sell", 100), ("volatility", "sell", 100),
            ("crowdedness", "sell", 110),
        ),
    },
}
