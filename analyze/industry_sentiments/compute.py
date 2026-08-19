"""Pure pandas transformation logic for analyze.industry_sentiments.

No DB / IO dependencies — operates on in-memory DataFrames only.

GPU ACCELERATION
================

When the process-level ``cudf.pandas`` hook is active, ALL pandas
operations transparently run on GPU via cuDF. There is NO manual
``import cudf`` / ``cudf.from_pandas()`` / ``to_pandas()`` branching.

The single pandas code path handles both CPU and GPU modes. Object-dtype
columns (python ``date``, string ``industry_label``) are dropped before
GPU-sensitive operations and reattached after — cudf.pandas cannot
handle object dtypes, but transparently accelerates all numeric ops.

``should_use_gpu`` is called for LOGGING ONLY.
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import should_use_gpu
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

    # Log GPU decision for awareness (no branching).
    if should_use_gpu(df, op_type="groupby_agg"):
        print(f"    [cuDF router] {len(df):,} rows — groupby_agg (GPU-worthy)", flush=True)

    # Object-dtype ``date`` can't go through cuDF. Drop for the
    # groupby+merge, reattach after.
    date_col = df["date"].copy()
    work = df.drop(columns=["date"])

    first_close = (
        work.groupby("code", as_index=False)
          .agg(first_close=("close", "first"))
    )
    work = work.merge(first_close, on="code", how="left")
    work["rebased"] = (work["close"] / work["first_close"]) * 100.0
    work["date"] = date_col.values
    return work


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

    # Log GPU decision for awareness (no branching).
    if should_use_gpu(df, op_type="groupby_agg"):
        print(f"    [cuDF router] {len(df):,} rows — groupby_agg (GPU-worthy)", flush=True)

    # Cache industry_label by industry_id — label is constant per
    # industry_id, so take first non-empty.
    label_by_industry = (
        df[df["industry_label"].notna() & (df["industry_label"] != "")]
          .groupby("industry_id")["industry_label"].first().to_dict()
    )

    # Object-dtype columns can't go through cuDF. Keep only numeric columns
    # for the GPU-sensitive groupby+agg.
    agg_cols = ["date", "industry_id", "code", "rebased", "pe", "pool_size"]
    work = df[agg_cols].copy()

    agg_rows = []

    # 'all' slice: every member index.
    all_agg = (
        work.groupby(["date", "industry_id"], as_index=False)
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
        sub = work[work["pool_size"] == bucket]
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
    # var() returns NaN when only 1 member — leave as NULL in DB.
    result["var_price"] = result["var_price"].where(
        result["var_price"].notna(), other=None
    )
    return result
