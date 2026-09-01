"""Dividend-side compute logic for analyze.pe_and_dividends.

Covers:
  - trailing-12m DPS: event-based rolling 365d sum (+dps on ex_date,
    -dps on ex_date+365d, cumsum, merge_asof to trading dates)
  - index dividend_yield: weighted sum of constituent trailing-12m DPS / close
  - etf/stock dividend_yield: trailing-12m DPS / close (merge_asof, no grid)
  - monthly dividend stats: rolling 5y population std of dividend_yield
    (x100 as percentage), dividend_stability_5y (CV-based, frequency-robust),
    last_dividend_per_share + dividend_issued_this_month (vectorized as-of /
    month-key merges — never iterrows)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import to_dt64
from _common.df_utils.rolling import grouped_rolling_agg
from analyze.pe_and_dividends.config import (
    TRAILING_DIVIDEND_DAYS,
    ROLLING_5Y_DAYS,
    STABILITY_WINDOW_YEARS,
)


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
    # requires EXACTLY matching dtypes with the event stream.
    codes = events["code"].unique()
    td = to_dt64(sorted(trading_dates))
    date_grid = pd.MultiIndex.from_product(
        [codes, td], names=["code", "date"]
    ).to_frame(index=False)

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
#  Index dividend_yield — weighted sum of constituent DPS / close
# ---------------------------------------------------------------------------
def compute_index_dividend_yield(
    close_df: pd.DataFrame,
    composition_df: pd.DataFrame,
    stock_dps_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compute dividend_yield for each index code per date.

    index_dps[index_code, date] = SUM(weight_fraction × stock_trailing_dps)
    dividend_yield = index_dps / close

    Args:
        close_df: DataFrame with columns code, date, close (index data).
        composition_df: DataFrame with columns index_code, stock_code,
            weight_pct.
        stock_dps_df: DataFrame with columns code, date, trailing_dps
            (constituent stock trailing-12m DPS).

    Returns:
        DataFrame with columns: code, date, dividend_yield.
    """
    if close_df.empty or composition_df.empty or stock_dps_df.empty:
        return pd.DataFrame(columns=["code", "date", "dividend_yield"])

    # Prepare composition: weight_fraction = weight_pct / 100
    comp = composition_df.copy()
    comp["weight_fraction"] = comp["weight_pct"] / 100.0

    # Join stock DPS with composition on stock_code
    merged = stock_dps_df.merge(
        comp[["index_code", "stock_code", "weight_fraction"]],
        left_on="code",
        right_on="stock_code",
        how="inner",
    )
    merged["weighted_dps"] = merged["weight_fraction"] * merged["trailing_dps"]

    # Group by (index_code, date) and sum
    index_dps = merged.groupby(["index_code", "date"], sort=False)["weighted_dps"].sum().reset_index()
    index_dps = index_dps.rename(columns={"index_code": "code", "weighted_dps": "index_dps"})

    # Join with close and compute dividend_yield
    result = close_df.merge(index_dps, on=["code", "date"], how="left")
    result["dividend_yield"] = np.where(
        (result["close"].notna()) & (result["close"] > 0) & (result["index_dps"].notna()),
        result["index_dps"] / result["close"],
        np.nan,
    )

    return result[["code", "date", "dividend_yield"]]


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
    merged["dividend_yield"] = np.where(
        close.notna() & (close > 0) & (dps > 0),
        dps / close,
        np.nan,
    )

    return merged[["code", "date", "dividend_yield"]]


# ---------------------------------------------------------------------------
#  Monthly dividend stats (added IN PLACE to the month-end frame)
# ---------------------------------------------------------------------------
def add_monthly_dividend_stats(
    monthly: pd.DataFrame,
    composition_df: pd.DataFrame | None,
    stock_dividends_df: pd.DataFrame | None,
    sec_type: str,
) -> None:
    """Add the four dividend stat columns to the month-end frame IN PLACE:
      - dividend_var_5y: rolling 5y population std of dividend_yield x100
      - dividend_stability_5y: CV-based score on annualized DPS
      - last_dividend_per_share: latest single DPS as of each month-end
      - dividend_issued_this_month: any ex-date in the same calendar month

    Args:
        monthly: month-end frame with columns code, date (datetime64),
            dividend_yield; modified in place.
        composition_df: index composition (index path only) or None.
        stock_dividends_df: the security's OWN dividend events (stock: from
            stats.stock_dividends; etf: from etf_adjustment) or None.
        sec_type: 'index', 'etf', or 'stock'.
    """
    # ---- Rolling 5y std of dividend_yield (x100 → percentage) ----------
    # dividend_var_5y stores POPULATION std (ddof=0) of the fractional
    # dividend_yield, scaled x100 to express it as a percentage (e.g. a std
    # of 0.005 on the fractional yield becomes 0.5). NULL when fewer than 2
    # non-NULL dividend_yield values exist in the window.
    monthly["dividend_var_5y"] = (
        grouped_rolling_agg(
            monthly, "code", "dividend_yield",
            window=ROLLING_5Y_DAYS, min_periods=2, agg="std", ddof=0,
            sort=False,
        )
        * 100.0
    )

    # ---- dividend_stability_5y (frequency-robust, annualized DPS) ------
    monthly["dividend_stability_5y"] = compute_dividend_stability_5y(
        monthly, composition_df, stock_dividends_df, sec_type
    )

    # ---- last_dividend_per_share + dividend_issued_this_month ----------
    # Rolling record of the latest single dividend per share amount as of
    # each month-end, plus a flag for whether any ex-dividend event falls in
    # the same calendar month as the month-end date (drives bold styling in
    # the UI). SKIPPED for index: the index processing pipeline strips
    # exchange suffixes from stock_dividend codes (so "000001.SZ" → "000001"),
    # which would falsely match bare index codes like "000001" (上证指数).
    # Indices have no direct dividend events, so both columns are NULL/FALSE.
    if sec_type == "index":
        monthly["last_dividend_per_share"] = np.nan
        monthly["dividend_issued_this_month"] = False
    else:
        _add_last_dividend_and_flag(monthly, stock_dividends_df)


