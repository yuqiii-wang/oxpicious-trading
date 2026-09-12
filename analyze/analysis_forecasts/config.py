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
    ma_{W} ± k·std_{W}days) within the same window (band inputs join
    from analysis.mov_ave_spreads_detail / stats.*_tech_stats).

  - mov_gap: per (sec_type, code, stat_month, gap_window, side, pct,
    is_market_hyped) the days whose gap_{W}days (W-day price return,
    W ∈ {2, 3}, joined from analysis.mov_ave_rsi) sits in the top/bottom
    pct% of the trailing 5-year window ending at stat_month (same
    percentile + streak-mid machinery as mov_rsi).

  - forecast_results: the RESULT data keyed by the surrogate
    forecast_id — mean forward changes at the next, 5d, 20d, 60d
    horizons; max/min forward changes (close-based) at the 5d/20d/60d
    horizons only; the swing ratio (max_low_change_ratio — (1 + the widest path high) / (1 + the deepest path low across the bucket's trigger days' n-day forward windows, signed; ≥ 1, large = a large within-period close swing)) at the
    5d/20d/60d horizons; per-horizon swing-aware reversal
    probabilities — P(the n-day forward window's adverse path extreme
    beyond the fixed 1% bar: the window swung past ±1% against the
    side at some close; at the 5d horizon about the closes of the 5
    days after the signal) — and
    occurrence counts. Each mov_rsi /
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
TABLE_IDENTITIES = "analysis_forecasts.forecast_identities"
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
    "with streak-merge (since 2026-09, replacing the legacy fixed "
    "5-day cooldown): dates that keep satisfying the bucket condition "
    "CONTINUOUSLY collapse into ONE forecast signal anchored at the "
    "streak's MID day (the ((L-1)//2 + 1)-th day, the high_low_streaks "
    "convention); the bucket's mean streak length is recorded on "
    "analysis_forecasts.forecast_identities.streak_signal_days. "
    "Buckets are split by PK member is_market_hyped (ANY bucket date "
    "inside one of the code's stats.mov_ave_market_hypes episodes); "
    "result data in analysis_forecasts.forecast_results via "
    "forecast_id: mean forward fractional changes at the "
    "next-day, 5d, 20d and 60d horizons; close-based max/min forward "
    "changes and the swing ratio (max_low_change_ratio — (1 + the widest path high) / (1 + the deepest path low across the bucket's trigger days' n-day forward windows, signed; ≥ 1, large = a large within-period close swing)) "
    "at the 5d/20d/60d horizons; plus "
    "the swing-aware probability of a REVERSAL against the bucket side beyond the fixed 1% bar (reverse_threshold column, 0.01 — the n-day forward window's ADVERSE PATH EXTREME vs ±1%: the window swung past the bar against the side at some close, not merely the period-end; path extreme < −thr for top / > +thr for bottom)"
    ". Rows are emitted only where day_count > 0 "
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
    "stats.mov_ave_market_hypes episodes), with streak-merge (since "
    "2026-09, replacing the legacy fixed 5-day cooldown): dates that "
    "keep satisfying the breach condition CONTINUOUSLY collapse into "
    "ONE forecast signal anchored at the streak's MID day (the "
    "((L-1)//2 + 1)-th day, the high_low_streaks convention); the "
    "bucket's mean streak length is recorded on "
    "analysis_forecasts.forecast_identities.streak_signal_days. Result "
    "data in analysis_forecasts.forecast_results via forecast_id: mean "
    "forward fractional changes at the next-day, 5d, 20d and 60d "
    "horizons; close-based max/min forward changes and the swing "
    "ratio (max_low_change_ratio — (1 + the widest path high) / "
    "(1 + the deepest path low across the bucket's n-day forward "
    "windows, signed; ≥ 1, large = a large within-period close "
    "swing)) at the 5d/20d/60d horizons; plus the per-horizon "
    "swing-aware probability of a REVERSAL against the breach side "
    "beyond the fixed 1% bar (reverse_threshold column, 0.01 — the "
    "n-day forward window's ADVERSE PATH EXTREME vs ±1%: the window "
    "swung past the bar against the side at some close, not merely "
    "the period-end; path extreme < −thr for upper / "
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
    "streak-merge (since 2026-09, replacing the legacy fixed 5-day "
    "cooldown): dates that keep satisfying the gap condition "
    "CONTINUOUSLY collapse into ONE forecast signal anchored at the "
    "streak's MID day (the ((L-1)//2 + 1)-th day, the "
    "high_low_streaks convention); the bucket's mean streak length is "
    "recorded on analysis_forecasts.forecast_identities."
    "streak_signal_days. "
    "Buckets are split by PK member is_market_hyped (ANY bucket date "
    "inside one of the code's stats.mov_ave_market_hypes episodes); "
    "result data in analysis_forecasts.forecast_results via "
    "forecast_id: mean forward fractional changes at the next-day, 5d, "
    "20d and 60d horizons; close-based max/min forward changes and "
    "the swing ratio (max_low_change_ratio — (1 + the widest path "
    "high) / (1 + the deepest path low across the bucket's n-day "
    "forward windows, signed; ≥ 1, large = a large within-period "
    "close swing)) at the 5d/20d/60d horizons; plus the per-horizon "
    "swing-aware probability of a REVERSAL against the bucket side "
    "beyond the fixed 1% bar (reverse_threshold column, 0.01 — the "
    "n-day forward window's ADVERSE PATH EXTREME vs ±1%: the window "
    "swung past the bar against the side at some close, not merely "
    "the period-end; path extreme < −thr for top / "
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
    "P(the n-day forward window's path low < −reverse_threshold) "
    "(base_down_prob — the top/upper-side reverse_prob base), "
    "P(the window's path high > +reverse_threshold) (base_up_prob — "
    "the bottom/lower-side reverse_prob base) — the same "
    "swing-aware adverse-path-extreme event the bucket reverse_prob "
    "counts, at the same fixed "
    "reverse_threshold the bucket rows of the (code, stat_month, period) "
    "use (lift stays in one scale) and the valid-day count (base_count). "
    "reverse_threshold the bucket rows of the (code, stat_month, period) "
    "use (lift stays in one scale) and the valid-day count (base_count). "
    "Reading a bucket's ave_change / reverse_prob against these turns them "
    "into lift. "
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
# modules (mov_ave_spread).
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

