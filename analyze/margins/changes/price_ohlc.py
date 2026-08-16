"""Price OHLC fetch + 4 margin/price OHLC ratios for margin_changes.

Fetches daily price OHLC (open / high / low / close) from
``stats.{index,etf,stock}_basic_stats`` for ALL sec_types and computes
the 4 OHLC margin/price ratios per trend episode:

  - ratio_open_margin_vs_price   = open_margin_balance  / open_price
  - ratio_close_margin_vs_price  = close_margin_balance / close_price
  - ratio_high_margin_vs_price   = high_margin_balance  / high_price
  - ratio_low_margin_vs_price    = low_margin_balance   / low_price
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analyze.margins.changes.constants import INSERT_COLUMNS


# Map sec_type → price OHLC source table + code column name.
_PRICE_OHLC_TABLES = {
    "index": ("stats.index_basic_stats", "code"),
    "etf":   ("stats.etf_basic_stats",   "code"),
    "stock": ("stats.stock_basic_stats", "code"),
}


async def fetch_price_ohlc(conn, sec_types: list[str]) -> pd.DataFrame:
    """Fetch daily price OHLC (open/high/low/close) for all sec_types.

    Returns a DataFrame with columns [sec_type, code, date, open, high,
    low, close] for merging with trend episodes.
    """
    frames: list[pd.DataFrame] = []
    for sec_type in sec_types:
        table_info = _PRICE_OHLC_TABLES.get(sec_type)
        if table_info is None:
            continue
        table, code_col = table_info
        rows = await conn.fetch(
            f"""
            SELECT $1::text AS sec_type, {code_col} AS code, date,
                   open, high, low, close
            FROM {table}
            WHERE open IS NOT NULL AND close IS NOT NULL
            """,
            sec_type,
        )
        if rows:
            frames.append(pd.DataFrame({
                "sec_type": [r["sec_type"] for r in rows],
                "code": [r["code"] for r in rows],
                "date": [r["date"] for r in rows],
                "open": [float(r["open"]) if r["open"] is not None else np.nan for r in rows],
                "high": [float(r["high"]) if r["high"] is not None else np.nan for r in rows],
                "low": [float(r["low"]) if r["low"] is not None else np.nan for r in rows],
                "close": [float(r["close"]) if r["close"] is not None else np.nan for r in rows],
            }))
    if not frames:
        return pd.DataFrame(columns=["sec_type", "code", "date", "open", "high", "low", "close"])
    return pd.concat(frames, ignore_index=True)


def compute_price_ohlc_ratio(
    episodes: pd.DataFrame,
    price_ohlc: pd.DataFrame,
) -> pd.DataFrame:
    """Compute the 4 OHLC margin/price ratios per episode.

    For each trend episode [start_date, end_date]:
      - open_price  = price open on start_date
      - close_price = price close on end_date
      - high_price  = MAX(price high) over the window
      - low_price   = MIN(price low)  over the window

      - ratio_open_margin_vs_price   = open_margin_balance  / open_price
      - ratio_close_margin_vs_price  = close_margin_balance / close_price
      - ratio_high_margin_vs_price   = high_margin_balance  / high_price
      - ratio_low_margin_vs_price    = low_margin_balance   / low_price

    Ratios are NULL when price is unavailable / 0 (NULLIF guard).
    """
    if episodes.empty:
        return episodes

    if price_ohlc.empty:
        for col in ("ratio_open_margin_vs_price", "ratio_high_margin_vs_price",
                     "ratio_low_margin_vs_price", "ratio_close_margin_vs_price"):
            episodes[col] = np.nan
        return episodes

    # Tag each episode with a unique index for groupby after merge.
    episodes["_ep_idx"] = range(len(episodes))

    # Merge episodes with price OHLC on (sec_type, code).
    merged = episodes[["_ep_idx", "sec_type", "code", "start_date", "end_date"]].merge(
        price_ohlc[["sec_type", "code", "date", "open", "high", "low", "close"]],
        on=["sec_type", "code"],
        how="inner",
    )
    # Filter to within the episode date range.
    in_range = (
        (merged["date"] >= merged["start_date"])
        & (merged["date"] <= merged["end_date"])
    )
    merged = merged[in_range]

    # Per-episode price OHLC: open = first day's open, close = last day's
    # close, high = max high, low = min low.
    # Use groupby + agg with named aggregations.
    price_ohlc_per_ep = merged.groupby("_ep_idx").agg(
        open_price=("open", "first"),
        close_price=("close", "last"),
        high_price=("high", "max"),
        low_price=("low", "min"),
    )

    # Map back to episodes.
    episodes["__open_price"] = episodes["_ep_idx"].map(price_ohlc_per_ep["open_price"])
    episodes["__close_price"] = episodes["_ep_idx"].map(price_ohlc_per_ep["close_price"])
    episodes["__high_price"] = episodes["_ep_idx"].map(price_ohlc_per_ep["high_price"])
    episodes["__low_price"] = episodes["_ep_idx"].map(price_ohlc_per_ep["low_price"])

    # Compute ratios with NULLIF guard (price must be > 0).
    for ratio_col, margin_col, price_col in [
        ("ratio_open_margin_vs_price",  "open_margin_balance",  "__open_price"),
        ("ratio_close_margin_vs_price", "close_margin_balance", "__close_price"),
        ("ratio_high_margin_vs_price",  "high_margin_balance",  "__high_price"),
        ("ratio_low_margin_vs_price",   "low_margin_balance",   "__low_price"),
    ]:
        price_safe = episodes[price_col]
        episodes[ratio_col] = (
            episodes[margin_col] / price_safe
        ).where(price_safe > 0)

    # Drop helper columns.
    episodes = episodes.drop(columns=[
        "_ep_idx", "__open_price", "__close_price", "__high_price", "__low_price",
    ])

    # Restore original column order.
    episodes = episodes[INSERT_COLUMNS]
    return episodes
