"""Roof/floor anchor LINES constants for analysis.mov_ave_spread.

analysis.mov_ave_spreads_roof_and_floor_lines (renamed 2026-10 from
analysis.mov_ave_spreads_detail_ohlc) — rolling window anchors + roof/floor
lines per window. Internal step of the parent mov_ave_spread pipeline (see
steps/ohlc.py).
"""
OHLC_TABLE = "analysis.mov_ave_spreads_roof_and_floor_lines"
OHLC_ANALYSIS_NAME = "mov_ave_spread_ohlc"

# OHLC windows (trading days). Each window W produces the anchor column set
#   open_Wd      — open price on the W-th trading day before `date`
#   high_Wd      — top-high anchor: the MAXIMUM valid CLOSE in the 1st
#                  HALF of the window; the stored value is that anchor
#                  date's CLOSE
#   low_Wd       — top-low anchor: the MINIMUM valid CLOSE in the 1st
#                  half; the stored value is that anchor date's CLOSE
#   high_2nd_Wd  — second-high anchor: the MAXIMUM valid CLOSE found in
#                  [top_high_pos + gap, window_end] (value = that anchor
#                  date's INTRADAY HIGH)
#   low_2nd_Wd   — second-low anchor: the MINIMUM valid CLOSE found in
#                  [top_low_pos + gap, window_end] (value = that anchor
#                  date's INTRADAY LOW)
#   change_since_last_high_Wd / days_since_last_high_Wd — the move since
#                  the LAST high anchor (the later-dated of the two high
#                  anchors: the 2nd whenever present, else the 1st — see
#                  the DB column comments in
#                  03_mov_ave_spreads_roof_and_floor_lines.sql);
#                  change_since_last_low_Wd / days_since_last_low_Wd —
#                  the mirror on the low side
# HALF-SPLIT 1st ANCHORS + DYNAMIC 2nd RANGE: the window [date-W+1,
# date] is cut in half — h = L // 2 with L the window length in
# trading-day positions (for odd L the 2nd half gets the extra day);
# the 1st extreme is the max/min valid CLOSE of the 1st half. The 2nd
# extreme is NOT confined to the 2nd half — its search range starts
# `gap` trading days AFTER the 1st anchor (see ohlc_second_gap_td), so
# the time-distance gap is enforced CONSTRUCTIVELY and the 2nd anchor
# date is ALWAYS strictly after the 1st anchor date wherever both
# exist. Ties go to the earliest date; NaN closes are skipped. The
# columns are NULL when the window has fewer than 2 positions or the
# half holds no valid close; when the 1st anchor is too close to the
# window end (top + gap > window_end), only the 2nd anchor is NULLed
# while the 1st stays valid.
#
# MIGRATION NOTE (2026-09): the gap was 10% of W before; it is now 20%
# of W with a 20-trading-day floor for W >= 60. SEMANTICS CHANGE — rows
# written by the 10% rule are not detectable by find_ohlc_repair_dates
# and must be recomputed via a --force rebuild of the OHLC step.
OHLC_WINDOWS = (20, 60, 120, 255, 500, 750, 1275)

# 2nd-anchor time-distance gap (1st -> 2nd extreme), in trading days:
# gap = ceil(0.20 * W), with a 20-trading-day floor for windows
# W >= 2 * OHLC_GAP_FLOOR_TD (i.e. W >= 60 in OHLC_WINDOWS; W = 20 keeps
# the pure 20% gap because a 20td floor would push the 2nd-anchor search
# start past the window end for EVERY row — the 1st anchor is confined
# to the 1st half, top <= W//2 - 1 = 9, top + 20 > 19 = window end).
# Rationale (study temp_scripts/study_ohlc_gap20_cycles.py, 2026-09):
# after a hype peak the first retest/dead-cat bounce lands within ~20
# trading days; under the old 10% rule ~40% of period-60 and ~26% of
# period-120 roof lines connected the peak to such a quick retest
# (double-top illusion hiding the decay). The floor clears that bounce
# band; for W >= 120 the 20% term already dominates (24td+). The floor
# never NULLs a full window: top <= W//2 - 1 and
# top + floor <= W - 1 wherever W >= 2 * floor.
OHLC_GAP_PCT = 0.20
OHLC_GAP_FLOOR_TD = 20


