"""Pure pandas transformation logic for analyze.pe_and_dividends.

Builds:
  - Daily detail rows (pe_ma20 + dividend_yield) for analysis.pe_and_dividends
  - Monthly 5y rolling stats rows for analysis.pe_and_dividend_stats

Key algorithms:
  - pe_ma20: pandas rolling(20).mean(min_periods=1) of PE per code
  - trailing-12m DPS: event-based rolling 365d sum (+dps on ex_date,
    -dps on ex_date+365d, cumsum, forward-fill to trading dates)
  - index dividend_yield: weighted sum of constituent trailing-12m DPS / close
  - monthly stats: rolling(1275, min_periods=1) min/max PE + std (x100 as
    percentage) of dividend_yield on daily data, filtered to month-end dates
  - last_dividend_per_share + dividend_issued_this_month: rolling record of
    the latest single DPS amount as of each month-end + same-month flag
  - dividend_stability_5y: CV-based score on annualized DPS over trailing
    5 calendar years (frequency-robust: sums to annual totals first)
"""
from __future__ import annotations

import datetime

import numpy as np
import pandas as pd

from _common.df_utils import should_use_gpu
from analyze._common.sanitize import sanitize_for_db_insert
from analyze.pe_and_dividends.config import (
    PE_MA_WINDOW,
    TRAILING_DIVIDEND_DAYS,
    ROLLING_5Y_DAYS,
    STABILITY_WINDOW_YEARS,
)


# ---------------------------------------------------------------------------
#  pe_ma20 — 20-day rolling mean of PE per code (index/etf/stock)
# ---------------------------------------------------------------------------
def compute_pe_ma20(df: pd.DataFrame) -> pd.Series:
    """Compute 20-trading-day moving average of PE per code.

    INVALID-VALUE RULE: a PE of <= 0 (zero, negative, or NULL) is treated
    as missing data — 0 means "no earnings reported" and negative PE is a
    sign-flip that breaks the linear scale, so neither can be averaged
    meaningfully. PE <= 0 / NULL → NaN BEFORE the rolling mean, so:
      * The MA = mean of the non-NaN (genuinely positive) PE values in
        the window (pandas .mean() skipna=True default).
      * The MA column is MASKED to NaN on days where the source PE is
        NaN (<= 0 or NULL) — i.e. on a no-earnings / null-PE day the MA
        is also NULL (entry val NULL for invalid data), but the MA on
        the next valid day still correctly uses the rolling window of
        positive PE values.
    Mirrors the rz_balance 0/NULL→NaN rule in analyze.margins.compute
    ("skip the date as a holiday; denominator does not count for null").

    Args:
        df: DataFrame with columns code, date, pe (index / etf / stock
            PE source — for index from stats.index_valuation.pe, for
            etf / stock pre-computed by builds.etf / builds.stock).

    Returns:
        Series aligned to df's index with pe_ma20 values. NaN where the
        source PE is invalid (<=0 / NULL) OR where the rolling window
        contains no valid PE values.
    """
    if df.empty:
        return pd.Series(dtype=float)
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    # Clean: PE <= 0 or NULL → NaN. A 0 / negative PE is meaningless for
    # a moving average (no earnings / sign-flip). Treated as missing.
    pe_clean = pd.to_numeric(df["pe"], errors="coerce").where(
        lambda s: s > 0
    )
    df = df.assign(__pe_clean=pe_clean)
    result = df.groupby("code", sort=False)["__pe_clean"].rolling(
        window=PE_MA_WINDOW, min_periods=1
    ).mean()
    result = result.reset_index(level=0, drop=True)
    # Re-align to original df order
    result = result.reindex(df.index)
    # Mask: NULL the MA on days where source PE is invalid (NaN).
    # The rolling mean skips NaN source values, so on an invalid-PE day
    # the MA still gets a value (mean of last 20 valid PEs). Per spec
    # ("entry val NULL if zero or null data"), mask the MA output to NaN
    # on invalid-PE days.
    result = result.where(pe_clean.reindex(df.index).notna())
    return result


