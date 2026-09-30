"""Dividend-yield compute logic for the ``dividends`` submodule of
analyze.pe_and_dividends.

Covers:
  - trailing-12m DPS: event-based rolling 365d sum (+dps on ex_date,
    -dps on ex_date+365d, cumsum, merge_asof to trading dates)
  - index dividend_yield: weighted sum of constituent trailing-12m DPS / close
  - etf/stock dividend_yield: trailing-12m DPS / close (merge_asof, no grid)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import host_array, host_unique, to_dt64
from analyze.pe_and_dividends.dividends.config import TRAILING_DIVIDEND_DAYS

# Minimum representable dividend_yield at the detail table's NUMERIC(10,6)
# storage precision: a value below half of 1e-6 rounds to stored 0.000000,
# and the schema treats exact 0.0 as NO DATA (a yield is strictly positive
# whenever a dividend exists). Null these micro-quotients instead so the
# stored value is never an exact zero — otherwise the incremental
# self-heal (which re-upserts zero rows) would flag them forever.
MIN_STORED_YIELD = 5e-7


# ---------------------------------------------------------------------------
#  trailing-12m DPS — event-based rolling 365d sum per stock
# ---------------------------------------------------------------------------
def _running_dps_events(dividends_df: pd.DataFrame) -> pd.DataFrame:
    """Build the running trailing-12m DPS event stream per stock.

    Event-based approach:
      - +dps event on ex_dividend_date (dividend enters the 365d window)
      - -dps event on ex_dividend_date + 365d (dividend leaves the window)
      - cumsum per stock = running trailing-12m DPS

    Args:
        dividends_df: DataFrame with columns code, ex_dividend_date
            (datetime64), dividend_per_share_pre_tax.

    Returns:
        DataFrame with columns code, date (datetime64, globally sortable),
        running_dps — sorted by (code, date).
    """
    dps_col = "dividend_per_share_pre_tax"
    plus = dividends_df[["code", "ex_dividend_date", dps_col]].rename(
        columns={"ex_dividend_date": "date", dps_col: "delta"}
    )
    # Vectorized +365d exit event (no per-row apply). int64-seconds
    # arithmetic — datetime64 + Timedelta is a cuDF fast-path fallback.
    minus = plus.copy()
    minus["date"] = pd.to_datetime(
        minus["date"].astype("datetime64[s]").astype("int64")
        + TRAILING_DIVIDEND_DAYS * 86400,
        unit="s",
    )
    # pd.to_datetime(..., unit="s") yields datetime64[ns] while `plus` holds
    # [us] — concat of mixed units is a cuDF fallback ("All columns must be
    # the same type"). Cast to the source dtype before concat.
    minus["date"] = minus["date"].astype(plus["date"].dtype)
    minus["delta"] = -minus["delta"]

    events = pd.concat([plus, minus], ignore_index=True)
    events["date"] = to_dt64(events["date"])
    events = events.sort_values(["code", "date"])
    events["running_dps"] = events.groupby("code", sort=False)["delta"].cumsum()
    return events[["code", "date", "running_dps"]]


def compute_trailing_12m_dps(
    dividends_df: pd.DataFrame,
    trading_dates: list,
) -> pd.DataFrame:
    """Compute trailing-12m dividend per share per stock per trading date.

    Forward-fills the running DPS event stream to a full
    (code × trading_date) grid via merge_asof. Used by the INDEX path,
    where every constituent must contribute on every index trading date
    (even when the constituent itself is suspended). The stock/etf path
    uses ``compute_simple_dividend_yield`` which merge_asof's directly
    against close rows (no grid, identical results).

    Args:
        dividends_df: DataFrame with columns code, ex_dividend_date,
            dividend_per_share_pre_tax.
        trading_dates: list of date objects (the date axis).

    Returns:
        DataFrame with columns: code, date (datetime64), trailing_dps.
        One row per (stock_code, trading_date). Only includes stocks
        that have at least one dividend event.
    """
    if dividends_df.empty or not trading_dates:
        return pd.DataFrame(columns=["code", "date", "trailing_dps"])

    events = _running_dps_events(dividends_df)
    if events.empty:
        return pd.DataFrame(columns=["code", "date", "trailing_dps"])

    # Date grid: one row per (code, trading_date). [us] unit — merge_asof
    # requires EXACTLY matching dtypes with the event stream. Built with
    # raw numpy repeat/tile (same product ordering as the former
    # MultiIndex.from_product, which logs a cudf fallback per call).
    codes = host_unique(events["code"])
    td = host_array(to_dt64(sorted(trading_dates)))
    date_grid = pd.DataFrame({
        "code": np.repeat(codes, td.size),
        "date": np.tile(td, len(codes)),
    })

    # merge_asof requires the `on` column to be globally sorted.
    # Sort both DataFrames by date (the `on` key) — the `by` key (code)
    # handles the group-wise matching internally.
    result = pd.merge_asof(
        date_grid.sort_values("date"),
        events.sort_values("date"),
        on="date",
        by="code",
        direction="backward",
    )

    # Fill NaN running_dps with 0 (no dividends in window yet)
    result["running_dps"] = result["running_dps"].fillna(0.0)

    return result[["code", "date", "running_dps"]].rename(
        columns={"running_dps": "trailing_dps"}
    )


# ---------------------------------------------------------------------------
#  Index dividend_yield — cap-weighted average of constituent yields
# ---------------------------------------------------------------------------
def compute_index_dividend_yield(
    composition_df: pd.DataFrame,
    stock_dps_df: pd.DataFrame,
    stock_close_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compute dividend_yield for each index code per date.

    dividend_yield[index_code, date] = SUM_s ( weight_fraction_s × dps_s,t / close_s,t )

    the cap-weighted average of the constituents' own trailing-12m yields
    (numerator and denominator are both per-share quantities, so the
    result is a true yield).

    UNIT NOTE (the reason stock closes are required): dividing the
    weighted DPS (CNY per share of the average constituent) by the index
    CLOSE (index POINTS) — the former formula — produces values ~100x
    too small (the SSE composite showed ~0.05% instead of ~2.5%):
    capitalization weights do not convert per-share CNY into
    index-point CNY. Averaging the constituents' own dps/close ratios
    keeps every term per-share and the weights only express composition.

    Args:
        composition_df: DataFrame with columns index_code, stock_code,
            weight_pct (the LATEST snapshot; stock_code suffix-stripped —
            the caller normalizes).
        stock_dps_df: DataFrame with columns code (suffix-stripped), date,
            trailing_dps (constituent trailing-12m DPS).
        stock_close_df: DataFrame with columns code (suffix-stripped),
            date, close — the constituents' own close prices.

    Returns:
        DataFrame with columns: code (index_code), date, dividend_yield.
        One row per (index, date) where at least one constituent has both
        a trailing DPS and a close.
    """
    if composition_df.empty or stock_dps_df.empty or stock_close_df.empty:
        return pd.DataFrame(columns=["code", "date", "dividend_yield"])

    comp = composition_df.copy()
    comp["weight_fraction"] = comp["weight_pct"] / 100.0

    # Join constituent DPS with composition weights, then with the
    # constituent's own close (all keys are suffix-stripped codes).
    merged = stock_dps_df.merge(
        comp[["index_code", "stock_code", "weight_fraction"]],
        left_on="code",
        right_on="stock_code",
        how="inner",
    )
    merged = merged.merge(
        stock_close_df.rename(columns={"close": "stock_close"}),
        on=["code", "date"],
        how="inner",
    )
    merged["weighted_yield"] = (
        merged["weight_fraction"]
        * merged["trailing_dps"]
        / merged["stock_close"]
    )

    index_dy = (
        merged.groupby(["index_code", "date"], sort=False)["weighted_yield"]
        .sum()
        .reset_index()
        .rename(columns={"index_code": "code", "weighted_yield": "dividend_yield"})
    )
    # Strictly-positive gate: every term is >= 0, so a sum of exactly 0
    # means NO constituent had a dividend in its trailing window (the
    # source dividend history has limited depth) — that is NO DATA, not a
    # 0% yield. NULL it (same semantics as the stock/etf dps > 0 gate),
    # along with quotients below the NUMERIC(10,6) storage floor.
    index_dy["dividend_yield"] = index_dy["dividend_yield"].where(
        index_dy["dividend_yield"] >= MIN_STORED_YIELD
    )
    return index_dy[["code", "date", "dividend_yield"]]


