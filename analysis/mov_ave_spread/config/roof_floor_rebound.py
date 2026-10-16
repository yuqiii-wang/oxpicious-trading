"""Roof/floor REBOUND AUDIT constants for analysis.mov_ave_spread.

analysis.mov_ave_spreads_roof_floor_rebounds — one row per recorded
extreme event (episode extreme of fresh W-day extremes) with its
rebound + retest outcome and amplitudes: the 2026-10 retest study
(temp_scripts/study_floor_roof_retest.py) formulated as an auditable
mov_ave table. Internal step of the parent mov_ave_spread pipeline
(see the roof_floor_rebound step package).
"""
REBOUND_TABLE = "analysis.mov_ave_spreads_roof_floor_rebounds"
REBOUND_ANALYSIS_NAME = "mov_ave_spread_roof_floor_rebounds"

# Event scales (trading days) — the roof/floor windows (the 20d window
# is not a roof/floor window). NOTE: a W-day trailing extreme needs W
# lookback rows, so large-W cohorts are empty early in a code's history
# (a data-window property, not a finding).
REBOUND_WINDOWS = (60, 120, 255, 500, 750, 1275)

# Event definition (the 2026-10 study parameterization).
#   candidate : close[t] is the min/max of close[t-W+1, t+CONFIRM_K]
#               (a fresh W-day extreme holding for K more trading days;
#               computed on CLOSES — wicks ignored)
#   episode   : candidates within EPISODE_GAP trading days of the
#               cluster's first candidate collapse into ONE episode;
#               the event is the episode's extreme-close date and level
REBOUND_CONFIRM_K = 10
REBOUND_EPISODE_GAP = 40

# Rebound: a close reaches level*(1 +- REBOUND_PCT) within
# REBOUND_WITHIN trading days of the event (+ for floor, - for roof).
REBOUND_PCT = 0.05
REBOUND_WITHIN = 60

# Retest: the first day after the rebound whose intraday LOW (floor) /
# HIGH (roof) enters the +- REBOUND_ZONE_TAU zone around the level.
REBOUND_ZONE_TAU = 0.01

# Outcome resolution at the retest: broken = a close REBOUND_BREAK_TAU
# beyond the level; held = a close REBOUND_RESOLVE_PCT back away from
# it — whichever happens first (unresolved = neither by the data end).
REBOUND_BREAK_TAU = 0.01
REBOUND_RESOLVE_PCT = 0.05

# Durability extras: CLEAN_BREAK_DAYS = the break is "clean" when the
# close this many days after the break is still beyond the level; the
# break-depth amplitude scans the same span. RETEST_GAP_DAYS = the day
# gap separating distinct retest episodes (zone-touch runs).
REBOUND_CLEAN_BREAK_DAYS = 20
REBOUND_RETEST_GAP_DAYS = 10

# The audit outcome labels (static.label_dim 'rf_rebound_outcome';
# _common.labels.RF_REBOUND_OUTCOME_CODE is the mirror — imported by
# the step package, not duplicated here).

# DB schema column order (after the PK columns sec_type, code, side,
# period, event_date — the trailing-window scale column is `period`,
# matching the roof/floor lines table; `window` is a PG reserved word).
# side is its static.label_dim 'side' code
# (top = roof / recorded MAX, bottom = floor / recorded MIN).
REBOUND_SIDE_TOP = 0    # 'side' domain: roof (recorded MAX event)
REBOUND_SIDE_BOTTOM = 1 # 'side' domain: floor (recorded MIN event)

REBOUND_COLUMNS = (
    "level",
    "outcome",
    "rebound_date",
    "retest_date",
    "days_to_retest",
    "rebound_pct",
    "bounce_pct",
    "break_depth_pct",
    "days_to_break",
    "clean_break",
    "later_break",
    "retest_episodes",
)
REBOUND_DATE_COLUMNS = ("rebound_date", "retest_date")

REBOUND_DESCRIPTION = (
    "Roof/floor rebound audit (ETF + Index + Stock) — the 2026-10 retest "
    "study formulated as a table. One row per recorded extreme event: "
    "the episode extreme of fresh W-day extremes (candidate = "
    "close[t] is the min/max of close[t-W+1, t+10]; candidates within 40 "
    "trading days cluster into one episode; computed on closes), for "
    "side floor (recorded MIN; static.label_dim 'side' bottom) or roof "
    "(recorded MAX; 'side' top), window ∈ {60, 120, 255, 500, 750, "
    "1275} trading days. rebound = a close at level*(1±5%) within 60 "
    "trading days; retest = the first day after the rebound whose low "
    "(floor) / high (roof) enters the ±1% zone; outcome "
    "(static.label_dim 'rf_rebound_outcome'): no_rebound, "
    "never_retested, held (a close 5% back away before any close 1% "
    "beyond the level), broken (a close 1% beyond first), unresolved "
    "(right-censored). Amplitudes as signed/positive fractions: "
    "rebound_pct (rebound-completion close vs level), bounce_pct (held: "
    "max favorable close within 60d of retest), break_depth_pct "
    "(broken: max adverse close within 20d of the break). Durability: "
    "clean_break (still beyond the level 20d after the break), "
    "later_break (held but eventually exceeded on a later retest), "
    "retest_episodes (zone-touch runs separated by ≥10 out-of-zone "
    "days). WHOLESALE-rebuilt per sec_type on every run that computes "
    "it (outcomes depend on post-event data — the high_low_pct_streaks "
    "precedent). Source: same DataFrame as the mov_ave_spread parent "
    "pipeline (no second DB round-trip). The sec_type column "
    "discriminates the source universe ('etf' | 'index' | 'stock')."
)
