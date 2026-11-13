"""study_kit.db.fetch_amt_metrics — the MATERIALIZED trading-amount
read (the 量能 lane).

Retrieves analysis.mov_ave_trading_amt for ONE code — the amt MAs
(近5/20/60/120/252日成交额均值), their slopes (量能扩张/萎缩), the
Bollinger σ of the MA lines, the market-share MAs and the
market-share-vs-MA gaps (放量/缩量 vs the market's own share) — the
numbers a volume study would otherwise recompute from bars.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from study_kit.db._shared import coerce_frame, require_sec_type
from study_kit.db.query import query

# The materialized amount-metric columns, grouped (the SELECT list).
AMT_COLUMNS: tuple[str, ...] = (
    "trading_amt_ma5", "trading_amt_ma20", "trading_amt_ma60",
    "trading_amt_ma120", "trading_amt_ma252",
    "trading_amt_std5", "trading_amt_std20", "trading_amt_std60",
    "trading_amt_std120", "trading_amt_std252",
    "trading_amt_market_share_ma5", "trading_amt_market_share_ma20",
    "trading_amt_market_share_ma60", "trading_amt_market_share_ma120",
    "trading_amt_market_share_ma252",
    "trading_amt_slope",
    "trading_amt_ma5_slope", "trading_amt_ma20_slope",
    "trading_amt_ma60_slope", "trading_amt_ma120_slope",
    "trading_amt_ma252_slope",
    "trading_amt_market_share_vs_ma5", "trading_amt_market_share_vs_ma20",
    "trading_amt_market_share_vs_ma60", "trading_amt_market_share_vs_ma120",
    "trading_amt_market_share_vs_ma252",
)


def fetch_amt_metrics(
    sec_type: str,
    code: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> pd.DataFrame:
    """Materialized trading-amount metrics, newest last — NO compute.

    One row per date with the amt MAs (yuan; the 近W日成交额均值),
    their Bollinger σ, the market-share MAs and the signed
    market-share-vs-MA gaps (positive = gaining relative liquidity —
    the 放量 side), plus the raw amount MA slopes (the 量能扩张/萎缩
    gauge). Backing table: analysis.mov_ave_trading_amt (sec_type ∈
    etf / stock / industry — the same universe the parent pipeline
    publishes; NO index rows: index amounts live in the base stats
    tables, fetch those via fetch_daily_bars's trading_amount column).

    ``start``/``end`` are inclusive 'YYYY-MM-DD' strings (default: the
    code's full stored history). The per-side up/down-day contrast the
    table does not store is ``study_kit.studies.volume_contrast`` over
    fetch_daily_bars's (close, trading_amount).
    """
    sec_code = require_sec_type(sec_type, {"etf": 0, "stock": 2})
    select = ", ".join(f"t.{c}" for c in AMT_COLUMNS)
    conditions = "t.sec_type = %s AND t.code = %s"
    params: list[Any] = [sec_code, code]
    if start is not None:
        conditions += " AND t.date >= %s"
        params.append(start)
    if end is not None:
        conditions += " AND t.date <= %s"
        params.append(end)
    sql = (
        f"SELECT t.date, t.code, {select} "
        "FROM analysis.mov_ave_trading_amt t "
        f"WHERE {conditions} ORDER BY t.date ASC"
    )
    df = query(sql, params)
    if df.empty:
        raise ValueError(
            f"no amt metrics for {sec_type} {code} over [{start}, "
            f"{end}] — mov_ave_trading_amt covers etf/stock/industry "
            "(NOT index; use fetch_daily_bars's trading_amount there)")
    return coerce_frame(df)