# ---- Streak-merge signal semantics (2026-09; replaces cooldown) -------
#
# The bucket engines share ONE unified signal pipeline
# (wide.iter_bucket_subsets) with two modes:
#
#   MULTI-DAY STREAK (merge=True — mov_rsi / mov_std / mov_gap /
#   px_vol_state): dates that keep satisfying the bucket condition
#   CONTINUOUSLY are treated as ONE forecast signal — the run collapses
#   to its MID day (the ((L-1)//2 + 1)-th day, the high_low_streaks
#   mean-mid anchor; a 1-day run anchors itself), the forward changes
#   are measured from that mid day, and the bucket's MEAN run length is
#   recorded on
#   analysis_forecasts.forecast_identities.streak_signal_days (the
#   result rows' streak_starts / streak_ends / streak_days carry each
#   merged signal's run span). px_vol_state moved onto this convention
#   in 2026-09 (its state runs used to admit every qualifying day at a
#   constant streak_signal_days = 1).
#
#   ONE-DAY (merge=False — mov_pairs / mov_pairs_ema): every qualifying
#   day is its own signal with a 1-day run. A cross day's predecessor
#   sits on the other side of zero, so consecutive cross days are
#   mutually exclusive and a streak-merge pass would be a no-op — the
#   engines skip it and streak_signal_days is the 1 constant.
#
# margin_ratio / opp_pair (state families without run semantics) and
# high_low_streaks (its own ex-post streak anchors) keep their bespoke
# loops. The mov tables' cooldown_days PK column was dropped with the
# 2026-09 migration; the SIGNALS layer keeps its own detection-side
# COOLDOWN (a live trigger cannot know an ongoing streak's mid ex-post
# — see analyze.analysis_signals.config).

# Forward-change horizons (trading days): next-day, 5d, 20d, 60d.
FORWARD_HORIZONS = (1, 5, 20, 60)

# Horizons with max/min forward-change + max_low_change_ratio columns
# (5d/20d/60d — the next-day horizon has none).
MM_HORIZONS = (5, 20, 60)

# A forward move beyond this (fractional) threshold against the bucket
# side counts as a reversal — measured SWING-AWARE on the n-day
# forward window's ADVERSE PATH EXTREME (path_low_{n}d = the lowest
# close of (t, t+n] vs the signal close for top/upper, path_high_{n}d
# = the highest for bottom/lower): at n = 5 the reversal event is the
# window swinging 1% beyond the signal-day close AT ANY of the 5
# closes, not merely at the period-end (the path extreme dominates the
# endpoint, so the endpoint event is the strictly weaker subset). The
# bar is FIXED (mode "fixed", 2026-09-08): every reverse_prob reads as
# a plain "P > 1%" — P(the period swings ≥ 1% against the side) — one
# scale for every code and horizon.
REVERSE_THRESHOLD = 0.01

# ---- Previous adaptive bar ("std" mode, study 2026-09; DISABLED) ------------
#
# The alternative reverse_threshold(code, month, n) = k_n · σ(code,
# month, n), σ = population std of the n-day forward changes over ALL
# of the code's trailing-window days (recomputed every stat month — the
# window ends at the stat month, so no look-ahead). Its k per horizon
# was selected by temp_scripts/study_reverse_threshold.py (rolling M-1
# P90 gate OOS on index/etf/stock): at t = k·σ the no-edge reversal
# rate is Φ(−k) at EVERY horizon, which de-saturates the 20d/60d
# reverse_probs the fixed 1% bar pins at ~1.0 (its σ-equivalent shrinks
# from ~0.7σ at the next-day horizon to ~0.1σ at 60d) and makes the
# cross-period MAX(reverse_prob) confidence comparable. OOS gated
# dir_ave vs the fixed bar at the gate's P90: 20d +10/+14/+19%,
# 60d +16/+15/+29% (index/etf/stock), short horizons ~neutral. k = 2
# degenerates (dead share > 55%). DISABLED 2026-09-08 in favour of the
# interpretable fixed bar — flip REVERSE_THRESHOLD_MODE back to "std"
# to restore (the constants stay for that purpose).
REVERSE_THRESHOLD_MODE = "fixed"  # "fixed" | "std"
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

# The trailing calendar window recorded on EVERY analysis_forecasts row
# (``lookback_period`` column, TEXT — a recorded build parameter, not a
# PK member): f"{WINDOW_YEARS}y", i.e. each snapshot summarizes the
# window (stat_month - WINDOW_YEARS, stat_month]. '5y' while
# WINDOW_YEARS = 5; changing WINDOW_YEARS re-derives this automatically
# but requires a --force rebuild (rows are not re-keyed by lookback).
LOOKBACK_PERIOD = f"{WINDOW_YEARS}y"

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
# mode / fallback). trigger_dates is the DATE[] of the row's own bucket
# days (ascending, length == occurrence_count — NULL when the count is
# 0/NULL), row-local per period. streak_starts / streak_ends are the
# parallel DATE[] of each merged signal's qualifying-run start / end
# calendar date and streak_days the parallel BIGINT[] of each run's
# trading-day count (element-wise parallel to trigger_dates; the
# streak engines mov_rsi / mov_std / mov_gap / mov_pairs /
# mov_pairs_ema / px_vol — the pairs families' runs are single days,
# NULL for margin_ratio / opp_pair / high_low_streaks), row-local per
# period. trigger_excess is the parallel NUMERIC(10,6)[] of each
# signal's TRIGGER EXCESS — the mid day's trigger value minus the
# bucket's qualifying bar (signed, value − bar: the live_signals
# signal_excess convention; the pairs families' bar is the zero line,
# so there the excess IS the day's spread) — defined for the
# scalar-bar engines mov_rsi / mov_std / mov_gap / mov_pairs /
# mov_pairs_ema only, NULL arrays elsewhere (the state families have
# no scalar qualifying bar), row-local per period.
RESULT_COLUMNS: list[str] = [
    "forecast_id",
    "period",
    "lookback_period",
    "config",
    "ave_change",
    "std_change",
    "max_change",
    "min_change",
    "occurrence_count",
    "trigger_dates",
    "streak_starts",
    "streak_ends",
    "streak_days",
    "trigger_excess",
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
    "lookback_period",
    "base_count",
    "base_ave_change",
    "base_down_prob",
    "base_up_prob",
    "reverse_threshold",
]

