"""Pure pandas transformation logic for analyze.industry_sentiments.

No DB / IO dependencies — operates on in-memory DataFrames only.

GPU acceleration: when the cuDF router determines the GPU is worthwhile
for the row count (groupby_agg op_type for the groupby+agg steps,
elementwise op_type for the rebase division), the computation runs on
cuDF and is brought back to pandas once at the end. The CPU path
(pandas Cython) is always available as a fallback.
"""
from __future__ import annotations

import pandas as pd

from analyze._common._cuDF import should_use_gpu
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

    GPU acceleration: when the cuDF router determines the GPU is
    worthwhile for this row count (groupby_agg op_type — the groupby
    + agg first_close + merge + elementwise division is ~2s/M rows on
    CPU), the entire sequence runs on a cuDF DataFrame and is brought
    back to pandas once at the end. The H2D/D2H transfer is amortized
    over all three operations.
    """
    df = df.sort_values(["code", "date"]).reset_index(drop=True)

    # GPU path: cuDF groupby + agg + merge + elementwise.
    if should_use_gpu(df, op_type="groupby_agg"):
        import cudf  # type: ignore[import-untyped]
        # cuDF can't handle object-dtype ``date`` columns (python date
        # objects). Drop it for the GPU pass and restore after — date is
        # only needed for sorting (already done above).
        date_col = df["date"].copy()
        work = df.drop(columns=["date"])
        gdf = cudf.from_pandas(work)
        # groupby + agg first_close per code.
        g_first = gdf.groupby("code", as_index=False)["close"].first()
        g_first = g_first.rename(columns={"close": "first_close"})
        gdf = gdf.merge(g_first, on="code", how="left")
        gdf["rebased"] = (gdf["close"] / gdf["first_close"]) * 100.0
        result = gdf.to_pandas()
        result["date"] = date_col.values
        return result

    # CPU path (pandas Cython).
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

    GPU acceleration: when the cuDF router determines the GPU is
    worthwhile for this row count (groupby_agg op_type), the groupby +
    agg operations run on cuDF. The 'all' slice and each per-bucket
    slice are computed on GPU and brought back to pandas for the final
    concat + industry_label map. The per-bucket filter + groupby pattern
    benefits from GPU's parallel hash aggregate.
    """
    df = df.copy()
    df["pool_size"] = df["stock_num"].apply(classify_pool)

    # Cache industry_label by industry_id — label is constant per
    # industry_id, so take first non-empty.
    label_by_industry = (
        df[df["industry_label"].notna() & (df["industry_label"] != "")]
          .groupby("industry_id")["industry_label"].first().to_dict()
    )

    # ---- GPU path ----
    # The groupby+agg pattern is the dominant cost. cuDF's hash aggregate
    # is ~25× faster than pandas for this operation. The concat + map
    # at the end stays on CPU (small, cheap).
    use_gpu = should_use_gpu(df, op_type="groupby_agg")

    if use_gpu:
        import cudf  # type: ignore[import-untyped]
        # cuDF can't handle object-dtype ``date`` columns (python date
        # objects) or ``industry_label`` (string with NaN). Transfer only
        # the numeric + category columns needed for the aggregate.
        # Keep date as a separate column for later reattachment.
        date_col = df["date"].copy()
        agg_cols = ["date", "industry_id", "code", "rebased", "pe", "pool_size"]
        work = df[agg_cols].copy()
        gdf = cudf.from_pandas(work)

        def _gpu_agg(g: "cudf.DataFrame") -> "cudf.DataFrame":
            """Run the groupby+agg on GPU, return a cuDF DataFrame."""
            return g.groupby(["date", "industry_id"], as_index=False).agg(
                mean_price=("rebased", "mean"),
                var_price=("rebased", "var"),
                mean_pe=("pe", "mean"),
                index_count=("code", "nunique"),
            )

        agg_rows = []

        # 'all' slice: every member index.
        g_all = _gpu_agg(gdf)
        g_all["pool_size"] = "all"
        agg_rows.append(g_all.to_pandas())

        # Per-bucket slices: filter by pool_size, then aggregate.
        for bucket in ["small", "mid", "large"]:
            g_sub = gdf[gdf["pool_size"] == bucket]
            if len(g_sub) == 0:
                continue
            g_bucket = _gpu_agg(g_sub)
            g_bucket["pool_size"] = bucket
            agg_rows.append(g_bucket.to_pandas())

        result = pd.concat(agg_rows, ignore_index=True)

    else:
        # CPU path (pandas Cython).
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
