"""Cross-family fetch vocabulary (analysis.analysis_signals.fetch.
inputs._shared) — the table-name constants, the active-code /
first-date lookups every family's input fetch rides on, and the
shared frame-finalize step (epoch float8 date → datetime64[us] +
the day-0 ohlcv-boost axis columns as int8 label codes) the
long-format family fetches end with. Family-specific fetchers live
in their own modules (_mov_rsi / _boll_std / _ma_cross /
_options_wall / _options_vix).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Set

import numpy as np

from _common.labels import AXIS_MISSING, PATTERN_CODE, SEC_TYPE_CODE
from _common.build_commons import (
    RECENT_TRADING_DAYS,
    fetch_codes_with_recent_data_async,
    recent_trading_day_cutoff,
)
from _common.df_utils import compute_emas, epoch_col_to_dt64, safe_columns
import pandas as pd

from analysis.analysis_signals.config import (
    SEC_TYPE_IDENTITY_TABLE,
    UNCLASSIFIED_CLASS,
)
from analysis.analysis_signals import config as cfg

logger = logging.getLogger(__name__)

RSI_TABLE = "analysis.mov_ave_rsi"
# the mov_ave_spread momentum step's store (the momentum family's value
# source — log-return axes ln(1+momentum) computed at the fetch boundary)
MOMENTUM_TABLE = "analysis.mov_ave_momentum"
# the rf_break family's value source: the roof/floor LEVEL ESTIMATES
# (the donchian method's window-extreme rows are the breakout bars)
RF_ESTIMATE_TABLE = "analysis.mov_ave_spreads_roof_floor_estimates"
# the day-0 ohlcv-boost axis stores (_common/labels' axis codes): the
# classified OHLC pattern + the (vol_state, px_speed) amount/price-speed
# cell, LEFT JOINed per (sec_type smallint, code, date)
PATTERN_TABLE = "analysis.mov_ave_spreads_detail_ohlc_gaps"
PX_VOL_TABLE = "analysis.mov_ave_price_vs_amt"
# the turnover-climax axis's raw-amount source per sec_type (the base
# stats tables carry trading_amount for indices only; etf/stock amounts
# live in the liquidity-margin stores — the detail table's own
# trading_amt_ma* source tables)
AMOUNT_SOURCE = {
    "index": "stats.index_basic_stats",
    "etf": "stats.etf_liquidity_margin",
    "stock": "stats.stock_liquidity_margin",
}
# the 255d turnover-MA denominator (analysis.mov_ave_spreads_detail.
# trading_amt_ma255 — the climax ratio's normal-turnover basis)
AMT_MA_TABLE = "analysis.mov_ave_spreads_detail"

# True first-data-date source per sec_type (the base OHLCV table — the
# same rows the input fetch admits via b.close IS NOT NULL).
_FIRST_DATE_SOURCE = {
    "index": "stats.index_basic_stats",
    "etf": "stats.etf_basic_stats",
    "stock": "stats.stock_basic_stats",
}

# stats.options_terms.underlying_target_type per signals sec_type (the
# options families' universe gate — None = no options for the sec_type)
_OW_TARGET_TYPE = {"index": "INDEX", "etf": "ETF"}

# the SSE ETF underlyings' code prefixes (51/56/58xxxx) — their chain OI
# is an accumulated-volume estimator (builds/options/sse: no daily-stats
# endpoint, OI chained from volume), not true OI
_SSE_ETF_PREFIX = "5"


async def fetch_options_universe(
    conn, sec_type: str,
) -> Set[str]:
    """The sec_type's option underlyings (DISTINCT underlying_code over
    stats.options_terms by underlying_target_type) — the options
    families' universe before the family exclusions
    (resolve_options_universe below) and the active-code intersection.
    Empty for sec_types without options (stock)."""
    target = _OW_TARGET_TYPE.get(sec_type)
    if target is None:
        return set()
    rows = await conn.fetch(
        """
        SELECT DISTINCT underlying_code
        FROM stats.options_terms
        WHERE underlying_target_type = $1
        """,
        target,
    )
    return {r["underlying_code"] for r in rows}


async def resolve_options_universe(conn, label: str,
                                   sec_type: str) -> list[str]:
    """The options families' universe per (family, sec_type): the
    sec_type's option underlyings (stats.options_terms by target type)
    with the family's population exclusions applied. options_wall drops
    the SSE ETF underlyings — the walls build writes them, but their
    strength ranks ride the accumulated-volume OI estimator, so the
    family does not trade them (the documented universe: the 3 indices
    + the SZSE ETFs). options_vix keeps every underlying (the vol index
    is settlement-price based — no OI input). Empty for sec_types
    without options."""
    if _OW_TARGET_TYPE.get(sec_type) is None:
        return []
    universe = sorted(await fetch_options_universe(conn, sec_type))
    if label == "options_wall" and sec_type == "etf":
        universe = [c for c in universe
                    if not c.startswith(_SSE_ETF_PREFIX)]
    return universe


def amt_ratio_fragments(sec_type: str, base_alias: str = "b",
                        ma_alias: str | None = None) -> tuple[str, str]:
    """(select fragment, join fragment) for the turnover-climax axis:
    the day's raw trading_amount over its own 255d turnover MA. NULL-
    safe both legs (a missing amount or MA row → NULL → AXIS_MISSING
    once finalize_input_frame buckets it). ``ma_alias`` names an
    already-joined detail-table alias carrying trading_amt_ma255 (the
    ma_cross fetch keys on that table); the default joins the detail
    table as ``t`` itself."""
    st_code = SEC_TYPE_CODE[sec_type]
    if ma_alias is None:
        ma_join = (f"LEFT JOIN {AMT_MA_TABLE} t "
                   f"ON t.sec_type = {st_code} "
                   f"AND t.code = {base_alias}.code "
                   f"AND t.date = {base_alias}.date")
        denom = "NULLIF(t.trading_amt_ma255, 0)"
    else:
        ma_join = ""
        denom = f"NULLIF({ma_alias}.trading_amt_ma255, 0)"
    joins = (
        f"LEFT JOIN {AMOUNT_SOURCE[sec_type]} b2 "
        f"ON b2.code = {base_alias}.code AND b2.date = {base_alias}.date"
    ) + (f"\n        {ma_join}" if ma_join else "")
    select = f"(b2.trading_amount::float8 / {denom})::float8 AS amt_ratio"
    return select, joins


def finalize_input_frame(df: pd.DataFrame) -> pd.DataFrame:
    """The long-format family fetches' shared final step, in place:
    epoch float8 → datetime64[us] (object python dates would poison
    every downstream op; the ::float8 SQL casts avoid Decimal) and the
    boost axes as int8 codes (strings never enter the frame — the
    label-code contract); missing store rows → AXIS_MISSING. Fetches
    that selected the amt_ratio column additionally get the bucketed
    turnover-climax code (0 normal / 1 heavy / 2 climax)."""
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["pattern"] = df["pattern"].map(PATTERN_CODE) \
        .fillna(AXIS_MISSING).astype("int8")
    df["vol_state"] = df["vol_state"].fillna(AXIS_MISSING).astype("int8")
    df["px_speed"] = df["px_speed"].fillna(AXIS_MISSING).astype("int8")
    if "amt_ratio" in safe_columns(df):
        ratio = df["amt_ratio"]
        df["amt_climax"] = np.where(
            ratio.isna(), AXIS_MISSING,
            np.where(ratio >= cfg.AMT_CLIMAX_EXTREME, 2,
                     np.where(ratio >= cfg.AMT_CLIMAX_HEAVY, 1, 0)),
        ).astype("int8")
        df = df.drop(columns=["amt_ratio"])
    return df


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


# the market-level breadth state frame's columns (fetch_market_breadth)
_BREADTH_COLUMNS = [
    "date", "mkt_breadth_ema10", "mkt_thrust", "mkt_9to1_up",
    "mkt_9to1_dn", "mkt_up_ratio", "mkt_dn_ratio",
]


async def fetch_market_breadth(conn, sec_type: str) -> pd.DataFrame:
    """The sec_type's own MARKET-LEVEL breadth states, one row per
    trading date (the ma_cross breadth rules' value source, 2026-10-16):

      - adv/dec per date over EVERY clean-close code of the sec_type's
        base stats table (the whole universe — 493 index codes — not
        the requested family codes; breadth is a market property)
      - mkt_breadth_ema10 = the 10-session EMA of adv/(adv+dec), the
        day's ratio neutral-filled (0.5) where the code floor is not
        met so the EMA stays continuous (the fused CUDA kernel path —
        a NaN-bearing column would fall back to pandas)
      - mkt_thrust = the Zweig Breadth Thrust day: the EMA at/above
        0.615 while it sat below 0.40 BREADTH_THRUST_WITHIN sessions
        earlier (the from->to crossing inside the window)
      - mkt_9to1_up / mkt_9to1_dn = the 9:1 up/down days (adv >= 9x
        dec / dec >= 9x adv, both at the code floor); mkt_up_ratio /
        mkt_dn_ratio carry the day's ratio (floored at the 9:1 bar's
        side value when the denominator is 0) for the history record.

    The frame is tiny (one row per session); the engines join it per
    DATE onto every code's frame — the market day is identical context
    for all codes, each code's cell measures its own forward blend."""
    base = _FIRST_DATE_SOURCE[sec_type]
    # the same clean-row filter the family fetches admit
    ohl = {"index": "AND COALESCE(b.is_ohl_estimated, FALSE) = FALSE",
           "etf": "AND COALESCE(b.is_ohl_estimated, FALSE) = FALSE",
           "stock": "AND COALESCE(b.is_ohl_estimated, FALSE) = FALSE"}
    rows = await conn.fetch(
        f"""
        WITH lagged AS (
            SELECT b.date,
                   b.close,
                   lag(b.close) OVER (
                       PARTITION BY b.code ORDER BY b.date
                   ) AS prev_close
            FROM {base} b
            WHERE b.close IS NOT NULL
              {ohl[sec_type]}
        )
        SELECT extract(epoch from date)::float8 AS date,
               count(*) FILTER (WHERE close > prev_close) AS adv,
               count(*) FILTER (WHERE close < prev_close) AS dec
        FROM lagged
        WHERE prev_close IS NOT NULL
        GROUP BY date
        ORDER BY date
        """
    )
    if not rows:
        return pd.DataFrame(columns=_BREADTH_COLUMNS)
    df = pd.DataFrame(
        [dict(r) for r in rows], columns=["date", "adv", "dec"],
    )
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    n = (df["adv"] + df["dec"]).astype("float64")
    ratio = (df["adv"] / n.where(n > 0)).where(
        n >= cfg.BREADTH_MIN_CODES,
    )
    # neutral-fill below the code floor (early sessions) — keeps the
    # EMA on the CUDA kernel path (NaN would fall back to pandas)
    ratio = ratio.fillna(0.5)
    work = pd.DataFrame(
        {"code": np.repeat("mkt", len(df)), "ratio": ratio},
        index=df.index,
    )
    work = compute_emas(work, "code", "ratio", [cfg.BREADTH_EMA_SPAN])
    ema = work[f"ema{cfg.BREADTH_EMA_SPAN}"]
    within = cfg.BREADTH_THRUST_WITHIN
    ema_prev = ema.shift(within)
    thrust = (ema >= cfg.BREADTH_THRUST_TO) \
        & (ema_prev < cfg.BREADTH_THRUST_FROM)
    dec_safe = df["dec"].where(df["dec"] > 0, 1)
    adv_safe = df["adv"].where(df["adv"] > 0, 1)
    up_ratio = (df["adv"] / dec_safe).where(n >= cfg.BREADTH_MIN_CODES)
    dn_ratio = (df["dec"] / adv_safe).where(n >= cfg.BREADTH_MIN_CODES)
    r9 = cfg.BREADTH_9TO1_RATIO
    out = pd.DataFrame(
        {
            "date": df["date"],
            "mkt_breadth_ema10": ema,
            "mkt_thrust": thrust.fillna(False),
            "mkt_9to1_up": (up_ratio >= r9).fillna(False),
            "mkt_9to1_dn": (dn_ratio >= r9).fillna(False),
            "mkt_up_ratio": up_ratio.fillna(r9),
            "mkt_dn_ratio": dn_ratio.fillna(r9),
        },
        index=df.index,
    )
    return out[_BREADTH_COLUMNS]
