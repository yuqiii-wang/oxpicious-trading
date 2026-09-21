"""Async DB fetch primitives for analyze.options.

Loads per-(date, contract_code) option rows with the fields needed for
the rolling skewness computation and the OI stats computation,
plus incremental missing-row detection for both target tables.

Open expiry handling: for each (option_type, underlying_code), the mean
of all expiry dates is computed. Contract rows where expiry_date >
dataset_max_date (still open/not matured) get their expiry_date replaced
with this mean. This collapses all open expiry groups into a single
representative group. EXCEPTION: options_oi_stats is the dedicated
per-REAL-expiry table (fetch_missing_oi_groups / compute_options_oi_stats
work on raw expiry dates, no collapse) — see 16_options.sql for why.

B-A4 / B-A2 conventions:
  - dates stay ``datetime64`` from fetch through compute (cuDF-native
    groupby/sort/compare); python ``date`` objects are materialized ONCE
    per column via :func:`_common.df_utils.to_py_dates` only at the
    tuple-return boundary (PK detection) — never ``.dt.date``.
  - the open-expiry collapse is the shared vectorized
    :func:`analyze.options.compute._shared._apply_open_expiry_collapse`
    (merge + ``where``), not a per-row ``apply``.
  - missing-PK detection is a vectorized anti-join
    (``merge(..., indicator=True)``), not ``iterrows``.
"""
from __future__ import annotations

import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64, host_array
from analyze.options.compute._shared import _apply_open_expiry_collapse
from analyze.options.config import (
    SKEWNESS_TABLE_NAME,
    EXPIRY_IDENTITY_TABLE,
    WALLS_TABLE_NAME,
    IV_SKEW_TABLE_NAME,
    OI_TABLE_NAME,
)

# PK columns shared by the expiry-group tables.
_PK_COLUMNS = ["date", "option_type", "underlying_code", "expiry_date"]

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