# ---------------------------------------------------------------------------
#  ETF/Stock dividend_yield — trailing-12m DPS / close
# ---------------------------------------------------------------------------
def compute_simple_dividend_yield(
    close_df: pd.DataFrame,
    dividends_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compute dividend_yield for ETFs or stocks (no composition weighting).

    trailing_12m_dps = event-based rolling 365d sum of dividends per share
    dividend_yield = trailing_12m_dps / close

    The running DPS event stream is merge_asof'd DIRECTLY against the
    close rows (per security, backward as-of) — no (code × date) grid is
    materialized. Rows dropped for suspension gaps are exactly the rows
    close_df lacks, so results are identical to the former grid approach
    at a fraction of the cost (no 30M-row grid for the stock universe).

    For ETFs, pass dividends_df with columns: code, ex_dividend_date (from
    etf_adjustment.date where implied_dividend_per_share > 0), dividend_per_share_pre_tax
    (from etf_adjustment.implied_dividend_per_share).

    For stocks, pass dividends_df from stats.stock_dividends directly.

    Args:
        close_df: DataFrame with columns code, date (datetime64), close.
        dividends_df: DataFrame with columns code, ex_dividend_date,
            dividend_per_share_pre_tax.

    Returns:
        DataFrame with columns: code, date, dividend_yield.
    """
    if close_df.empty:
        return pd.DataFrame(columns=["code", "date", "dividend_yield"])

    if dividends_df is None or dividends_df.empty:
        result = close_df[["code", "date"]].copy()
        result["dividend_yield"] = np.nan
        return result

    events = _running_dps_events(dividends_df)
    if events.empty:
        result = close_df[["code", "date"]].copy()
        result["dividend_yield"] = np.nan
        return result

    # As-of join: latest event <= each (code, date) close row.
    left = close_df[["code", "date", "close"]].sort_values("date")
    merged = pd.merge_asof(
        left,
        events.sort_values("date"),
        on="date",
        by="code",
        direction="backward",
    )
    dps = merged["running_dps"].fillna(0.0)
    close = merged["close"]
    with np.errstate(divide="ignore", invalid="ignore"):
        quot = np.where(close > 0, dps / close, np.nan)
    merged["dividend_yield"] = np.where(quot >= MIN_STORED_YIELD, quot, np.nan)

    return merged[["code", "date", "dividend_yield"]]
