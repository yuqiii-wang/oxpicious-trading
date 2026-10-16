"""Quantitative roof/floor LEVEL ESTIMATE constants for
analysis.mov_ave_spread.

analysis.mov_ave_spreads_roof_floor_estimates — the estimated
resistance (roof) / support (floor) price level AT a date under one
quantitative method, computed from the strictly-causal rolling window
ending at that date. Internal step of the parent mov_ave_spread pipeline
(see the roof_floor_estimate step package).
"""
ESTIMATE_TABLE = "analysis.mov_ave_spreads_roof_floor_estimates"
ESTIMATE_ANALYSIS_NAME = "mov_ave_spread_roof_floor_estimates"

# Estimate windows (trading days) — the roof/floor windows (the 20d window
# is NOT a roof/floor window; see ROOF_FLOOR_PERIODS in the API service).
# Every method uses the same window region rule as the anchors:
# [max(0, i-period+1), i] in trading-day positions (partial windows at a
# code's history start; NaN closes skipped).
ESTIMATE_WINDOWS = (60, 120, 255, 500, 750, 1275)

# Method registry — labels mirror _common.labels.ROOF_FLOOR_METHOD_CODE /
# static.label_dim 'roof_floor_method' (trendline=0 donchian=1 quantile=2
# channel=3 boll=4 median=5; 'median' is the cross-method consensus row).
#   trendline — the roof/floor anchor LINE (mov_ave_spreads_roof_and_
#               floor_lines semantics) extrapolated to the row date
#   donchian  — window max intraday high / min intraday low
#   quantile  — rolling 90th / 10th percentile of valid closes
#   channel   — OLS close-on-position fit at the row date ± k * residual σ
#   boll      — SMA(period) ± k * population σ of close
#   median    — NaN-skipping median of the five methods above, per
#               (date, period)
ESTIMATE_METHOD_ORDER = ("trendline", "donchian", "quantile", "channel", "boll", "median")

# quantile method band percentiles (the close distribution's upper / lower
# deciles of the window).
ESTIMATE_QUANTILE_HI = 0.90
ESTIMATE_QUANTILE_LO = 0.10

# channel method: residual σ multiplier of the linear-regression channel.
ESTIMATE_CHANNEL_K = 2.0
# channel method: minimum valid closes in the window for a level (a
# fit needs >= 2 for a slope; 3 keeps the residual σ non-degenerate).
ESTIMATE_CHANNEL_MIN_OBS = 3

# boll method: population σ multiplier around the window SMA.
ESTIMATE_BOLL_K = 2.0

# DB schema (LONG format): one row per (sec_type, code, date, period,
# method) — the value columns in table order (after the PK columns
# sec_type, code, date, period, method). The internal compute pipeline
# keeps the WIDE per-window layout (one roof_/floor_ column pair per
# (method, window) on a (sec_type, code, date) frame) and melts to this
# long schema only at the DB-write boundary (roof_floor_estimate/
# long_frame.build_estimate_long_frame).
ESTIMATE_COLUMNS = ("today_close", "roof_estimate", "floor_estimate")


def estimate_wide_columns(w: int) -> tuple:
    """Wide per-(method, window) compute column names: roof_{m}_{w}d /
    floor_{m}_{w}d for every method in ESTIMATE_METHOD_ORDER."""
    return tuple(
        f"{side}_{m}_{w}d"
        for m in ESTIMATE_METHOD_ORDER
        for side in ("roof", "floor")
    )


ESTIMATE_WIDE_COLUMNS = tuple(
    c for w in ESTIMATE_WINDOWS for c in estimate_wide_columns(w)
)

ESTIMATE_DESCRIPTION = (
    "Quantitative roof/floor level estimates (ETF + Index + Stock). LONG "
    "format: one row per (security, business date, period, method) with "
    "period ∈ {60, 120, 255, 500, 750, 1275} trading days and method the "
    "static.label_dim 'roof_floor_method' code {trendline, donchian, "
    "quantile, channel, boll, median}. Each row carries the estimated "
    "resistance (roof_estimate) / support (floor_estimate) price level AT "
    "`date` under that method, computed from the strictly-causal rolling "
    "window of `period` trading days ending at `date` (region rule "
    "[max(0, i-period+1), i] in trading-day positions; partial windows at "
    "a code's history start; NaN closes skipped). trendline: the roof/"
    "floor anchor LINE of mov_ave_spreads_roof_and_floor_lines "
    "extrapolated to `date` (high_2nd + high_line_slope × "
    "days_since_last_high per side; NULL when the anchors do not "
    "determine a line). donchian: the window's maximum intraday high / "
    "minimum intraday low. quantile: the rolling 90th / 10th percentile "
    "of valid closes in the window. channel: the OLS fit of valid closes "
    "on trading-day position over the window evaluated at `date`, ± 2 × "
    "residual σ (NULL under 3 valid closes). boll: SMA(period) ± 2 × "
    "population σ of close. median: the NaN-skipping median of the five "
    "methods per (date, period) — the cross-method consensus. "
    "today_close is the close on `date` (estimate reference). Source: "
    "same DataFrame as the mov_ave_spread parent pipeline (no second DB "
    "round-trip). The sec_type column discriminates the source universe "
    "('etf' | 'index' | 'stock')."
)