def _add_last_dividend_and_flag(
    monthly_df: pd.DataFrame,
    stock_dividends_df: pd.DataFrame | None,
) -> None:
    """Add last_dividend_per_share and dividend_issued_this_month columns
    to monthly_df IN PLACE (vectorized as-of lookups — no iterrows).

    For each month-end row:
      - last_dividend_per_share: the dividend_per_share_pre_tax of the latest
        ex_dividend_date <= month-end date (summed when multiple events share
        the same ex-date). NaN when no dividend event exists on or before the
        month-end, or when the code has no dividend events at all.
      - dividend_issued_this_month: TRUE if any ex_dividend_date falls in the
        same (year, month) as the month-end date. FALSE otherwise. Drives the
        bold styling on the Last Div cell in the UI.

    NOTE: This function is ONLY called for stock/etf sec_types. The caller
    (add_monthly_dividend_stats) skips it entirely for index, because the
    index processing pipeline strips exchange suffixes from stock_dividend
    codes (so "000001.SZ" → "000001"), which would falsely match bare index
    codes like "000001" (上证指数). Indices have no direct dividend events.

    Args:
        monthly_df: DataFrame with columns code, date (datetime64
            month-end dates); modified in place. Requires a unique
            RangeIndex aligned to row positions.
        stock_dividends_df: DataFrame with columns code, ex_dividend_date,
            dividend_per_share_pre_tax — the security's OWN dividend events
            (stock: from stats.stock_dividends; etf: from etf_adjustment).
    """
    if (
        stock_dividends_df is None
        or stock_dividends_df.empty
        or monthly_df.empty
    ):
        monthly_df["last_dividend_per_share"] = np.nan
        monthly_df["dividend_issued_this_month"] = False
        return

    # Sum DPS per (code, ex_dividend_date) so multiple events on the same
    # ex-date (e.g. cash + stock dividend) collapse into one amount.
    div = stock_dividends_df[
        ["code", "ex_dividend_date", "dividend_per_share_pre_tax"]
    ].copy()
    div["ex_dividend_date"] = to_dt64(div["ex_dividend_date"])
    div = (
        div.groupby(["code", "ex_dividend_date"], sort=False)[
            "dividend_per_share_pre_tax"
        ]
        .sum()
        .reset_index()
        .rename(columns={"ex_dividend_date": "date"})
        .sort_values("date")  # merge_asof: globally sorted `on`
    )

    # --- last_dividend_per_share: merge_asof backward per code -----------
    left = monthly_df[["code", "date"]].copy()
    left["_ord"] = np.arange(len(monthly_df))
    merged = pd.merge_asof(
        left.sort_values("date"),
        div,
        on="date",
        by="code",
        direction="backward",
    )
    # _ord labels are exactly monthly_df's RangeIndex values — assignment
    # aligns them back to row positions.
    monthly_df["last_dividend_per_share"] = merged.set_index("_ord")[
        "dividend_per_share_pre_tax"
    ]

    # --- dividend_issued_this_month: (code, year*12+month) key merge -----
    monthly_df["_mkey"] = (
        monthly_df["date"].dt.year * 12 + monthly_df["date"].dt.month
    )
    div["_mkey"] = div["date"].dt.year * 12 + div["date"].dt.month
    ev_keys = div[["code", "_mkey"]].drop_duplicates().assign(_hit=1)
    hit = monthly_df[["code", "_mkey"]].merge(
        ev_keys, on=["code", "_mkey"], how="left"
    )
    # merge(how="left") preserves left row order → positional alignment.
    monthly_df["dividend_issued_this_month"] = (
        (hit["_hit"].fillna(0) == 1).to_numpy()
    )
    monthly_df.drop(columns=["_mkey"], inplace=True)