# ---- forecast_identities (shared-PK identity registry) -----------------------
#
# One row per forecast bucket: the identity columns (sec_type, code,
# stat_month) keyed by the surrogate forecast_id, tagged with the
# bucket family (the motivation table name) and the bucket's mean
# streak length (streak_signal_days — the merged-signal semantics of
# the 2026-09 streak migration; 1 for the state families). Since the
# 2026-09 forecast_id-keyed rebuild this is the ONLY table storing the
# identity (the motivation tables carry forecast_id + their
# family-unique bucket keys alone), written by _write_month in the
# SAME transaction as the motivation + result rows; searched by
# forecast_id via fetch.fetch_forecast_identity (python -m
# analyze.analysis_forecasts --search-forecast-id) and by identity via
# the (sec_type, code, bucket, stat_month) / (sec_type, bucket,
# stat_month) indexes (the UI/API identity endpoint, the signals
# layer). base_rates is NOT registered (no forecast_id).
IDENTITY_COLUMNS: list[str] = [
    "forecast_id",
    "sec_type",
    "code",
    "stat_month",
    "bucket",
    "streak_signal_days",
    "lookback_period",
]

# ---- Primary keys / write column sets --------------------------------------
#
# 2026-09 code-clustered, forecast_id-keyed shape: every motivation
# table carries PK (code, forecast_id) on HASH (code) partitions — the
# code-clustered read/write axis (a per-security read prunes to ONE
# partition and walks the code-leading PK; forecast_id-only
# joins/searches use the secondary idx_<table>_forecast_id). The other
# identity columns (sec_type, stat_month — opp_pair: industry_id plays
# code) are NOT stored on the motivation tables — they live ONLY in
# forecast_identities (one registry row per forecast_id, written in the
# same transaction), which is also the search-by-identity table. The
# family-unique bucket-key columns below are plain NOT NULL columns —
# functionally dependent on forecast_id (the writer allocates one id
# per bucket), kept out of the PK.
#
# cooldown_days was a bucket-key member before the 2026-09
# streak-merge migration — dropped.

# Bucket-key columns per family (refine the identities row's bucket;
# is_market_hyped splits the buckets by hype overlap). The state /
# pair families' bucket keys are declared in their own sections below.
BUCKET_KEY_COLUMNS_MOV_RSI = ["rsi_window", "side", "pct", "is_market_hyped"]
BUCKET_KEY_COLUMNS_MOV_STD = ["ma_window", "k", "side", "is_market_hyped"]
BUCKET_KEY_COLUMNS_MOV_GAP = ["gap_window", "side", "pct", "is_market_hyped"]
BUCKET_KEY_COLUMNS_MOV_PAIRS = ["pair_window", "side", "is_market_hyped"]
BUCKET_KEY_COLUMNS_HIGH_LOW_STREAKS = [
    "band_period", "pct_type", "side", "is_market_hyped",
]

# mov_rsi / mov_std / mov_gap columns in write order (forecast_id +
# code + bucket keys + lookback). The remaining identity columns are
# intentionally absent — the underlying indicator values are also NOT
# stored: rsi_{W}days and gap_{W}days live in analysis.mov_ave_rsi,
# ma/std in analysis.mov_ave_spreads_detail + stats.*_tech_stats
# (joinable via the identities registry + the bucket keys); only the
# market-hype overlap is materialized here.
MOV_RSI_COLUMNS = (
    ["forecast_id", "code"] + BUCKET_KEY_COLUMNS_MOV_RSI + ["lookback_period"]
)
MOV_STD_COLUMNS = (
    ["forecast_id", "code"] + BUCKET_KEY_COLUMNS_MOV_STD + ["lookback_period"]
)
MOV_GAP_COLUMNS = (
    ["forecast_id", "code"] + BUCKET_KEY_COLUMNS_MOV_GAP + ["lookback_period"]
)
MOV_PAIRS_COLUMNS = (
    ["forecast_id", "code"] + BUCKET_KEY_COLUMNS_MOV_PAIRS
    + ["lookback_period"]
)
MOV_PAIRS_EMA_COLUMNS = MOV_PAIRS_COLUMNS
HIGH_LOW_STREAKS_COLUMNS = (
    ["forecast_id", "code"] + BUCKET_KEY_COLUMNS_HIGH_LOW_STREAKS
    + ["lookback_period"]
)

# ---- px_vol_state: recent price-change × trading-amount state buckets -------
#
# Fourth bucket family (see database/sql/analysis/analysis_forecasts/
# 05_px_vol_state.sql + the 2026-09 temp_scripts studies
# study_ma_spread_index_9grid*.py / study_ma_spread_index_sharp_slow.py /
# study_px_vol_state_forecast.py): a day joins a bucket when BOTH its
# σ-standardized price speed AND its z-scored log trading-amount LEVEL
# fall in the named states. Every rolling stat is shifted 1 row (no
# look-ahead) and uses
# the code's OWN trailing moments, so the bars adapt per index
# (σ_ret spans ~1%–2.9% daily across equity-like indices; fixed ±2% /
# 量比 1.5/0.8 bars systematically misfire on high/low-σ codes).
#
# Calibration: k/z bars are chosen to reproduce the legacy fixed
# thresholds' POOLED trigger rates on equity-like indices (up 8.4% /
# down 7.6% / 放量 4.0% / 缩量 14.2%), so bucket sample shares stay
# comparable with the studies. The z bars were calibrated on the
# RETIRED ratio5d metric and carry over to log_level as the starting
# calibration (re-measure the pooled rates after a rebuild).
TABLE_PX_VOL = "analysis_forecasts.px_vol_state"
ANALYSIS_NAME_PX_VOL = "px_vol"

