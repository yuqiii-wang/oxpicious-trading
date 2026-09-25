"""Target tables, write-column sets and the identity registry (config)."""
from __future__ import annotations

# ---- Target tables ---------------------------------------------------------

TABLE_FORECAST = "analysis_forecasts.forecast_results"
TABLE_IDENTITIES = "analysis_forecasts.forecast_identities"
TABLE_MOV_RSI = "analysis_forecasts.mov_rsi"
TABLE_MOV_STD = "analysis_forecasts.mov_std"
TABLE_BASE_RATE = "analysis_forecasts.base_rates"

ANALYSIS_NAME_RSI = "mov_rsi"
ANALYSIS_NAME_STD = "mov_std"
ANALYSIS_NAME_BASE_RATE = "base_rates"

# ---- Primary keys / write column sets --------------------------------------
#
# 2026-09 code-clustered, forecast_id-keyed shape: every motivation
# table carries PK (code, forecast_id) on HASH (code) partitions — the
# code-clustered read/write axis (a per-security read prunes to ONE
# partition and walks the code-leading PK; forecast_id-only
# joins/searches use the secondary idx_<table>_forecast_id). The other
# identity columns (sec_type, stat_date) are NOT stored on the
# motivation tables — they live ONLY in
# forecast_identities (one registry row per forecast_id, written in the
# same transaction), which is also the search-by-identity table. The
# family-unique bucket-key columns below are plain NOT NULL columns —
# functionally dependent on forecast_id (the writer allocates one id
# per bucket), kept out of the PK.
#
# cooldown_days was a bucket-key member before the 2026-09
# streak-merge migration — dropped.

# Bucket-key columns per family (refine the identities row's bucket;
# regime_state splits the buckets by the market regime of their
# trigger days — stats.market_regimes' 4-state label). The state /
# pair families' bucket keys are declared in their own sections below.
BUCKET_KEY_COLUMNS_MOV_RSI = ["rsi_window", "side", "pct", "regime_state"]
BUCKET_KEY_COLUMNS_MOV_STD = ["ma_window", "k", "side", "regime_state"]
BUCKET_KEY_COLUMNS_MOV_PAIRS = [
    "fast_leg", "pair_window", "side", "regime_state",
]
BUCKET_KEY_COLUMNS_HIGH_LOW_STREAKS = [
    "band_period", "pct_type", "side", "regime_state",
]

# ---- Motivation-table write columns -----------------------------------------

# mov_rsi / mov_std columns in write order (forecast_id +
# code + bucket keys + lookback). The remaining identity columns are
# intentionally absent — the underlying indicator values are also NOT
# stored: rsi_{W}days lives in analysis.mov_ave_rsi,
# ma/std in analysis.mov_ave_spreads_detail + stats.*_tech_stats
# (joinable via the identities registry + the bucket keys); only the
# market-regime split is materialized here.
MOV_RSI_COLUMNS = (
    ["forecast_id", "code"] + BUCKET_KEY_COLUMNS_MOV_RSI + ["lookback_period"]
)
MOV_STD_COLUMNS = (
    ["forecast_id", "code"] + BUCKET_KEY_COLUMNS_MOV_STD + ["lookback_period"]
)
MOV_PAIRS_COLUMNS = (
    ["forecast_id", "code"] + BUCKET_KEY_COLUMNS_MOV_PAIRS
    + ["lookback_period"]
)
MOV_PAIRS_EMA_COLUMNS = MOV_PAIRS_COLUMNS

# Columns in write order (forecast_id + bucket keys + lookback).
# band_period (NOT "period" — RESERVED by the shared result-row
# pipeline: build_result_rows stamps forecast_results' period
# 'next'/'5d'/'20d'/'mixed' onto every row dict, overwriting any
# same-named motivation key). No cooldown_days member — each streak
# contributes exactly ONE trigger and streaks are inherently separated
# (a streak ends only after a 6+-day in-band gap or a side switch), so
# trigger suppression is redundant (state-family shape, like
# margin_ratio). No hype-derived parameter columns — the config JSONB
# carries the bucket's streak-length context.
HIGH_LOW_STREAKS_COLUMNS = (
    ["forecast_id", "code"] + BUCKET_KEY_COLUMNS_HIGH_LOW_STREAKS
    + ["lookback_period"]
)

