"""Step 6 (attach ETF amounts) and Step 7 (compute MA5 ratio)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from analyze._common import grouped_rolling_agg
from analyze.sec_alloc_perf_attribution.config import RATIO_CAP


# ---------------------------------------------------------------------------
#  Step 6: merge benchmark + subject ETF turnover
# ---------------------------------------------------------------------------
def attach_etf_amounts(
    merged: pd.DataFrame,
    etf_amount_long: pd.DataFrame,
    sec_type: str,
    subject_code: str,
) -> pd.DataFrame:
    """Attach benchmark_etf_trading_amount + code_etf_trading_amount.

    For ALL subjects: benchmark_etf_trading_amount = aggregate ETF
    turnover tracking the benchmark index on this date.

    For sec_type='index': code_etf_trading_amount = aggregate ETF
    turnover tracking the SUBJECT index (keyed on subject_code). Index
    subjects have no own row in etf_liquidity_margin, so the aggregate
    ETF turnover tracking the subject index is the correct amount.
    """
    if etf_amount_long.empty:
        merged["benchmark_etf_trading_amount"] = None
        if "code_etf_trading_amount" not in merged.columns:
            merged["code_etf_trading_amount"] = None
        return merged

    # benchmark_etf_trading_amount: merge on (date, benchmark_code=index_code).
    merged = merged.merge(
        etf_amount_long.rename(columns={
            "index_code": "benchmark_code",
            "etf_amount": "benchmark_etf_trading_amount",
        }),
        on=["date", "benchmark_code"], how="left",
    )

    # code_etf_trading_amount for index subjects: aggregate ETF turnover
    # tracking the subject index (keyed on subject_code).
    if sec_type == "index":
        subject_amt = (
            etf_amount_long[etf_amount_long["index_code"] == subject_code]
            .rename(columns={"etf_amount": "code_etf_trading_amount"})
            [["date", "code_etf_trading_amount"]]
        )
        # Drop any pre-existing column before merge (guard for determinism).
        if "code_etf_trading_amount" in merged.columns:
            merged = merged.drop(columns=["code_etf_trading_amount"])
        merged = merged.merge(subject_amt, on="date", how="left")

    return merged


# ---------------------------------------------------------------------------
#  Step 7: capped ratio + grouped rolling MA(5) via shared grouped_rolling_agg
# ---------------------------------------------------------------------------
def compute_ma5_ratio(merged: pd.DataFrame) -> pd.DataFrame:
    """Compute etf_trading_amount_ratio_benchmark_to_code_ma5.

    Mirrors the SQL GENERATED ratio logic (NULL when either amount is
    NULL or zero), PLUS a cap at |ratio| < 1e6 to match the SQL column's
    NUMERIC(10,4) limit. Then compute rolling(5).mean() per benchmark_code
    group with min_periods=1 so the first 4 days get a partial average.

    Uses the shared ``grouped_rolling_agg`` helper (Cython-compiled
    groupby.rolling().mean() — no Python lambda callback per group).
    """
    bench_amt = pd.to_numeric(
        merged["benchmark_etf_trading_amount"], errors="coerce"
    )
    code_amt = pd.to_numeric(
        merged["code_etf_trading_amount"], errors="coerce"
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        raw_ratio = bench_amt / code_amt
    merged["_ratio"] = np.where(
        bench_amt.isna() | code_amt.isna()
        | (bench_amt == 0) | (code_amt == 0)
        | (np.abs(raw_ratio) >= RATIO_CAP),
        np.nan,
        raw_ratio,
    )

    ma5 = grouped_rolling_agg(
        merged, "benchmark_code", "_ratio",
        window=5, min_periods=1, agg="mean",
    )
    merged["etf_trading_amount_ratio_benchmark_to_code_ma5"] = ma5
    merged = merged.drop(columns=["_ratio"])
    return merged
