"""study_kit.db — the read-only query layer for sandbox study scripts.

A plain psycopg connection through the container's preset libpq env
(PGHOST / PGPORT / PGDATABASE / PGUSER / PGPASSWORD → the
``readonly_analytics`` role: SELECT-only, role-level
``default_transaction_read_only = on`` — a write attempt fails
server-side). ``query()`` runs any SELECT into a pandas DataFrame;
``fetch_daily_bars()`` is the daily-OHLCV shortcut over the
reconstructed views (database/sql/stats/99_reconstruct_views.sql):

    index   → stats.v_index_daily
    etf     → stats.v_etf_margin   (adjusted prices: adj_*)
    stock   → stats.v_stock_daily
    futures → stats.v_futures_daily

Column conventions (all four views): keys ``(date, code)`` — no
ts_code / trade_date; volume means 成交额 ``trading_amount`` (yuan)
unless the ask names 交易量 ``trading_shares``; index history starts
2020-01-01; calendar gaps are market closures, never missing rows.
Prefer SQL aggregation over wide fetches (read caps: 1 MB per read,
16k chars per stdout) and pull warmup history BEFORE a studied window
when the script computes its own rolling stats.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

import pandas as pd
import psycopg

# sec_type → the reconstructed daily view (keys (date, code)).
DAILY_VIEWS: dict[str, str] = {
    "index": "stats.v_index_daily",
    "etf": "stats.v_etf_margin",
    "stock": "stats.v_stock_daily",
    "futures": "stats.v_futures_daily",
}

# The daily projection per view — the same canonical names out of every
# sec_type (pct_change is change_pct on the index/futures views; the ETF
# view adds adjusted prices). trading_amount is 成交额 (yuan) everywhere.
_DAILY_PROJECTIONS: dict[str, str] = {
    "index": """date, code, name, open, high, low, close,
                change_pct AS pct_change, trading_shares, trading_amount,
                ma5, ma20, ma60, ma120, ma255, ema12, ema26""",
    "etf": """date, code, name, open, high, low, close, adj_close,
              pct_change, trading_shares, trading_amount,
              ma5, ma20, ma60, ma120, ma255, ema12, ema26""",
    "stock": """date, code, name, open, high, low, close,
                pct_change, trading_shares, trading_amount,
                ma5, ma20, ma60, ma120, ma255, ema12, ema26""",
    "futures": """date, code, name, open, high, low, close,
                  change_pct AS pct_change, trading_shares, trading_amount,
                  open_interest""",
}

# Columns kept as-is (dates / identifiers); everything else numeric.
_TEXT_COLUMNS = frozenset({"date", "code", "name"})


def connect() -> psycopg.Connection:
    """One read-only psycopg connection from the preset PG* env."""
    return psycopg.connect()


def query(sql: str, params: Optional[Iterable[Any]] = None) -> pd.DataFrame:
    """Run ONE SELECT and return the rows as a DataFrame.

    READ-ONLY by the role (writes raise server-side). Numeric result
    columns arrive as Decimal from psycopg and are coerced to float
    here, so downstream pandas math is native. Prefer narrow aggregate
    SQL over wide fetches — the middleware caps reads at 1 MB.
    """
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        if cur.description is None:
            raise ValueError("query returned no result set — SELECT only")
        columns = [d.name for d in cur.description]
        return _coerce(pd.DataFrame(cur.fetchall(), columns=columns))


def fetch_daily_bars(
    sec_type: str,
    code: str,
    start: str,
    end: Optional[str] = None,
    adjusted: bool = False,
) -> pd.DataFrame:
    """Daily bars for ONE code over [start, end] (inclusive 'YYYY-MM-DD'
    strings; end defaults to the table's max date), newest last, with
    the precomputed MA5/20/60/120/255 and EMA12/26 columns — reuse them
    instead of recomputing MAs when they cover the window.

    ``adjusted=True`` swaps the ETF price columns to the adjusted set
    (adj_open/adj_high/adj_low/adj_close) — the study-safe prices for a
    total-return view. ``code`` is the plain 6-digit form ('000001' SSE
    Composite, no exchange suffix). Identity rows without a bar (the
    views LEFT JOIN identity over every covered calendar day) are
    dropped — the result holds BARS only, so rolling windows never
    swallow a NULL close.

    Warmup: pull extra history BEFORE the studied window when the
    script computes its own rolling stats (MA255 needs ~1 extra year;
    index history starts 2020-01-01).
    """
    view = DAILY_VIEWS.get(sec_type)
    if view is None:
        raise ValueError(
            f"sec_type {sec_type!r}: expected one of {sorted(DAILY_VIEWS)}")
    projection = _DAILY_PROJECTIONS[sec_type]
    if adjusted and sec_type == "etf":
        projection = projection.replace(
            "open, high, low, close, adj_close",
            "adj_open AS open, adj_high AS high, adj_low AS low, "
            "adj_close AS close",
        ).replace(" adj_close,", "")
    conditions = "code = %s AND date >= %s AND close IS NOT NULL"
    params: list[Any] = [code, start]
    if end is not None:
        conditions += " AND date <= %s"
        params.append(end)
    sql = (
        f"SELECT {projection} FROM {view} WHERE {conditions} "
        "ORDER BY date ASC"
    )
    df = query(sql, params)
    if df.empty:
        raise ValueError(
            f"no bars for {sec_type} {code} over [{start}, {end}] — "
            "check the code (plain 6-digit form) and the range")
    return df


def _coerce(df: pd.DataFrame) -> pd.DataFrame:
    """Decimal/None result columns → float (dates and identifiers stay)."""
    for col in df.columns:
        if col in _TEXT_COLUMNS:
            continue
        converted = pd.to_numeric(df[col], errors="coerce")
        if converted.notna().any() or len(df) == 0:
            df[col] = converted
    return df
