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
    5d/20d/60d horizons; per-horizon reversal probabilities (against
    each row's adaptive reverse_threshold, k·σ of the code's window
    n-day forward changes) and occurrence counts. Each mov_rsi /
    mov_std / mov_gap row carries a
    forecast_id linking 1:1 to its result rows (4 periods).

  - base_rates: per (sec_type, code, stat_month, period) the
    UNCONDITIONAL same-window reference — mean n-day forward change
    and P(change < −reverse_threshold) / P(change > +reverse_threshold)
    over ALL of the code's
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
    "probability of a REVERSAL against the bucket side beyond the bucket's "
    "adaptive reverse_threshold (reverse_threshold column; change < −thr "
    "for top / > +thr for bottom). Rows are emitted only where day_count > 0 "
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
    "5d/20d/60d horizons; plus the per-horizon probability of a "
    "REVERSAL against the breach side beyond the bucket's adaptive "
    "reverse_threshold (reverse_threshold column; change < −thr for upper / "
    "> +thr for lower). Rows are emitted only where day_count > 0 and only for "
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
    "5d/20d/60d horizons; plus the per-horizon probability of a "
    "REVERSAL against the bucket side beyond the bucket's adaptive "
    "reverse_threshold (reverse_threshold column; change < −thr for top / "
    "> +thr for bottom). Rows are emitted only where day_count > 0 and "
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
    "P(change < −reverse_threshold) (base_down_prob — the top/upper-side "
    "reverse_prob base), P(change > +reverse_threshold) (base_up_prob — "
    "the bottom/lower-side reverse_prob base), the same adaptive "
    "reverse_threshold the bucket rows of the (code, stat_month, period) "
    "use (lift stays in one scale) and the valid-day count (base_count). "
    "Reading a bucket's ave_change / reverse_prob against these turns them "
    "into lift (a fixed ±1% bar would be near-saturated at the 20d/60d "
    "horizons — the adaptive k·σ bar keeps the probabilities comparable "
    "across horizons). "
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
# side counts as a reversal. LEGACY fixed bar — today only the fallback
# for codes whose window σ is degenerate (see REVERSE_THRESHOLD_MODE)
# and the forecast_results / base_rates reverse_threshold column DEFAULT
# (rows written before the adaptive bar keep 0.01).
REVERSE_THRESHOLD = 0.01

# ---- Adaptive reversal bar ("std" mode, study 2026-09) -----------------------
#
# reverse_threshold(code, month, n) = k_n · σ(code, month, n) where σ =
# population std of the n-day forward changes over ALL of the code's
# trailing-window days (the base_rates population; recomputed every
# stat month — the window ends at the stat month, so no look-ahead).
# One k per horizon (selected by temp_scripts/study_reverse_threshold.py,
# before/after era split, rolling M-1 P90 gate OOS on index/etf/stock):
# at t = k·σ the no-edge reversal rate is Φ(−k) at EVERY horizon, which
# de-saturates the 20d/60d reverse_probs the fixed 1% bar pins at ~1.0
# (its σ-equivalent shrinks from ~0.7σ at the next-day horizon to ~0.1σ
# at 60d) and makes the cross-period MAX(reverse_prob) confidence
# comparable. OOS gated dir_ave vs the fixed bar at the gate's P90:
# 20d +10/+14/+19%, 60d +16/+15/+29% (index/etf/stock), short horizons
# ~neutral. k = 2 degenerates (dead share > 55%).
REVERSE_THRESHOLD_MODE = "std"  # "std" | "fixed"
REVERSE_THRESHOLD_STD_K: dict[int, float] = {
    1: 0.5,    # next — the fixed 1% bar's median σ-equivalent (~0.55σ)
    5: 0.75,
    20: 1.0,
    60: 1.0,
}

# Window valid-days below which σ is not trusted (the code falls back to
# the fixed REVERSE_THRESHOLD bar for that month/horizon). The 5y
# full-window gate yields ~1,220 days, so this only bites pathological
# windows.
REVERSE_THRESHOLD_STD_MIN_DAYS = 60

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
# reverse_threshold is the bar that row's reverse_prob was computed
# against (adaptive k·σ in "std" mode, the legacy fixed bar in "fixed"
# mode / fallback).
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
    "reverse_threshold",
]

