"""Configuration for analyze.analysis_forecasts.

Monthly per-security forecast analysis stored in the
``analysis_forecasts`` schema, split into MOTIVATION (bucket-defining)
and RESULT tables:

  - mov_rsi: per (sec_type, code, stat_month, rsi_window, side, pct,
    is_market_hyped) the days whose rsi_{W}days sits in the top/bottom
    pct% of the trailing 5-year window ending at stat_month (the RSI
    values themselves join from analysis.mov_ave_rsi via rsi_window).

  - mov_std: per (sec_type, code, stat_month, ma_window, k, side,
    is_market_hyped) the Bollinger-breach days (price beyond
    ma_{W} ± k·std_{W}days) within the same window (motivation:
    breach magnitude mean_excess_close / mean_excess_max /
    max_excess_max; band inputs join from
    analysis.mov_ave_spreads_detail / stats.*_tech_stats).

  - forecast_results: the RESULT data keyed by the surrogate
    forecast_id — mean forward changes at the next, 5d, 20d, 60d
    horizons; max/min forward changes (close-based) at the 5d/20d/60d
    horizons only; the within-window close swing amplitude
    (max_low_change_ratio) at the 5d/20d/60d horizons; per-horizon >1%
    reversal probabilities and occurrence counts. Each mov_rsi /
    mov_std row carries a forecast_id linking 1:1 to its result row.

Each stat month is a COMPLETED calendar month-end; results are immutable
once written (closes / RSI / MA / std inside the window are historical
facts), so the run is incremental at month granularity.
"""
from __future__ import annotations

# ---- Target tables ---------------------------------------------------------

TABLE_FORECAST = "analysis_forecasts.forecast_results"
TABLE_MOV_RSI = "analysis_forecasts.mov_rsi"
TABLE_MOV_STD = "analysis_forecasts.mov_std"

ANALYSIS_NAME_RSI = "mov_rsi"
ANALYSIS_NAME_STD = "mov_std"

DESCRIPTION_RSI = (
    "RSI extreme-day monthly forecasts (ETF + Index + Stock). For each "
    "security and completed month-end (stat_month), over the trailing "
    "5-year window (stat_month - 5y, stat_month] of the code's own "
    "trading days: for each RSI window W (6/10/14/20/60, "
    "mirroring analysis.mov_ave_rsi; 120/255/500 removed) and each "
    "percentile width pct "
    "(1/5/10/25), buckets the days whose rsi_{W}days is in the TOP pct% "
    "(overbought) or BOTTOM pct% (oversold) of the window's non-NULL "
    "rsi_{W}days distribution (linear-interpolated percentile threshold), "
    "with cooldown suppression: after an accepted trigger day the next "
    "5 trading days (PK member cooldown_days) cannot join the bucket. "
    "Buckets are split by PK member is_market_hyped (ANY bucket date "
    "inside one of the code's analysis.mov_ave_market_hypes episodes); "
    "result data in analysis_forecasts.forecast_results via "
    "forecast_id: mean forward fractional changes at the "
    "next-day, 5d, 20d and 60d horizons; close-based max/min forward "
    "changes and the within-window close swing amplitude "
    "(max_low_change_ratio) at the 5d/20d/60d horizons; plus the per-horizon "
    "probability of a >1% REVERSAL against the bucket side (change < -1% "
    "for top / > +1% for bottom). Rows are emitted only where day_count > 0 "
    "and only for codes whose own history spans the FULL window (first "
    "data date <= window start — a code first listed 2020-01 enters only "
    "from the 2025-01 snapshot; no partial-window stats). "
    "Incremental at month granularity (only stat_months missing from "
    "mov_rsi are computed); --force deletes the sec_type's rows (mov_rsi "
    "+ linked forecast_results) and recomputes every target month."
)