def _records_frame(rows, columns: list[str]) -> pd.DataFrame:
    """Build a DataFrame from asyncpg records via ``rec_cols``.

    One positional-unpack pass over the rows (repo convention from
    ``_common.build_commons``) instead of a per-row ``dict(r)`` for
    multi-million-row fetches. ``columns`` re-orders/selects by name
    (keys follow the SELECT order).
    """
    return pd.DataFrame(rec_cols(rows), columns=columns)


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
    Dates stay datetime64 (compute boundary); conversion to python dates
    happens only at the DB-write boundary.

    Args:
        conn: async DB connection.
        sec_type: Optional filter ('index' or 'etf') on underlying_target_type.
    """
    sec_filter = _sec_type_where(sec_type)
    sql = f"""
        SELECT
            extract(epoch from t.date)::float8 AS date,
            t.contract_code,
            t.option_type,
            t.underlying_code,
            extract(epoch from t.expiry_date)::float8 AS expiry_date,
            k.strike_price::float8 AS strike_price,
            s.underlying_close::float8 AS underlying_close,
            v.open_interest::float8 AS open_interest
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

    df = _records_frame(rows, SKEWNESS_FETCH_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["expiry_date"] = epoch_col_to_dt64(
        df["expiry_date"], index=df.index)
    return df


def _missing_pk_tuples(
    collapsed: pd.DataFrame,
    existing_rows,
    pk_columns: list[str],
) -> list[tuple]:
    """Vectorized missing-PK detection via anti-join.

    Args:
        collapsed: DataFrame with the candidate PK rows (datetime64
            date columns).
        existing_rows: asyncpg records of the existing PK rows (or []).
        pk_columns: PK column names.

    Returns:
        List of PK tuples (python ``date`` objects in the date columns)
        present in ``collapsed`` but not among ``existing_rows``.
    """
    date_cols = [c for c in pk_columns if c in ("date", "expiry_date")]
    if existing_rows:
        existing = _records_frame(existing_rows, pk_columns)
        for c in date_cols:
            # Existing PK rows come from tables whose date columns are
            # still DATE — select them with extract(epoch)::float8 (see
            # the fetchers) so both anti-join sides share the same
            # datetime64[us] unit.
            existing[c] = epoch_col_to_dt64(existing[c],
                                            index=existing.index)
        merged = collapsed.merge(
            existing[pk_columns].drop_duplicates(),
            on=pk_columns, how="left", indicator=True,
        )
        missing = merged.loc[
            merged["_merge"] == "left_only", pk_columns
        ].copy()
    else:
        missing = collapsed[pk_columns].copy()

    # Host-pure tuple materialization: ONE numpy transfer per column,
    # date columns -> python date objects (the to_py_dates convention
    # without the object-column __setitem__ fallback on the frame).
    cols = []
    for c in pk_columns:
        arr = host_array(missing[c].to_numpy())
        if c in date_cols and arr.dtype.kind == "M":
            arr = arr.astype("datetime64[D]").astype(object)
        cols.append(arr.tolist())
    return list(zip(*cols))


async def fetch_missing_skewness_groups(
    conn,
    sec_type: str | None = None,
    table_name: str = SKEWNESS_TABLE_NAME,
    skew_type: str | None = None,
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
        skew_type: Optional skew_type filter on the target table's existing
            rows (options_skewness_stats separates data sources by
            skew_type: 'oi_moneyness' / 'iv_smile'). Only used when the
            target table has that column.
    """
    sec_filter = _sec_type_where(sec_type)
    sql = f"""
        SELECT DISTINCT extract(epoch from t.date)::float8 AS date,
               t.option_type, t.underlying_code,
               extract(epoch from t.expiry_date)::float8 AS expiry_date
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        JOIN stats.options_volume_oi v
          ON v.date = t.date AND v.contract_code = t.contract_code
        WHERE {_SKEWNESS_VALID_WHERE}
          {sec_filter}
        ORDER BY date, option_type, underlying_code, expiry_date
    """
    rows = await conn.fetch(sql)
    if not rows:
        return []

    df = _records_frame(rows, _PK_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["expiry_date"] = epoch_col_to_dt64(
        df["expiry_date"], index=df.index)

    # Vectorized open expiry collapse + unique PK rows
    collapsed = _apply_open_expiry_collapse(df)[_PK_COLUMNS].drop_duplicates()

    existing_pks: list = []
    try:
        type_filter = ""
        if skew_type is not None:
            type_filter = " WHERE skew_type = $1"
        existing_pks = await conn.fetch(
            f"SELECT extract(epoch from date)::float8 AS date, "
            f"option_type, underlying_code, "
            f"extract(epoch from expiry_date)::float8 AS expiry_date "
            f"FROM {table_name}{type_filter}",
            *( [skew_type] if skew_type is not None else [] ),
        )
    except Exception:
        pass

    return _missing_pk_tuples(collapsed, existing_pks, _PK_COLUMNS)


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
        SELECT DISTINCT extract(epoch from t.date)::float8 AS date,
               t.option_type, t.underlying_code,
               extract(epoch from t.expiry_date)::float8 AS expiry_date
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        WHERE {_SKEWNESS_VALID_WHERE}
          {sec_filter}
        ORDER BY date, option_type, underlying_code, expiry_date
    """
    rows = await conn.fetch(sql)
    if not rows:
        return []

    df = _records_frame(rows, _PK_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["expiry_date"] = epoch_col_to_dt64(
        df["expiry_date"], index=df.index)

    # Vectorized open expiry collapse + unique PK rows
    collapsed = _apply_open_expiry_collapse(df)[_PK_COLUMNS].drop_duplicates()

    # Host-pure tuple materialization (ONE numpy transfer per column;
    # date columns -> python date objects — the to_py_dates convention
    # without the object-column __setitem__ fallback on the frame).
    vals = {}
    for c in _PK_COLUMNS:
        arr = host_array(collapsed[c].to_numpy())
        if c in ("date", "expiry_date"):
            arr = arr.astype("datetime64[D]").astype(object)
        vals[c] = arr.tolist()
    return list(zip(
        vals["date"], vals["option_type"], vals["underlying_code"],
        vals["expiry_date"],
    ))


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

    Dates stay datetime64 (compute boundary).

    Args:
        conn: async DB connection.
        sec_type: Optional filter ('index' or 'etf') on underlying_target_type.
    """
    sec_filter = _sec_type_where(sec_type)
    sql = f"""
        SELECT
            extract(epoch from t.date)::float8 AS date,
            t.contract_code,
            t.option_type,
            t.underlying_code,
            extract(epoch from t.expiry_date)::float8 AS expiry_date,
            v.open_interest::float8 AS open_interest,
            s.underlying_close::float8 AS underlying_close
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

    df = _records_frame(rows, OI_FETCH_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["expiry_date"] = epoch_col_to_dt64(
        df["expiry_date"], index=df.index)
    return df


async def fetch_missing_oi_groups(
    conn,
    sec_type: str | None = None,
) -> list:
    """Fetch (date, option_type, underlying_code, expiry_date) tuples
    missing from analysis.options_oi_stats.

    NO open expiry collapse — options_oi_stats is the dedicated
    per-REAL-expiry table (its rows are keyed by the real contract expiry
    date, not the collapsed open-group convention), so candidates are the
    raw distinct source tuples and existing rows are matched as-is.

    Args:
        conn: async DB connection.
        sec_type: Optional filter ('index' or 'etf') on underlying_target_type.
    """
    sec_filter = _sec_type_where(sec_type)
    sql = f"""
        SELECT DISTINCT extract(epoch from t.date)::float8 AS date,
               t.option_type, t.underlying_code,
               extract(epoch from t.expiry_date)::float8 AS expiry_date
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        JOIN stats.options_volume_oi v
          ON v.date = t.date AND v.contract_code = t.contract_code
        WHERE {_SKEWNESS_VALID_WHERE}
          {sec_filter}
        ORDER BY date, option_type, underlying_code, expiry_date
    """
    rows = await conn.fetch(sql)
    if not rows:
        return []

    df = _records_frame(rows, _PK_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["expiry_date"] = epoch_col_to_dt64(
        df["expiry_date"], index=df.index)

    candidates = df[_PK_COLUMNS].drop_duplicates()

    existing_pks: list = []
    try:
        existing_pks = await conn.fetch(
            f"SELECT extract(epoch from date)::float8 AS date, "
            f"option_type, underlying_code, "
            f"extract(epoch from expiry_date)::float8 AS expiry_date "
            f"FROM {OI_TABLE_NAME}"
        )
    except Exception:
        pass

    return _missing_pk_tuples(candidates, existing_pks, _PK_COLUMNS)


# ---- Options IV skew fetchers ---------------------------------------------

IV_SKEW_FETCH_COLUMNS = [
    "date", "contract_code", "option_type", "underlying_code",
    "expiry_date", "strike_price", "underlying_close", "open_interest",
    "implied_vol", "delta", "theta", "gamma", "vega", "rho",
]

# Valid IV range for premium-calibrated implied vol (frontend convention:
# 0 < IV < 5 as a fraction, i.e. 0-500 vol points).
_IV_SKEW_VALID_WHERE = """
    k.strike_price > 0
    AND s.underlying_close > 0
    AND g.implied_vol > 0
    AND g.implied_vol < 5
    AND g.delta IS NOT NULL
"""


async def fetch_iv_skew_rows(conn, sec_type: str | None = None) -> pd.DataFrame:
    """Fetch contract rows with valid IV + delta for the IV skew
    computation, carrying ALL greeks (delta/theta/gamma/vega/rho) for the
    greek skew pipeline.

    Returns a DataFrame with columns:
        date, contract_code, option_type, underlying_code, expiry_date,
        strike_price, underlying_close, open_interest, implied_vol,
        delta, theta, gamma, vega, rho

    Dates stay datetime64 (compute boundary).

    Args:
        conn: async DB connection.
        sec_type: Optional filter ('index' or 'etf') on underlying_target_type.
    """
    sec_filter = _sec_type_where(sec_type)
    sql = f"""
        SELECT
            extract(epoch from t.date)::float8 AS date,
            t.contract_code,
            t.option_type,
            t.underlying_code,
            extract(epoch from t.expiry_date)::float8 AS expiry_date,
            k.strike_price::float8 AS strike_price,
            s.underlying_close::float8 AS underlying_close,
            v.open_interest::float8 AS open_interest,
            g.implied_vol::float8 AS implied_vol,
            g.delta::float8 AS delta,
            g.theta::float8 AS theta,
            g.gamma::float8 AS gamma,
            g.vega::float8 AS vega,
            g.rho::float8 AS rho
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        JOIN stats.options_volume_oi v
          ON v.date = t.date AND v.contract_code = t.contract_code
        JOIN stats.options_greeks g
          ON g.date = t.date AND g.contract_code = t.contract_code
        WHERE {_IV_SKEW_VALID_WHERE}
          {sec_filter}
        ORDER BY t.underlying_code, t.expiry_date, t.date
    """
    rows = await conn.fetch(sql)
    if not rows:
        return pd.DataFrame(columns=IV_SKEW_FETCH_COLUMNS)

    df = _records_frame(rows, IV_SKEW_FETCH_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["expiry_date"] = epoch_col_to_dt64(
        df["expiry_date"], index=df.index)
    return df


async def fetch_missing_iv_skew_groups(
    conn,
    sec_type: str | None = None,
    table_name: str = IV_SKEW_TABLE_NAME,
    skew_type: str | None = None,
) -> list:
    """Fetch (date, option_type, underlying_code, expiry_date) tuples
    missing from the IV skew stats table.

    Applies open expiry collapse before detection so that open expiry
    groups are correctly identified by their mean expiry_date.

    Returns list of (date, option_type, underlying_code, expiry_date) tuples
    that need computation.

    Args:
        conn: async DB connection.
        sec_type: Optional filter ('index' or 'etf') on underlying_target_type.
        table_name: Target table checked for missing PKs (defaults to the
            IV skew table; the iv_smile corr pipeline passes the skewness
            stats table).
        skew_type: Optional skew_type filter on the target table's existing
            rows (used when table_name is options_skewness_stats, which
            separates data sources by skew_type).
    """
    sec_filter = _sec_type_where(sec_type)
    sql = f"""
        SELECT DISTINCT extract(epoch from t.date)::float8 AS date,
               t.option_type, t.underlying_code,
               extract(epoch from t.expiry_date)::float8 AS expiry_date
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        JOIN stats.options_volume_oi v
          ON v.date = t.date AND v.contract_code = t.contract_code
        JOIN stats.options_greeks g
          ON g.date = t.date AND g.contract_code = t.contract_code
        WHERE {_IV_SKEW_VALID_WHERE}
          {sec_filter}
        ORDER BY date, option_type, underlying_code, expiry_date
    """
    rows = await conn.fetch(sql)
    if not rows:
        return []

    df = _records_frame(rows, _PK_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["expiry_date"] = epoch_col_to_dt64(
        df["expiry_date"], index=df.index)

    collapsed = _apply_open_expiry_collapse(df)[_PK_COLUMNS].drop_duplicates()

    existing_pks: list = []
    try:
        type_filter = ""
        if skew_type is not None:
            type_filter = " WHERE skew_type = $1"
        existing_pks = await conn.fetch(
            f"SELECT extract(epoch from date)::float8 AS date, "
            f"option_type, underlying_code, "
            f"extract(epoch from expiry_date)::float8 AS expiry_date "
            f"FROM {table_name}{type_filter}",
            *( [skew_type] if skew_type is not None else [] ),
        )
    except Exception:
        pass

    return _missing_pk_tuples(collapsed, existing_pks, _PK_COLUMNS)


# ---- Volatility index fetchers ----------------------------------------------

VOL_INDEX_FETCH_COLUMNS = [
    "date", "option_type", "underlying_code", "underlying_target_type",
    "expiry_date", "days_to_expiry",
    "strike_price", "underlying_close", "settle",
]

# Model-free variance replication prices the OTM strip off raw settlement
# quotes only — no IV/delta calibration involved (K, S, Q > 0 suffice).
_VOL_INDEX_VALID_WHERE = """
    k.strike_price > 0
    AND s.underlying_close > 0
    AND s.settle > 0
    AND t.days_to_expiry > 0
"""


async def fetch_vol_index_rows(conn, sec_type: str | None = None) -> pd.DataFrame:
    """Fetch valid option contract rows for the vol-index computation.

    Returns a DataFrame with columns:
        date, option_type, underlying_code, underlying_target_type,
        expiry_date, days_to_expiry, strike_price, underlying_close, settle

    ``underlying_target_type`` resolves the venue unit convention (CFFEX
    index quotes are native points; SZSE ETF quotes are x1000 strike /
    underlying with settle already yuan per share — scaled in
    compute/vix.py).

    Args:
        conn: async DB connection.
        sec_type: Optional filter ('index' or 'etf') on underlying_target_type.
    """
    sec_filter = _sec_type_where(sec_type)
    sql = f"""
        SELECT
            extract(epoch from t.date)::float8 AS date,
            t.contract_code,
            t.option_type,
            t.underlying_code,
            t.underlying_target_type,
            extract(epoch from t.expiry_date)::float8 AS expiry_date,
            t.days_to_expiry,
            k.strike_price::float8 AS strike_price,
            s.underlying_close::float8 AS underlying_close,
            s.settle::float8 AS settle
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        JOIN stats.options_volume_oi v
          ON v.date = t.date AND v.contract_code = t.contract_code
        WHERE {_VOL_INDEX_VALID_WHERE}
          {sec_filter}
        ORDER BY t.underlying_code, t.date, t.expiry_date,
                 k.strike_price, t.option_type
    """
    rows = await conn.fetch(sql)
    if not rows:
        return pd.DataFrame(columns=VOL_INDEX_FETCH_COLUMNS)

    df = _records_frame(
        rows,
        ["date", "contract_code", "option_type", "underlying_code",
         "underlying_target_type", "expiry_date", "days_to_expiry",
         "strike_price", "underlying_close", "settle"],
    )
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["expiry_date"] = epoch_col_to_dt64(
        df["expiry_date"], index=df.index)
    return df


async def fetch_missing_vol_index_dates(
    conn,
    sec_type: str | None = None,
) -> list:
    """Fetch (underlying_code, date) pairs missing from options_vol_index.

    Date-granular (no expiry dimension, no open-expiry collapse): a date is
    computable whenever any valid contract row exists for the underlying.

    Args:
        conn: async DB connection.
        sec_type: Optional filter ('index' or 'etf') on underlying_target_type.
    """
    from analyze.options.config import (
        VOL_INDEX_PK_COLUMNS,
        VOL_INDEX_TABLE_NAME,
    )

    sec_filter = _sec_type_where(sec_type)
    sql = f"""
        SELECT DISTINCT t.underlying_code,
               extract(epoch from t.date)::float8 AS date
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        WHERE {_VOL_INDEX_VALID_WHERE}
          {sec_filter}
        ORDER BY t.underlying_code, date
    """
    rows = await conn.fetch(sql)
    if not rows:
        return []

    df = _records_frame(rows, VOL_INDEX_PK_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    candidates = df[VOL_INDEX_PK_COLUMNS].drop_duplicates()

    existing_pks: list = []
    try:
        existing_pks = await conn.fetch(
            f"SELECT underlying_code, "
            f"extract(epoch from date)::float8 AS date "
            f"FROM {VOL_INDEX_TABLE_NAME}"
        )
    except Exception:
        pass

    return _missing_pk_tuples(candidates, existing_pks, VOL_INDEX_PK_COLUMNS)


# ---- Options walls fetchers -----------------------------------------------

WALLS_FETCH_COLUMNS = [
    "date", "contract_code", "option_type", "underlying_code",
    "expiry_date", "strike_price", "open_interest", "underlying_close",
]


async def fetch_options_walls_rows(conn, sec_type: str | None = None) -> pd.DataFrame:
    """Fetch all valid option contract rows for the walls computation.

    Returns a DataFrame with columns:
        date, contract_code, option_type, underlying_code,
        expiry_date, strike_price, open_interest, underlying_close

    ``underlying_close`` (LEFT JOIN — NULL when settlement is missing)
    is required by the zone wall pipeline (gap / BREACHED state).

    Dates stay datetime64 (compute boundary).

    Args:
        conn: async DB connection.
        sec_type: Optional filter ('index' or 'etf') on underlying_target_type.
    """
    sec_filter = _sec_type_where(sec_type)
    sql = f"""
        SELECT
            extract(epoch from t.date)::float8 AS date,
            t.contract_code,
            t.option_type,
            t.underlying_code,
            extract(epoch from t.expiry_date)::float8 AS expiry_date,
            k.strike_price::float8 AS strike_price,
            v.open_interest::float8 AS open_interest,
            s.underlying_close::float8 AS underlying_close
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_volume_oi v
          ON v.date = t.date AND v.contract_code = t.contract_code
        LEFT JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        WHERE k.strike_price > 0
          {sec_filter}
        ORDER BY t.option_type, t.underlying_code, t.expiry_date, t.date
    """
    rows = await conn.fetch(sql)
    if not rows:
        return pd.DataFrame(columns=WALLS_FETCH_COLUMNS)

    df = _records_frame(rows, WALLS_FETCH_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["expiry_date"] = epoch_col_to_dt64(
        df["expiry_date"], index=df.index)
    return df


async def fetch_missing_walls_groups(
    conn,
    sec_type: str | None = None,
) -> list:
    """Fetch (date, option_type, underlying_code, expiry_date, wall_type)
    tuples missing from analysis.options_walls.

    Returns list of tuples that need computation. Each expiry group needs
    the zone wall for both option types (CALL/PUT).
    """
    sec_filter = _sec_type_where(sec_type)
    sql = f"""
        SELECT DISTINCT extract(epoch from t.date)::float8 AS date,
               t.option_type, t.underlying_code,
               extract(epoch from t.expiry_date)::float8 AS expiry_date
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_volume_oi v
          ON v.date = t.date AND v.contract_code = t.contract_code
        WHERE k.strike_price > 0
          {sec_filter}
        ORDER BY date, option_type, underlying_code, expiry_date
    """
    rows = await conn.fetch(sql)
    if not rows:
        return []

    df = _records_frame(rows, _PK_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["expiry_date"] = epoch_col_to_dt64(
        df["expiry_date"], index=df.index)

    collapsed = _apply_open_expiry_collapse(df)[_PK_COLUMNS].drop_duplicates()

    existing_pks: list = []
    try:
        existing_pks = await conn.fetch(
            f"SELECT extract(epoch from date)::float8 AS date, "
            f"option_type, underlying_code, "
            f"extract(epoch from expiry_date)::float8 AS expiry_date, "
            f"wall_type "
            f"FROM {WALLS_TABLE_NAME}"
        )
    except Exception:
        pass

    # Each expiry group needs the zone wall per option type: duplicate the
    # candidate PK frame per wall type (vectorized), THEN anti-join on
    # the full 5-column PK so a group missing only one wall type is
    # detected per type.
    from analyze.options.config import WALL_TYPES
    candidates = pd.concat(
        [collapsed.assign(wall_type=wt) for wt in WALL_TYPES],
        ignore_index=True,
    )
    return _missing_pk_tuples(
        candidates, existing_pks, _PK_COLUMNS + ["wall_type"],
    )