# ---------------------------------------------------------------------------
#  trailing-12m DPS — event-based rolling 365d sum per stock
# ---------------------------------------------------------------------------
def compute_trailing_12m_dps(
    dividends_df: pd.DataFrame,
    trading_dates: list[datetime.date],
) -> pd.DataFrame:
    """Compute trailing-12m dividend per share per stock per trading date.

    Uses the event-based approach:
      - +dps event on ex_dividend_date (dividend enters the 365d window)
      - -dps event on ex_dividend_date + 365d (dividend leaves the window)
      - Cumsum per stock = running trailing-12m DPS
      - Forward-fill to all trading dates via merge_asof

    Args:
        dividends_df: DataFrame with columns code, ex_dividend_date,
            dividend_per_share_pre_tax.
        trading_dates: list of date objects (the date axis).

    Returns:
        DataFrame with columns: code, date, trailing_dps.
        One row per (stock_code, trading_date). Only includes stocks
        that have at least one dividend event.
    """
    if dividends_df.empty or not trading_dates:
        return pd.DataFrame(columns=["code", "date", "trailing_dps"])

    # Step 1: create +dps and -dps events
    plus_events = dividends_df[["code", "ex_dividend_date", "dividend_per_share_pre_tax"]].rename(
        columns={"ex_dividend_date": "date", "dividend_per_share_pre_tax": "delta"}
    ).copy()
    minus_events = dividends_df[["code", "ex_dividend_date", "dividend_per_share_pre_tax"]].copy()
    minus_events["date"] = minus_events["ex_dividend_date"].apply(
        lambda d: d + datetime.timedelta(days=TRAILING_DIVIDEND_DAYS)
    )
    minus_events = minus_events.rename(
        columns={"dividend_per_share_pre_tax": "delta"}
    )[["code", "date", "delta"]]
    minus_events["delta"] = -minus_events["delta"]

    events = pd.concat([plus_events, minus_events], ignore_index=True)
    events["date"] = pd.to_datetime(events["date"])

    # Step 2: cumsum per code = running trailing-12m DPS
    events = events.sort_values(["code", "date"])
    events["running_dps"] = events.groupby("code", sort=False)["delta"].cumsum()

    # Step 3: forward-fill to trading dates via merge_asof
    # Create a date grid: one row per (code, trading_date)
    codes = events["code"].unique()
    td_series = pd.Series(sorted(trading_dates))
    date_grid = pd.MultiIndex.from_product(
        [codes, td_series], names=["code", "date"]
    ).to_frame(index=False)
    date_grid["date"] = pd.to_datetime(date_grid["date"])

    # merge_asof requires the `on` column to be globally sorted.
    # Sort both DataFrames by date (the `on` key) — the `by` key (code)
    # handles the group-wise matching internally.
    date_grid = date_grid.sort_values("date")
    events_sorted = events[["code", "date", "running_dps"]].sort_values("date")

    # merge_asof: for each (code, trading_date), find the latest event <= trading_date
    result = pd.merge_asof(
        date_grid,
        events_sorted,
        on="date",
        by="code",
        direction="backward",
    )

    # Fill NaN running_dps with 0 (no dividends in window yet)
    result["running_dps"] = result["running_dps"].fillna(0.0)
    result["date"] = result["date"].dt.date

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
    trading_dates: list[datetime.date],
) -> pd.DataFrame:
    """Compute dividend_yield for ETFs or stocks (no composition weighting).

    trailing_12m_dps = event-based rolling 365d sum of dividends per share
    dividend_yield = trailing_12m_dps / close

    For ETFs, pass dividends_df with columns: code, ex_dividend_date (from
    etf_adjustment.date where implied_dividend_per_share > 0), dividend_per_share_pre_tax
    (from etf_adjustment.implied_dividend_per_share).

    For stocks, pass dividends_df from stats.stock_dividends directly.

    Args:
        close_df: DataFrame with columns code, date, close.
        dividends_df: DataFrame with columns code, ex_dividend_date,
            dividend_per_share_pre_tax.
        trading_dates: list of date objects.

    Returns:
        DataFrame with columns: code, date, dividend_yield.
    """
    if close_df.empty:
        return pd.DataFrame(columns=["code", "date", "dividend_yield"])

    if dividends_df.empty:
        result = close_df.copy()
        result["dividend_yield"] = np.nan
        return result[["code", "date", "dividend_yield"]]

    # Compute trailing-12m DPS per stock/ETF
    stock_dps = compute_trailing_12m_dps(dividends_df, trading_dates)

    # Join with close and compute dividend_yield
    result = close_df.merge(
        stock_dps, left_on=["code", "date"], right_on=["code", "date"], how="left"
    )
    result["trailing_dps"] = result["trailing_dps"].fillna(0.0)
    result["dividend_yield"] = np.where(
        (result["close"].notna()) & (result["close"] > 0) & (result["trailing_dps"] > 0),
        result["trailing_dps"] / result["close"],
        np.nan,
    )

    return result[["code", "date", "dividend_yield"]]