DESCRIPTION_STD = (
    "Bollinger-breach monthly forecasts (ETF + Index + Stock). For each "
    "security and completed month-end (stat_month), over the trailing "
    "5-year window (stat_month - 5y, stat_month] of the code's own "
    "trading days: for each MA window W (5/20/60; 120/255 removed) and "
    "each sigma "
    "multiple k (0.5/1.0/1.5/2.0/2.5/3.0), buckets the breach days — "
    "price > ma_{W} + k*std_{W}days (side=upper) or price < "
    "ma_{W} - k*std_{W}days (side=lower), with ma_{W} from "
    "stats.*_tech_stats and std_{W}days from "
    "analysis.mov_ave_spreads_detail. Buckets are split by PK member "
    "is_market_hyped (ANY breach date inside one of the code's "
    "analysis.mov_ave_market_hypes episodes), with cooldown suppression: "
    "after an accepted breach day the next 5 trading days (PK member "
    "cooldown_days) cannot join the bucket. Breach magnitude cols: "
    "mean_excess_close = mean fractional close excursion beyond the "
    "band; mean_excess_max / max_excess_max = mean / max fractional "
    "intraday excursion, high for upper / low for lower breaches; result "
    "data in analysis_forecasts.forecast_results via forecast_id: mean "
    "forward fractional changes at the next-day, 5d, 20d and 60d "
    "horizons; close-based max/min forward changes and the mean "
    "within-window close swing amplitude (max_low_change_ratio) at the "
    "5d/20d/60d horizons; plus the per-horizon probability of a >1% "
    "REVERSAL against the breach side (change < -1% for upper / > +1% "
    "for lower). Rows are emitted only where day_count > 0 and only for "
    "codes whose own history spans the FULL window (first data date <= "
    "window start — a code first listed 2020-01 enters only from the "
    "2025-01 snapshot; no partial-window stats). Incremental at "
    "month granularity (only stat_months missing from mov_std are "
    "computed); --force deletes the sec_type's rows (mov_std + linked "
    "forecast_results) and recomputes every target month."
)

# ---- Universe --------------------------------------------------------------

SEC_TYPES = ("index", "etf", "stock")

# Identity table per sec_type — used by the recent-data pre-filter
# (fetch_codes_with_recent_data_async). Same mapping as the other analyze
# modules (recurring_cycles / mov_ave_spread).
SEC_TYPE_IDENTITY_TABLE = {
    "etf":   "stats.etf_identity",
    "index": "stats.index_identity",
    "stock": "stats.stock_identity",
}

# ---- Bucket definitions ----------------------------------------------------

# RSI windows — mirrors analysis.mov_ave_rsi (Wilder RSI columns).
RSI_WINDOWS = (6, 10, 14, 20, 60)

# Percentile widths for the RSI extreme buckets (percent).
RSI_PCTS = (1, 5, 10, 25)

# Bucket sides: top = overbought (highest-pct% RSI days),
#               bottom = oversold (lowest-pct% RSI days).
RSI_SIDES = ("top", "bottom")

# MA / sigma windows — mirrors analysis.mov_ave_spreads_detail std_*days
# and stats.*_tech_stats ma{5,20,60,120,255}.
MA_WINDOWS = (5, 20, 60)

# Sigma multiples defining the Bollinger bounds.
STD_MULTIPLES = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0)

# Breach sides: upper = price > ma + k*std, lower = price < ma - k*std.
STD_SIDES = ("upper", "lower")

# Cooldown: after an ACCEPTED trigger day, the next N grid trading days
# cannot join the bucket (triggers inside the skip window do NOT restart
# the cooldown — the first trigger after it is accepted). PK member of
# both mov tables; one row per (bucket config × cooldown value). Single
# fixed value for now — widen the tuple to compare variants later.
COOLDOWN_DAYS = (5,)

# Forward-change horizons (trading days): next-day, 5d, 20d, 60d.
FORWARD_HORIZONS = (1, 5, 20, 60)

# Horizons with max/min forward-change + max_low_change_ratio columns
# (5d/20d/60d — the next-day horizon has none).
MM_HORIZONS = (5, 20, 60)

# A forward move beyond this (fractional) threshold against the bucket
# side counts as a reversal (">1% reverse change").
REVERSE_THRESHOLD = 0.01

