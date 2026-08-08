"""DB-upsert orchestrator.

Upserts the classification state to stats.sec_classification +
stats.sec_index_tags + stats.sec_owners, delegating each leaf's rows to its
own upsert module in hierarchy order:

    owners (stats.sec_owners)
      → indices (sec_classification type='index' + sec_index_tags)
      → etfs   (sec_classification type='etf')
      → stocks (sec_classification type='stock')

Labels (sector_label, industry_label, industry_slug) are DENORMALIZED onto
every sec_classification row by looking them up from the in-memory catalog
at upsert time (in each leaf upsert module).  No separate catalog table is
needed — the former stats.sec_sector_industry_map has been DROPPED.

``force`` — when True, truncates stats.sec_classification entirely before
upserting, removing stale rows (e.g. ETFs no longer in the CSV, indices no
longer in the JSON).  When False (default), existing index/ETF rows are
upserted in place (stale rows preserved).  sec_index_tags and sec_owners
are always truncated + rebuilt (needed for correctness when JSON is
hand-edited).  Stock rows are always DELETEd + re-inserted (the set of
qualifying indices can change between runs).

After all leaf upserts, ``_update_is_active`` recomputes the ``is_active``
flag for every row from the identity tables (index/stock/etf_identity):
a security is active iff it has >=1 record in the trailing 365 days.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict

from _common.build_commons import truncate_table_async

from builds.classification.sector_industry.owners import upsert_owners
from builds.classification.sector_industry.index.upsert import (
    upsert_index_tags,
    upsert_indices,
)
from builds.classification.sector_industry.index.etf.upsert import upsert_etfs
from builds.classification.sector_industry.index.stock.upsert import upsert_stocks


async def upsert_to_db(
    conn,
    state: Dict[str, Any],
    verbose: bool = True,
    force: bool = False,
) -> None:
    """Upsert the classification state to the DB (orchestrates the leaf upserts)."""
    catalog = state["catalog"]

    # --- 0. Force mode: truncate sec_classification to remove stale rows ---
    if force:
        if verbose:
            print(f"    [DB] Force mode: truncating stats.sec_classification...",
                  flush=True)
        await truncate_table_async(conn, "stats.sec_classification")

    # --- 0. Migrate old NULL parent_index_code → '' (new NOT NULL schema) ---
    # Idempotent: no-op once all rows have been migrated.
    await conn.execute(
        "UPDATE stats.sec_classification SET parent_index_code = '' "
        "WHERE parent_index_code IS NULL"
    )

    # --- 0b. Upsert owners (stats.sec_owners) ---
    # Runs BEFORE sec_classification so the logical owner_id reference is
    # always valid (truncate + rebuild each run).
    await upsert_owners(conn, state.get("owners", []), verbose=verbose)

    # --- 1. Upsert indices + index tags ---
    await upsert_indices(conn, catalog, state["indices"], verbose=verbose)
    await upsert_index_tags(conn, state["indices"], verbose=verbose)

    # --- 2. Upsert ETFs ---
    await upsert_etfs(conn, catalog, state["etfs"], verbose=verbose)

    # --- 3. Upsert stocks ---
    await upsert_stocks(conn, catalog, state["stocks"], verbose=verbose)

    # --- 4. Recompute is_active from identity tables (trailing 1 year) ---
    await _update_is_active(conn, verbose=verbose)


# Trailing window for the is_active check: a security is active iff it has
# >=1 record in its identity table within the last IS_ACTIVE_LOOKBACK_DAYS days.
IS_ACTIVE_LOOKBACK_DAYS = 365

# (sec_type, identity_table) pairs used to derive is_active.  index codes are
# bare 6-digit; stock/etf codes carry the .SZ/.SS exchange suffix — in both
# cases sec_classification.code matches the identity-table code directly.
_IDENTITY_TABLES = (
    ("index", "stats.index_identity"),
    ("stock", "stats.stock_identity"),
    ("etf", "stats.etf_identity"),
)


async def _update_is_active(conn, verbose: bool = True) -> None:
    """Recompute stats.sec_classification.is_active from identity tables.

    A security is active iff it has >=1 record in the trailing
    IS_ACTIVE_LOOKBACK_DAYS days in its identity table:
        index → stats.index_identity   (code = bare 6-digit)
        stock → stats.stock_identity   (code = with .SZ/.SS suffix)
        etf   → stats.etf_identity     (code = with .SS/.SZ suffix)
    Dummy indices (is_dummy=TRUE) are always active — they are synthetic
    parents for orphan ETFs and have no identity records of their own.
    Everything else (delisted ETFs, dead indices, old stocks with no recent
    data) is marked inactive.
    """
    threshold = datetime.date.today() - datetime.timedelta(
        days=IS_ACTIVE_LOOKBACK_DAYS)

    # Reset all rows to inactive, then mark active based on recent records.
    await conn.execute("UPDATE stats.sec_classification SET is_active = FALSE")

    # Dummy indices are synthetic parents — always active.
    await conn.execute(
        "UPDATE stats.sec_classification SET is_active = TRUE "
        "WHERE is_dummy = TRUE")

    for sec_type, identity_table in _IDENTITY_TABLES:
        await conn.execute(f"""
            UPDATE stats.sec_classification sc
               SET is_active = TRUE
              FROM (
                    SELECT code
                      FROM {identity_table}
                     WHERE date >= $1::date
                     GROUP BY code
                   ) recent
             WHERE sc.code = recent.code
               AND sc.type = $2
        """, threshold, sec_type)

    if verbose:
        row = await conn.fetchrow(
            "SELECT COUNT(*) FILTER (WHERE is_active) AS active, "
            "COUNT(*) AS total FROM stats.sec_classification")
        print(f"    [DB] is_active: {row['active']:,}/{row['total']:,} rows "
              f"active (threshold {threshold.isoformat()})", flush=True)