# base_rates columns in write order (PK: sec_type, code, stat_month,
# period). The unconditional reference the bucket results are read
# against (lift) — the probs use the SAME reverse_threshold as the
# bucket rows of the same (code, stat_month, period).
BASE_RATE_COLUMNS: list[str] = [
    "sec_type",
    "code",
    "stat_month",
    "period",
    "base_count",
    "base_ave_change",
    "base_down_prob",
    "base_up_prob",
    "reverse_threshold",
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

# ---- px_vol_state: recent price-change × trading-amount state buckets -------
#
# Fourth bucket family (see database/sql/analysis/analysis_forecasts/
# 05_px_vol_state.sql + the 2026-09 temp_scripts studies
# study_ma_spread_index_9grid*.py / study_ma_spread_index_sharp_slow.py /
# study_px_vol_state_forecast.py): a day joins a bucket when BOTH its
# σ-standardized price speed AND its z-scored 量比 fall in the named
# states. Every rolling stat is shifted 1 row (no look-ahead) and uses
# the code's OWN trailing moments, so the bars adapt per index
# (σ_ret spans ~1%–2.9% daily across equity-like indices; fixed ±2% /
# 量比 1.5/0.8 bars systematically misfire on high/low-σ codes).
#
# Calibration: k/z bars are chosen to reproduce the legacy fixed
# thresholds' POOLED trigger rates on equity-like indices (up 8.4% /
# down 7.6% / 放量 4.0% / 缩量 14.2%), so bucket sample shares stay
# comparable with the studies.
TABLE_PX_VOL = "analysis_forecasts.px_vol_state"
ANALYSIS_NAME_PX_VOL = "px_vol"

# Rolling windows (rows): σ_ret of ret_1d + liangbi moments; the 量比
# base window (trading_amount mean of t-lb_window..t-1). All shifted
# 1 row before use.
PX_VOL_SIGMA_WINDOW = 255
PX_VOL_SIGMA_MIN_DAYS = 60
PX_VOL_LB_WINDOW = 5

# t = ret_1d / σ_ret state bars.
PX_VOL_K_SLOW_UP = 1.26
PX_VOL_K_SLOW_DN = 1.29
PX_VOL_K_SHARP = 2.0

# z_量比 state bars.
PX_VOL_Z_HEAVY = 2.0
PX_VOL_Z_SHRINK = -0.92

# σ_ret floor: below this the code is bond-like (σ ≈ 0.01–0.02% for
# 中证短融/企债) and no bucket ever fires — tiny wiggles would be
# classified as extremes.
PX_VOL_SIGMA_FLOOR = 0.005

# Speed-state names in config-axis order (k // 3 = speed, k % 3 = state).
PX_VOL_SPEEDS: tuple[str, ...] = (
    "sharp_up", "slow_up", "flat", "slow_dn", "sharp_dn",
)
PX_VOL_VOL_STATES: tuple[str, ...] = ("heavy", "normal", "shrink")

# Reversal side per speed (mirrors the mov_* side semantics so
# analysis_signals.gate consumes the table unchanged); flat buckets
# carry side='flat' and a NULL reverse_prob (no directional claim).
PX_VOL_SPEED_SIDE: dict[str, str] = {
    "sharp_up": "top", "slow_up": "top",
    "flat": "flat",
    "slow_dn": "bottom", "sharp_dn": "bottom",
}

# px_vol_state columns in write order (bucket keys + side + recorded
# build parameters + link + motivation cols).
PK_COLUMNS_PX_VOL = [
    "code", "sec_type", "stat_month", "px_speed", "vol_state",
    "is_market_hyped",
]
PX_VOL_COLUMNS = PK_COLUMNS_PX_VOL + [
    "side",
    "sigma_window", "lb_window",
    "k_slow_up", "k_slow_dn", "k_sharp", "z_heavy", "z_shrink",
    "sigma_floor",
    "forecast_id",
]

DESCRIPTION_PX_VOL = (
    "Recent-day price-change × trading-amount state monthly forecasts "
    "(ETF + Index + Stock). For each security and completed month-end "
    "(stat_month), over the trailing 5-year window (stat_month - 5y, "
    "stat_month] of the code's own trading days, buckets the days "
    "whose σ-standardized 1-day price change t = ret_1d / σ_ret "
    "(σ_ret = the code's rolling-255-row, min-60 std of ret_1d, "
    "shifted 1 row) and z-scored 量比 (trading_amount vs its own "
    "5-row trailing mean, z vs the code's rolling-255 moments of "
    "量比, shifted 1 row) BOTH fall in the named states — px_speed "
    "sharp_up (t>2.0) / slow_up (1.26<t<=2.0) / flat (-1.29<=t<=1.26) "
    "/ slow_dn (-2.0<=t<-1.29) / sharp_dn (t<-2.0) × vol_state heavy "
    "(z>2.0) / normal / shrink (z<-0.92). Per-code adaptive bars "
    "(calibrated to the legacy ±2% / 量比 1.5/0.8 pooled trigger "
    "rates) are recorded on every row; days with σ_ret below the "
    "0.005 floor (bond-like indices) or NULL trading_amount never "
    "join a bucket. State cells have no cooldown; buckets are split "
    "by is_market_hyped and carry side top/bottom/flat (flat rows "
    "get NULL reverse_prob). Result data in "
    "analysis_forecasts.forecast_results via forecast_id: mean/std/"
    "max/min forward changes at next/5d/20d/60d, occurrence counts, "
    "max_low_change_ratio and reverse_prob at the bucket's adaptive "
    "reverse_threshold (k_n·σ of the code's window forward changes). "
    "Incremental at month granularity (missing stat_months + refresh "
    "of the last REFRESH_MONTHS); --force deletes the sec_type's rows "
    "(px_vol_state + linked forecast_results) and recomputes."
)

# ---- margin_ratio_state: margin-buy intensity state buckets ----------------
#
# Fifth bucket family (see database/sql/analysis/analysis_forecasts/
# 06_margin_ratio.sql + the 2026-09 temp_scripts/study_margin_ratio_forecast.py,
# docs/margin_ratio_study.md): a day joins a bucket by its 融资买入额/成交额
# ratio (rz_buy / trading_amount, RONGZI only, etf + stock) state vs the
# code's OWN trailing distribution. The 2026-09 study verified the
# Margin Trends hypothesis: the ratio is indicative of short-term future
# volatility (vol5 rank-IC +0.054, 90% of months positive) and trend
# (trend5 IC -0.040, 82% negative) — a CROWDING (contrarian) signal:
# high ratio → weaker forward returns + higher realized vol.
#
# z = (ratio - μ)/σ of the code's rolling Z_WINDOW-row (min
# Z_MIN_PERIODS non-NULL ratio observations) moments, SHIFTED 1 row
# (no look-ahead — px_vol convention). States on buy days (rz_buy > 0,
# trading_amount > 0): vlow z <= -2 / low (-2,-1] / mid (-1,+1] /
# high (+1,+2] / vhigh z > +2; plus no_buy (rz_buy <= 0). Undefined z
# (short history) → no bucket; index has no margin data → no buckets.
TABLE_MARGIN_RATIO = "analysis_forecasts.margin_ratio_state"
ANALYSIS_NAME_MARGIN_RATIO = "margin_ratio"

# States in z-axis order (no_buy first, then ascending z buckets).
MARGIN_RATIO_STATES: tuple[str, ...] = (
    "no_buy", "vlow", "low", "mid", "high", "vhigh",
)

# Reversal side per state (mirrors the mov_* / px_vol side semantics so
# analysis_signals.gate consumes the table unchanged): high/vhigh are
# the crowding states (side 'top' — reverse_prob = P(change < -thr),
# the study's bearish reading); vlow/low/no_buy are the mild-bullish
# states (side 'bottom'); mid carries side='flat' and NULL
# reverse_prob (no directional claim — the central bulk has none).
MARGIN_RATIO_STATE_SIDE: dict[str, str] = {
    "no_buy": "bottom", "vlow": "bottom", "low": "bottom",
    "mid": "flat",
    "high": "top", "vhigh": "top",
}

# Rolling moments of ratio (rows ≈ 5y of trading days; shifted 1 row).
MARGIN_RATIO_Z_WINDOW = 1220
MARGIN_RATIO_Z_MIN_PERIODS = 250

# z state bars (recorded on every row).
MARGIN_RATIO_VLOW_BAR = -2.0
MARGIN_RATIO_LOW_BAR = -1.0
MARGIN_RATIO_HIGH_BAR = 1.0
MARGIN_RATIO_VHIGH_BAR = 2.0

# PK + write columns (bucket keys + side + recorded build parameters +
# link + motivation cols).
PK_COLUMNS_MARGIN_RATIO = [
    "code", "sec_type", "stat_month", "ratio_state", "is_market_hyped",
]
MARGIN_RATIO_COLUMNS = PK_COLUMNS_MARGIN_RATIO + [
    "side",
    "z_window", "z_min_periods",
    "vlow_bar", "low_bar", "high_bar", "vhigh_bar",
    "forecast_id",
]

DESCRIPTION_MARGIN_RATIO = (
    "Margin-buy intensity state monthly forecasts (ETF + Stock — index "
    "has no own margin data). For each security and completed month-end "
    "(stat_month), over the trailing 5-year window (stat_month - 5y, "
    "stat_month] of the code's own trading days, buckets the days by "
    "the 融资买入额/成交额 ratio (rz_buy / trading_amount, RONGZI only) "
    "state vs the code's own trailing distribution: z = (ratio - μ)/σ "
    "of the rolling-1220-row (min 250 non-NULL) moments shifted 1 row — "
    "no_buy (rz_buy <= 0 that day) / vlow (z <= -2) / low (-2,-1] / "
    "mid (-1,+1] / high (+1,+2] / vhigh (z > +2); undefined z → no "
    "bucket. State cells (no cooldown) split by is_market_hyped. "
    "Crowding semantics per the 2026-09 study (trend5 rank-IC -0.040 / "
    "vol5 +0.054): high/vhigh carry side top (bearish), vlow/low/"
    "no_buy side bottom, mid side flat (NULL reverse_prob). Result data "
    "in analysis_forecasts.forecast_results via forecast_id: mean/std/"
    "max/min forward changes at next/5d/20d/60d, occurrence counts, "
    "max_low_change_ratio and reverse_prob at the bucket's adaptive "
    "reverse_threshold (k_n·σ of the code's window forward changes). "
    "Incremental at month granularity (missing stat_months + refresh "
    "of the last REFRESH_MONTHS); --force deletes the sec_type's rows "
    "(margin_ratio_state + linked forecast_results) and recomputes."
)

# ---- opp_pair_state: industry opposite-pair trend forecasts -----------------
#
# Sixth bucket family (see database/sql/analysis/analysis_forecasts/
# 07_opp_pair_state.sql): PAIR forecasts over the industry composite
# trends of analysis_composites.industry_corr_benchmark_offsets — by
# industry pair, when ONE industry's (benchmark-offset) trend is
# dropping, the forecast RESULT is the future trend of the OTHER side
# industry. All trend legs live on the benchmark-OFFSET space the
# composites analysis defines: with MA_W the trailing-W-row rolling
# mean of the industry composite mean_close (pool_size 'all') and MA_M
# the benchmark's (000300) MA_W, the W-day offset trend change of
# industry X ending at t is
#     (MA_X[t] − k·MA_M[t]) − (MA_X[t−W] − k·MA_M[t−W])
#     with k = MA_X[t−W] / MA_M[t−W]
# which is exactly 0 at the lookback start, so normalized by the
# industry's own MA level it reduces to the RELATIVE MA RETURN
#     rel_X(t) = MA_X[t]/MA_X[t−W] − MA_M[t]/MA_M[t−W]
# (an industry whose trend grows while the benchmark grows MORE is
# DROPPING after the offset — rel_X < 0). The forward target is the
# same offset math on the other side industry B over [t, t+n]:
#     fwd_B(t,n) = MA_B[t+n]/MA_B[t] − MA_M[t+n]/MA_M[t]
TABLE_OPP_PAIR = "analysis_forecasts.opp_pair_state"
ANALYSIS_NAME_OPP_PAIR = "opp_pair"

# Bucket trigger industry / sec_type constants. sec_type='index' keeps
# the shared month-gating / gate machinery working (industry_id codes
# are type='index' classification members); the pair universe itself is
# the offsets table's industry pairs, NOT stats.index_identity.
OPP_PAIR_SEC_TYPE = "index"

# Trend windows W (trading-day rows) of the MA curves — the same
# composite-trend smoothing scale as the offsets analysis's short/medium
# windows (255 is a regime filter, too slow for day-level buckets).
OPP_PAIR_TREND_WINDOWS = (20, 60)

# Offset benchmark + pool slice the PAIR SET is read from
# (analysis_composites.industry_corr_benchmark_offsets PK members).
OPP_PAIR_BENCHMARK = "000300"
OPP_PAIR_POOL_SIZE = "all"

# Constant reversal side: the trigger is the dropping (down) state of
# the FIRST industry, so side='bottom' — reverse_prob = P(the OTHER
# side industry's forward offset change > +reverse_threshold), i.e. the
# CONFIRMATION probability of the opposite-pair forecast (B rises when
# A drops), NOT a reversal probability. Mirrors the mov_* side semantics
# so analysis_signals.gate consumes the table unchanged (side bottom →
# action buy on the OTHER side industry).
OPP_PAIR_SIDE = "bottom"

# PK + write columns (bucket keys + side + recorded build parameters +
# link). No is_market_hyped split — industries have no hype source.
PK_COLUMNS_OPP_PAIR = [
    "sec_type", "industry_id", "pair_industry_id", "stat_month",
    "trend_window",
]
OPP_PAIR_COLUMNS = PK_COLUMNS_OPP_PAIR + [
    "side",
    "benchmark_code", "pool_size",
    "forecast_id",
]

DESCRIPTION_OPP_PAIR = (
    "Industry opposite-pair trend monthly forecasts (the "
    "analysis_composites.industry_corr_benchmark_offsets pairs as "
    "buckets). By PAIR: when ONE industry's benchmark-offset trend is "
    "dropping, the forecast RESULT is the future trend of the OTHER "
    "side industry. All legs live on the offset space the composites "
    "analysis defines — with MA_W the trailing-W-row rolling mean of "
    "the industry composite mean_close (stats.industry_basic_stats, "
    "pool_size 'all') and MA_M the benchmark's (000300) MA_W, the "
    "W-day offset trend change (rebased at the lookback start, k = "
    "MA_X[t-W]/MA_M[t-W]) normalized by the industry's own MA level "
    "reduces to the relative MA return rel_X(t) = MA_X[t]/MA_X[t-W] - "
    "MA_M[t]/MA_M[t-W]; the trigger is rel_A(t) < 0 (industry A's "
    "trend grows less than the benchmark = dropping after the "
    "offset), and the forward target is the other side industry B's "
    "fwd_B(t,n) = MA_B[t+n]/MA_B[t] - MA_M[t+n]/MA_M[t] at the "
    "next/5d/20d/60d horizons. One bucket row per (sec_type='index', "
    "industry_id = the dropping industry A, pair_industry_id = the "
    "forecast target B, stat_month, trend_window W in 20/60) — every "
    "unordered pair of the offsets table (pool 'all', benchmark "
    "000300) materialized in BOTH directions, no hype split and no "
    "cooldown (state buckets; industries have no hype source). The "
    "config JSONB records the bucket's mean trigger trend (mean_rel) "
    "and the pair's latest offsets-table context (pair_score = "
    "opposite score, pair_corr = offset_sub_corr, score_date). "
    "side='bottom' so forecast_results.reverse_prob = P(B's forward "
    "offset change > +reverse_threshold) — the pair forecast's "
    "CONFIRMATION probability (B rises when A drops), at B's adaptive "
    "reverse_threshold (k_n·σ of B's window forward offset changes). "
    "Result data in analysis_forecasts.forecast_results via "
    "forecast_id. Incremental at month granularity (missing "
    "stat_months + refresh of the last REFRESH_MONTHS); --force "
    "deletes the opp_pair rows + linked forecast_results and "
    "recomputes."
)
