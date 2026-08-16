"""builds/options/tables.py — Shared 7-table split + COPY insert for options builds.

Both builds.options.szse and builds.options.cffex write the same 7
stats.options_* tables from one wide per-(date, contract_code) frame.
This module deduplicates that logic (previously a slow iterrows loop
duplicated verbatim in both builds) into a column-wise split, and loads
the result via PostgreSQL COPY (``_common.db_commons.copy_insert_async``),
the fastest bulk path — safe here because both builds only insert dates
that are missing from stats.options_identity (PK-checked upstream) and
dedupe within the batch on (date, contract_code).

Table order matters: options_identity is the FK parent and is inserted
first; dict ordering preserves it.

USAGE
=====

    from builds.options.tables import build_split_tables, insert_split_tables

    tables = build_split_tables(options_db,
                                underlying_target_type="ETF",
                                exchange="SZSE")
    await insert_split_tables(conn, tables)
"""
from __future__ import annotations

from typing import Dict, List

import pandas as pd

from _common.db_commons import copy_insert_async

# ---------------------------------------------------------------------------
# Column split: db table -> {db column: frame column or scalar constant}
# ---------------------------------------------------------------------------
def build_split_tables(
    options_db: pd.DataFrame,
    *,
    underlying_target_type: str,
    exchange: str,
) -> Dict[str, List[dict]]:
    """Split the wide options frame into the 7 stats.options_* tables.

    Args:
        options_db: deduped frame with datetime.date ``date`` /
            ``expiry_date`` columns and all derived columns.
        underlying_target_type: 'ETF' (SZSE) or 'INDEX' (CFFEX).
        exchange: 'SZSE' or 'CFFEX'.

    Returns:
        Ordered dict {table_name: list of row dicts}, FK parent
        (options_identity) first.
    """
    n = len(options_db)
    df = options_db
    const = lambda v: pd.Series([v] * n, index=df.index)  # noqa: E731

    def recs(mapping: Dict[str, object]) -> List[dict]:
        sub = pd.DataFrame({
            db_col: (df[src] if isinstance(src, str) else src)
            for db_col, src in mapping.items()
        })
        return sub.to_dict("records")

    identity = recs({
        "date": "date",
        "contract_code": "contract_code",
        "contract_name": "contract_name",
    })

    terms = recs({
        "date": "date",
        "contract_code": "contract_code",
        "underlying_code": "underlying_code",
        "underlying_name": "underlying_name",
        "underlying_target_type": const(underlying_target_type),
        "exchange": const(exchange),
        "option_type": "option_type",
        "expiry_month": "expiry_month",
        "expiry_date": "expiry_date",
        "days_to_expiry": df["days_to_expiry"].astype(int),
    })

    strike = recs({
        "date": "date",
        "contract_code": "contract_code",
        "strike_str": "strike_str",
        "strike_price_raw": "strike_price_raw",
        "strike_price": "strike_price",
        "has_a_suffix": df["has_a_suffix"].astype(int),
    })

    settlement = recs({
        "date": "date",
        "contract_code": "contract_code",
        "prev_settle": "prev_settle",
        "close": "close",
        "settle": "settle",
        "pct_change": "pct_change",
        "prev_settle_norm": "prev_settle_norm",
        "close_norm": "close_norm",
        "settle_norm": "settle_norm",
        "underlying_close": "underlying_close",
        "moneyness_ratio": "moneyness_ratio",
    })

    greeks = recs({
        "date": "date",
        "contract_code": "contract_code",
        "implied_vol": "implied_vol",
        "delta": "delta",
        "theta": "theta",
        "gamma": "gamma",
        "vega": "vega",
        "rho": "rho",
    })

    volume_oi = recs({
        "date": "date",
        "contract_code": "contract_code",
        "volume": "volume",
        "volume_wan": "volume_wan",
        "open_interest": "open_interest",
        "open_interest_wan": "open_interest_wan",
    })

    aggregate = recs({
        "date": "date",
        "contract_code": "contract_code",
        "total_volume_underlying": "total_volume_underlying",
        "total_oi_underlying": "total_oi_underlying",
        "volume_pct": "volume_pct",
        "open_interest_pct": "open_interest_pct",
        "oi_call_put_ratio": "oi_call_put_ratio",
        "vol_call_put_ratio": "vol_call_put_ratio",
        "open_interest_call": "open_interest_call",
        "open_interest_put": "open_interest_put",
        "volume_call": "volume_call",
        "volume_put": "volume_put",
        "oi_total_call_put_ratio": "oi_total_call_put_ratio",
    })

    # FK parent first — dict ordering is preserved.
    return {
        "stats.options_identity": identity,
        "stats.options_terms": terms,
        "stats.options_strike": strike,
        "stats.options_settlement": settlement,
        "stats.options_greeks": greeks,
        "stats.options_volume_oi": volume_oi,
        "stats.options_aggregate": aggregate,
    }


async def insert_split_tables(conn, tables: Dict[str, List[dict]]) -> None:
    """COPY-insert each split table (FK parent first)."""
    for tbl, rows in tables.items():
        if rows:
            inserted = await copy_insert_async(conn, tbl, rows)
            print(f"    [DB] Inserted {inserted:,} rows into {tbl}", flush=True)
        else:
            print(f"    [DB] No new rows to insert into {tbl}", flush=True)