def ohlc_second_gap_td(w: int) -> int:
    """Time-distance gap between the 1st and 2nd OHLC anchors, in
    trading days, for window W: ceil(0.20 * W) with a 20td floor for
    W >= 2 * OHLC_GAP_FLOOR_TD (see the constants above)."""
    # Exact ceil(w * 20 / 100) via integer math — math.ceil(w * 0.20)
    # would round 24.000000000000004 up to 25 for w = 120 (0.2 is not
    # exactly representable in binary float).
    gap: int = max(1, -(-(w * 20) // 100))
    if w >= 2 * OHLC_GAP_FLOOR_TD:
        gap = max(gap, OHLC_GAP_FLOOR_TD)
    return gap

# DB schema (LONG format): one row per (sec_type, code, date, period) —
# the per-period columns in table order (after the PK columns sec_type,
# code, date). period ∈ OHLC_WINDOWS. The internal compute pipeline keeps
# the WIDE per-window layout (OHLC_WIDE_COLUMNS below, one column set per
# window on a (sec_type, code, date) frame) and melts to this long schema
# only at the DB-write boundary (steps/ohlc.build_ohlc_long_frame).
OHLC_COLUMNS = (
    "today_close",
    "open_over_period",
    "high_over_period",
    "high_date_over_period",
    "low_over_period",
    "low_date_over_period",
    "high_2nd_over_period",
    "high_2nd_date_over_period",
    "low_2nd_over_period",
    "low_2nd_date_over_period",
    "high_line_slope_over_period",
    "low_line_slope_over_period",
    "change_since_last_high",
    "days_since_last_high",
    "change_since_last_low",
    "days_since_last_low",
)


# Internal WIDE per-window compute column names (pre-melt) for window W:
#   open_Wd, high_Wd, high_date_Wd, low_Wd, low_date_Wd,
#   high_2nd_Wd, high_2nd_date_Wd, low_2nd_Wd, low_2nd_date_Wd,
#   high_line_slope_Wd, low_line_slope_Wd,
#   change_since_last_high_Wd, days_since_last_high_Wd,
#   change_since_last_low_Wd, days_since_last_low_Wd
def ohlc_wide_columns(w: int) -> tuple:
    return (
        f"open_{w}d", f"high_{w}d", f"high_date_{w}d",
        f"low_{w}d", f"low_date_{w}d",
        f"high_2nd_{w}d", f"high_2nd_date_{w}d",
        f"low_2nd_{w}d", f"low_2nd_date_{w}d",
        f"high_line_slope_{w}d", f"low_line_slope_{w}d",
        f"change_since_last_high_{w}d", f"days_since_last_high_{w}d",
        f"change_since_last_low_{w}d", f"days_since_last_low_{w}d",
    )


OHLC_WIDE_COLUMNS = tuple(
    c for w in OHLC_WINDOWS for c in ohlc_wide_columns(w)
)

# DATE-type subsets. Used by the melt / sanitize logic to skip numeric
# conversion for the anchor-date columns.
OHLC_DATE_COLUMNS = tuple(
    c for c in OHLC_COLUMNS if "_date_" in c
)
OHLC_WIDE_DATE_COLUMNS = tuple(
    c for c in OHLC_WIDE_COLUMNS if "_date_" in c
)

# Numeric (NUMERIC(18,6)) columns of the wide internal layout.
OHLC_WIDE_NUMERIC_COLUMNS = tuple(
    c for c in OHLC_WIDE_COLUMNS
    if c not in OHLC_WIDE_DATE_COLUMNS
)

# Numeric (NUMERIC(18,6)) columns of the long schema.
OHLC_NUMERIC_COLUMNS = tuple(
    c for c in OHLC_COLUMNS
    if c not in OHLC_DATE_COLUMNS
)

OHLC_DESCRIPTION = (
    "Roof/floor anchor detail analysis (ETF + Index + Stock), renamed "
    "2026-10 from mov_ave_spread_ohlc (table mov_ave_spreads_detail_ohlc). "
    "LONG format: one row "
    "per (security, business date, period) with period ∈ {20, 60, 120, "
    "255, 500, 750, 1275} trading days. Stores today_close plus the "
    "rolling-window anchors for that period. The window "
    "[date-period+1, date] is cut in HALF (h = L // 2 in trading-day "
    "positions; for odd L the 2nd half gets the extra day). "
    "high_over_period / low_over_period are the top anchors: the "
    "MAXIMUM / MINIMUM valid CLOSE of the 1st half (ties -> earliest "
    "date; value = that date's close). "
    "high_2nd_over_period / low_2nd_over_period are the second anchors: "
    "the MAXIMUM / MINIMUM valid CLOSE found in [top_anchor_position + "
    "gap, window_end] — the 2nd anchor is NOT confined to the 2nd half; "
    "the time-distance gap (ceil(0.20*period), with a 20-trading-day "
    "floor for periods >= 60) is enforced CONSTRUCTIVELY by shifting "
    "the 2nd-anchor search range, guaranteeing the 2nd anchor date is "
    "strictly after the 1st anchor date wherever both exist (value = "
    "that date's INTRADAY high/low). When the 1st anchor is too close "
    "to the window end (top + gap > window_end), only the 2nd anchor "
    "is NULLed while the 1st stays valid. NaN closes are skipped; a half "
    "with no valid close NULLs its anchor, and windows with fewer than "
    "2 positions have no anchors. The "
    "columns (high_date_over_period, "
    "low_date_over_period, high_2nd_date_over_period, "
    "low_2nd_date_over_period) record the anchor dates. "
    "change_since_last_high = today_close MINUS the value of the LAST "
    "high anchor (the later-dated of the two high anchors — the 2nd "
    "whenever present, else the 1st; sign indicates growth or drop — "
    "negative = below the roof anchor, positive = above it) and "
    "days_since_last_high = trading days between `date` and that last "
    "high anchor date; change/days_since_last_low are the mirror on the "
    "low side (positive = above the floor anchor). Each side NULL when "
    "its window has no anchor of that side (change also NULL when the "
    "winning anchor's intraday value is missing). Source: same "
    "DataFrame as the mov_ave_spread parent pipeline (no second DB "
    "round-trip). The sec_type column discriminates the source universe "
    "('etf' | 'index' | 'stock')."
)