# ---------------------------------------------------------------------------
#  Build detail rows for analysis.pe_and_dividends
# ---------------------------------------------------------------------------
def build_detail_rows(
    close_df: pd.DataFrame,
    pe_ma20_series: pd.Series,
    dividend_yield_df: pd.DataFrame,
    sec_type: str,
) -> list[dict]:
    """Assemble final detail rows for analysis.pe_and_dividends.

    Args:
        close_df: DataFrame with columns code, date, close (and optionally pe).
        pe_ma20_series: Series aligned to close_df's index with pe_ma20 values.
            None for etf/stock.
        dividend_yield_df: DataFrame with columns code, date, dividend_yield.
        sec_type: 'index', 'etf', or 'stock'.

    Returns:
        List of dicts suitable for bulk insert.
    """
    if close_df.empty:
        return []

    # Start with close_df as the base
    out = close_df[["code", "date"]].copy()
    out["sec_type"] = sec_type

    # Add pe_ma20 (index + etf — ETF PE pre-computed by builds.etf)
    if sec_type in ("index", "etf", "stock") and pe_ma20_series is not None:
        out["pe_ma20"] = pe_ma20_series.reindex(close_df.index).values
    else:
        out["pe_ma20"] = np.nan

    # Add dividend_yield
    if not dividend_yield_df.empty:
        dy = dividend_yield_df[["code", "date", "dividend_yield"]].copy()
        out = out.merge(dy, on=["code", "date"], how="left")
    else:
        out["dividend_yield"] = np.nan

    # Select final columns
    out = out[["sec_type", "code", "date", "pe_ma20", "dividend_yield"]]

    # Sanitize for DB insert
    return sanitize_for_db_insert(
        out,
        numeric_cols=["pe_ma20", "dividend_yield"],
        round_to=6,
    )


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

    Args:
        detail_df: DataFrame with columns sec_type, code, date, pe_ma20,
            dividend_yield (the daily detail data).
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
        analysis.pe_and_dividend_stats.
    """
    if detail_df.empty:
        return []

    # Find month-end trading dates
    month_ends = find_month_end_dates(trading_dates)
    month_end_set = set(month_ends)

    # Filter detail to month-end dates
    detail_df = detail_df.copy()
    detail_df["date"] = pd.to_datetime(detail_df["date"]).dt.date
    monthly = detail_df[detail_df["date"].isin(month_end_set)].copy()
    if monthly.empty:
        return []

    # Sort by code, date for rolling computations
    monthly = monthly.sort_values(["code", "date"]).reset_index(drop=True)

    # ---- Rolling 5y std of dividend_yield (x100 → percentage) ----------
    # dividend_var_5y now stores POPULATION std (ddof=0) of the fractional
    # dividend_yield, scaled x100 to express it as a percentage (e.g. a std
    # of 0.005 on the fractional yield becomes 0.5). NULL when fewer than 2
    # non-NULL dividend_yield values exist in the window.
    monthly["dividend_var_5y"] = (
        monthly.groupby("code", sort=False)["dividend_yield"]
        .rolling(window=ROLLING_5Y_DAYS, min_periods=2)
        .std(ddof=0)
        .reset_index(level=0, drop=True)
        .reindex(monthly.index)
        * 100.0
    )

    # ---- Rolling 5y min/max of PE (index + etf) ------------------------
    if sec_type in ("index", "etf", "stock") and pe_df is not None and not pe_df.empty:
        # Merge PE into the full daily detail for rolling computation
        pe_daily = pe_df[["code", "date", "pe"]].copy()
        pe_daily["date"] = pd.to_datetime(pe_daily["date"]).dt.date
        pe_daily = pe_daily.sort_values(["code", "date"]).reset_index(drop=True)
        pe_daily["min_pe_5y"] = (
            pe_daily.groupby("code", sort=False)["pe"]
            .rolling(window=ROLLING_5Y_DAYS, min_periods=1)
            .min()
            .reset_index(level=0, drop=True)
            .reindex(pe_daily.index)
        )
        pe_daily["max_pe_5y"] = (
            pe_daily.groupby("code", sort=False)["pe"]
            .rolling(window=ROLLING_5Y_DAYS, min_periods=1)
            .max()
            .reset_index(level=0, drop=True)
            .reindex(pe_daily.index)
        )
        # Filter to month-end dates and merge
        pe_monthly = pe_daily[pe_daily["date"].isin(month_end_set)]
        monthly = monthly.merge(
            pe_monthly[["code", "date", "min_pe_5y", "max_pe_5y"]],
            on=["code", "date"],
            how="left",
        )
    else:
        monthly["min_pe_5y"] = np.nan
        monthly["max_pe_5y"] = np.nan

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
        monthly["last_dividend_per_share"] = pd.Series(
            [np.nan] * len(monthly), index=monthly.index
        )
        monthly["dividend_issued_this_month"] = pd.Series(
            [False] * len(monthly), index=monthly.index
        )
    else:
        (
            monthly["last_dividend_per_share"],
            monthly["dividend_issued_this_month"],
        ) = _compute_last_dividend_and_flag(monthly, stock_dividends_df)

    # ---- Determine is_active (latest month-end per code) ---------------
    max_date_per_code = monthly.groupby("code")["date"].max()
    monthly["is_active"] = monthly.apply(
        lambda row: row["date"] == max_date_per_code.get(row["code"]),
        axis=1,
    )

    # ---- Select final columns ------------------------------------------
    out = monthly[[
        "sec_type", "code", "date", "is_active",
        "min_pe_5y", "max_pe_5y",
        "dividend_var_5y", "dividend_stability_5y",
        "last_dividend_per_share", "dividend_issued_this_month",
    ]].copy()

    return sanitize_for_db_insert(
        out,
        numeric_cols=[
            "min_pe_5y", "max_pe_5y",
            "dividend_var_5y", "dividend_stability_5y",
            "last_dividend_per_share",
        ],
        round_to=10,
    )


def _compute_last_dividend_and_flag(
    monthly_df: pd.DataFrame,
    stock_dividends_df: pd.DataFrame | None,
) -> tuple[pd.Series, pd.Series]:
    """Compute last_dividend_per_share and dividend_issued_this_month per row.

    For each month-end row:
      - last_dividend_per_share: the dividend_per_share_pre_tax of the latest
        ex_dividend_date <= month-end date (summed when multiple events share
        the same ex-date). NaN when no dividend event exists on or before the
        month-end, or when the code has no dividend events at all.
      - dividend_issued_this_month: TRUE if any ex_dividend_date falls in the
        same (year, month) as the month-end date. FALSE otherwise. Drives the
        bold styling on the Last Div cell in the UI.

    NOTE: This function is ONLY called for stock/etf sec_types. The caller
    (compute_monthly_stats) skips it entirely for index, because the index
    processing pipeline strips exchange suffixes from stock_dividend codes
    (so "000001.SZ" → "000001"), which would falsely match bare index codes
    like "000001" (上证指数). Indices have no direct dividend events.

    Args:
        monthly_df: DataFrame with columns code, date (month-end dates).
        stock_dividends_df: DataFrame with columns code, ex_dividend_date,
            dividend_per_share_pre_tax — the security's OWN dividend events
            (stock: from stats.stock_dividends; etf: from etf_adjustment).

    Returns:
        (last_dividend_per_share, dividend_issued_this_month) — both Series
        aligned to monthly_df.index.
    """
    n = len(monthly_df)
    if n == 0:
        return pd.Series(dtype=float), pd.Series(dtype=bool)

    if stock_dividends_df is None or stock_dividends_df.empty:
        return (
            pd.Series([np.nan] * n, index=monthly_df.index),
            pd.Series([False] * n, index=monthly_df.index),
        )

    # Sum DPS per (code, ex_dividend_date) so multiple events on the same
    # ex-date (e.g. cash + stock dividend) collapse into one amount.
    div = stock_dividends_df.copy()
    div["ex_dividend_date"] = pd.to_datetime(div["ex_dividend_date"]).dt.date
    div_grouped = (
        div.groupby(["code", "ex_dividend_date"], sort=False)[
            "dividend_per_share_pre_tax"
        ]
        .sum()
        .reset_index()
    )

    # Per-code sorted list of (ex_date, dps) for latest-event lookup.
    code_events: dict[str, list[tuple[datetime.date, float]]] = {}
    for _, row in div_grouped.iterrows():
        code_events.setdefault(row["code"], []).append(
            (row["ex_dividend_date"], float(row["dividend_per_share_pre_tax"]))
        )
    for code in code_events:
        code_events[code].sort(key=lambda x: x[0])

    last_dps = np.full(n, np.nan)
    issued_this_month = np.full(n, False)

    for i, row in monthly_df.iterrows():
        code = row["code"]
        date = row["date"]  # month-end date (datetime.date)
        events = code_events.get(code)
        if not events:
            continue
        # Latest ex_dividend_date <= date (events is sorted ascending).
        for ex_date, dps in reversed(events):
            if ex_date <= date:
                last_dps[i] = dps
                break
        # Any ex_dividend_date in the same (year, month) as the month-end?
        for ex_date, _ in events:
            if ex_date.year == date.year and ex_date.month == date.month:
                issued_this_month[i] = True
                break

    return (
        pd.Series(last_dps, index=monthly_df.index),
        pd.Series(issued_this_month, index=monthly_df.index),
    )


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

    Returns a Series aligned to monthly_df's index.
    """
    n = len(monthly_df)
    if n == 0:
        return pd.Series(dtype=float)

    # Pre-compute annual DPS per code per calendar year
    if sec_type == "index" and composition_df is not None and stock_dividends_df is not None:
        annual_dps_map = _compute_index_annual_dps(composition_df, stock_dividends_df)
    elif stock_dividends_df is not None and not stock_dividends_df.empty:
        annual_dps_map = _compute_simple_annual_dps(stock_dividends_df)
    else:
        # No dividend data — all stability values are None
        return pd.Series([None] * n, index=monthly_df.index)

    # For each month-end row, compute stability over trailing 5 calendar years
    result = np.full(n, np.nan)
    for i, row in monthly_df.iterrows():
        code = row["code"]
        date = row["date"]
        if code not in annual_dps_map:
            continue

        # Trailing 5 calendar years (including the current year)
        years = range(date.year - STABILITY_WINDOW_YEARS + 1, date.year + 1)
        annual_values = []
        for y in years:
            val = annual_dps_map[code].get(y, 0.0)
            if val > 0:
                annual_values.append(val)

        if len(annual_values) < 2:
            # Need at least 2 years with non-zero DPS
            continue

        arr = np.array(annual_values, dtype=float)
        mean_val = arr.mean()
        if mean_val <= 0:
            continue
        std_val = arr.std(ddof=0)
        cv = std_val / mean_val
        stability = (1.0 - min(cv, 1.0)) * 100.0
        result[i] = stability

    return pd.Series(result, index=monthly_df.index)


