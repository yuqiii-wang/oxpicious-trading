"""Compute layer for analyze.pe_and_dividends.

Split by concern:
  - compute.pe         — PE logic: raw pe (invalid-value masked), annual
                         10y rolling min/max
  - compute.dividends  — dividend logic: trailing-12m DPS, dividend_yield,
                         annual var/stability/last-dividend stats

This package's ``__init__`` holds the SHARED assembly logic:
  - find_year_end_dates — year-end trading dates
  - build_detail_rows   — daily detail frame assembly
  - compute_annual_stats — annual stats orchestration (pe + dividends)

cudf.pandas conventions (B-A2 / B-A3 fixes, 2026-08-29):
  - Dates stay datetime64[us] end-to-end; python ``date`` objects are
    materialized ONLY inside ``sanitize_for_db_insert`` via its
    ``date_cols`` param (host numpy pass at the DB-write boundary).
    NEVER pre-convert object-date columns into the frame — cuDF cannot
    represent them and every subsequent frame op (even unrelated numeric
    column access) pays a MixedTypeError fast-path failure + fallback.
  - Per-row lookups (iterrows + nested reversed scans) are replaced by
    merge_asof / year-key merge / groupby aggregation — never iterrows.
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
    clean_pe,
    compute_annual_pe_extremes,
)
from analyze.pe_and_dividends.compute.dividends import (
    _running_dps_events,
    compute_trailing_12m_dps,
    compute_index_dividend_yield,
    compute_simple_dividend_yield,
    compute_annual_yield_var,
    add_annual_dividend_stats,
    compute_dividend_stability_10y,
)


# ---------------------------------------------------------------------------
#  Build the combined detail frame (the writer splits it into
#  analysis.pe / analysis.dividends)
# ---------------------------------------------------------------------------
def build_detail_rows(
    close_df: pd.DataFrame,
    pe_series: pd.Series | None,
    dividend_yield_df: pd.DataFrame,
    sec_type: str,
) -> pd.DataFrame:
    """Assemble the combined detail frame for the two split tables.

    Returns a DataFrame with columns sec_type, code, date (datetime64),
    pe, dividend_yield. NOT yet sanitized — the caller materializes
    DB rows via ``sanitize_for_db_insert(date_cols=["date"])`` so the
    datetime64 frame stays reusable for the monthly-stats compute.

    Args:
        close_df: DataFrame with columns code, date, close (and pe).
        pe_series: Series aligned to close_df's index with cleaned pe
            values (the clean_pe output).
        dividend_yield_df: DataFrame with columns code, date, dividend_yield.
        sec_type: 'index', 'etf', or 'stock'.
    """
    if close_df.empty:
        return pd.DataFrame(
            columns=["sec_type", "code", "date", "pe", "dividend_yield"]
        )

    # Start with close_df as the base
    out = close_df[["code", "date"]].copy()
    out["sec_type"] = sec_type

    # Add pe (Series assignment aligns on index — no .values copy)
    if pe_series is not None:
        out["pe"] = pe_series
    else:
        out["pe"] = np.nan

    # Add dividend_yield
    if dividend_yield_df is not None and not dividend_yield_df.empty:
        out = out.merge(
            dividend_yield_df[["code", "date", "dividend_yield"]],
            on=["code", "date"],
            how="left",
        )
    else:
        out["dividend_yield"] = np.nan

    return out[["sec_type", "code", "date", "pe", "dividend_yield"]]


# ---------------------------------------------------------------------------
#  Annual 10y rolling stats
# ---------------------------------------------------------------------------
def find_year_end_dates(dates: list[datetime.date]) -> list[datetime.date]:
    """Return the last trading date of each year from a sorted list of
    trading dates."""
    if not dates:
        return []
    year_ends: dict[int, datetime.date] = {}
    for d in dates:
        if d.year not in year_ends or d > year_ends[d.year]:
            year_ends[d.year] = d
    return sorted(year_ends.values())


def compute_annual_stats(
    detail_df: pd.DataFrame,
    pe_df: pd.DataFrame | None,
    composition_df: pd.DataFrame | None,
    stock_dividends_df: pd.DataFrame | None,
    trading_dates: list[datetime.date],
    sec_type: str,
) -> list[dict]:
    """Compute annual 10y rolling stats for analysis.pe_and_dividend_stats.

    Orchestrates the PE-side (compute.pe) and dividend-side
    (compute.dividends) annual logic over the year-end frame. The PE
    extremes and the yield var both roll over the FULL DAILY series
    (trailing ROLLING_10Y_DAYS observations) and are sampled at the
    year-end rows.

    Args:
        detail_df: DataFrame with columns sec_type, code, date (datetime64),
            pe, dividend_yield (the daily detail data — the frame
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

    # Year-end trading dates (from the MARKET-WIDE date axis, not per
    # code — a code suspended on the market year-end gets no row, as before).
    # to_dt64: aligned to the frame's datetime64[us] unit (isin requires
    # both sides to share the unit — cuDF mixed-unit fallback otherwise).
    year_end_ts = to_dt64(find_year_end_dates(trading_dates))

    # Filter detail to year-end dates (vectorized isin on datetime64 —
    # no .dt.date object conversion).
    annual = detail_df.copy()
    annual["date"] = to_dt64(annual["date"])
    annual = annual[annual["date"].isin(year_end_ts)]
    if annual.empty:
        return []

    # Sort by code, date for the merges
    annual = annual.sort_values(["code", "date"]).reset_index(drop=True)

    # ---- Rolling 10y min/max of PE (index + etf + stock) ----------------
    pe_annual = compute_annual_pe_extremes(pe_df, year_end_ts)
    if not pe_annual.empty:
        annual = annual.merge(
            pe_annual,
            on=["code", "date"],
            how="left",
        )
    else:
        annual["min_pe_10y"] = np.nan
        annual["max_pe_10y"] = np.nan

    # ---- Rolling 10y std of dividend_yield (daily series → year-end) ----
    dy_annual = compute_annual_yield_var(detail_df, year_end_ts)
    if not dy_annual.empty:
        annual = annual.merge(
            dy_annual,
            on=["code", "date"],
            how="left",
        )
    else:
        annual["dividend_var_10y"] = np.nan

    # ---- Dividend stats (stability / last-dividend + flag) --------------
    add_annual_dividend_stats(annual, composition_df, stock_dividends_df, sec_type)

    # ---- Determine is_active (latest year-end per code) -----------------
    # Vectorized groupby-transform (replaces the per-row .apply).
    annual["is_active"] = (
        annual["date"] == annual.groupby("code")["date"].transform("max")
    )

    # ---- Select final columns ------------------------------------------
    out = annual[[
        "sec_type", "code", "date", "is_active",
        "min_pe_10y", "max_pe_10y",
        "dividend_var_10y", "dividend_stability_10y",
        "last_dividend_per_share", "dividend_issued_this_year",
    ]]

    return sanitize_for_db_insert(
        out,
        numeric_cols=[
            "min_pe_10y", "max_pe_10y",
            "dividend_var_10y", "dividend_stability_10y",
            "last_dividend_per_share",
        ],
        round_to=10,
        date_cols=["date"],
    )


__all__ = [
    # PE side
    "clean_pe",
    "compute_annual_pe_extremes",
    # Dividend side
    "compute_trailing_12m_dps",
    "compute_index_dividend_yield",
    "compute_simple_dividend_yield",
    "compute_annual_yield_var",
    "compute_dividend_stability_10y",
    # Shared / orchestration
    "build_detail_rows",
    "compute_annual_stats",
    "find_year_end_dates",
]
