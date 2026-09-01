"""Compute layer for analyze.pe_and_dividends.

Split by concern:
  - compute.pe         — PE logic: pe_ma20, monthly 5y rolling min/max
  - compute.dividends  — dividend logic: trailing-12m DPS, dividend_yield,
                         monthly var/stability/last-dividend stats

This package's ``__init__`` holds the SHARED assembly logic:
  - find_month_end_dates — month-end trading dates
  - build_detail_rows    — daily detail frame assembly
  - compute_monthly_stats — monthly stats orchestration (pe + dividends)

cudf.pandas conventions (B-A2 / B-A3 fixes, 2026-08-29):
  - Dates stay datetime64[us] end-to-end; python ``date`` objects are
    materialized ONLY inside ``sanitize_for_db_insert`` via its
    ``date_cols`` param (host numpy pass at the DB-write boundary).
    NEVER pre-convert object-date columns into the frame — cuDF cannot
    represent them and every subsequent frame op (even unrelated numeric
    column access) pays a MixedTypeError fast-path failure + fallback.
  - Per-row lookups (iterrows + nested reversed scans) are replaced by
    merge_asof / month-key merge / groupby aggregation — never iterrows.
  - Grouped rolling via the shared ``grouped_rolling_agg`` helper (single
    pandas path; cudf.pandas routes to GPU transparently at volume).
"""
from __future__ import annotations

import datetime

import numpy as np
import pandas as pd

from _common.df_utils import to_dt64

from analyze._common.sanitize import sanitize_for_db_insert
from analyze.pe_and_dividends.compute.pe import (
    compute_pe_ma20,
    compute_monthly_pe_extremes,
)
from analyze.pe_and_dividends.compute.dividends import (
    _running_dps_events,
    compute_trailing_12m_dps,
    compute_index_dividend_yield,
    compute_simple_dividend_yield,
    add_monthly_dividend_stats,
    compute_dividend_stability_5y,
)


# ---------------------------------------------------------------------------
#  Build detail rows for analysis.pe_and_dividends
# ---------------------------------------------------------------------------
def build_detail_rows(
    close_df: pd.DataFrame,
    pe_ma20_series: pd.Series | None,
    dividend_yield_df: pd.DataFrame,
    sec_type: str,
) -> pd.DataFrame:
    """Assemble the final detail frame for analysis.pe_and_dividends.

    Returns a DataFrame with columns sec_type, code, date (datetime64),
    pe_ma20, dividend_yield. NOT yet sanitized — the caller materializes
    DB rows via ``sanitize_for_db_insert(date_cols=["date"])`` so the
    datetime64 frame stays reusable for the monthly-stats compute.

    Args:
        close_df: DataFrame with columns code, date, close (and optionally pe).
        pe_ma20_series: Series aligned to close_df's index with pe_ma20 values.
            None for etf/stock.
        dividend_yield_df: DataFrame with columns code, date, dividend_yield.
        sec_type: 'index', 'etf', or 'stock'.
    """
    if close_df.empty:
        return pd.DataFrame(
            columns=["sec_type", "code", "date", "pe_ma20", "dividend_yield"]
        )

    # Start with close_df as the base
    out = close_df[["code", "date"]].copy()
    out["sec_type"] = sec_type

    # Add pe_ma20 (Series assignment aligns on index — no .values copy)
    if pe_ma20_series is not None:
        out["pe_ma20"] = pe_ma20_series
    else:
        out["pe_ma20"] = np.nan

    # Add dividend_yield
    if dividend_yield_df is not None and not dividend_yield_df.empty:
        out = out.merge(
            dividend_yield_df[["code", "date", "dividend_yield"]],
            on=["code", "date"],
            how="left",
        )
    else:
        out["dividend_yield"] = np.nan

    return out[["sec_type", "code", "date", "pe_ma20", "dividend_yield"]]


# ---------------------------------------------------------------------------
#  Monthly 5y rolling stats
# ---------------------------------------------------------------------------
def find_month_end_dates(dates: list[datetime.date]) -> list[datetime.date]:
    """Return the last trading date of each month from a sorted list of
    trading dates."""
    if not dates:
        return []
    month_ends: dict[tuple[int, int], datetime.date] = {}
    for d in dates:
        key = (d.year, d.month)
        if key not in month_ends or d > month_ends[key]:
            month_ends[key] = d
    return sorted(month_ends.values())


