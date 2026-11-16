"""analysis.market_crowdedness.fetch — raw-input SQL reads.

Fetch-only DB reads for the crowdedness compute (every computation
lives in the compute package); the SELECTs render from
database/sql_views/analysis/market_crowdedness/fetch/ at call time —
the view tree mirrors this module (sql_views README convention).
"""
from __future__ import annotations

import datetime

import numpy as np
import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64
from _common.labels import SEC_TYPE_CODE
from _common.sql_views import render_sql_view


async def _fetch_membership(conn, sec_types: tuple[str, ...]) -> pd.DataFrame:
    """Fetch the per-(pool, stock) membership frames for the scoped
    sec_types, concatenated into ONE frame [sec_type, pool_code,
    stock_code] (int16 sec_type code rides the frame — the frame-passenger
    rule)."""
    parts: list[pd.DataFrame] = []
    if "index" in sec_types:
        # analysis/market_crowdedness/fetch/index_membership
        view = render_sql_view(
            "analysis/market_crowdedness/fetch/index_membership",
        )
        rows = await conn.fetch(view.sql)
        if rows:
            df = pd.DataFrame(rec_cols(rows),
                              columns=["pool_code", "stock_code"])
            df = df.assign(sec_type=np.int16(SEC_TYPE_CODE["index"]))
            parts.append(df)
    if "industry" in sec_types:
        # analysis/market_crowdedness/fetch/industry_membership
        view = render_sql_view(
            "analysis/market_crowdedness/fetch/industry_membership",
        )
        rows = await conn.fetch(view.sql)
        if rows:
            df = pd.DataFrame(rec_cols(rows),
                              columns=["pool_code", "stock_code"])
            df = df.assign(sec_type=np.int16(SEC_TYPE_CODE["industry"]))
            parts.append(df)
    if not parts:
        return pd.DataFrame(columns=["sec_type", "pool_code", "stock_code"])
    members = pd.concat(parts, ignore_index=True)
    # Defensive dedup (a stock listed twice in one snapshot / via several
    # member indices must not double-count in the pool).
    members = members.drop_duplicates(subset=["sec_type", "pool_code",
                                              "stock_code"])
    return members


async def _fetch_amounts(
    conn,
    stock_codes: list[str],
    lo: datetime.date,
    hi: datetime.date,
) -> pd.DataFrame:
    """Fetch per-(stock, date) trading amount for the window —
    [stock_code, date(datetime64[us]), amt(float64)]."""
    if not stock_codes:
        return pd.DataFrame(columns=["stock_code", "date", "amt"])
    # analysis/market_crowdedness/fetch/amounts
    view = render_sql_view(
        "analysis/market_crowdedness/fetch/amounts",
        stock_codes=sorted(stock_codes), lo=lo, hi=hi,
    )
    rows = await conn.fetch(view.sql, *view.params)
    if not rows:
        return pd.DataFrame(columns=["stock_code", "date", "amt"])
    cols = rec_cols(rows)
    cols["date"] = epoch_col_to_dt64(cols["date"])
    return pd.DataFrame(cols, columns=["stock_code", "date", "amt"])


async def _fetch_returns(
    conn,
    stock_codes: list[str],
    lo: datetime.date,
    hi: datetime.date,
) -> pd.DataFrame:
    """Fetch per-(stock, date) daily return + pe for the window —
    [stock_code, date(datetime64[us]), ret(float64), pe(float64)].

    ret = pct_change/100 where published, else close/prev_close − 1 over
    the stock's own calendar rows (the SSE-archive gap route; the SQL
    arrives sorted by (stock_code, date) so the grouped shift sees
    correct order)."""
    empty_cols = ["stock_code", "date", "ret", "pe"]
    if not stock_codes:
        return pd.DataFrame(columns=empty_cols)
    # analysis/market_crowdedness/fetch/returns
    view = render_sql_view(
        "analysis/market_crowdedness/fetch/returns",
        stock_codes=sorted(stock_codes), lo=lo, hi=hi,
    )
    rows = await conn.fetch(view.sql, *view.params)
    if not rows:
        return pd.DataFrame(columns=empty_cols)
    cols = rec_cols(rows)
    cols["date"] = epoch_col_to_dt64(cols["date"])
    df = pd.DataFrame(
        cols, columns=["stock_code", "date", "pct", "close", "pe"])
    prev_close = df.groupby("stock_code", sort=False)["close"].shift(1)
    ret_from_close = df["close"] / prev_close - 1.0
    df["ret"] = np.where(
        df["pct"].notna(), df["pct"] / 100.0, ret_from_close)
    return df.drop(columns=["pct", "close"])


async def _fetch_tech(
    conn,
    stock_codes: list[str],
    lo: datetime.date,
    hi: datetime.date,
) -> pd.DataFrame:
    """Fetch per-(stock, date) close + the precomputed tech inputs for
    the window — [stock_code, date(datetime64[us]), close, ma5_ratio,
    ma20, ma60, macd_hist] (float64). Source: stats.v_stock_daily
    (identity + basic + tech reconstruct; rows with none of the needed
    values drop out in SQL)."""
    empty_cols = ["stock_code", "date", "close", "ma5_ratio", "ma20",
                  "ma60", "macd_hist"]
    if not stock_codes:
        return pd.DataFrame(columns=empty_cols)
    # analysis/market_crowdedness/fetch/tech
    view = render_sql_view(
        "analysis/market_crowdedness/fetch/tech",
        stock_codes=sorted(stock_codes), lo=lo, hi=hi,
    )
    rows = await conn.fetch(view.sql, *view.params)
    if not rows:
        return pd.DataFrame(columns=empty_cols)
    cols = rec_cols(rows)
    cols["date"] = epoch_col_to_dt64(cols["date"])
    return pd.DataFrame(cols, columns=empty_cols)


async def _fetch_stock_names(conn, stock_codes: list[str]) -> dict[str, str]:
    """Latest display name per stock code (name falls back to the code)."""
    if not stock_codes:
        return {}
    # analysis/market_crowdedness/fetch/stock_names
    view = render_sql_view(
        "analysis/market_crowdedness/fetch/stock_names",
        stock_codes=sorted(stock_codes),
    )
    rows = await conn.fetch(view.sql, *view.params)
    return {
        r["stock_code"]: (r["name"] if r["name"] else r["stock_code"])
        for r in rows
    }
