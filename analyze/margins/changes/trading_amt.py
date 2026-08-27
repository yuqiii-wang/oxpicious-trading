"""Trading amount fetch + rz_buy/trading_amt ratio for margin_changes.

Fetches daily trading_amount (成交金额 / turnover) from
``stats.{stock,etf}_liquidity_margin`` + ``stats.index_basic_stats`` for
ALL sec_types and computes the ratio per trend episode:

  - rz_buy_vs_trading_amt_ratio = Σ rz_buy / Σ trading_amount

where Σ rz_buy is the per-episode sum computed by detect_trend_episodes
(carried in the ``sum_rz_buy`` helper column). This measures what
fraction of total market turnover is driven by rongzi (融资) buy
activity.
"""
from __future__ import annotations

import numpy as np
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
    for merging with trend episodes.

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


def compute_trading_amt_ratio(
    episodes: pd.DataFrame,
    trading_amt: pd.DataFrame,
) -> pd.DataFrame:
    """Compute rz_buy_vs_trading_amt_ratio for each trend episode.

    For each trend episode [start_date, end_date]:
      - sum_rz_buy      — Σ rz_buy over the window (from detection)
      - sum_trading_amt — SUM(trading_amount) over the window
      - rz_buy_vs_trading_amt_ratio = sum_rz_buy / sum_trading_amt

    Ratio is NULL when sum_trading_amt is unavailable or 0 (NULLIF guard).
    """
    if episodes.empty:
        return episodes

    # If no trading amount data, set ratio to NaN and return.
    if trading_amt.empty:
        episodes["rz_buy_vs_trading_amt_ratio"] = np.nan
        return episodes

    # Tag each episode with a unique index for groupby after merge.
    episodes["_ep_idx"] = np.arange(len(episodes))

    # Assign each daily trading_amount row to its episode via a BACKWARD
    # asof join on (sec_type, code) keyed by start_date, then keep only
    # rows inside [start_date, end_date]. Episodes are DISJOINT per
    # (sec_type, code) (contiguous run segmentation), so each daily row
    # maps to at most ONE episode — O(n_daily) memory instead of the
    # O(episodes × dates) explosion of a plain (sec_type, code) merge.
    #
    # SORTING CONTRACT (pandas AND cudf): merge_asof requires the ON key
    # to be GLOBALLY monotonically increasing — sorting by (by..., on)
    # is NOT sufficient and raises "left keys must be sorted". The
    # by-group matching is handled internally (hash-based), so groups
    # do NOT need to be contiguous.
    eps = episodes[["_ep_idx", "sec_type", "code", "start_date", "end_date"]].copy()
    eps["start_date"] = pd.to_datetime(eps["start_date"])
    eps["end_date"] = pd.to_datetime(eps["end_date"])
    eps = eps.sort_values("start_date").reset_index(drop=True)

    amt = trading_amt[["sec_type", "code", "date", "trading_amount"]].copy()
    amt["date"] = pd.to_datetime(amt["date"])
    amt = amt.sort_values("date").reset_index(drop=True)

    assigned = pd.merge_asof(
        amt,
        eps,
        left_on="date",
        right_on="start_date",
        by=["sec_type", "code"],
        direction="backward",
    )
    # Keep only rows whose date falls inside the matched episode's window
    # (rows in gaps between episodes, or before the first episode, drop out).
    in_range = assigned["date"] <= assigned["end_date"]
    merged = assigned[in_range]

    # Sum trading_amount per episode over the window.
    trading_amt_per_ep: pd.Series = merged.groupby("_ep_idx")["trading_amount"].sum()

    # Map back to episodes via merge (GPU-native; Series.map with a
    # Series arg falls back to CPU under cudf.pandas).
    sums = trading_amt_per_ep.rename("__sum_trading_amt").reset_index()
    episodes = episodes.merge(sums, on="_ep_idx", how="left")

    # Compute ratio with NULLIF guard (sum_trading_amt must be > 0).
    amt_safe: pd.Series = episodes["__sum_trading_amt"]
    episodes["rz_buy_vs_trading_amt_ratio"] = (
        episodes["sum_rz_buy"] / amt_safe
    ).where(amt_safe > 0)

    # Drop helper columns.
    return episodes.drop(columns=["_ep_idx", "__sum_trading_amt"])
