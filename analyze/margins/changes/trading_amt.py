"""Trading amount fetch for margin_changes.

Fetches daily trading_amount (成交金额 / turnover) from
``stats.{stock,etf}_liquidity_margin`` + ``stats.index_basic_stats`` for
ALL sec_types. The per-episode ratio

  - rz_buy_vs_trading_amt_ratio = Σ rz_buy / Σ trading_amount

is computed in detection.py (_aggregate_and_filter): the trend episodes
are CONTIGUOUS daily-row segments, so merging trading_amount onto the
daily rows (equality join on code+date — cudf-native hash join) and
summing per segment is EXACTLY the Σ over [start_date, end_date]. The
former pd.merge_asof window-assignment machinery was removed — cudf has
no merge_asof (26.08), so the 9M-row call always fell back to CPU
pandas.
"""
from __future__ import annotations

import pandas as pd


# Map sec_type → trading_amount source table.
# Stock / ETF trading_amount lives in the liquidity_margin tables;
# index trading_amount is in index_basic_stats directly.
_TRADING_AMT_TABLES: dict[str, str] = {
    "stock": "stats.stock_liquidity_margin",
    "etf":   "stats.etf_liquidity_margin",
    "index": "stats.index_basic_stats",
}


async def fetch_trading_amt(conn, sec_types: list[str]) -> pd.DataFrame:
    """Fetch daily trading_amount for all sec_types.

    Returns a DataFrame with columns [sec_type, code, date, trading_amount]
    for merging with the daily margin rows.

    Uses a single UNION ALL query across all sec_type tables to avoid
    per-type round-trips.
    """
    _empty_cols = ["sec_type", "code", "date", "trading_amount"]
    if not sec_types:
        return pd.DataFrame(columns=_empty_cols)

    parts: list[str] = []
    params: list[str] = []
    for sec_type in sec_types:
        table = _TRADING_AMT_TABLES.get(sec_type)
        if table is None:
            continue
        params.append(sec_type)
        parts.append(f"""
            SELECT ${len(params)}::text AS sec_type, code, date,
                   trading_amount
            FROM {table}
            WHERE trading_amount IS NOT NULL
        """)

    if not parts:
        return pd.DataFrame(columns=_empty_cols)

    query = "\nUNION ALL\n".join(parts)
    rows = await conn.fetch(query, *params)

    if not rows:
        return pd.DataFrame(columns=_empty_cols)

    df = pd.DataFrame(
        {
            "sec_type": [r["sec_type"] for r in rows],
            "code": [r["code"] for r in rows],
            "date": [r["date"] for r in rows],
            "trading_amount": [
                float(r["trading_amount"]) if r["trading_amount"] is not None
                else None for r in rows
            ],
        }
    )
    df["trading_amount"] = pd.to_numeric(df["trading_amount"], errors="coerce")
    return df