# ---------------------------------------------------------------------------
#  dividend_stability_5y — CV-based, frequency-robust annualized DPS
# ---------------------------------------------------------------------------
def compute_dividend_stability_5y(
    monthly_df: pd.DataFrame,
    composition_df: pd.DataFrame | None,
    stock_dividends_df: pd.DataFrame | None,
    sec_type: str,
) -> pd.Series:
    """Compute frequency-robust dividend stability score (0-100) per row.

    For each month-end date, looks back STABILITY_WINDOW_YEARS calendar
    years and computes:
      1. Annual DPS per calendar year (summed to annual totals so
         payment-frequency changes don't create artificial gaps)
      2. CV = std(annual_dps) / mean(annual_dps) over years with non-zero DPS
      3. stability = (1 - min(CV, 1)) × 100

    For index: annual_dps = SUM(weight_fraction × constituent annual DPS)
    For etf/stock: annual_dps = SUM(dividends in calendar year)

    Fully vectorized (code × year cross-merge + groupby — no per-row loop).

    Returns a Series aligned to monthly_df's index.
    """
    n = len(monthly_df)
    if n == 0:
        return pd.Series(dtype=float)

    # Pre-compute annual DPS per code per calendar year (code, year, annual_dps)
    if sec_type == "index" and composition_df is not None and stock_dividends_df is not None:
        annual = _compute_index_annual_dps(composition_df, stock_dividends_df)
    elif stock_dividends_df is not None and not stock_dividends_df.empty:
        annual = _compute_simple_annual_dps(stock_dividends_df)
    else:
        # No dividend data — all stability values are None
        return pd.Series(np.nan, index=monthly_df.index)

    if annual.empty:
        return pd.Series(np.nan, index=monthly_df.index)

    # Cross-merge monthly rows with their code's annual totals, keep the
    # trailing STABILITY_WINDOW_YEARS window (including the current year)
    # with non-zero DPS.
    work = monthly_df[["code", "date"]].copy()
    work["_ridx"] = np.arange(n)  # == monthly_df's RangeIndex labels
    work["_year"] = work["date"].dt.year

    cand = work.merge(annual, on="code", how="inner")
    cand = cand[
        (cand["year"] <= cand["_year"])
        & (cand["year"] > cand["_year"] - STABILITY_WINDOW_YEARS)
        & (cand["annual_dps"] > 0)
    ]
    if cand.empty:
        return pd.Series(np.nan, index=monthly_df.index)

    g = cand.groupby("_ridx", sort=False)["annual_dps"]
    counts = g.count()
    means = g.mean()
    stds = g.std(ddof=0)
    cv = (stds / means).clip(upper=1.0)
    stability = ((1.0 - cv) * 100.0).where(counts >= 2)
    # Sparse result → align back to the full monthly index.
    return stability.reindex(monthly_df.index)


def _compute_index_annual_dps(
    composition_df: pd.DataFrame,
    stock_dividends_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compute annual DPS per index code per calendar year.

    annual_dps[index_code, year] = SUM over constituents of (
        weight_fraction × SUM(stock dividends in year)
    )

    Returns:
        DataFrame with columns: code (index_code), year, annual_dps.
    """
    if composition_df.empty or stock_dividends_df.empty:
        return pd.DataFrame(columns=["code", "year", "annual_dps"])

    # Annual DPS per constituent stock
    stock_annual = _compute_simple_annual_dps(stock_dividends_df)

    comp = composition_df[["index_code", "stock_code", "weight_pct"]].copy()
    comp["weight_fraction"] = comp["weight_pct"] / 100.0

    merged = stock_annual.merge(
        comp[["index_code", "stock_code", "weight_fraction"]],
        left_on="code",
        right_on="stock_code",
        how="inner",
    )
    merged["weighted_dps"] = merged["weight_fraction"] * merged["annual_dps"]
    return (
        merged.groupby(["index_code", "year"], sort=False)["weighted_dps"]
        .sum()
        .reset_index()
        .rename(columns={"index_code": "code", "weighted_dps": "annual_dps"})
    )


def _compute_simple_annual_dps(
    dividends_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compute annual DPS per stock per calendar year.

    annual_dps[code, year] = SUM(dividend_per_share_pre_tax WHERE
        ex_dividend_date in year)

    Returns:
        DataFrame with columns: code, year, annual_dps.
    """
    if dividends_df.empty:
        return pd.DataFrame(columns=["code", "year", "annual_dps"])

    df = dividends_df[["code", "ex_dividend_date", "dividend_per_share_pre_tax"]].copy()
    # Vectorized year extraction (datetime64 — no per-row apply)
    df["year"] = pd.to_datetime(df["ex_dividend_date"]).dt.year
    return (
        df.groupby(["code", "year"], sort=False)[
            "dividend_per_share_pre_tax"
        ]
        .sum()
        .reset_index()
        .rename(columns={"dividend_per_share_pre_tax": "annual_dps"})
    )
