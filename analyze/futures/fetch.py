"""Async DB fetch primitives for analyze.futures.

Loads per-(date, code) futures close prices from stats.futures_basic_stats
and their underlying data (index close / treasury yield).
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import to_py_dates
from analyze.futures.config import (
    BOND_PRODUCT_TENOR,
    INDEX_PRODUCT_UNDERLYING,
)


async def fetch_futures_data(conn) -> pd.DataFrame:
    """Fetch all futures contracts with their underlying data.

    Returns a DataFrame with columns:
        date, code, product_code, contract_type, underlying_code,
        futures_close, index_close, bond_theoretical_price,
        is_index_future (bool flag), plus raw yield columns.

    For index futures: index_close is populated from stats.index_basic_stats.
    For bond futures: bond_theoretical_price is derived from
    stats.debt_treasury yield.

    Rows where the underlying data is missing are excluded.
    """
    _empty_cols = [
        "date", "code", "product_code", "contract_type",
        "underlying_code", "days_to_expiry", "futures_close",
        "index_close", "bond_theoretical_price", "is_index_future",
    ]

    # Step 1: fetch futures identity + basic stats
    futures_sql = """
        SELECT
            i.date,
            i.code,
            i.product_code,
            i.contract_type,
            i.underlying_code,
            i.days_to_expiry,
            b.close AS futures_close
        FROM stats.futures_identity i
        JOIN stats.futures_basic_stats b
          ON b.date = i.date AND b.code = i.code
        WHERE b.close IS NOT NULL
        ORDER BY i.code, i.date
    """
    fut_rows = await conn.fetch(futures_sql)
    if not fut_rows:
        return pd.DataFrame(columns=_empty_cols)

    df = pd.DataFrame([dict(r) for r in fut_rows])
    # python-date contract (serialization boundary) via the host-pass
    # helper — .dt.date is NOT implemented by cuDF (per-element fallback)
    df["date"] = pd.to_datetime(df["date"])
    df = to_py_dates(df, ["date"])
    df["futures_close"] = pd.to_numeric(df["futures_close"], errors="coerce")

    # Initialize columns that may not be populated
    df["index_close"] = pd.NA
    df["bond_theoretical_price"] = pd.NA

    # Step 2: fetch index close prices for index futures
    index_underlyings = sorted(set(
        INDEX_PRODUCT_UNDERLYING.values()
    ))
    index_close_sql = """
        SELECT date, code, close
        FROM stats.index_basic_stats
        WHERE code = ANY($1::text[]) AND close IS NOT NULL
        ORDER BY code, date
    """
    idx_rows = await conn.fetch(index_close_sql, index_underlyings)
    if idx_rows:
        index_close_df = pd.DataFrame([dict(r) for r in idx_rows])
        index_close_df["date"] = pd.to_datetime(index_close_df["date"])
        index_close_df = to_py_dates(index_close_df, ["date"])
        index_close_df["close"] = pd.to_numeric(
            index_close_df["close"], errors="coerce"
        )
        index_close_df = index_close_df.rename(
            columns={"close": "index_close", "code": "underlying_code"}
        )
        # Left-merge index close onto futures
        df = df.merge(
            index_close_df,
            on=["date", "underlying_code"],
            how="left",
        )
        # Drop the old index_close column (pd.NA) — replace with merged data
        if "index_close_x" in df.columns:
            df = df.drop(columns=["index_close_x"])
            df = df.rename(columns={"index_close_y": "index_close"})

    # Step 3: fetch treasury yields for bond futures
    yield_cols = [v[0] for v in BOND_PRODUCT_TENOR.values()]
    treasury_sql = f"""
        SELECT date, {", ".join(yield_cols)}
        FROM stats.debt_treasury
        WHERE date IS NOT NULL
        ORDER BY date
    """
    tr_rows = await conn.fetch(treasury_sql)
    if tr_rows:
        treasury_df = pd.DataFrame([dict(r) for r in tr_rows])
        treasury_df["date"] = pd.to_datetime(treasury_df["date"])
        treasury_df = to_py_dates(treasury_df, ["date"])
        for col in yield_cols:
            treasury_df[col] = pd.to_numeric(
                treasury_df[col], errors="coerce"
            )
        # Merge treasury yields
        df = df.merge(treasury_df, on="date", how="left")

    # Step 4: compute bond theoretical price from yield
    # price = 100 / (1 + yield/2)^(2·tenor_years)
    bond_mask = df["contract_type"] == "bond"
    if bond_mask.any():
        df.loc[bond_mask, "bond_theoretical_price"] = df[bond_mask].apply(
            lambda row: _compute_bond_price(
                row.get("product_code", ""),
                row,
            ),
            axis=1,
        )

    # Step 5: fill is_index_future flag
    df["is_index_future"] = df["contract_type"] == "index"

    # Step 6: filter out rows missing both index_close and
    # bond_theoretical_price (no underlying data available)
    has_underlying = (
        (df["is_index_future"] & df["index_close"].notna())
        | (~df["is_index_future"] & df["bond_theoretical_price"].notna())
    )
    df = df[has_underlying].reset_index(drop=True)

    # Reorder columns for readability
    cols_order = [
        "date", "code", "product_code", "contract_type",
        "underlying_code", "days_to_expiry", "futures_close",
        "index_close", "bond_theoretical_price",
        "is_index_future",
    ]
    # Add any remaining columns (raw yield cols) at the end
    for c in df.columns:
        if c not in cols_order:
            cols_order.append(c)
    df = df[cols_order]

    return df


def _compute_bond_price(product_code: str, row: pd.Series) -> float | None:
    """Convert treasury yield to a zero-coupon bond price proxy.

    price = 100 / (1 + yield/2)^(2·tenor_years)

    Args:
        product_code: e.g. 'T', 'TF', 'TL', 'TS'.
        row: pandas Series with yield columns.

    Returns:
        Theoretical bond price or None if data is missing.
    """
    if product_code not in BOND_PRODUCT_TENOR:
        return None

    yield_col, tenor_years = BOND_PRODUCT_TENOR[product_code]
    if yield_col not in row.index:
        return None

    y = row.get(yield_col)
    if y is None or pd.isna(y):
        return None

    y = float(y) / 100.0  # convert from % to decimal, e.g. 2.5% → 0.025
    if y <= -1.0:
        return None

    n_periods = 2.0 * tenor_years
    price = 100.0 / ((1.0 + y / 2.0) ** n_periods)
    return price


async def fetch_futures_identity_dates(conn) -> list:
    """Fetch distinct (date, code) pairs from futures_identity that need
    computation (not yet in analysis.futures_ext).

    Returns list of (date, code) tuples.
    """
    sql = """
        SELECT i.date, i.code
        FROM stats.futures_identity i
        LEFT JOIN analysis.futures_ext e
          ON e.date = i.date AND e.code = i.code
        WHERE e.date IS NULL
        ORDER BY i.code, i.date
    """
    rows = await conn.fetch(sql)
    return [(r["date"], r["code"]) for r in rows]