# ---- forecast_results / base_rates column sets -------------------------------

# forecast_results columns in write order (forecast_id, period, delay
# first). config is duplicated across all rows of the same forecast_id.
# (The swing-aware reverse_prob + its threshold bar were REMOVED
# 2026-09-25 — see config/horizons.py.) trigger_dates is the DATE[] of
# the row's own bucket days (ascending, length == occurrence_count —
# NULL when the count is 0/NULL), row-local per (period, delay).
# ave_close is the mean n-day PERIOD-END close over the same valid days
# as ave_change (raw price units; NULL on the mixed row — price levels
# do not blend). streak_starts / streak_ends are the
# parallel DATE[] of each merged signal's qualifying-run start / end
# calendar date and streak_days the parallel BIGINT[] of each run's
# trading-day count (element-wise parallel to trigger_dates; the
# streak engines mov_rsi / mov_std / mov_pairs /
# mov_pairs_ema — the pairs families' runs are single days,
# NULL for margin_ratio / high_low_streaks), row-local per
# period. trigger_excess is the parallel NUMERIC(10,6)[] of each
# signal's TRIGGER EXCESS — the anchor day's trigger value minus the
# bucket's qualifying bar (signed, value − bar: the live_signals
# signal_excess convention; the pairs families' bar is the zero line,
# so there the excess IS the day's spread) — defined for the
# scalar-bar engines mov_rsi / mov_std / mov_pairs /
# mov_pairs_ema only, NULL arrays elsewhere (the state families have
# no scalar qualifying bar), row-local per period.
#
# delay (PK member): the ANCHOR DELAY this row's forecast is measured
# from — the trading-day offset of the trigger within its qualifying
# streak (0 = the streak's first qualifying day, the moment the signal
# becomes observable; up to TRIGGER_DELAY_MAX). A persistent streak
# contributes one trigger per day 0..min(run_len-1, TRIGGER_DELAY_MAX),
# so each delay row's stats are conditioned on the signal having
# lasted that long; rows exist only for (bucket, delay) pairs the
# bucket's streaks actually reached.
RESULT_COLUMNS: list[str] = [
    "forecast_id",
    "period",
    "delay",
    "lookback_period",
    "config",
    "ave_change",
    "std_change",
    "max_change",
    "min_change",
    "ave_close",
    "occurrence_count",
    "trigger_dates",
    "streak_starts",
    "streak_ends",
    "streak_days",
    "trigger_excess",
]

# base_rates columns in write order (PK: sec_type, code, stat_date,
# period). The unconditional same-window reference the bucket
# ave_change is read against (lift). (The reversal base probs
# base_down_prob / base_up_prob + their threshold bar were REMOVED
# 2026-09-25 with forecast_results.reverse_prob — see
# config/horizons.py.)
BASE_RATE_COLUMNS: list[str] = [
    "sec_type",
    "code",
    "stat_date",
    "period",
    "lookback_period",
    "base_count",
    "base_ave_change",
]

# One row per forecast bucket: the identity columns (sec_type, code,
# stat_date) keyed by the surrogate forecast_id, tagged with the
# bucket family (the motivation table name) and the bucket's mean
# streak length (streak_signal_days — the merged-signal semantics of
# the 2026-09 streak migration; 1 for the state families) + the mean
# anchor delay (delayed_signal_days — the mean trading-day offset of
# the bucket's emitted trigger anchors within their streaks, 0 = the
# streak's first qualifying day, capped at TRIGGER_DELAY_MAX; 0 for
# 1-day signals). Both INTEGER whole days. Since the
# 2026-09 forecast_id-keyed rebuild this is the ONLY table storing the
# identity (the motivation tables carry forecast_id + their
# family-unique bucket keys alone), written by _write_snapshot in the
# SAME transaction as the motivation + result rows; searched by
# forecast_id via fetch.fetch_forecast_identity (python -m
# analyze.analysis_forecasts --search-forecast-id) and by identity via
# the (sec_type, code, bucket, stat_date) / (sec_type, bucket,
# stat_date) indexes (the UI/API identity endpoint, the signals
# layer). base_rates is NOT registered (no forecast_id).
IDENTITY_COLUMNS: list[str] = [
    "forecast_id",
    "sec_type",
    "code",
    "stat_date",
    "bucket",
    "streak_signal_days",
    "delayed_signal_days",
    "lookback_period",
]