# ---- Monthly snapshot grid -------------------------------------------------

# "5 y period, incremental monthly": one snapshot per completed month-end,
# by default the last 60 completed months (~5 years of monthly snapshots),
# each computed over a trailing 5-year window.
N_MONTHS = 60
WINDOW_YEARS = 5

# ---- forecast_results column names ------------------------------------------
#
# Per horizon n the column suffix is '' for the next-day horizon and
# f"{n}d" otherwise (ave_next_change / ave_next_5d_change / ...).


def _change_col(prefix: str, n: int) -> str:
    """ave change column for horizon ``n``."""
    return f"{prefix}_next_change" if n == 1 else f"{prefix}_next_{n}d_change"


def _reverse_prob_col(n: int) -> str:
    """>1% reversal-probability column for horizon ``n``."""
    return "reverse_prob" if n == 1 else f"reverse_prob_{n}d"


AVE_CHANGE_COLS = {n: _change_col("ave", n) for n in FORWARD_HORIZONS}
# Close-based max/min forward changes — 5d/20d/60d horizons only.
MAX_CHANGE_COLS = {n: f"max_{n}d_change" for n in MM_HORIZONS}
MIN_CHANGE_COLS = {n: f"min_{n}d_change" for n in MM_HORIZONS}
REVERSE_PROB_COLS = {n: _reverse_prob_col(n) for n in FORWARD_HORIZONS}

# Occurrence counts: per horizon n the number of bucket days with a
# VALID n-day forward change — the denominator of the mean change and
# reversal probability (suffix scheme as the change columns).
OCCURRENCE_COUNT_COLS = {
    n: "occurrence_count_next" if n == 1 else f"occurrence_count_{n}d"
    for n in FORWARD_HORIZONS
}

# Within-window close swing amplitude — 5d/20d/60d horizons only.
# Derived at write time from the row's own max/min forward changes:
# (1 + max change pct) / (1 + min change pct)
#   = max(close[t+1..t+n]) / min(close[t+1..t+n]).
MAX_LOW_RATIO_COLS = {n: f"max_low_change_ratio_{n}d" for n in MM_HORIZONS}

# forecast_results columns in write order (forecast_id first).
RESULT_COLUMNS = (
    ["forecast_id"]
    + [AVE_CHANGE_COLS[n] for n in FORWARD_HORIZONS]
    + [MAX_CHANGE_COLS[n] for n in MM_HORIZONS]
    + [MIN_CHANGE_COLS[n] for n in MM_HORIZONS]
    + [OCCURRENCE_COUNT_COLS[n] for n in FORWARD_HORIZONS]
    + [MAX_LOW_RATIO_COLS[n] for n in MM_HORIZONS]
    + [REVERSE_PROB_COLS[n] for n in FORWARD_HORIZONS]
)

# ---- Primary keys / write column sets --------------------------------------

# is_market_hyped is a PK member (buckets are split by hype overlap);
# cooldown_days is a PK member (trigger suppression window, fixed 5).
PK_COLUMNS_MOV_RSI = [
    "code", "sec_type", "stat_month", "rsi_window", "side", "pct",
    "cooldown_days", "is_market_hyped",
]
PK_COLUMNS_MOV_STD = [
    "code", "sec_type", "stat_month", "ma_window", "k", "side",
    "cooldown_days", "is_market_hyped",
]

# mov_rsi / mov_std columns in write order (bucket keys + link +
# motivation cols). The underlying indicator values are NOT stored —
# rsi_{W}days lives in analysis.mov_ave_rsi, ma/std in
# analysis.mov_ave_spreads_detail + stats.*_tech_stats (joinable via the
# bucket keys); only the market-hype overlap and breach magnitude are
# materialized here.
MOV_RSI_COLUMNS = PK_COLUMNS_MOV_RSI + ["forecast_id"]
MOV_STD_COLUMNS = (
    PK_COLUMNS_MOV_STD
    + ["forecast_id", "mean_excess_close", "mean_excess_max", "max_excess_max"]
)
