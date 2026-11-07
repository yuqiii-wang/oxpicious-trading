"""Async DB fetch primitives for analysis.futures.

Loads per-(date, code) futures close prices from stats.futures_basic_stats
and their underlying data (index close / treasury yield).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import epoch_col_to_dt64, safe_columns
from _common.labels import CONTRACT_TYPE_CODE
from _common.sql_views import frag, render_sql_view
from analysis.futures.config import (
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

    # Step 1: fetch futures identity + basic stats (futures_basic view)
    # ::float8 casts — the DB-read convention: numerics land as NATIVE
    # float64 columns (NULL -> NaN) instead of Decimal object columns,
    # which would poison every downstream op with cudf fallbacks.
    view = render_sql_view("analysis/futures/fetch/futures_basic")
    fut_rows = await conn.fetch(view.sql, *view.params)
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
    # index_closes view: underlying index close series.
    view = render_sql_view(
        "analysis/futures/fetch/index_closes", codes=index_underlyings,
    )
    idx_rows = await conn.fetch(view.sql, *view.params)
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

    # Step 3: fetch treasury yields for bond futures (treasury_yields
    # view; the tenor column list stays the BOND_PRODUCT_TENOR-derived
    # fragment).
    yield_cols = [v[0] for v in BOND_PRODUCT_TENOR.values()]
    view = render_sql_view(
        "analysis/futures/fetch/treasury_yields",
        yield_columns=frag(
            ", ".join(f"{c}::float8 AS {c}" for c in yield_cols)
        ),
    )
    tr_rows = await conn.fetch(view.sql, *view.params)
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
    # contract_type rides as its static.label_dim SMALLINT code
    # (0 index / 1 bond) — compare codes, never strings.
    bond_mask = df["contract_type"] == CONTRACT_TYPE_CODE["bond"]
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
    df["is_index_future"] = (
        df["contract_type"] == CONTRACT_TYPE_CODE["index"]
    )

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

    Scoped to pairs the analysis can actually process — an underlying
    data point must exist for the (date, contract_type): an index close
    in stats.index_basic_stats, or a treasury row for bond yields. The
    shared futures tables also carry the offshore CN/MCA families whose
    underlying index series is not downloaded yet; without the scope
    those pairs would re-report as missing on every run (fetch() drops
    them again at the underlying join).

    Returns list of (date, code) tuples.
    """
    sql = """
        SELECT i.date, i.code
        FROM stats.futures_identity i
        LEFT JOIN analysis.futures_ext e
          ON e.date = i.date AND e.code = i.code
        LEFT JOIN stats.index_basic_stats u
          ON u.code = i.underlying_code AND u.date = i.date
        LEFT JOIN stats.debt_treasury t
          ON t.date = i.date
        WHERE e.date IS NULL
          AND (
            (i.contract_type = $1 AND u.close IS NOT NULL)
            OR (i.contract_type = $2 AND t.date IS NOT NULL)
          )
        ORDER BY i.code, i.date
    """
    rows = await conn.fetch(sql, CONTRACT_TYPE_CODE["index"],
                            CONTRACT_TYPE_CODE["bond"])
    return [(r["date"], r["code"]) for r in rows]
