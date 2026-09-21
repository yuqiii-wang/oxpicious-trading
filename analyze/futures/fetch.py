"""Async DB fetch primitives for analyze.futures.

Loads per-(date, code) futures close prices from stats.futures_basic_stats
and their underlying data (index close / treasury yield).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import epoch_col_to_dt64, safe_columns
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
    # ::float8 casts — the DB-read convention: numerics land as NATIVE
    # float64 columns (NULL -> NaN) instead of Decimal object columns,
    # which would poison every downstream op with cudf fallbacks.
    futures_sql = """
        SELECT
            extract(epoch from i.date)::float8 AS date,
            i.code,
            i.product_code,
            i.contract_type,
            i.underlying_code,
            i.days_to_expiry,
            b.close::float8 AS futures_close
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
    # Dates stay datetime64[us] through ALL compute (the B-A2 convention:
    # an object-date column turns every later op into a cudf fallback);
    # the python-date materialization happens in the write path.
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)

    # Initialize columns that may not be populated (float64 NaN — pd.NA
    # would create an object column, a cudf fallback per later op).
    df["index_close"] = np.nan
    df["bond_theoretical_price"] = np.nan

    # Step 2: fetch index close prices for index futures
    index_underlyings = sorted(set(
        INDEX_PRODUCT_UNDERLYING.values()
    ))
    index_close_sql = """
        SELECT extract(epoch from date)::float8 AS date,
               code, close::float8 AS close
        FROM stats.index_basic_stats
        WHERE code = ANY($1::text[]) AND close IS NOT NULL
        ORDER BY code, date
    """
    idx_rows = await conn.fetch(index_close_sql, index_underlyings)
    if idx_rows:
        index_close_df = pd.DataFrame([dict(r) for r in idx_rows])
        index_close_df["date"] = epoch_col_to_dt64(
            index_close_df["date"], index=index_close_df.index)
        index_close_df = index_close_df.rename(
            columns={"close": "index_close", "code": "underlying_code"}
        )
        # Left-merge index close onto futures
        df = df.merge(
            index_close_df,
            on=["date", "underlying_code"],
            how="left",
        )
        # Drop the old index_close column (NaN) — replace with merged data
        if "index_close_x" in set(safe_columns(df)):
            df = df.drop(columns=["index_close_x"])
            df = df.rename(columns={"index_close_y": "index_close"})

    # Step 3: fetch treasury yields for bond futures
    yield_cols = [v[0] for v in BOND_PRODUCT_TENOR.values()]
    treasury_sql = f"""
        SELECT extract(epoch from date)::float8 AS date,
               {", ".join(f"{c}::float8 AS {c}" for c in yield_cols)}
        FROM stats.debt_treasury
        WHERE date IS NOT NULL
        ORDER BY date
    """
    tr_rows = await conn.fetch(treasury_sql)
    if tr_rows:
        treasury_df = pd.DataFrame([dict(r) for r in tr_rows])
        treasury_df["date"] = epoch_col_to_dt64(
            treasury_df["date"], index=treasury_df.index)
        # Merge treasury yields
        df = df.merge(treasury_df, on="date", how="left")

    # Step 4: compute bond theoretical price from yield (vectorized —
    # replaces the former per-row .apply of _compute_bond_price, one
    # proxy fallback per row): price = 100 / (1 + yield/2)^(2·tenor).
    # Rows with a missing yield or yield <= -100% stay NaN (the former
    # None), as do non-bond rows and unknown product codes.
    bond_mask = df["contract_type"] == "bond"
    if bool(bond_mask.any()):
        bond_cols = set(safe_columns(df))
        for pc, (yield_col, tenor_years) in BOND_PRODUCT_TENOR.items():
            if yield_col not in bond_cols:
                continue
            y = df[yield_col] / 100.0
            m = (
                bond_mask
                & df["product_code"].eq(pc)
                & df[yield_col].notna()
                & (y > -1.0)
            )
            if not bool(m.any()):
                continue
            y = y[m]
            df.loc[m, "bond_theoretical_price"] = (
                100.0 / (1.0 + y / 2.0) ** (2.0 * tenor_years)
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
