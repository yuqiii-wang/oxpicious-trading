"""Universe / snapshot-grid constants (analyze.analysis_forecasts.config)."""

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
