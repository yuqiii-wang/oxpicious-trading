"""The joined long-format input frame (analyze.analysis_forecasts
.fetch.inputs).

One row per (code, date) with the indicator columns every bucket family
consumes: price (+ ma / std / rsi / gap / relative-MA + relative-EMA
spreads), trading_amount + rz_buy (px_vol / margin_ratio) and pe +
dividend_yield (valuation). The frame the engines scatter into their
(T, C) grids — loaded through the repo's cudf.pandas contract: every
numeric column cast ``::float8`` at the SQL source (no Decimal object
columns), dates as epoch float8 materialized to datetime64[us] in ONE
host pass, explicit column-list construction so no numeric path ever
sees object dtype.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64

from analyze.analysis_forecasts.config import (
    GAP_WINDOWS,
    MA_WINDOWS,
    MOV_PAIRS_EMA_WINDOWS,
    MOV_PAIRS_WINDOWS,
    RSI_WINDOWS,
)

from ._sources import AMT_SOURCE, PRICE_SOURCE

logger = logging.getLogger(__name__)

# Output column order (matches the SELECT list below).
_COLUMNS = (
    ["code", "date", "price"]
    + [f"ma_{w}days" for w in MA_WINDOWS]
    + [f"rsi_{w}days" for w in RSI_WINDOWS]
    + [f"gap_{w}days" for w in GAP_WINDOWS]
    + [f"std_{w}days" for w in MA_WINDOWS]
    + [f"pair_{w}" for w in MOV_PAIRS_WINDOWS]
    + [f"ema_pair_{w}" for w in MOV_PAIRS_EMA_WINDOWS]
    + ["trading_amount", "rz_buy"]
    + ["pe", "dividend_yield"]
)

# --forecast-metrics stage keys → the extra source columns each family
# consumes (the base code/date/price block is always fetched — every
# engine derives forward changes from price). Families absent from the
# selected set drop their columns AND their LEFT JOINs from the fetch.
_FAMILY_COLUMNS: dict[str, tuple[str, ...]] = {
    "std": tuple(f"ma_{w}days" for w in MA_WINDOWS)
    + tuple(f"std_{w}days" for w in MA_WINDOWS),
    "rsi": tuple(f"rsi_{w}days" for w in RSI_WINDOWS),
    "gap": tuple(f"gap_{w}days" for w in GAP_WINDOWS),
    "pairs": tuple(f"pair_{w}" for w in MOV_PAIRS_WINDOWS),
    "epairs": tuple(f"ema_pair_{w}" for w in MOV_PAIRS_EMA_WINDOWS),
    "mratio": ("trading_amount", "rz_buy"),
    "pe": ("pe",),
    "div": ("dividend_yield",),
}
ALL_FAMILIES = set(_FAMILY_COLUMNS)


def _columns(families) -> list[str]:
    """The output column order for the selected families (base block +
    the union of the families' columns, in _COLUMNS order)."""
    fams = ALL_FAMILIES if families is None else set(families)
    extra = {c for f, cols in _FAMILY_COLUMNS.items() if f in fams
             for c in cols}
    return [c for c in _COLUMNS if c in ("code", "date", "price")
            or c in extra]


async def fetch_analysis_inputs(
    conn,
    sec_type: str,
    codes: list[str],
    since: date,
    families=None,
) -> pd.DataFrame:
    """Fetch the joined long-format input frame for the given codes.

    One row per (code, date) with columns ``_COLUMNS``. Rows are bounded
    to date >= ``since`` (the earliest trailing-window start across the
    target stat months); there is NO upper date bound — forward changes
    for the last bucket days of the newest month need post-month-end
    prices.

    Args:
        conn: asyncpg connection.
        sec_type: 'index', 'etf', or 'stock'.
        codes: active-code universe (pre-filtered).
        since: inclusive lower date bound.

    Returns:
        DataFrame sorted by (code, date) with columns ``_COLUMNS``.
    """
    if not codes:
        return pd.DataFrame(columns=_columns(families))

    base, price_expr = PRICE_SOURCE[sec_type]
    amt_join, amt_expr, rz_expr, est_filter = AMT_SOURCE[sec_type]
    fams = ALL_FAMILIES if families is None else set(families)
    sel, joins = [], []

    def add(select_frag: str, join_frag: str = "") -> None:
        sel.append(select_frag)
        if join_frag:
            joins.append(join_frag)

    add(f"{price_expr}::float8 AS price")
    if "std" in fams or "pairs" in fams:
        joins.append(f"""LEFT JOIN stats.{sec_type}_tech_stats t
               ON t.code = b.code AND t.date = b.date""")
    if "std" in fams:
        add(",\n       ".join(
            f"t.ma{w}::float8 AS ma_{w}days" for w in MA_WINDOWS))
        add(",\n       ".join(
            f"d.std_{w}days::float8 AS std_{w}days" for w in MA_WINDOWS))
    if "std" in fams or "pairs" in fams:
        joins.append(f"""LEFT JOIN analysis.mov_ave_spreads_detail d
               ON d.sec_type = '{sec_type}'
              AND d.code = b.code AND d.date = b.date""")
    if "rsi" in fams or "gap" in fams:
        add(",\n       ".join(
            f"r.rsi_{w}days::float8 AS rsi_{w}days" for w in RSI_WINDOWS))
        add(",\n       ".join(
            f"r.gap_{w}days::float8 AS gap_{w}days" for w in GAP_WINDOWS))
        joins.append(f"""LEFT JOIN analysis.mov_ave_rsi r
               ON r.sec_type = '{sec_type}'
              AND r.code = b.code AND r.date = b.date""")
    if "pairs" in fams:
        add(",\n       ".join(
            f"d.ma5_vs_ma{w}::float8 AS pair_{w}" for w in MOV_PAIRS_WINDOWS))
    if "epairs" in fams:
        add(",\n       ".join(
            f"e.ema6_vs_ema{w}::float8 AS ema_pair_{w}"
            for w in MOV_PAIRS_EMA_WINDOWS))
        joins.append(f"""LEFT JOIN analysis.mov_ave_spreads_detail_ema e
               ON e.sec_type = '{sec_type}'
              AND e.code = b.code AND e.date = b.date""")
    if "mratio" in fams:
        add(f"{amt_expr}::float8 AS trading_amount")
        add(f"{rz_expr}::float8 AS rz_buy")
        if amt_join:
            joins.append(amt_join)
    if "pe" in fams:
        add("pdv.pe::float8 AS pe")
        joins.append(f"""LEFT JOIN analysis.pe pdv
               ON pdv.sec_type = '{sec_type}'
              AND pdv.code = b.code AND pdv.date = b.date""")
    if "div" in fams:
        add("dyv.dividend_yield::float8 AS dividend_yield")
        joins.append(f"""LEFT JOIN analysis.dividends dyv
               ON dyv.sec_type = '{sec_type}'
              AND dyv.code = b.code AND dyv.date = b.date""")

    sql = f"""
        SELECT b.code,
               extract(epoch from b.date)::float8 AS date,
               {",\n       ".join(sel)}
        FROM {base}
        {chr(10).join(joins)}
        WHERE b.code = ANY($1::text[])
          AND b.close IS NOT NULL
          AND b.date >= $2
          {est_filter}
        ORDER BY b.code, b.date ASC
    """

    rows = await conn.fetch(sql, sorted(codes), since)
    if not rows:
        return pd.DataFrame(columns=_columns(families))

    # Column-materialized ctor + epoch→datetime64[us] (object python dates
    # would poison every downstream op; ::float8 casts avoid Decimal).
    df = pd.DataFrame(rec_cols(rows), columns=_columns(families))
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    # SQL-nullable numerics arrive as python None lists; a sec_type with
    # NO rows at all for a column (index: rz_buy is NULL::float8
    # everywhere) makes the ctor build a STRING column — coerce the
    # nullable numerics to float64 (NaN) once at the boundary.
    for col in ("trading_amount", "rz_buy", "pe", "dividend_yield"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df