def compute_monthly_stats(
    detail_df: pd.DataFrame,
    pe_df: pd.DataFrame | None,
    composition_df: pd.DataFrame | None,
    stock_dividends_df: pd.DataFrame | None,
    trading_dates: list[datetime.date],
    sec_type: str,
) -> list[dict]:
    """Compute monthly 5y rolling stats for analysis.pe_and_dividend_stats.

    Orchestrates the PE-side (compute.pe) and dividend-side
    (compute.dividends) monthly logic over the month-end frame.

    Args:
        detail_df: DataFrame with columns sec_type, code, date (datetime64),
            pe_ma20, dividend_yield (the daily detail data — the frame
            returned by build_detail_rows, NOT sanitized DB dicts).
        pe_df: DataFrame with columns code, date, pe (raw PE from
            index_valuation). None for etf/stock.
        composition_df: DataFrame with columns index_code, stock_code,
            weight_pct. None for etf/stock.
        stock_dividends_df: DataFrame with columns code, ex_dividend_date,
            dividend_per_share_pre_tax. None for etf/stock (use the same
            dividends data for stock sec_type).
        trading_dates: list of all trading dates.
        sec_type: 'index', 'etf', or 'stock'.

    Returns:
        List of dicts suitable for bulk insert into
        analysis.pe_and_dividend_stats (dates as python date objects).
    """
    if detail_df.empty:
        return []

    # Month-end trading dates (from the MARKET-WIDE date axis, not per
    # code — a code suspended on the market month-end gets no row, as before).
    # to_dt64: aligned to the frame's datetime64[us] unit (isin requires
    # both sides to share the unit — cuDF mixed-unit fallback otherwise).
    month_end_ts = to_dt64(find_month_end_dates(trading_dates))

    # Filter detail to month-end dates (vectorized isin on datetime64 —
    # no .dt.date object conversion).
    monthly = detail_df.copy()
    monthly["date"] = to_dt64(monthly["date"])
    monthly = monthly[monthly["date"].isin(month_end_ts)]
    if monthly.empty:
        return []

    # Sort by code, date for rolling computations
    monthly = monthly.sort_values(["code", "date"]).reset_index(drop=True)

    # ---- Rolling 5y min/max of PE (index + etf + stock) -----------------
    pe_monthly = compute_monthly_pe_extremes(pe_df, month_end_ts)
    if not pe_monthly.empty:
        monthly = monthly.merge(
            pe_monthly,
            on=["code", "date"],
            how="left",
        )
    else:
        monthly["min_pe_5y"] = np.nan
        monthly["max_pe_5y"] = np.nan

    # ---- Dividend stats (var / stability / last-dividend + flag) --------
    add_monthly_dividend_stats(monthly, composition_df, stock_dividends_df, sec_type)

    # ---- Determine is_active (latest month-end per code) ---------------
    # Vectorized groupby-transform (replaces the per-row .apply).
    monthly["is_active"] = (
        monthly["date"] == monthly.groupby("code")["date"].transform("max")
    )

    # ---- Select final columns ------------------------------------------
    out = monthly[[
        "sec_type", "code", "date", "is_active",
        "min_pe_5y", "max_pe_5y",
        "dividend_var_5y", "dividend_stability_5y",
        "last_dividend_per_share", "dividend_issued_this_month",
    ]]

    return sanitize_for_db_insert(
        out,
        numeric_cols=[
            "min_pe_5y", "max_pe_5y",
            "dividend_var_5y", "dividend_stability_5y",
            "last_dividend_per_share",
        ],
        round_to=10,
        date_cols=["date"],
    )


__all__ = [
    # PE side
    "compute_pe_ma20",
    "compute_monthly_pe_extremes",
    # Dividend side
    "compute_trailing_12m_dps",
    "compute_index_dividend_yield",
    "compute_simple_dividend_yield",
    "compute_dividend_stability_5y",
    # Shared / orchestration
    "build_detail_rows",
    "compute_monthly_stats",
    "find_month_end_dates",
]