# Rolling windows (rows): σ_ret of ret_1d + the log-amount level
# moments; PX_VOL_LB_WINDOW is the classic 量比 base window
# (trading_amount mean of t-lb_window..t-1) — evidence-only since the
# log_level refactor. All shifted 1 row before use.
PX_VOL_SIGMA_WINDOW = 255
PX_VOL_SIGMA_MIN_DAYS = 60
PX_VOL_LB_WINDOW = 5

# The vol leg's metric (recorded on every analysis.mov_ave_price_vs_amt
# row as amt_metric and audited by fetch.assert_price_vs_amt_params):
#   "log_level" — px_z = z-scored log(trading_amount) vs the code's own
#                 trailing PX_VOL_SIGMA_WINDOW-row moments (shift 1) —
#                 the LEVEL statement heavy/shrink claim. The retired
#                 "ratio5d" (z-scored 量比 vs its own moments) fired
#                 heavy on drought bounces: in a declining-volume
#                 regime the 5-day base collapses, so a day whose
#                 amount sat far below the code's level scored
#                 ratio ≈ 1.7 → z > 2 ("Amt Up" on visibly low amt).
PX_VOL_AMT_METRIC = "log_level"

# t = ret_1d / σ_ret state bars.
PX_VOL_K_SLOW_UP = 1.26
PX_VOL_K_SLOW_DN = 1.29
PX_VOL_K_SHARP = 2.0

# Amount-level z state bars (px_z).
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

# Speed / state name → ordinal (the (T, C) state matrices' int encoding,
# see wide.build_px_vol_state_matrices — the scatter of the
# analysis.mov_ave_price_vs_amt registry rows). The -1 sentinel ("no
# valid state that day") is assigned at matrix-build time.
PX_VOL_SPEED_ORD: dict[str, int] = {
    s: i for i, s in enumerate(PX_VOL_SPEEDS)
}
PX_VOL_VOL_ORD: dict[str, int] = {
    v: i for i, v in enumerate(PX_VOL_VOL_STATES)
}

# px_vol_state columns in write order (forecast_id + code + bucket keys
# + side + recorded build parameters + lookback).
BUCKET_KEY_COLUMNS_PX_VOL = ["px_speed", "vol_state", "is_market_hyped"]
PX_VOL_COLUMNS = ["forecast_id", "code"] + BUCKET_KEY_COLUMNS_PX_VOL + [
    "side",
    "sigma_window", "lb_window",
    "k_slow_up", "k_slow_dn", "k_sharp", "z_heavy", "z_shrink",
    "sigma_floor",
    "lookback_period",
]

DESCRIPTION_PX_VOL = (
    "Recent-day price-change × trading-amount state monthly forecasts "
    "(ETF + Index + Stock). For each security and completed month-end "
    "(stat_month), over the trailing 5-year window (stat_month - 5y, "
    "stat_month] of the code's own trading days, buckets the days "
    "whose σ-standardized 1-day price change t = ret_1d / σ_ret "
    "(σ_ret = the code's rolling-255-row, min-60 std of ret_1d, "
    "shifted 1 row) and z-scored log trading-amount LEVEL (z vs the "
    "code's rolling-255 moments of log(trading_amount), shifted 1 "
    "row — heavy/shrink are LEVEL statements vs the code's own "
    "trailing-year amount distribution) BOTH fall in the named "
    "states — px_speed "
    "sharp_up (t>2.0) / slow_up (1.26<t<=2.0) / flat (-1.29<=t<=1.26) "
    "/ slow_dn (-2.0<=t<-1.29) / sharp_dn (t<-2.0) × vol_state heavy "
    "(z>2.0) / normal / shrink (z<-0.92). Per-code adaptive bars "
    "(the k bars calibrated to the legacy ±2% pooled trigger rate; "
    "the z bars carried over from the retired ratio5d metric) are "
    "recorded on every row; days with σ_ret below the "
    "0.005 floor (bond-like indices) or NULL trading_amount never "
    "join a bucket. State runs are STREAK-MERGED like the mov_* event "
    "families (since 2026-09): consecutive days holding the same "
    "(speed, vol) state collapse into ONE forecast signal anchored at "
    "the run's MID day, the bucket's mean streak length recorded on "
    "forecast_identities.streak_signal_days; buckets are split "
    "by is_market_hyped and carry side top/bottom/flat (flat rows "
    "get NULL reverse_prob). Result data in "
    "analysis_forecasts.forecast_results via forecast_id: mean/std/"
    "max/min forward changes at next/5d/20d/60d, occurrence counts, "
    "max_low_change_ratio — (1 + the widest path high) / (1 + the "
    "deepest path low across the bucket's n-day forward windows, "
    "signed; ≥ 1, large = a large within-period close swing) and "
    "the swing-aware reverse_prob at the fixed "
    "1% bar (the n-day forward window's ADVERSE PATH EXTREME vs "
    "±1% — the window swung past the bar against the side at some "
    "close, not merely the period-end). "
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
# the crowding states (side 'top' — reverse_prob = P(the forward
# window's path low < -thr), the study's bearish reading); vlow/low/
# no_buy are the mild-bullish states (side 'bottom'); mid carries
# side='flat' and NULL reverse_prob (no directional claim — the central
# bulk has none).
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

# PK + write columns (forecast_id + code + bucket keys + side +
# recorded build parameters + lookback).
BUCKET_KEY_COLUMNS_MARGIN_RATIO = ["ratio_state", "is_market_hyped"]
MARGIN_RATIO_COLUMNS = (
    ["forecast_id", "code"] + BUCKET_KEY_COLUMNS_MARGIN_RATIO + [
        "side",
        "z_window", "z_min_periods",
        "vlow_bar", "low_bar", "high_bar", "vhigh_bar",
        "lookback_period",
    ]
)

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
    "max_low_change_ratio — (1 + the widest path high) / (1 + the "
    "deepest path low across the bucket's n-day forward windows, "
    "signed; ≥ 1, large = a large within-period close swing) and "
    "the swing-aware reverse_prob at the fixed "
    "1% bar (the n-day forward window's ADVERSE PATH EXTREME vs "
    "±1% — the window swung past the bar against the side at some "
    "close, not merely the period-end). "
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

# PK + write columns (forecast_id + industry_id + bucket keys + side +
# recorded build parameters). No is_market_hyped split — industries
# have no hype source. industry_id (the dropping side) is the
# partition key + PK lead, and plays the identities row's code; the
# remaining identity columns (sec_type = constant 'index', stat_month)
# live ONLY in forecast_identities. pair_industry_id (the forecast
# target) is a bucket metric.
BUCKET_KEY_COLUMNS_OPP_PAIR = ["pair_industry_id", "trend_window", "side"]
OPP_PAIR_COLUMNS = (
    ["forecast_id", "industry_id"] + BUCKET_KEY_COLUMNS_OPP_PAIR + [
        "benchmark_code", "pool_size",
        "lookback_period",
    ]
)

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
    "CONFIRMATION probability (B rises when A drops), at B's fixed "
    "reverse_threshold (the period-end n-day offset change vs ±1%). "
    "Result data in analysis_forecasts.forecast_results via "
    "forecast_id. Incremental at month granularity (missing "
    "stat_months + refresh of the last REFRESH_MONTHS); --force "
    "deletes the opp_pair rows + linked forecast_results and "
    "recomputes."
)

