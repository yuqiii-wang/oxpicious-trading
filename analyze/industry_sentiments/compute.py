"""Pure pandas transformation logic for analyze.industry_sentiments.

No DB / IO dependencies — operates on in-memory DataFrames only.
"""
from __future__ import annotations

import pandas as pd

from analyze.industry_sentiments.helpers import classify_pool


def rebase_closes(df: pd.DataFrame) -> pd.DataFrame:
    """Rebase each code's close series to 100 at its FIRST available close
    (per-index history-start anchor).

    Adds a ``rebased`` column = (close / first_close) * 100.0.
    Indices listed later start at 100 on their own first date — this is
    the scale-invariant rebase convention documented in
    05_industry_sentiments.sql.

    Input columns: code, date, close (+ others).
    Output: same DataFrame with ``rebased`` column added, sorted by
    (code, date).
    """
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    first_close = (
        df.groupby("code", as_index=False)
          .agg(first_close=("close", "first"))
    )
    df = df.merge(first_close, on="code", how="left")
    df["rebased"] = (df["close"] / df["first_close"]) * 100.0
    return df


def aggregate_by_pool(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate rebased prices / raw PE per (date, industry_id, pool_size).

    For each (date, industry_id) emits 4 rows — one per pool_size slice:
      'all' = every member index on that date.
      'small'/'mid'/'large' = only indices whose stock_num classifies into
                              that bucket (NULL stock_num -> 'all' only).

    Aggregates per slice (index-level):
      mean_price  = AVG(rebased)  — rebased-to-100 close
      var_price   = VAR(rebased)  — cross-index dispersion (NULL when <2)
      mean_pe     = AVG(pe)       — raw PE (NULL excluded by pandas mean)
      index_count = nunique(code) — close-based count

    total_trading_amount is NOT computed here — it comes from a separate
    SQL union-set aggregation over stocks (see __main__ Step 5).

    industry_label is filled from a per-industry cache (first non-empty
    label seen in df).
    """
    df = df.copy()
    df["pool_size"] = df["stock_num"].apply(classify_pool)

    # Cache industry_label by industry_id — label is constant per
    # industry_id, so take first non-empty.
    label_by_industry = (
        df[df["industry_label"].notna() & (df["industry_label"] != "")]
          .groupby("industry_id")["industry_label"].first().to_dict()
    )

    agg_rows = []

    # 'all' slice: every member index.
    all_agg = (
        df.groupby(["date", "industry_id"], as_index=False)
          .agg(
              mean_price=("rebased", "mean"),
              var_price=("rebased", "var"),
              mean_pe=("pe", "mean"),
              index_count=("code", "nunique"),
          )
    )
    all_agg["pool_size"] = "all"
    agg_rows.append(all_agg)

    # Per-bucket slices: filter by pool_size, then aggregate.
    for bucket in ["small", "mid", "large"]:
        sub = df[df["pool_size"] == bucket]
        if sub.empty:
            continue
        bucket_agg = (
            sub.groupby(["date", "industry_id"], as_index=False)
               .agg(
                   mean_price=("rebased", "mean"),
                   var_price=("rebased", "var"),
                   mean_pe=("pe", "mean"),
                   index_count=("code", "nunique"),
               )
        )
        bucket_agg["pool_size"] = bucket
        agg_rows.append(bucket_agg)

    result = pd.concat(agg_rows, ignore_index=True)
    result["industry_label"] = result["industry_id"].map(
        lambda iid: label_by_industry.get(iid, iid)
    )
    # var() returns NaN when only 1 member (can't compute variance) —
    # leave as NULL in DB.
    result["var_price"] = result["var_price"].where(
        result["var_price"].notna(), other=None
    )
    return result
