"""Pure pandas/numpy transformation logic for analyze.intraday_industry_sentiments.

Inputs (from fetch.py):
  - benchmark_bars: list[dict] with keys {date, time, close, prev_close}
  - member_bars:   list[dict] with keys {member_code, member_weight,
                      industry_id, is_industry_not_strategy, industry_label,
                      date, time, close, prev_close}

Outputs (ready for bulk_upsert_async):
  - industry_rows: list[dict] for analysis.intraday_industry_market_movements
      {industry_id, date, time, benchmark_code, is_industry_not_strategy,
       benchmark_price_pct_relative_prev_date_close,
       industry_price_pct_relative_prev_date_close,
       industry_price_pct_vs_benchmark_price_pct}
  - index_rows:    list[dict] for analysis.intraday_index_market_movements
      {code, date, time, sec_type, industry_id, benchmark_code,
       code_price_pct_relative_prev_date_close}

Industry aggregate uses SIMPLE MEAN of member code_price_pct across the
industry at each (date, time) tick (per the schema comment in
10_intraday_industry_sentiments.sql). Members with NULL prev_close or NULL
close at a tick are excluded from the mean (not zeroed) — the industry's
mean at that tick reflects only members with a computable % change.

industry_price_pct_vs_benchmark_price_pct is the signed diff
(industry_pct - benchmark_pct). NULL when either input is NULL. The UI
top-plot shade is driven by this diff (green when > 0, red when < 0),
centered about the benchmark line — NOT a 0-baseline area.
"""
from __future__ import annotations

import pandas as pd


def _safe_div_minus_one(num: float | None, den: float | None) -> float | None:
    """Compute (num / den - 1) returning None on None/zero-denominator."""
    if num is None or den is None or den == 0:
        return None
    return num / den - 1.0


def compute_movements(
    benchmark_bars: list[dict],
    member_bars: list[dict],
    benchmark_code: str,
    sec_type: str = "index",
) -> tuple[list[dict], list[dict]]:
    """Build industry-aggregate (parent) + per-index (child) rows from bars.

    Returns (industry_rows, index_rows). Returns ([], []) when either input
    is empty (nothing to compute for this (benchmark, date) pair).
    """
    if not benchmark_bars or not member_bars:
        return [], []

    # ---- Benchmark series: one row per (date, time) tick with benchmark_price_pct ----
    bench_df = pd.DataFrame(benchmark_bars)
    bench_df = bench_df.dropna(subset=["date", "time"]).copy()
    bench_df["benchmark_price_pct_relative_prev_date_close"] = bench_df.apply(
        lambda r: _safe_div_minus_one(r["close"], r["prev_close"]),
        axis=1,
    )
    # Keep only (date, time) + benchmark_pct (the per-tick benchmark move is
    # the same for every industry at that tick — we broadcast it via a map).
    bench_pct_map = {
        (row["date"], row["time"]): row[
            "benchmark_price_pct_relative_prev_date_close"
        ]
        for _, row in bench_df.iterrows()
    }

    # ---- Member series: one row per (code, date, time) with code_price_pct ----
    mdf = pd.DataFrame(member_bars)
    mdf = mdf.dropna(subset=["date", "time"]).copy()
    mdf["code_price_pct_relative_prev_date_close"] = mdf.apply(
        lambda r: _safe_div_minus_one(r["close"], r["prev_close"]),
        axis=1,
    )

    # Industry-level aggregate: simple mean of code_price_pct across members
    # in the same (industry_id, date, time) group. NULL pct rows excluded
    # by pandas groupby mean (skipna=True by default).
    agg = (
        mdf.dropna(subset=["code_price_pct_relative_prev_date_close"])
        .groupby(
            ["industry_id", "date", "time"],
            as_index=False,
        )
        .agg(
            industry_price_pct_relative_prev_date_close=(
                "code_price_pct_relative_prev_date_close",
                "mean",
            ),
            is_industry_not_strategy=(
                "is_industry_not_strategy",
                "first",
            ),
        )
    )

    # ---- Build parent (industry) rows: broadcast benchmark_pct to each
    #      (industry_id, date, time) row via the dict-map. ----
    industry_rows: list[dict] = []
    for _, row in agg.iterrows():
        key = (row["date"], row["time"])
        bench_pct = bench_pct_map.get(key)
        ind_pct = row["industry_price_pct_relative_prev_date_close"]
        # Signed diff (industry - benchmark). NULL when either side is NULL.
        if bench_pct is None or ind_pct is None or pd.isna(ind_pct):
            diff_pct = None
        else:
            diff_pct = float(ind_pct) - float(bench_pct)
        industry_rows.append(
            {
                "industry_id": row["industry_id"],
                "date": row["date"],
                "time": row["time"],
                "benchmark_code": benchmark_code,
                "is_industry_not_strategy": bool(row["is_industry_not_strategy"]),
                "benchmark_price_pct_relative_prev_date_close": bench_pct,
                "industry_price_pct_relative_prev_date_close": ind_pct,
                "industry_price_pct_vs_benchmark_price_pct": diff_pct,
            }
        )

    # ---- Build child (per-index) rows: one per (code, date, time) with the
    #      code_price_pct + denormalized industry_id + benchmark_code. ----
    # Parent presence set for FK validation: (industry_id, date, time) must
    # be in the agg for a child row to be emitted.
    parent_keys = set(
        (row["industry_id"], row["date"], row["time"])
        for _, row in agg.iterrows()
    )
    index_rows: list[dict] = []
    for _, row in mdf.iterrows():
        # Only emit a child row if a corresponding parent (industry, date,
        # time) row exists — the strict composite FK requires it. Parent
        # rows were built above for every (industry, date, time) pair that
        # had at least one member with a non-NULL pct. Skip child rows
        # whose own pct is NULL (no prev_close → can't compute).
        pct = row["code_price_pct_relative_prev_date_close"]
        if pct is None or pd.isna(pct):
            continue
        industry_id = row["industry_id"]
        key = (industry_id, row["date"], row["time"])
        if key not in parent_keys:
            continue
        index_rows.append(
            {
                "code": row["member_code"],
                "date": row["date"],
                "time": row["time"],
                "sec_type": sec_type,
                "industry_id": industry_id,
                "benchmark_code": benchmark_code,
                "code_price_pct_relative_prev_date_close": pct,
            }
        )

    return industry_rows, index_rows
