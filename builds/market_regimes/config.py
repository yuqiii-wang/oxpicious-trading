"""Pure constants for builds.market_regimes — no I/O, no pandas import.

Everything the market-REGIME state build needs to know about its
target table (stats.market_regimes), its detector parameters (the
2026-09 Phase-A study calibration, docs/market_regimes_study.md), and
its per-sec_type source tables lives here. REPLACES builds.market_hypes
(the boolean episode detector over stats.mov_ave_market_hypes —
dropped).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
#  Target table
# ---------------------------------------------------------------------------

# stats.market_regimes (database/sql/stats/18_market_regimes.sql):
# one row per (sec_type, code, TRADING date) — the DAILY 4-state regime
# label. PK (code, sec_type, date), hash-partitioned by code.
TABLE = "stats.market_regimes"

# Derived shading table (same SQL file, written by this build): one row
# per CONTIGUOUS same-regime run of the daily registry — the UI's
# regime-shading source (the shared CodeTrendChart's Regimes toggle +
# the MA-Spread panel's regime chips, served by GET
# /api/analysis/market-regimes). Replaces the former per-query VIEW
# (gaps-and-islands recomputed on every request) with a pure row read.
SPANS_TABLE = "stats.market_regime_spans"

# Regime vocabulary (the taxonomy's exhaustive states, ordinal order).
REGIMES: tuple[str, ...] = ("calm", "hot", "panic", "quiet")

# ---- Detector parameters (Phase-A study calibration, recorded per row) ----
# All inputs TRAILING + SHIFTED 1 row (no look-ahead — the px_vol
# log_level convention; see compute.py for the derivation).
VOL_SPAN = 20              # EWMA span (trading rows) of ret_1d^2
VOL_MIN_PERIODS = 10
VOL_Z_WINDOW = 255         # trailing rows for the vol_z moments
VOL_Z_MIN_PERIODS = 60
AMT_Z_WINDOW = 255         # trailing rows for the amt_z moments
AMT_Z_MIN_PERIODS = 60

# Regime bars: vol_z > VOL_BAR separates elevated volatility (hot /
# panic); amt_z > AMT_BAR separates volume expansion (hot / quiet).
# (1.0, 1.0) is the study's chosen calibration — index day coverage
# calm 71% / hot 6% / panic 8% / quiet 15%; the (0.75, 0.75) variant
# was tested and rejected (weaker premise separation, worse acceptance).
VOL_BAR = 1.0
AMT_BAR = 1.0

# All output column names of stats.market_regimes in the order they
# appear in the table (must stay in sync with the CREATE TABLE in
# 18_market_regimes.sql — COPY inserts with this explicit column order).
MARKET_REGIMES_COLUMNS = (
    "sec_type", "code", "date", "regime",
    "vol", "vol_z", "amt_z",
    "vol_span", "vol_min_periods",
    "vol_z_window", "vol_z_min_periods",
    "amt_z_window", "amt_z_min_periods",
    "vol_bar", "amt_bar",
)

# All output column names of stats.market_regime_spans in the order
# they appear in the table (must stay in sync with the CREATE TABLE in
# 18_market_regimes.sql — COPY inserts with this explicit column order).
MARKET_REGIME_SPANS_COLUMNS = (
    "sec_type", "code", "regime", "start_date", "end_date", "span_days",
)

MARKET_REGIMES_DESCRIPTION = (
    "Daily market-REGIME state registry: one row per (sec_type, code, "
    "trading date) classifying the day into ONE of four exhaustive "
    "regimes — hot (vol_z > vol_bar AND amt_z > amt_bar: elevated "
    "volatility WITH volume expansion — the retired boolean 'hyped'), "
    "panic (vol_z > vol_bar AND amt_z <= amt_bar: elevated volatility "
    "WITHOUT volume expansion — exhaustion/unwind, invisible to the old "
    "AND-gated hype boolean), quiet (vol_z <= vol_bar AND amt_z > "
    "amt_bar: volume expansion without price movement) or calm "
    "(everything else). vol_z = z of the code's EWMA realized "
    "return-volatility (sqrt(EWMA(ret_1d^2, span=20))) vs its own "
    "trailing 255-row moments, amt_z = z of log(trading_amount) vs its "
    "own trailing 255-row moments — BOTH legs shifted 1 row (no "
    "look-ahead; the px_vol log_level convention — unlike the retired "
    "stats.mov_ave_market_hypes centered ±10y look-ahead percentile "
    "base). Days without state yet (first 60 rows) carry regime='calm' "
    "with NULL evidence. The parameter columns record the build's "
    "calibration. Phase-A study: docs/market_regimes_study.md — the "
    "regime split carries materially different forward behavior "
    "(mov_rsi/mov_std reversal means 5-9x stronger in hot/panic than "
    "calm on the index universe). Built by builds.market_regimes, "
    "wholesale-rebuilt per scope on every run (margin_changes "
    "precedent). Derived shading table: stats.market_regime_spans "
    "(materialized by this build). "
    "The sec_type column discriminates the source universe ('etf' | "
    "'index' | 'stock')."
)

# ---------------------------------------------------------------------------
#  Source tables (stats schema)
# ---------------------------------------------------------------------------

SEC_TYPES = ("etf", "index", "stock")

# Identity table per sec_type — the active-universe pre-filter
# (fetch_codes_with_recent_data_async). Same convention as the retired
# market_hypes build.
SEC_TYPE_IDENTITY_TABLE = {
    "etf":   "stats.etf_identity",
    "index": "stats.index_identity",
    "stock": "stats.stock_identity",
}

# Per-sec_type source SQL: code, date (epoch float — the fetch.py
# convention), price and trading_amount. The regime legs need NOTHING
# else (no std columns — the vol leg is computed from returns). Same
# JOIN structure / price semantics as the forecast engines'
# fetch_analysis_inputs (etf uses adjustment-adjusted close; index /
# stock close from basic_stats; estimated closes excluded — synthetic
# flat closes would zero ret_1d and poison the vol leg).
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
        JOIN stats.etf_liquidity_margin m ON m.date = i.date AND m.code = i.code
        WHERE i.code = ANY($1::text[])
          AND COALESCE(b.is_close_estimated, FALSE) = FALSE
          AND m.trading_amount IS NOT NULL AND m.trading_amount > 0
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
        WHERE i.code = ANY($1::text[])
          AND b.close IS NOT NULL
          AND COALESCE(b.is_close_estimated, FALSE) = FALSE
          AND b.trading_amount IS NOT NULL AND b.trading_amount > 0
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
        JOIN stats.stock_liquidity_margin m ON m.date = i.date AND m.code = i.code
        WHERE i.code = ANY($1::text[])
          AND b.close IS NOT NULL
          AND m.trading_amount IS NOT NULL AND m.trading_amount > 0
        ORDER BY i.code, i.date ASC
    """,
}
