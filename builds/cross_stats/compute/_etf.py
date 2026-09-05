"""Pair-grain ETF amounts + capped ratio + grouped rolling MA5.

MA5 uses the shared ``grouped_rolling_agg`` helper (groupby.rolling().mean()
— no python callback per group; runs on GPU under cudf.pandas).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg
from builds.cross_stats.config import RATIO_CAP


def attach_etf_amounts(
    merged: pd.DataFrame,
    etf_amount_long: pd.DataFrame,
    sec_type: str,
    subject_code: str,
) -> pd.DataFrame:
    """Attach benchmark_etf_trading_amount + code_etf_trading_amount.

    ALL subjects: benchmark_etf_trading_amount = aggregate ETF turnover
    tracking the benchmark index on this date. sec_type='index':
    code_etf_trading_amount = aggregate ETF turnover tracking the SUBJECT
    index (index subjects have no own etf_liquidity_margin row).
    """
    if etf_amount_long.empty:
        merged["benchmark_etf_trading_amount"] = np.nan
        if "code_etf_trading_amount" not in merged.columns:
            merged["code_etf_trading_amount"] = np.nan
        return merged

    merged = merged.merge(
        etf_amount_long.rename(columns={
            "index_code": "benchmark_code",
            "etf_amount": "benchmark_etf_trading_amount",
        }),
        on=["date", "benchmark_code"], how="left",
    )

    if sec_type == "index":
        subject_amt = (
            etf_amount_long[etf_amount_long["index_code"] == subject_code]
            .rename(columns={"etf_amount": "code_etf_trading_amount"})
            [["date", "code_etf_trading_amount"]]
        )
        if "code_etf_trading_amount" in merged.columns:
            merged = merged.drop(columns=["code_etf_trading_amount"])
        merged = merged.merge(subject_amt, on="date", how="left")

    return merged


def compute_ma5_ratio(merged: pd.DataFrame) -> pd.DataFrame:
    """etf_trading_amount_ratio_benchmark_to_code + its MA5.

    Ratio NULL when either amount is NULL/zero or |ratio| >= RATIO_CAP
    (the NUMERIC(10,4) limit — the cap is applied BEFORE the MA5 window
    so value and MA stay consistent). MA5 = rolling(5, min_periods=1)
    mean per benchmark_code group within the subject frame (all rows in
    one subject frame share the subject code, so benchmark_code alone is
    the full (code, sec_type, benchmark_code) partition).
    """
    bench_amt = pd.to_numeric(
        merged["benchmark_etf_trading_amount"], errors="coerce"
    )
    code_amt = pd.to_numeric(
        merged["code_etf_trading_amount"], errors="coerce"
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        raw_ratio = bench_amt / code_amt
    merged["etf_trading_amount_ratio_benchmark_to_code"] = np.where(
        bench_amt.isna() | code_amt.isna()
        | (bench_amt == 0) | (code_amt == 0)
        | (np.abs(raw_ratio) >= RATIO_CAP),
        np.nan,
        raw_ratio,
    )

    ma5 = grouped_rolling_agg(
        merged, "benchmark_code",
        "etf_trading_amount_ratio_benchmark_to_code",
        window=5, min_periods=1, agg="mean",
    )
    merged["etf_trading_amount_ratio_benchmark_to_code_ma5"] = ma5
    return merged
