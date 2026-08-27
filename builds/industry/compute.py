"""Pure pandas transformation logic for builds.industry (stats.industry_basic_stats).

No DB / IO dependencies — operates on in-memory DataFrames only.

GPU ACCELERATION
================

When the process-level ``cudf.pandas`` hook is active, ALL pandas
operations transparently run on GPU via cuDF. There is NO manual
``import cudf`` / ``cudf.from_pandas()`` / ``to_pandas()`` branching.

The single pandas code path handles both CPU and GPU modes. The ``date``
column must be ``datetime64[ns]`` (NOT object python dates) — object-dtype
date columns raise cuDF MixedTypeError and poison the frame into
"Fast-to-slow transfer is blocked" CPU fallbacks for every subsequent op.
Callers convert to datetime64 at load time and back to python dates only
at the DB-insert boundary.

``should_use_gpu`` is called for LOGGING ONLY.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import should_use_gpu

# OHLC fields rebased to 100 at each index's first available close.
_OHLC_FIELDS = ("open", "high", "low", "close")


def classify_pool_vectorized(stock_num: pd.Series) -> pd.Series:
    """Vectorized pool_size classification (no per-row apply/lambda).

    NULL stock_num -> "" (index only contributes to the 'all' slice,
    excluded from every bucket filter ``pool_size == bucket``).

    Thresholds:
      small  = stock_num < 51    (tight thematic indices, e.g. 中证银行 50)
      mid    = 51-180            (mid-cap baskets, e.g. CSI 100/200)
      large  = > 180             (broad baskets, e.g. CSI 300/500/800/1000)
    """
    notna = stock_num.notna()
    return pd.Series(
        np.select(
            [notna & (stock_num < 51),
             notna & (stock_num <= 180),
             notna],
            ["small", "mid", "large"],
            default="",
        ),
        index=stock_num.index,
        dtype="object",
    )


def rebase_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Rebase each code's OHLC series to 100 at its FIRST available close
    (per-index history-start anchor).

    Adds ``rebased_open`` / ``rebased_high`` / ``rebased_low`` /
    ``rebased_close`` columns. All four fields share the SINGLE per-index
    scale factor ``100 / first_close`` so the composite OHLC preserves each
    member's intraday shape on a common close-anchored scale. Indices listed
    later start at 100 on their own first date — this is the scale-invariant
    rebase convention documented in stats/13_industry_baseline.sql.

    Input columns: code, date (datetime64), open, high, low, close
    (+ others); rows are pre-filtered to close > 0. NULL open/high/low stay
    NULL (excluded from that field's mean only).
    Output: same DataFrame with the four ``rebased_*`` columns added,
    sorted by (code, date).
    """
    work = df.sort_values(["code", "date"]).reset_index(drop=True)

    # Log GPU decision for awareness (no branching).
    if should_use_gpu(work, op_type="groupby_agg"):
        print(f"    [cuDF router] {len(work):,} rows — groupby_agg (GPU-worthy)", flush=True)

    first_close = (
        work.groupby("code", as_index=False)
          .agg(first_close=("close", "first"))
    )
    work = work.merge(first_close, on="code", how="left")
    factor = 100.0 / work["first_close"]
    for field in _OHLC_FIELDS:
        work[f"rebased_{field}"] = work[field] * factor
    return work


def aggregate_by_pool(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate rebased OHLC / raw PE per (date, industry_id, pool_size).

    For each (date, industry_id) emits 4 rows — one per pool_size slice:
      'all' = every member index on that date.
      'small'/'mid'/'large' = only indices whose stock_num classifies into
                              that bucket (NULL stock_num -> 'all' only).

    Aggregates per slice (index-level):
      mean_open/high/low/close = AVG(rebased_*) — the composite index OHLC
                                 (mean_close is the former mean_price)
      var_price  = VAR(rebased_close) — cross-index dispersion (NULL when <2)
      mean_pe    = AVG(pe) — raw PE (NULL excluded by pandas mean)
      index_count = nunique(code) — close-based count

    total_trading_amount is NOT computed here — it comes from a separate
    SQL union-set aggregation over stocks (see __main__ Step 5).

    industry_label is filled from a per-industry cache (first non-empty
    label seen in df) via a merge (GPU-native; no .map(lambda) UDF).
    """
    df = df.copy()
    df["pool_size"] = classify_pool_vectorized(df["stock_num"])

    # Log GPU decision for awareness (no branching).
    if should_use_gpu(df, op_type="groupby_agg"):
        print(f"    [cuDF router] {len(df):,} rows — groupby_agg (GPU-worthy)", flush=True)

    # Cache industry_label by industry_id — label is constant per
    # industry_id, so take first non-empty. Kept as a small frame for a
    # GPU-native merge onto the result (a dict .map() lambda would force
    # a per-row CPU fallback).
    label_by_industry = (
        df[df["industry_label"].notna() & (df["industry_label"] != "")]
          .groupby("industry_id", as_index=False)
          .agg(industry_label=("industry_label", "first"))
          .rename(columns={"industry_label": "cached_label"})
    )

    agg_cols = [
        "date", "industry_id", "code", "pool_size", "pe",
        "rebased_open", "rebased_high", "rebased_low", "rebased_close",
    ]
    work = df[agg_cols].copy()

    agg_spec = {
        "mean_open": ("rebased_open", "mean"),
        "mean_high": ("rebased_high", "mean"),
        "mean_low": ("rebased_low", "mean"),
        "mean_close": ("rebased_close", "mean"),
        "var_price": ("rebased_close", "var"),
        "mean_pe": ("pe", "mean"),
        "index_count": ("code", "nunique"),
    }

    agg_rows = []

    # 'all' slice: every member index.
    all_agg = (
        work.groupby(["date", "industry_id"], as_index=False)
            .agg(**agg_spec)
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
                .agg(**agg_spec)
        )
        bucket_agg["pool_size"] = bucket
        agg_rows.append(bucket_agg)

    result = pd.concat(agg_rows, ignore_index=True)
    # Labels via merge (GPU-native); industries without a cached label
    # fall back to their id.
    result = result.merge(
        label_by_industry, on="industry_id", how="left"
    )
    result["industry_label"] = result["cached_label"].fillna(
        result["industry_id"]
    )
    result = result.drop(columns=["cached_label"])
    # var() returns NaN when only 1 member — leave as NULL in DB.
    result["var_price"] = result["var_price"].where(
        result["var_price"].notna(), other=None
    )
    return result