def _compute_index_annual_dps(
    composition_df: pd.DataFrame,
    stock_dividends_df: pd.DataFrame,
) -> dict[str, dict[int, float]]:
    """Compute annual DPS per index code per calendar year.

    annual_dps[index_code, year] = SUM over constituents of (
        weight_fraction × SUM(stock dividends in year)
    )

    Returns:
        {index_code: {year: annual_dps}}
    """
    if composition_df.empty or stock_dividends_df.empty:
        return {}

    # Compute annual DPS per stock
    stock_annual = _compute_simple_annual_dps(stock_dividends_df)

    # Prepare composition
    comp = composition_df.copy()
    comp["weight_fraction"] = comp["weight_pct"] / 100.0

    # For each index, sum weighted constituent annual DPS
    result: dict[str, dict[int, float]] = {}
    for _, comp_row in comp.iterrows():
        index_code = comp_row["index_code"]
        stock_code = comp_row["stock_code"]
        weight = comp_row["weight_fraction"]

        if stock_code not in stock_annual:
            continue

        if index_code not in result:
            result[index_code] = {}
        for year, dps in stock_annual[stock_code].items():
            result[index_code][year] = result[index_code].get(year, 0.0) + weight * dps

    return result


def _compute_simple_annual_dps(
    dividends_df: pd.DataFrame,
) -> dict[str, dict[int, float]]:
    """Compute annual DPS per stock per calendar year.

    annual_dps[code, year] = SUM(dividend_per_share_pre_tax WHERE
        ex_dividend_date in year)

    Returns:
        {code: {year: annual_dps}}
    """
    if dividends_df.empty:
        return {}

    df = dividends_df.copy()
    df["year"] = df["ex_dividend_date"].apply(lambda d: d.year if hasattr(d, 'year') else pd.Timestamp(d).year)
    grouped = df.groupby(["code", "year"])["dividend_per_share_pre_tax"].sum()

    result: dict[str, dict[int, float]] = {}
    for (code, year), dps in grouped.items():
        if code not in result:
            result[code] = {}
        result[code][int(year)] = float(dps)
    return result