# ---- mov_pairs: MA-pair cross (golden / death cross) event buckets ----------
#
# Seventh bucket family (see database/sql/analysis/analysis_forecasts/
# 08_mov_pairs.sql): CROSS-EVENT buckets built on the EXISTING
# relative-MA-spread columns of analysis.mov_ave_spreads_detail —
# ma5_vs_ma{W} = (ma5 - ma_{W}) / ma_{W}, the parent mov_ave_spread
# analysis's own spread definition (no new MA computation). A day joins
# a bucket when the stored spread changes sign that day: side 'top' a
# CROSS UP / golden cross (spread[t] > 0 and spread[t-1] <= 0 — ma5
# rises through the slow MA), side 'bottom' a CROSS DOWN / death cross
# (spread[t] < 0 and spread[t-1] >= 0). NULL spreads never trigger
# (either MA still warming up); triggers are streak-merged (dates that
# keep crossing CONTINUOUSLY collapse to their MID day — the 2026-09
# streak migration that replaced the fixed 5-day cooldown) and
# hype-split exactly like the mov_rsi / mov_std / mov_gap event
# families.
TABLE_MOV_PAIRS = "analysis_forecasts.mov_pairs"
ANALYSIS_NAME_MOV_PAIRS = "mov_pairs"

# Slow MA leg of the pair (trading days) — selects the
# analysis.mov_ave_spreads_detail.ma5_vs_ma{W} column. The fast leg is
# fixed at ma5 (the spread columns' definition). ma5_vs_ma20 exists in
# the source too but is not built (5-vs-20 flips too often to be a
# regime cross; widen the tuple to add it).
MOV_PAIRS_WINDOWS = (60, 120, 255)

# Cross sides: top = cross up (golden cross, the pair turns bullish),
# bottom = cross down (death cross, the pair turns bearish) — the same
# side semantics the gate and the other mov_* families use.
MOV_PAIRS_SIDES = ("top", "bottom")

MOV_PAIRS_COLUMNS = (
    ["forecast_id", "code"] + BUCKET_KEY_COLUMNS_MOV_PAIRS + ["lookback_period"]
)

DESCRIPTION_MOV_PAIRS = (
    "MA-pair cross (golden / death cross) monthly forecasts (ETF + "
    "Index + Stock). Built on the EXISTING relative-MA-spread columns "
    "of analysis.mov_ave_spreads_detail — ma5_vs_ma{W} = (ma5 - "
    "ma_{W}) / ma_{W}, the parent mov_ave_spread analysis's own spread "
    "definition, W in 60/120/255 (fast leg fixed ma5; no new MA "
    "computation). For each security and completed month-end "
    "(stat_month), over the trailing 5-year window (stat_month - 5y, "
    "stat_month] of the code's own trading days, buckets the CROSS "
    "days — the days the stored spread changes sign: side=top a CROSS "
    "UP / golden cross (spread[t] > 0 and spread[t-1] <= 0, ma5 rises "
    "through the slow MA), side=bottom a CROSS DOWN / death cross "
    "(spread[t] < 0 and spread[t-1] >= 0); NULL spreads (either MA "
    "still warming up) never trigger. ONE-DAY event signals: a cross "
    "day's predecessor sits on the other side of zero, so consecutive "
    "cross days are mutually exclusive — every cross day is its own "
    "forecast signal (streak_signal_days = 1; the legacy fixed 5-day "
    "cooldown was removed 2026-09) and the is_market_hyped PK split "
    "applies. "
    "Result data in analysis_forecasts.forecast_results via "
    "forecast_id: mean forward fractional changes at the next-day, "
    "5d, 20d and 60d horizons; close-based max/min forward changes "
    "and the swing ratio (max_low_change_ratio — (1 + the widest "
    "path high) / (1 + the deepest path low across the bucket's "
    "n-day forward windows, signed; ≥ 1, large = a large "
    "within-period close swing)) at the 5d/20d/60d horizons; plus "
    "the per-horizon swing-aware probability of a REVERSAL against "
    "the cross side beyond the fixed 1% bar (reverse_threshold "
    "column, 0.01 — the n-day forward window's ADVERSE PATH "
    "EXTREME vs ±1%: path extreme < -thr for "
    "top / > +thr for bottom). Rows are emitted only where day_count "
    "> 0 and only for codes whose own history spans the FULL window. "
    "Incremental at month granularity: stat_months missing from "
    "mov_pairs are computed and the most recent REFRESH_MONTHS "
    "stat_months are refreshed each run; --force deletes the "
    "sec_type's rows (mov_pairs + linked forecast_results) and "
    "recomputes every target month."
)

