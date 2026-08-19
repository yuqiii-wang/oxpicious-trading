"""Async DB fetch primitives for analyze.options.

Loads per-(date, contract_code) option rows with the fields needed for
the rolling skewness computation and the OI stats computation,
plus incremental missing-row detection for both target tables.

Open expiry handling: for each (option_type, underlying_code), the mean
of all expiry dates is computed. Contract rows where expiry_date >
dataset_max_date (still open/not matured) get their expiry_date replaced
with this mean. This collapses all open expiry groups into a single
representative group.
"""
from __future__ import annotations

import pandas as pd
import numpy as np

from analyze.options.config import (
    SKEWNESS_TABLE_NAME,
    EXPIRY_IDENTITY_TABLE,
)

# Map sec_type filter to underlying_target_type column values.
# sec_type='index' -> underlying_target_type IN ('INDEX')
_SEC_TYPE_MAP = {
    "index": "INDEX",
    "etf": "ETF",
}

def _sec_type_where(sec_type: str | None) -> str:
    """Return SQL WHERE clause fragment for sec_type filtering."""
    if sec_type is None:
        return ""
    target = _SEC_TYPE_MAP.get(sec_type.lower())
    if target is None:
        return ""
    return f"AND t.underlying_target_type = '{target}'"


# ---- Skewness stats fetchers -----------------------------------------------

SKEWNESS_FETCH_COLUMNS = [
    "date", "contract_code", "option_type", "underlying_code",
    "expiry_date", "strike_price", "underlying_close", "open_interest",
]

# For skewness stats, we need ALL contracts (including expired) for
# full rolling history. Filter: strike > 0, underlying_close > 0 only.
_SKEWNESS_VALID_WHERE = """
    k.strike_price > 0
    AND s.underlying_close > 0
"""