TABLE_MARGIN_RATIO = "analysis_forecasts.margin_ratio_state"
ANALYSIS_NAME_MARGIN_RATIO = "margin_ratio"

# PK + write columns (forecast_id + code + bucket keys + side +
# recorded build parameters + lookback).
BUCKET_KEY_COLUMNS_MARGIN_RATIO = ["ratio_state", "regime_state"]
MARGIN_RATIO_COLUMNS = (
    ["forecast_id", "code"] + BUCKET_KEY_COLUMNS_MARGIN_RATIO + [
        "side",
        "z_window", "z_min_periods",
        "vlow_bar", "low_bar", "high_bar", "vhigh_bar",
        "lookback_period",
    ]
)

TABLE_MOV_PAIRS = "analysis_forecasts.mov_pairs"
ANALYSIS_NAME_MOV_PAIRS = "mov_pairs"

TABLE_HIGH_LOW_STREAKS = "analysis_forecasts.high_low_streaks"
ANALYSIS_NAME_HIGH_LOW_STREAKS = "high_low_streaks"

TABLE_MOV_PAIRS_EMA = "analysis_forecasts.mov_pairs_ema"
ANALYSIS_NAME_MOV_PAIRS_EMA = "mov_pairs_ema"

# Identical write shape as the MA family (the row dicts are source
# agnostic — only the fetched spread columns differ; the fast_leg
# bucket key discriminates the ma5/ema6 vs close-price crosses).
MOV_PAIRS_EMA_COLUMNS = MOV_PAIRS_COLUMNS

TABLE_PE = "analysis_forecasts.pe_state"
ANALYSIS_NAME_PE = "pe"

TABLE_DIVIDEND = "analysis_forecasts.dividend_state"
ANALYSIS_NAME_DIVIDEND = "dividend"

# PK + write columns (forecast_id + code + bucket keys + lookback) —
# identical for both families, the mov_rsi shape minus the window axis
# (the extreme-percentile buckets key on side + pct alone; no recorded
# build-parameter columns — the former z bars are gone with the pct
# refactor).
BUCKET_KEY_COLUMNS_PE = ["side", "pct", "regime_state"]
PE_COLUMNS = (
    ["forecast_id", "code"] + BUCKET_KEY_COLUMNS_PE + ["lookback_period"]
)
BUCKET_KEY_COLUMNS_DIVIDEND = BUCKET_KEY_COLUMNS_PE
DIVIDEND_COLUMNS = PE_COLUMNS

# Motivation table short name (identity.bucket value) → schema-qualified
# table. Declared at the END of the module: it references every family's
# TABLE_* constant above. bucket == the table name minus the schema
# prefix. (The former opp_pair_state family — industry opposite-pair
# buckets, the only family whose subject column was industry_id rather
# than code — was removed 2026-09 with its table and SQL file.)
IDENTITY_BUCKET_TABLES: dict[str, str] = {
    "mov_rsi":            TABLE_MOV_RSI,
    "mov_std":            TABLE_MOV_STD,
    "mov_pairs":          TABLE_MOV_PAIRS,
    "mov_pairs_ema":      TABLE_MOV_PAIRS_EMA,
    "margin_ratio_state": TABLE_MARGIN_RATIO,
    "high_low_streaks":   TABLE_HIGH_LOW_STREAKS,
    "pe_state":           TABLE_PE,
    "dividend_state":     TABLE_DIVIDEND,
}