# ---- high_low_streaks: MA-Spread High/Low streak mean-mid anchor buckets ----
#
# Ninth bucket family (see database/sql/analysis/analysis_forecasts/
# 10_high_low_streaks.sql + the 2026-09 study
# temp_scripts/study_high_low_streaks_forecast.py): every band-break
# EXCURSION STREAK of analysis.mov_ave_high_low_pct_streaks (the
# mov_ave_spread analysis's own streak table — no streak recomputation;
# run python -m analyze.mov_ave_spread first) is audited at its MEAN-MID
# anchor day. A streak with day_count = n contributes its
# ((n-1)//2 + 1)-th trading day of the span as the ONE trigger day —
# the floor of the MEAN elapsed day (n-1)/2 ("mean mid elapsed day once
# entered a streak"; an 8-day low streak anchors its 4th day). The
# anchor is EX-POST: the streak length is known only after the streak
# closes, so the buckets audit streak-period behaviour (the study shows
# a strong mean-reversion reading: below-band streaks drift UP from the
# mid anchor, above-band streaks drift DOWN — e.g. stock period=255
# pct=1: bottom +5.6% mean fwd5d, top -8.0%), they are NOT a live
# trigger.
#
# side (the streaks table has no side column): the END date is
# out-of-band by construction, so the side is read off the UNROUNDED
# close on end_date vs the end date's OWN month band in
# analysis.mov_ave_high_low_pct — top when close > high_val, bottom
# when close < low_val (the exact test the streaks step used; a tie
# falls to the NEARER band — comparing the stored 2dp-rounded close
# instead is ambiguous for ~36% of ETF rows, whose adjusted prices are
# small enough that 2dp rounding creates ties).
TABLE_HIGH_LOW_STREAKS = "analysis_forecasts.high_low_streaks"
ANALYSIS_NAME_HIGH_LOW_STREAKS = "high_low_streaks"

# Band axes mirroring the source table (mov_ave_spread config's
# HIGH_LOW_PCT_PERIODS / HIGH_LOW_PCT_TYPES — re-declared locally to
# keep the import direction one-way, the RSI_WINDOWS precedent).
HIGH_LOW_STREAKS_PERIODS = (255, 500, 750, 1275)
HIGH_LOW_STREAKS_TYPES = (1, 5, 10)

# Streak sides: top = above-band excursion (close[end_date] > the end
# month band's high_val), bottom = below-band (< low_val) — the same
# side semantics the gate and the other families use.
HIGH_LOW_STREAKS_SIDES = ("top", "bottom")

# Columns in write order (forecast_id + bucket keys + lookback).
# band_period (NOT "period" — RESERVED by the shared result-row
# pipeline: build_result_rows stamps forecast_results' period
# 'next'/'5d'/'20d'/'60d' onto every row dict, overwriting any
# same-named motivation key). No cooldown_days member — each streak
# contributes exactly ONE trigger and streaks are inherently separated
# (a streak ends only after a 6+-day in-band gap or a side switch), so
# trigger suppression is redundant (state-family shape, like px_vol /
# margin_ratio). No hype-derived parameter columns — the config JSONB
# carries the bucket's streak-length context.
HIGH_LOW_STREAKS_COLUMNS = (
    ["forecast_id", "code"] + BUCKET_KEY_COLUMNS_HIGH_LOW_STREAKS
    + ["lookback_period"]
)

DESCRIPTION_HIGH_LOW_STREAKS = (
    "MA-Spread High/Low streak mean-mid anchor monthly forecasts (ETF + "
    "Index + Stock). Built on the EXISTING band-break excursion streaks "
    "of analysis.mov_ave_high_low_pct_streaks (the mov_ave_spread "
    "analysis's own streak table — no streak recomputation; run "
    "python -m analyze.mov_ave_spread first so the source is current). "
    "For each security and completed month-end (stat_month), over the "
    "trailing 5-year window (stat_month - 5y, stat_month] of the code's "
    "own trading days: every streak whose MEAN-MID anchor day — the "
    "((day_count-1)//2 + 1)-th trading day of the span, the floor of "
    "the mean elapsed day (an 8-day streak anchors its 4th day) — "
    "falls in the window contributes that ONE day as the bucket's "
    "trigger (side top an ABOVE-band excursion: the unrounded close on "
    "end_date above the end month's high_val band; side bottom a "
    "BELOW-band excursion: below low_val — the streaks step's own "
    "test, ties to the nearer band). The anchor is EX-POST (the streak "
    "length is known only after the streak closes — an audit of "
    "streak-period behaviour, not a live trigger). No cooldown (one "
    "trigger per streak; streaks are inherently separated by a 6+-day "
    "in-band gap or a side switch). Buckets are split by is_market_hyped "
    "(ANY ANCHOR date inside one of the code's "
    "stats.mov_ave_market_hypes episodes). Result data in "
    "analysis_forecasts.forecast_results via forecast_id: mean/std/max/"
    "min forward changes from the ANCHOR day's close at the next/5d/20d/"
    "60d horizons, occurrence counts, trigger_dates, "
    "max_low_change_ratio — (1 + the widest path high) / (1 + the "
    "deepest path low across the bucket's n-day forward windows, "
    "signed; ≥ 1, large = a large within-period close swing) and "
    "the swing-aware reverse_prob at the fixed 1% bar (the window's "
    "ADVERSE PATH EXTREME vs the anchor close; the path low < -thr "
    "for top / path high > +thr for bottom — mean-reversion "
    "reading). The config "
    "JSONB records the bucket's streak-length context (mean/min/max "
    "day_count). Rows are emitted only where occurrence_count > 0 and "
    "only for codes whose own history spans the FULL window. "
    "Incremental at month granularity: stat_months missing from "
    "high_low_streaks are computed and the most recent REFRESH_MONTHS "
    "stat_months are refreshed each run; --force deletes the sec_type's "
    "rows (high_low_streaks + linked forecast_results) and recomputes "
    "every target month."
)

# ---- mov_pairs_ema: EMA-pair cross buckets (mov_pairs' EMA sibling) ---------
#
# The identical cross-event machinery on the EXISTING relative-EMA-spread
# columns of analysis.mov_ave_spreads_detail_ema — ema6_vs_ema{W} =
# (ema6 - ema_{W}) / ema_{W} (fast leg fixed ema6; the source table has no
# ema5). Same table shape, PK, sides and one-day signal semantics as
# mov_pairs; its own
# table + identity so the two families stay separately refreshable.
TABLE_MOV_PAIRS_EMA = "analysis_forecasts.mov_pairs_ema"
ANALYSIS_NAME_MOV_PAIRS_EMA = "mov_pairs_ema"

