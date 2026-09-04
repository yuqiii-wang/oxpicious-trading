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

  - mov_gap: per (sec_type, code, stat_month, gap_window, side, pct,
    is_market_hyped) the days whose gap_{W}days (W-day price return,
    W ∈ {2, 3}, joined from analysis.mov_ave_rsi) sits in the top/bottom
    pct% of the trailing 5-year window ending at stat_month (same
    percentile + cooldown machinery as mov_rsi).

  - forecast_results: the RESULT data keyed by the surrogate
    forecast_id — mean forward changes at the next, 5d, 20d, 60d
    horizons; max/min forward changes (close-based) at the 5d/20d/60d
    horizons only; the best-to-worst n-day outcome ratio
    (max_low_change_ratio — NOT a within-window path swing) at the
    5d/20d/60d horizons; per-horizon >1% reversal probabilities and
    occurrence counts. Each mov_rsi / mov_std / mov_gap row carries a
    forecast_id linking 1:1 to its result rows (4 periods).

  - base_rates: per (sec_type, code, stat_month, period) the
    UNCONDITIONAL same-window reference — mean n-day forward change
    and P(change < -1%) / P(change > +1%) over ALL of the code's
    window trading days — so bucket ave_change / reverse_prob read
    as lift vs base rate.

Each stat month is a COMPLETED calendar month-end; results are immutable
once written (closes / RSI / MA / std inside the window are historical
facts), so the run is incremental at month granularity.
"""
from __future__ import annotations

# ---- Target tables ---------------------------------------------------------

TABLE_FORECAST = "analysis_forecasts.forecast_results"
TABLE_MOV_RSI = "analysis_forecasts.mov_rsi"
TABLE_MOV_STD = "analysis_forecasts.mov_std"
TABLE_MOV_GAP = "analysis_forecasts.mov_gap"
TABLE_BASE_RATE = "analysis_forecasts.base_rates"

ANALYSIS_NAME_RSI = "mov_rsi"
ANALYSIS_NAME_STD = "mov_std"
ANALYSIS_NAME_GAP = "mov_gap"
ANALYSIS_NAME_BASE_RATE = "base_rates"

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
    "changes and the best-to-worst n-day outcome ratio "
    "(max_low_change_ratio) at the 5d/20d/60d horizons; plus the per-horizon "
    "probability of a >1% REVERSAL against the bucket side (change < -1% "
    "for top / > +1% for bottom). Rows are emitted only where day_count > 0 "
    "and only for codes whose own history spans the FULL window (first "
    "data date <= window start — a code first listed 2020-01 enters only "
    "from the 2025-01 snapshot; no partial-window stats). "
    "Incremental at month granularity: stat_months missing from "
    "mov_rsi are computed and the most recent REFRESH_MONTHS "
    "stat_months are refreshed each run (their long-horizon forward "
    "windows may not have been complete at first write); --force "
    "deletes the sec_type's rows (mov_rsi "
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
    "horizons; close-based max/min forward changes and the "
    "best-to-worst n-day outcome ratio (max_low_change_ratio) at the "
    "5d/20d/60d horizons; plus the per-horizon probability of a >1% "
    "REVERSAL against the breach side (change < -1% for upper / > +1% "
    "for lower). Rows are emitted only where day_count > 0 and only for "
    "codes whose own history spans the FULL window (first data date <= "
    "window start — a code first listed 2020-01 enters only from the "
    "2025-01 snapshot; no partial-window stats). "
    "Incremental at month granularity: stat_months missing from "
    "mov_std are computed and the most recent REFRESH_MONTHS "
    "stat_months are refreshed each run (their long-horizon forward "
    "windows may not have been complete at first write); --force "
    "deletes the sec_type's rows (mov_std + linked forecast_results) "
    "and recomputes every target month."
)

DESCRIPTION_GAP = (
    "Short-term price-gap (N-day return) extreme-day monthly forecasts "
    "(ETF + Index + Stock). For each security and completed month-end "
    "(stat_month), over the trailing 5-year window (stat_month - 5y, "
    "stat_month] of the code's own trading days: for each gap window W "
    "(2/3, mirroring analysis.mov_ave_rsi gap_2days / gap_3days — the "
    "W-day fractional price return (price[t] - price[t-W]) / price[t-W]) "
    "and each percentile width pct (1/5/10/25), buckets the days whose "
    "gap_{W}days is in the TOP pct% (sharp W-day rally) or BOTTOM pct% "
    "(sharp W-day selloff) of the window's non-NULL gap_{W}days "
    "distribution (linear-interpolated percentile threshold), with "
    "cooldown suppression: after an accepted trigger day the next 5 "
    "trading days (PK member cooldown_days) cannot join the bucket. "
    "Buckets are split by PK member is_market_hyped (ANY bucket date "
    "inside one of the code's analysis.mov_ave_market_hypes episodes); "
    "result data in analysis_forecasts.forecast_results via "
    "forecast_id: mean forward fractional changes at the next-day, 5d, "
    "20d and 60d horizons; close-based max/min forward changes and the "
    "best-to-worst n-day outcome ratio (max_low_change_ratio) at the "
    "5d/20d/60d horizons; plus the per-horizon probability of a >1% "
    "REVERSAL against the bucket side (change < -1% for top / "
    "> +1% for bottom). Rows are emitted only where day_count > 0 and "
    "only for codes whose own history spans the FULL window (first data "
    "date <= window start — a code first listed 2020-01 enters only "
    "from the 2025-01 snapshot; no partial-window stats). "
    "Incremental at month granularity: stat_months missing from "
    "mov_gap are computed and the most recent REFRESH_MONTHS "
    "stat_months are refreshed each run (their long-horizon forward "
    "windows may not have been complete at first write); --force "
    "deletes the sec_type's rows (mov_gap + linked forecast_results) "
    "and recomputes every target month."
)

DESCRIPTION_BASE_RATE = (
    "Unconditional same-window base rates for the forecast analyses "
    "(RSI-extreme / Bollinger-breach / gap-extreme buckets). Per "
    "security and completed month-end (stat_month), over the same "
    "trailing 5-year window (stat_month - 5y, stat_month] and the same "
    "price space as the buckets, but over ALL of the code's window "
    "trading days (not just the extreme days): per horizon (next/5d/"
    "20d/60d) the mean forward fractional change (base_ave_change), "
    "P(change < -1%) (base_down_prob — the top/upper-side reverse_prob "
    "base), P(change > +1%) (base_up_prob — the bottom/lower-side "
    "reverse_prob base) and the valid-day count (base_count). Reading a "
    "bucket's ave_change / reverse_prob against these turns them into "
    "lift (the fixed ±1% reversal threshold is near-saturated at the "
    "20d/60d horizons — the base rate is what makes those readable). "
    "Same full-window gate as the mov_* tables; one row per (code, "
    "period) where base_count > 0. Incremental at month granularity: "
    "stat_months missing from base_rates are computed and the most "
    "recent REFRESH_MONTHS stat_months are refreshed each run; --force "
    "deletes the sec_type's rows and recomputes every target month."
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

# N-day price-return windows for the gap buckets — mirrors
# analysis.mov_ave_rsi.gap_{W}days (GAP_WINDOWS in analyze.mov_ave_spread.rsi).
GAP_WINDOWS = (2, 3)

# Percentile widths for the gap extreme buckets (percent) — same widths
# as the RSI family.
GAP_PCTS = RSI_PCTS

# Bucket sides: top = sharp W-day rally (highest-pct% gap days),
#               bottom = sharp W-day selloff (lowest-pct% gap days).
GAP_SIDES = RSI_SIDES

# Cooldown: after an ACCEPTED trigger day, the next N grid trading days
# cannot join the bucket (triggers inside the skip window do NOT restart
# the cooldown — the first trigger after it is accepted). PK member of
# all mov tables; one row per (bucket config × cooldown value). Single
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

# The most recent completed months refreshed on EVERY run (incremental
# or force): a month written right after month-end carries permanently
# truncated 20d/60d occurrence counts (its forward windows were not
# complete yet at write time). 4 calendar months > 60 trading days, so
# after a refresh every 60d forward window is full.
REFRESH_MONTHS = 4

# ---- forecast_results columns (consolidated, normalized to long format) ------
#
# Each forecast bucket writes up to 4 rows to forecast_results — one per
# period. Columns have NO period suffix; the ``period`` column carries
# that role ('next', '5d', '20d', '60d'). max_change / min_change /
# max_low_change_ratio are NULL for period='next' (no close-based
# high/low at the 1-day horizon); std_change exists at ALL horizons
# (dispersion of the 1-day changes is well defined).

# period string for each horizon (n = forward days)
PERIOD_FOR_HORIZON: dict[int, str] = {
    1: "next",
    5: "5d",
    20: "20d",
    60: "60d",
}
ALL_PERIODS: tuple[str, ...] = ("next", "5d", "20d", "60d")

# forecast_results columns in write order (forecast_id, period first).
# config is duplicated across all period rows of the same forecast_id.
RESULT_COLUMNS: list[str] = [
    "forecast_id",
    "period",
    "config",
    "ave_change",
    "std_change",
    "max_change",
    "min_change",
    "occurrence_count",
    "max_low_change_ratio",
    "reverse_prob",
]

# base_rates columns in write order (PK: sec_type, code, stat_month,
# period). The unconditional reference the bucket results are read
# against (lift).
BASE_RATE_COLUMNS: list[str] = [
    "sec_type",
    "code",
    "stat_month",
    "period",
    "base_count",
    "base_ave_change",
    "base_down_prob",
    "base_up_prob",
]

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
PK_COLUMNS_MOV_GAP = [
    "code", "sec_type", "stat_month", "gap_window", "side", "pct",
    "cooldown_days", "is_market_hyped",
]

# mov_rsi / mov_std / mov_gap columns in write order (bucket keys + link +
# motivation cols). The underlying indicator values are NOT stored —
# rsi_{W}days and gap_{W}days live in analysis.mov_ave_rsi, ma/std in
# analysis.mov_ave_spreads_detail + stats.*_tech_stats (joinable via the
# bucket keys); only the market-hype overlap and breach magnitude are
# materialized here.
MOV_RSI_COLUMNS = PK_COLUMNS_MOV_RSI + ["forecast_id"]
MOV_STD_COLUMNS = PK_COLUMNS_MOV_STD + ["forecast_id"]
MOV_GAP_COLUMNS = PK_COLUMNS_MOV_GAP + ["forecast_id"]
