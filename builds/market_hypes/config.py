"""Pure constants for builds.market_hypes — no I/O, no pandas import.

Everything the market-hype EPISODE build needs to know about its target
table (stats.mov_ave_market_hypes), its recorded build parameters, and
its per-sec_type source tables lives here. MIGRATED from
analyze.mov_ave_spread.config (the HYPE_* / MARKET_HYPES_* block) with
one change: the source SQL is owned by this build (it fetches price +
trading_amount from the stats schema and computes the std_{W}days
columns itself) instead of reusing the mov_ave_spread parent
pipeline's DataFrame.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
#  Target table
# ---------------------------------------------------------------------------

# stats.mov_ave_market_hypes (database/sql/stats/16_mov_ave_market_hypes.sql;
# MIGRATED from analysis.mov_ave_market_hypes). PK (code, sec_type,
# start_date, end_date, min_checkin_period), hash-partitioned by code.
TABLE = "stats.mov_ave_market_hypes"

# Check-in windows (trading rows) — one EPISODE SET per window per
# (sec_type, code). min_checkin_period IS the MINIMUM episode span for
# its bucket; the span is bounded above by the NEXT window (exclusive).
# Mirrors the min_checkin_period column (part of the PK) in the DDL.
HYPE_CHECKIN_PERIODS = (5, 20, 60, 120, 255)

# Audit base window (trading rows) for the percentile thresholds:
# CENTERED ±10 trading years around each audited date — NOT a trailing /
# rolling-back window. The base for date t spans the 2550 rows (10 trading
# years) BEFORE t, t itself, and the 2550 rows AFTER t (total 5101 rows ≈
# 20 trading years). Windows near the start / end of a code's history are
# naturally truncated (the newest dates have no future rows yet — their
# base is effectively the trailing 10y); a base with fewer than
# HYPE_THRESHOLD_MIN_PERIODS non-NULL observations has no thresholds
# (the date is not hyped).
HYPE_THRESHOLD_HALF_WINDOW_ROWS = 2550
HYPE_THRESHOLD_WINDOW_ROWS = 2 * HYPE_THRESHOLD_HALF_WINDOW_ROWS + 1
HYPE_THRESHOLD_MIN_PERIODS = 255

# Maximum episode span for the LONGEST bucket (255d): the whole
# 10y+10y = 20y centered threshold base (2 * 2550 rows). Shorter buckets
# are capped by the next check-in window instead (see
# HYPE_EPISODE_SPAN_MAX) — e.g. a 20d-bucket episode spans 20..59 rows,
# a 60d-bucket one 60..119, and a 255d-bucket one 255..5100.
HYPE_MAX_EPISODE_ROWS = 2 * HYPE_THRESHOLD_HALF_WINDOW_ROWS

# Episode-span upper bound (EXCLUSIVE) per check-in window: the next
# window in HYPE_CHECKIN_PERIODS, or HYPE_MAX_EPISODE_ROWS (the full
# 20y base) for the longest window. Together with the window itself
# (the inclusive lower bound) this partitions episode lengths into
# disjoint buckets — one calendar turmoil lands in exactly the bucket
# whose range contains its span.
HYPE_EPISODE_SPAN_MAX = {
    w: (
        HYPE_CHECKIN_PERIODS[i + 1]
        if i + 1 < len(HYPE_CHECKIN_PERIODS)
        else HYPE_MAX_EPISODE_ROWS
    )
    for i, w in enumerate(HYPE_CHECKIN_PERIODS)
}

# Parameter set recorded on every row (the schema defaults). All three
# are strict-greater-than comparisons in percent units (0-100):
#   - satisfaction: fraction of check-in dates within the window that
#     must be EXCEEDED for is_hyped = TRUE (60.0 = "> 60% of the days").
#   - amt percentile: centered-20y (±10y) percentile of daily
#     trading_amount that a date must EXCEED on the liquidity leg
#     (60.0 = 60th pct).
#   - std percentile: centered-20y (±10y) percentile of std_{W}days
#     that a date must EXCEED on the volatility leg. Deliberately LOW
#     (30.0 = 30th pct): the W-day trailing σ lags a sudden turmoil by
#     construction (the window still holds W-1 pre-turmoil rows on day
#     1), so the volatility leg must clear a modest bar to let episodes
#     start at the turmoil's first big-move day — the 2024-09-24 rally
#     audit (159673.SZ) showed a 60th-pct std leg delayed episode starts
#     by a full month while the amt leg fired from day one.
# Changing any of these requires a --force rebuild (they are recorded
# per row but are NOT part of the PK).
HYPE_CHECKIN_SATISFACTION_THRESHOLD = 60.0
HYPE_TRADING_AMT_THRESHOLD_PCT = 60.0
HYPE_STD_THRESHOLD_PCT = 30.0

# Volatility source column per check-in window (matching timescale):
# the W-day rolling population σ of price computed by fetch.py
# (grouped_rolling_agg std ddof=0 per (sec_type, code) — the same
# computation the mov_ave_spread pipeline performs for its detail
# table's std_{W}days columns).
HYPE_STD_COLUMN_BY_PERIOD = {
    5:   "std_5days",
    20:  "std_20days",
    60:  "std_60days",
    120: "std_120days",
    255: "std_255days",
}

# All output column names of stats.mov_ave_market_hypes in the order
# they appear in the table (must stay in sync with the CREATE TABLE in
# 16_mov_ave_market_hypes.sql — COPY inserts with this explicit column
# order). NOTE: the PK is (sec_type, code, start_date, end_date,
# min_checkin_period); the three threshold columns are recorded build
# parameters, not key columns. trading_amt_hype_days / std_hype_days
# count the days within the episode span on which each leg individually
# checked in (diagnostics for which leg drove the episode).
MARKET_HYPES_COLUMNS = (
    "sec_type", "code",
    "start_date", "end_date", "min_checkin_period", "hype_days",
    "min_checkin_satisfaction_threshold",
    "min_trading_amt_threshold",
    "trading_amt_hype_days",
    "min_std_threshold",
    "std_hype_days",
)

MARKET_HYPES_DESCRIPTION = (
    "Market-hype EPISODE detector (ETF + Index + Stock). One row per "
    "(sec_type, code, min_checkin_period, episode): a CONCATENATED hype "
    "episode — a maximal span of trading dates anchored on a maximal run "
    "of consecutive hyped dates and extended through the surrounding "
    "check-in evidence (the W rows before the run's first hyped date, "
    "back to its first check-in, and the W rows after the last hyped "
    "date, to its last check-in). start_date / end_date bracket the "
    "span; hype_days = the span length in trading dates. min_checkin_"
    "period (W) is the bucket's MINIMUM span and the next window its "
    "EXCLUSIVE maximum (20d bucket: 20..59 rows; 60d: 60..119; 120d: "
    "120..254; 255d: 255..5100 = the whole ±10y threshold base), so "
    "each calendar turmoil lands in exactly the bucket matching its "
    "length. A date is hyped when, within the last W trading rows "
    "ending at it, MORE than min_checkin_satisfaction_threshold "
    "percent of the dates are check-ins — a check-in being a date "
    "whose daily trading_amount EXCEEDS its centered-20y "
    "min_trading_amt_threshold percentile AND whose W-day rolling "
    "population σ (std_{W}days, matching timescale) EXCEEDS its "
    "centered-20y min_std_threshold percentile. The audit base window "
    "is CENTERED on each audited date — 2550 trading rows (10 trading "
    "years) before the date plus 2550 rows after it (NOT a trailing/"
    "rolling-back window) — with a 255-row (1 trading year) minimum "
    "before thresholds exist; bases near the start/end of a code's "
    "history are naturally truncated (the newest dates have no future "
    "rows yet). Because the base looks both ways, historical rows use "
    "their following decade (retrospective audit; run --force to "
    "refresh historical rows' flags after new data arrives). "
    "trading_amt_hype_days / std_hype_days count the days within the "
    "episode span on which each leg individually checked in. Non-hyped "
    "dates leave no footprint; episodes are REBUILT WHOLESALE per "
    "sec_type on every run (new dates shift episode boundaries — the "
    "margin_changes precedent). One episode set per check-in window "
    "(5/20/60/120/255); the three threshold columns record the build's "
    "parameter set (defaults 60.0/60.0/30.0). Source: price + "
    "trading_amount fetched from the stats schema (std_{W}days computed "
    "by the build). MIGRATED from the analysis.mov_ave_market_hypes "
    "table / the analyze.mov_ave_spread market-hypes step. The sec_type "
    "column discriminates the source universe ('etf' | 'index' | "
    "'stock')."
)

# ---------------------------------------------------------------------------
#  Source tables (stats schema)
# ---------------------------------------------------------------------------

SEC_TYPES = ("etf", "index", "stock")

# Identity table per sec_type — used by the recent-data pre-filter
# (fetch_codes_with_recent_data_async) to find codes with at least one row
# in the last RECENT_TRADING_DAYS trading days. A code with no recent data
# (delisted / suspended / never-traded) is excluded from the build
# universe entirely so its full history is skipped.
SEC_TYPE_IDENTITY_TABLE = {
    "etf":   "stats.etf_identity",
    "index": "stats.index_identity",
    "stock": "stats.stock_identity",
}

# Per-sec_type source SQL: code, date (epoch float — the fetch.py
# convention), price (for the rolling σ legs) and trading_amount (the
# liquidity leg). Same JOIN structure / price semantics as the
# mov_ave_spread parent pipeline's fetch_source_data (etf uses
# adjustment-adjusted close; index close from basic_stats; stock close
# from basic_stats) so this build's (code, date) universe and σ values
# match what the detail pipeline computes. The tech_stats INNER JOIN is
# kept for universe parity (rows lacking tech_stats were excluded by
# the parent too) — no columns are selected from it.
SEC_TYPE_SOURCE_SQL = {
    "etf": """
        SELECT
            i.code,
            extract(epoch from i.date)::float8 AS date,
            COALESCE(a.adj_close, b.close)::float8 AS price,
            m.trading_amount::float8
        FROM stats.etf_identity i
        JOIN stats.etf_basic_stats b ON b.date = i.date AND b.code = i.code
        LEFT JOIN stats.etf_adjustment a ON a.date = i.date AND a.code = i.code
        JOIN stats.etf_tech_stats t   ON t.date = i.date AND t.code = i.code
        JOIN stats.etf_liquidity_margin m ON m.date = i.date AND m.code = i.code
        WHERE i.code = ANY($1::text[])
        ORDER BY i.code, i.date ASC
    """,
    "index": """
        SELECT
            i.code,
            extract(epoch from i.date)::float8 AS date,
            b.close::float8 AS price,
            b.trading_amount::float8
        FROM stats.index_identity i
        JOIN stats.index_basic_stats b ON b.date = i.date AND b.code = i.code
        JOIN stats.index_tech_stats  t ON t.date = i.date AND t.code = i.code
        WHERE i.code = ANY($1::text[])
        ORDER BY i.code, i.date ASC
    """,
    "stock": """
        SELECT
            i.code,
            extract(epoch from i.date)::float8 AS date,
            b.close::float8 AS price,
            m.trading_amount::float8
        FROM stats.stock_identity i
        JOIN stats.stock_basic_stats b ON b.date = i.date AND b.code = i.code
        JOIN stats.stock_tech_stats  t ON t.date = i.date AND t.code = i.code
        JOIN stats.stock_liquidity_margin m ON m.date = i.date AND m.code = i.code
        WHERE i.code = ANY($1::text[])
          AND b.close IS NOT NULL
        ORDER BY i.code, i.date ASC
    """,
}