# Slow EMA leg of the pair (trading days) — selects the
# analysis.mov_ave_spreads_detail_ema.ema6_vs_ema{W} column.
# ema6_vs_ema20 exists too but is not built (same rationale as the
# ma5-vs-ma20 pair).
MOV_PAIRS_EMA_WINDOWS = (60, 120, 255)

# Same cross sides as the MA family.
MOV_PAIRS_EMA_SIDES = MOV_PAIRS_SIDES

# Identical write shape as the MA family (the row dicts are source
# agnostic — only the fetched spread columns differ).
MOV_PAIRS_EMA_COLUMNS = MOV_PAIRS_COLUMNS

DESCRIPTION_MOV_PAIRS_EMA = (
    "EMA-pair cross (golden / death cross) monthly forecasts (ETF + "
    "Index + Stock) — the EMA sibling of the mov_pairs family. Built "
    "on the EXISTING relative-EMA-spread columns of "
    "analysis.mov_ave_spreads_detail_ema — ema6_vs_ema{W} = (ema6 - "
    "ema_{W}) / ema_{W}, the parent mov_ave_spread analysis's own EMA "
    "spread definition, W in 60/120/255 (fast leg fixed ema6; no new "
    "EMA computation). For each security and completed month-end "
    "(stat_month), over the trailing 5-year window (stat_month - 5y, "
    "stat_month] of the code's own trading days, buckets the CROSS "
    "days — the days the stored spread changes sign: side=top a CROSS "
    "UP / golden cross (spread[t] > 0 and spread[t-1] <= 0, ema6 rises "
    "through the slow EMA), side=bottom a CROSS DOWN / death cross "
    "(spread[t] < 0 and spread[t-1] >= 0); NULL spreads (either EMA "
    "still warming up) never trigger. ONE-DAY event signals exactly "
    "like mov_pairs: a cross day's predecessor sits on the other side "
    "of zero, so consecutive cross days are mutually exclusive — every "
    "cross day is its own forecast signal (streak_signal_days = 1; "
    "the legacy fixed 5-day cooldown was removed 2026-09) and the "
    "is_market_hyped PK split applies. "
    "Result data in analysis_forecasts.forecast_results via "
    "forecast_id: mean forward fractional changes at the next-day, "
    "5d, 20d and 60d horizons; close-based max/min forward changes "
    "and the swing ratio (max_low_change_ratio — (1 + the widest "
    "path high) / (1 + the deepest path low across the bucket's "
    "n-day forward windows, signed; ≥ 1, large = a large "
    "within-period close swing)) at the 5d/20d/60d horizons; plus "
    "the per-horizon swing-aware probability of a REVERSAL against "
    "the cross side beyond the fixed 1% bar (reverse_threshold "
    "column, 0.01 — the n-day forward window's ADVERSE PATH "
    "EXTREME vs ±1%: path extreme < -thr for "
    "top / > +thr for bottom). Rows are emitted only where day_count "
    "> 0 and only for codes whose own history spans the FULL window. "
    "Incremental at month granularity: stat_months missing from "
    "mov_pairs_ema are computed and the most recent REFRESH_MONTHS "
    "stat_months are refreshed each run; --force deletes the "
    "sec_type's rows (mov_pairs_ema + linked forecast_results) and "
    "recomputes every target month."
)

# ---- pe_state / dividend_state: valuation state bucket families ------------
#
# Tenth and eleventh bucket families (see database/sql/analysis/
# analysis_forecasts/11_pe_state.sql + 12_dividend_state.sql): z-STATE
# buckets over the two valuation series of analysis.pe /
# analysis.dividends, ONE FAMILY PER METRIC (the 2026-09 split of the
# former combined pe_dividend family — each table carries one series and
# no metric column): pe_state over the raw PE (lower the better), and
# dividend_state over the trailing-12m dividend yield (higher the
# better). Each is standardized by the code's OWN trailing moments (the
# margin_ratio convention: rolling z bars, shifted 1 row — no
# look-ahead). The families' defining semantics is the OPPOSITE side
# mapping: PE is LOWER-the-better (a high-PE day is an expensive /
# stretched valuation → its extreme states are BEARISH, side 'top'),
# while the yield is HIGHER-the-better (a high-yield day is a cheap,
# well-supported valuation → its extreme states are BULLISH, side
# 'bottom') — the side is materialized on every row so the gate / UI
# read it unchanged.
TABLE_PE = "analysis_forecasts.pe_state"
ANALYSIS_NAME_PE = "pe"

TABLE_DIVIDEND = "analysis_forecasts.dividend_state"
ANALYSIS_NAME_DIVIDEND = "dividend"

# z states in ascending order (the margin_ratio state ladder, no no_buy
# analog — a NULL series day simply has no bucket).
VAL_STATES: tuple[str, ...] = ("vlow", "low", "mid", "high", "vhigh")

# Rolling moments of the series (rows ≈ 5y of trading days; shifted 1
# row — the margin_ratio Z_WINDOW/MIN_PERIODS convention: a slow
# valuation series needs ~1y of non-NULL observations before its z is
# trusted). One calibration shared by both families (the former
# pe_dividend family's bars); recorded on every row of either table.
VAL_Z_WINDOW = 1220
VAL_Z_MIN_PERIODS = 250

# z state bars (recorded on every row).
VAL_VLOW_BAR = -2.0
VAL_LOW_BAR = -1.0
VAL_HIGH_BAR = 1.0
VAL_VHIGH_BAR = 2.0

# state → side — pe LOWER-the-better (high z = expensive = bearish
# 'top', low z = cheap = bullish 'bottom'); mid = 'flat' (no
# directional claim, reverse_prob NULL).
PE_STATE_SIDE: dict[str, str] = {
    "vlow": "bottom", "low": "bottom",
    "mid": "flat",
    "high": "top", "vhigh": "top",
}

# state → side — the dividend family REVERSES the pe mapping (yield
# HIGHER-the-better: high z = cheap / well-supported = bullish
# 'bottom'); mid = 'flat'.
DIVIDEND_STATE_SIDE: dict[str, str] = {
    "vlow": "top", "low": "top",
    "mid": "flat",
    "high": "bottom", "vhigh": "bottom",
}

