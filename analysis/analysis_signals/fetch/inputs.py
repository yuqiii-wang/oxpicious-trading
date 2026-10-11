"""Input loaders of the in-engine rsi-pct bucket computation
(analysis.analysis_signals.fetch.inputs).

The signals tier computes its OWN buckets off the raw RSI percentile
data since the forecast layer was removed (2026-10-05): per sec_type
the joined (code, date) frame of price + the RSI window columns
(analysis.mov_ave_rsi — the value source), the active-code universe,
per-code first-data dates (the live gate) and the latest RSI data date
(the ROLLING LATEST snapshot key). The frame also carries the three
day-0 ohlcv-boost axis columns (engines/ohlcv_boost._axes — the
ohlc_gaps pattern + the price_vs_amt (vol_state, px_speed) cell,
LEFT JOINed per (code, date), missing → the AXIS_MISSING int8 code).

All NUMERIC columns are cast to native float8 in SQL (Decimal objects
would poison the cudf.pandas fast path) and the date columns arrive as
epoch float8, materialized via epoch_col_to_dt64 — the frame the
engine receives is cudf-representable from construction.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Set

from _common.labels import SEC_TYPE_CODE
from _common.build_commons import (
    RECENT_TRADING_DAYS,
    fetch_codes_with_recent_data_async,
    rec_cols,
    recent_trading_day_cutoff,
)
from _common.df_utils import epoch_col_to_dt64
import pandas as pd

from analysis.analysis_signals.config import (
    SEC_TYPE_IDENTITY_TABLE,
    BOLL_WINDOWS,
    RSI_WINDOWS,
    UNCLASSIFIED_CLASS,
)
from analysis.analysis_signals.engines.ohlcv_boost import (
    AXIS_MISSING,
    PATTERN_CODE,
)
from analysis.mov_ave_spread.config import OHL_EST_FILTER, PRICE_SOURCE

logger = logging.getLogger(__name__)

RSI_TABLE = "analysis.mov_ave_rsi"
# the day-0 ohlcv-boost axis stores (engines/ohlcv_boost._axes): the
# classified OHLC pattern + the (vol_state, px_speed) amount/price-speed
# cell, LEFT JOINed per (sec_type smallint, code, date)
PATTERN_TABLE = "analysis.mov_ave_spreads_detail_ohlc_gaps"
PX_VOL_TABLE = "analysis.mov_ave_price_vs_amt"

# True first-data-date source per sec_type (the base OHLCV table — the
# same rows the input fetch admits via b.close IS NOT NULL).
_FIRST_DATE_SOURCE = {
    "index": "stats.index_basic_stats",
    "etf": "stats.etf_basic_stats",
    "stock": "stats.stock_basic_stats",
}

_INPUT_COLUMNS = ["code", "date", "price"] + [
    f"rsi_{w}days" for w in RSI_WINDOWS
] + [f"is_typical_{w}days" for w in RSI_WINDOWS] + [
    f"ep_size_{w}days" for w in RSI_WINDOWS
] + [
    "pattern", "vol_state", "px_speed",
]

# the bollinger family's PRE-compute fetch vocabulary (price + the boost
# axes — the z/episode columns are added in-engine by the family's
# _compute.add_boll_value_columns; the names here are the POST-compute
# frame's shape for the typed empty path)
_BOLL_FETCH_COLUMNS = ["code", "date", "price", "pattern", "vol_state",
                       "px_speed"]
_BOLL_INPUT_COLUMNS = ["code", "date", "price"] + [
    f"boll_z_{w}days" for w in BOLL_WINDOWS
] + [f"is_typical_{w}days" for w in BOLL_WINDOWS] + [
    f"ep_size_{w}days" for w in BOLL_WINDOWS
] + [
    "pattern", "vol_state", "px_speed",
]


async def fetch_active_codes(conn, sec_type: str) -> Set[str]:
    """Codes with at least one identity-table row in the last
    RECENT_TRADING_DAYS trading days (delisted / suspended securities
    are excluded from the emission universe entirely)."""
    identity_table = SEC_TYPE_IDENTITY_TABLE[sec_type]
    cutoff = recent_trading_day_cutoff(RECENT_TRADING_DAYS)
    codes = await fetch_codes_with_recent_data_async(
        conn, identity_table, n_trading_days=RECENT_TRADING_DAYS,
    )
    logger.info(f"      pre-filter: {len(codes):,} {sec_type} codes have "
          f"data in the last {RECENT_TRADING_DAYS} trading days "
          f"(cutoff={cutoff.isoformat()})")
    return codes


async def fetch_latest_rsi_date(conn, sec_type: str) -> date | None:
    """The sec_type's LATEST AVAILABLE RSI DATA DATE (max(date) on
    analysis.mov_ave_rsi) — the ROLLING LATEST snapshot's key and
    window end. Keying on DATA (not ``last_business_day()``) keeps the
    key idempotent across weekends / holidays (no data → no new key →
    the run is a clean no-op) and never ahead of the value source.
    Returns None when the table has no rows for the sec_type."""
    return await conn.fetchval(
        f"SELECT max(date) FROM {RSI_TABLE} WHERE sec_type = $1",
        SEC_TYPE_CODE[sec_type],
    )


async def fetch_first_dates(
    conn,
    sec_type: str,
    codes: list[str],
) -> dict[str, date]:
    """Per-code TRUE first data date (min(date) on the base OHLCV
    table with the same close IS NOT NULL filter the input fetch
    uses) — the live gate's input (a code joins a snapshot from its
    own first-data date)."""
    if not codes:
        return {}
    rows = await conn.fetch(
        f"""
        SELECT b.code, min(b.date) AS first_date
        FROM {_FIRST_DATE_SOURCE[sec_type]} b
        WHERE b.code = ANY($1::text[])
          AND b.close IS NOT NULL
        GROUP BY b.code
        """,
        sorted(codes),
    )
    return {r["code"]: r["first_date"] for r in rows}


async def fetch_rsi_inputs(
    conn,
    sec_type: str,
    codes: list[str],
    since: date,
) -> pd.DataFrame:
    """The joined long-format input frame for the given codes: one row
    per (code, date) with the price (the mov_ave_spread convention —
    ETF adjusted close) + one rsi_{W}days column per RSI window + the
    day-level tail-episode flags + episode sizes (the rsi analysis
    layer's population semantics) + the day-0 ohlcv-boost axis columns
    (pattern / vol_state / px_speed — int8 codes, AXIS_MISSING where
    the axis store has no row).

    Rows are bounded to date >= ``since`` (the earliest trailing-window
    start across the target snapshots); there is NO upper date bound —
    forward changes for the last bucket days of the newest snapshot
    need post-snapshot-end prices. Codes without a single RSI row are
    dropped by the code pre-filter (the caller intersects the universe
    with the RSI table's codes)."""
    if not codes:
        return pd.DataFrame(columns=_INPUT_COLUMNS)

    base, price_expr = PRICE_SOURCE[sec_type]
    st_code = SEC_TYPE_CODE[sec_type]
    ohl_est_filter = OHL_EST_FILTER[sec_type]
    rsi_cols = ",\n       ".join(
        f"r.rsi_{w}days::float8 AS rsi_{w}days" for w in RSI_WINDOWS
    )
    # the day-level tail-episode flags (TRUE = the first-touch pass's
    # days / FALSE = the re-entry pass's days / NULL out-of-band — the
    # gap-1 continuations inherit their pass's flag) + the EPISODE
    # SIZES (every in-band day — start, re-entry AND the gap-1
    # continuations — carries its episode's trading-day count; NULL
    # out-of-band). The rsi analysis layer owns the re-entry semantics
    # (mov_ave_spread steps/rsi_typical.py); BOTH signal tiers'
    # population rides the sizes (non-NULL = in-band — the deep days
    # the percentile bars select are the gap-1 continuations; the
    # threshold tier joined the curve tier's 2026-10-06 episode
    # population 2026-10-07), the curve tier weighting each day 1/size.
    flag_cols = ",\n       ".join(
        f"r.is_typical_{w}days::int::float8 AS is_typical_{w}days"
        for w in RSI_WINDOWS
    )
    size_cols = ",\n       ".join(
        f"r.typical_ep_size_{w}days::float8 AS ep_size_{w}days"
        for w in RSI_WINDOWS
    )
    sql = f"""
        WITH r AS (
            SELECT * FROM {RSI_TABLE}
            WHERE sec_type = $3
        )
        SELECT b.code,
               extract(epoch from b.date)::float8 AS date,
               {price_expr}::float8 AS price,
               {rsi_cols},
               {flag_cols},
               {size_cols},
               g.pattern,
               p.vol_state::float8 AS vol_state,
               p.px_speed::float8 AS px_speed
        FROM {base}
        LEFT JOIN {RSI_TABLE} r
               ON r.sec_type = {st_code}
              AND r.code = b.code AND r.date = b.date
        LEFT JOIN {PATTERN_TABLE} g
               ON g.sec_type = {st_code}
              AND g.code = b.code AND g.date = b.date
        LEFT JOIN {PX_VOL_TABLE} p
               ON p.sec_type = {st_code}
              AND p.code = b.code AND p.date = b.date
        WHERE b.code = ANY($1::text[])
          AND b.close IS NOT NULL
          AND b.date >= $2
          {ohl_est_filter}
        ORDER BY b.code, b.date ASC
    """
    rows = await conn.fetch(sql, sorted(codes), since, st_code)
    if not rows:
        return pd.DataFrame(columns=_INPUT_COLUMNS)
    # Column-materialized ctor + epoch→datetime64[us] (object python
    # dates would poison every downstream op; ::float8 casts avoid
    # Decimal).
    df = pd.DataFrame(rec_cols(rows), columns=_INPUT_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    # the boost axes as int8 codes (strings never enter the frame —
    # the label-code contract); missing store rows → AXIS_MISSING
    df["pattern"] = df["pattern"].map(PATTERN_CODE) \
        .fillna(AXIS_MISSING).astype("int8")
    df["vol_state"] = df["vol_state"].fillna(AXIS_MISSING).astype("int8")
    df["px_speed"] = df["px_speed"].fillna(AXIS_MISSING).astype("int8")
    return df


async def fetch_rsi_codes(conn, sec_type: str) -> Set[str]:
    """Codes carrying at least one RSI row for the sec_type — the
    universe pre-filter's RSI leg (a code without RSI data can never
    enter a percentile bucket)."""
    rows = await conn.fetch(
        f"SELECT DISTINCT code FROM {RSI_TABLE} WHERE sec_type = $1",
        SEC_TYPE_CODE[sec_type],
    )
    return {r["code"] for r in rows}


# ---------------------------------------------------------------------------
#  The bollinger family's value source (IN-ENGINE since 2026-10-10 — the
#  analysis.mov_ave_boll table is gone): the fetch returns price + boost
#  axes over each code's FULL history; the family's _compute adds the
#  σ-depth z + episode columns on the frame.
# ---------------------------------------------------------------------------

async def fetch_latest_boll_date(conn, sec_type: str) -> date | None:
    """The sec_type's LATEST AVAILABLE PRICE DATA DATE (max(date) on the
    base OHLCV table with the same close IS NOT NULL filter the input
    fetch uses) — the bollinger family's ROLLING LATEST snapshot key
    (the same data-keyed idempotence rule as the RSI leg: the family's
    values are computed from prices, so prices are its data horizon)."""
    return await conn.fetchval(
        f"SELECT max(b.date) FROM {_FIRST_DATE_SOURCE[sec_type]} b "
        f"WHERE b.close IS NOT NULL",
    )


async def fetch_boll_inputs(
    conn,
    sec_type: str,
    codes: list[str],
) -> pd.DataFrame:
    """The bollinger family's joined long-format input frame — the RSI
    fetch's base shape (one row per (code, date): the price in the
    mov_ave_spread convention — ETF adjusted close, index/stock basic
    close — plus the day-0 ohlcv-boost axis columns pattern / vol_state
    / px_speed, int8 codes, AXIS_MISSING where the axis store has no
    row) WITHOUT any value columns: the σ-depth z + episode layer are
    computed in-engine by the family's _compute.add_boll_value_columns.

    NO lower date bound — each code's FULL history is fetched, because
    the trailing SMA/σ windows need W prior rows for the earliest target
    date and the episode segmentation reads preceding band days
    arbitrarily far back (the RSI fetch can bound to ``since`` only
    because its values are precomputed over full history). No upper
    bound either (forward changes need post-snapshot prices). All
    numerics cast ::float8, the date epoch-converted — the frame the
    engine receives is cudf-representable from construction."""
    if not codes:
        return pd.DataFrame(columns=_BOLL_FETCH_COLUMNS)

    base, price_expr = PRICE_SOURCE[sec_type]
    st_code = SEC_TYPE_CODE[sec_type]
    ohl_est_filter = OHL_EST_FILTER[sec_type]
    sql = f"""
        SELECT b.code,
               extract(epoch from b.date)::float8 AS date,
               {price_expr}::float8 AS price,
               g.pattern,
               p.vol_state::float8 AS vol_state,
               p.px_speed::float8 AS px_speed
        FROM {base}
        LEFT JOIN {PATTERN_TABLE} g
               ON g.sec_type = {st_code}
              AND g.code = b.code AND g.date = b.date
        LEFT JOIN {PX_VOL_TABLE} p
               ON p.sec_type = {st_code}
              AND p.code = b.code AND p.date = b.date
        WHERE b.code = ANY($1::text[])
          AND b.close IS NOT NULL
          {ohl_est_filter}
        ORDER BY b.code, b.date ASC
    """
    rows = await conn.fetch(sql, sorted(codes))
    if not rows:
        return pd.DataFrame(columns=_BOLL_FETCH_COLUMNS)
    df = pd.DataFrame(rec_cols(rows), columns=_BOLL_FETCH_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["pattern"] = df["pattern"].map(PATTERN_CODE) \
        .fillna(AXIS_MISSING).astype("int8")
    df["vol_state"] = df["vol_state"].fillna(AXIS_MISSING).astype("int8")
    df["px_speed"] = df["px_speed"].fillna(AXIS_MISSING).astype("int8")
    return df


async def fetch_index_classes(
    conn,
    sec_type: str,
    codes: list[str],
) -> dict[str, str]:
    """Per-code PRIMARY classification industry (stats.sec_classification
    .industry_id — the finer L2 id: BROAD_SSE/BROAD_CSI/... for broad
    indices, SEMI/PHARMA_BROAD/... for sector indices) — the curve
    tier's slope pooling key (the 2026-10-06 per-index-class refinement:
    unlike families must not borrow each other's gradient; 2026-10-07:
    the pool key moved from the L1 sector_id to the L2 industry_id —
    pool within the industry, not the sector). Codes without a
    classification row map to UNCLASSIFIED_CLASS (their pooled slope
    stands or falls on its own significance)."""
    if not codes:
        return {}
    rows = await conn.fetch(
        """
        SELECT code, min(industry_id) AS industry_id
        FROM stats.sec_classification
        WHERE type = $1
          AND code = ANY($2::text[])
          AND industry_id IS NOT NULL AND industry_id <> ''
        GROUP BY code
        """,
        sec_type,
        sorted(codes),
    )
    found = {r["code"]: r["industry_id"] for r in rows}
    return {c: found.get(c, UNCLASSIFIED_CLASS) for c in codes}