async def fetch_options_skewness_rows(conn, sec_type: str | None = None) -> pd.DataFrame:
    """Fetch all valid option contract rows for the skewness computation.

    Returns a DataFrame with columns:
        date, contract_code, option_type, underlying_code, expiry_date,
        strike_price, underlying_close, open_interest

    Returns raw contract-level data; compute.py aggregates to expiry-group
    level (OI-weighted mean moneyness) before rolling calculations.

    Args:
        conn: async DB connection.
        sec_type: Optional filter ('index' or 'etf') on underlying_target_type.
    """
    sec_filter = _sec_type_where(sec_type)
    sql = f"""
        SELECT
            t.date,
            t.contract_code,
            t.option_type,
            t.underlying_code,
            t.expiry_date,
            k.strike_price,
            s.underlying_close,
            v.open_interest
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        JOIN stats.options_volume_oi v
          ON v.date = t.date AND v.contract_code = t.contract_code
        WHERE {_SKEWNESS_VALID_WHERE}
        {sec_filter}
        ORDER BY t.option_type, t.underlying_code, t.expiry_date, t.date
    """
    rows = await conn.fetch(sql)
    if not rows:
        return pd.DataFrame(columns=SKEWNESS_FETCH_COLUMNS)

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["expiry_date"] = pd.to_datetime(df["expiry_date"]).dt.date
    for col in ("strike_price", "underlying_close", "open_interest"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


async def fetch_missing_skewness_groups(
    conn,
    sec_type: str | None = None,
    table_name: str = SKEWNESS_TABLE_NAME,
) -> list:
    """Fetch (date, option_type, underlying_code, expiry_date) tuples
    missing from the target stats table (skewness or OI).

    Applies open expiry collapse before detection so that open expiry
    groups are correctly identified by their mean expiry_date.

    Returns list of (date, option_type, underlying_code, expiry_date) tuples
    that need computation.

    Args:
        conn: async DB connection.
        sec_type: Optional filter ('index' or 'etf') on underlying_target_type.
        table_name: Target table checked for missing PKs (defaults to the
            skewness table; the OI pipeline passes its own table).
    """
    sec_filter = _sec_type_where(sec_type)
    sql = f"""
        SELECT DISTINCT t.date, t.option_type, t.underlying_code, t.expiry_date
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        JOIN stats.options_volume_oi v
          ON v.date = t.date AND v.contract_code = t.contract_code
        WHERE {_SKEWNESS_VALID_WHERE}
          {sec_filter}
        ORDER BY t.date, t.option_type, t.underlying_code, t.expiry_date
    """
    rows = await conn.fetch(sql)
    if not rows:
        return []

    # Convert to DataFrame and apply collapse
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["expiry_date"] = pd.to_datetime(df["expiry_date"]).dt.date

    # Apply open expiry collapse
    collapsed = _collapse_open_expiry_df(df)

    # Check which PKs are missing from the target table
    existing_pks = set()
    try:
        existing_rows = await conn.fetch(
            f"SELECT date, option_type, underlying_code, expiry_date "
            f"FROM {table_name}"
        )
        existing_pks = set(
            (r["date"], r["option_type"],
             r["underlying_code"], r["expiry_date"])
            for r in existing_rows
        )
    except Exception:
        pass

    missing = []
    for _, r in collapsed.iterrows():
        pk = (r["date"], r["option_type"], r["underlying_code"], r["expiry_date"])
        if pk not in existing_pks:
            missing.append(pk)

    return missing


async def fetch_expiry_identity_rows(conn, sec_type: str | None = None) -> list:
    """Fetch distinct (date, option_type, underlying_code, expiry_date) tuples
    for populating analysis.options_expiry_identity.

    Applies open expiry collapse: for each (option_type, underlying_code),
    computes the mean of all expiry dates. Rows with expiry_date >
    dataset_max_date get their expiry_date replaced with this mean.

    Args:
        conn: async DB connection.
        sec_type: Optional filter ('index' or 'etf') on underlying_target_type.
    """
    sec_filter = _sec_type_where(sec_type)
    sql = f"""
        SELECT DISTINCT t.date, t.option_type, t.underlying_code, t.expiry_date
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        WHERE {_SKEWNESS_VALID_WHERE}
          {sec_filter}
        ORDER BY t.date, t.option_type, t.underlying_code, t.expiry_date
    """
    rows = await conn.fetch(sql)
    if not rows:
        return []

    # Convert to DataFrame for collapse
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["expiry_date"] = pd.to_datetime(df["expiry_date"]).dt.date

    # Apply open expiry collapse
    collapsed = _collapse_open_expiry_df(df)

    return [
        (r["date"], r["option_type"], r["underlying_code"], r["expiry_date"])
        for _, r in collapsed.iterrows()
    ]


def _collapse_open_expiry_df(df: pd.DataFrame) -> pd.DataFrame:
    """Apply open expiry collapse to a DataFrame with expiry_date column.

    For each (option_type, underlying_code), computes the mean of all
    expiry dates. Rows with expiry_date > max(date) get their
    expiry_date replaced with this mean. Then re-aggregates to unique
    (date, option_type, underlying_code, expiry_date) rows.

    Args:
        df: DataFrame with columns: date, option_type, underlying_code,
            expiry_date (and possibly others).

    Returns:
        DataFrame with open expiry groups collapsed to mean expiry_date,
        containing only unique (date, option_type, underlying_code,
        expiry_date) tuples.
    """
    if df.empty:
        return df

    dataset_max_date = df["date"].max()

    # Convert expiry_date to numeric (ordinal) for mean computation
    result = df.copy()
    result["_expiry_ordinal"] = result["expiry_date"].apply(
        lambda d: d.toordinal() if hasattr(d, "toordinal") else pd.Timestamp(d).toordinal()
    )

    # Compute mean expiry ordinal per (option_type, underlying_code)
    mean_ordinals = (
        result.groupby(["option_type", "underlying_code"])["_expiry_ordinal"]
        .apply(lambda g: g.drop_duplicates().mean())
        .to_dict()
    )

    # Convert mean ordinals back to dates
    mean_map = {}
    for k, v in mean_ordinals.items():
        if pd.notna(v):
            mean_map[k] = pd.Timestamp.fromordinal(int(round(v))).date()
        else:
            mean_map[k] = None

    # Replace expiry_date for open rows
    open_mask = result["expiry_date"] > dataset_max_date

    if open_mask.any():
        result.loc[open_mask, "expiry_date"] = result.loc[open_mask].apply(
            lambda r: mean_map.get(
                (r["option_type"], r["underlying_code"]),
                r["expiry_date"],
            ),
            axis=1,
        )

    # Return unique PK combinations
    pk_cols = ["date", "option_type", "underlying_code", "expiry_date"]
    return result[pk_cols].drop_duplicates().reset_index(drop=True)


# ---- OI stats fetchers ---------------------------------------------------

OI_FETCH_COLUMNS = [
    "date", "contract_code", "option_type", "underlying_code",
    "expiry_date", "open_interest", "underlying_close",
]


async def fetch_oi_rows(conn, sec_type: str | None = None) -> pd.DataFrame:
    """Fetch all valid option contract rows for the OI stats computation.

    Returns a DataFrame with columns:
        date, contract_code, option_type, underlying_code,
        expiry_date, open_interest, underlying_close

    Args:
        conn: async DB connection.
        sec_type: Optional filter ('index' or 'etf') on underlying_target_type.
    """
    sec_filter = _sec_type_where(sec_type)
    sql = f"""
        SELECT
            t.date,
            t.contract_code,
            t.option_type,
            t.underlying_code,
            t.expiry_date,
            v.open_interest,
            s.underlying_close
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        JOIN stats.options_volume_oi v
          ON v.date = t.date AND v.contract_code = t.contract_code
        WHERE {_SKEWNESS_VALID_WHERE}
          {sec_filter}
        ORDER BY t.underlying_code, t.expiry_date, t.date
    """
    rows = await conn.fetch(sql)
    if not rows:
        return pd.DataFrame(columns=OI_FETCH_COLUMNS)

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["expiry_date"] = pd.to_datetime(df["expiry_date"]).dt.date
    for col in ("open_interest", "underlying_close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df