# PK + write columns (forecast_id + code + bucket keys + side + recorded
# build parameters + lookback) — identical for both families.
BUCKET_KEY_COLUMNS_PE = ["val_state", "is_market_hyped"]
PE_COLUMNS = (
    ["forecast_id", "code"] + BUCKET_KEY_COLUMNS_PE + [
        "side",
        "z_window", "z_min_periods",
        "vlow_bar", "low_bar", "high_bar", "vhigh_bar",
        "lookback_period",
    ]
)
BUCKET_KEY_COLUMNS_DIVIDEND = BUCKET_KEY_COLUMNS_PE
DIVIDEND_COLUMNS = PE_COLUMNS

DESCRIPTION_PE = (
    "Valuation state monthly forecasts over the PE series of "
    "analysis.pe (ETF + Index + Stock — raw PE: index PE from "
    "stats.index_valuation.pe, etf/stock PE pre-computed by builds; "
    "NULL on no-earnings / invalid-PE days → no bucket there). For "
    "each security and completed month-end (stat_month), over the "
    "trailing 5-year window (stat_month - 5y, stat_month] of the "
    "code's own trading days, buckets the days by the pe's z state vs "
    "the code's OWN trailing distribution: z = (pe - μ)/σ of the "
    "rolling-1220-row (min 250 non-NULL) moments shifted 1 row — vlow "
    "(z <= -2) / low (-2,-1] / mid (-1,+1] / high (+1,+2] / vhigh "
    "(z > +2); undefined z → no bucket. State cells are STREAK-MERGED "
    "(the 2026-09 unified pipeline, px_vol convention): consecutive "
    "days in the same state collapse into ONE forecast signal anchored "
    "at the run's MID day, the bucket's mean streak length recorded on "
    "forecast_identities.streak_signal_days; buckets are split by "
    "is_market_hyped. SIDE — pe is LOWER-the-better: high/vhigh "
    "(expensive) carry side top (bearish) and vlow/low (cheap) side "
    "bottom; mid = flat (NULL reverse_prob). Result data in "
    "analysis_forecasts.forecast_results via forecast_id: mean/std/"
    "max/min forward changes at next/5d/20d/60d, occurrence counts, "
    "max_low_change_ratio — (1 + the widest path high) / (1 + the "
    "deepest path low across the bucket's n-day forward windows, "
    "signed; ≥ 1, large = a large within-period close swing) and "
    "the swing-aware reverse_prob at the fixed "
    "1% bar (the n-day forward window's ADVERSE PATH EXTREME vs "
    "±1% — the window swung past the bar against the side at some "
    "close, not merely the period-end). The config JSONB records the "
    "bucket's mean pe level and mean z. "
    "Incremental at month granularity (missing stat_months + refresh "
    "of the last REFRESH_MONTHS); --force deletes the sec_type's rows "
    "(pe_state + linked forecast_results) and recomputes."
)

DESCRIPTION_DIVIDEND = (
    "Valuation state monthly forecasts over the dividend-yield series "
    "of analysis.dividends (ETF + Index + Stock — trailing-12m D/P, "
    "fractional; NULL for non-payers → only paying codes form dividend "
    "buckets). For each security and completed month-end (stat_month), "
    "over the trailing 5-year window (stat_month - 5y, stat_month] of "
    "the code's own trading days, buckets the days by the yield's z "
    "state vs the code's OWN trailing distribution: z = "
    "(dividend_yield - μ)/σ of the rolling-1220-row (min 250 non-NULL) "
    "moments shifted 1 row — vlow (z <= -2) / low (-2,-1] / mid "
    "(-1,+1] / high (+1,+2] / vhigh (z > +2); undefined z → no bucket. "
    "State cells are STREAK-MERGED (the 2026-09 unified pipeline, "
    "px_vol convention): consecutive days in the same state collapse "
    "into ONE forecast signal anchored at the run's MID day, the "
    "bucket's mean streak length recorded on "
    "forecast_identities.streak_signal_days; buckets are split by "
    "is_market_hyped. SIDE — the yield is HIGHER-the-better, the "
    "REVERSE of the pe family's mapping: high/vhigh (cheap, "
    "well-supported) carry side bottom (bullish) and vlow/low side "
    "top (bearish); mid = flat (NULL reverse_prob). Result data in "
    "analysis_forecasts.forecast_results via forecast_id: mean/std/"
    "max/min forward changes at next/5d/20d/60d, occurrence counts, "
    "max_low_change_ratio — (1 + the widest path high) / (1 + the "
    "deepest path low across the bucket's n-day forward windows, "
    "signed; ≥ 1, large = a large within-period close swing) and "
    "the swing-aware reverse_prob at the fixed "
    "1% bar (the n-day forward window's ADVERSE PATH EXTREME vs "
    "±1% — the window swung past the bar against the side at some "
    "close, not merely the period-end). The config JSONB records the "
    "bucket's mean yield level and mean z. "
    "Incremental at month granularity (missing stat_months + refresh "
    "of the last REFRESH_MONTHS); --force deletes the sec_type's rows "
    "(dividend_state + linked forecast_results) and recomputes."
)

# Motivation table short name (identity.bucket value) → schema-qualified
# table. Declared at the END of the module: it references every family's
# TABLE_* constant above. bucket == the table name minus the schema
# prefix; opp_pair_state is the ONLY family whose subject column is not
# "code" (it stores the dropping industry_id as identity.code — the
# forecast-target pair_industry_id stays on the motivation row).
IDENTITY_BUCKET_TABLES: dict[str, str] = {
    "mov_rsi":            TABLE_MOV_RSI,
    "mov_std":            TABLE_MOV_STD,
    "mov_gap":            TABLE_MOV_GAP,
    "mov_pairs":          TABLE_MOV_PAIRS,
    "mov_pairs_ema":      TABLE_MOV_PAIRS_EMA,
    "px_vol_state":       TABLE_PX_VOL,
    "margin_ratio_state": TABLE_MARGIN_RATIO,
    "opp_pair_state":     TABLE_OPP_PAIR,
    "high_low_streaks":   TABLE_HIGH_LOW_STREAKS,
    "pe_state":           TABLE_PE,
    "dividend_state":     TABLE_DIVIDEND,
}
