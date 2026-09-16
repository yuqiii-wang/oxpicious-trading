"""Entry point for analyze.futures.

Run via ``python -m analyze.futures``.

Pipeline:
  1. Fetch futures basic stats + identity with underlying_code mapping.
  2. Fetch underlying data:
     - Index futures → stats.index_basic_stats.close
     - Bond futures  → stats.debt_treasury yield (converted to bond price)
  3. Compute per-(date, code) gap, changing_rate (1st-order derivative
     of the gap — convergence/divergence direction), correlation
     metrics, and the rolling AR(1) of the basis (slope + half-life).
  4. Write rows to analysis.futures_ext:
     - ``--force``: DELETE all + chunked COPY-insert.
     - default:     chunked upsert (ON CONFLICT DO UPDATE on PK).
  5. Compute the basis-convergence quintile summary (mean forward gap
     change per gap quintile per contract_type) and upsert it into
     analysis.futures_gap_quintiles (asof_date-stamped snapshots).
  6. Upsert analysis.analysis_identity (name='futures_ext' and
     name='futures_gap_quintiles').

Incremental mode rationale:
  The gap metrics (basis) for past dates are historical facts — futures
  close prices and index/treasury data don't change retroactively. So
  existing rows are valid; only new (date, code) pairs need computation.
"""
from __future__ import annotations


# Runtime bootstrap — pre-check → silence warnings → cudf.pandas hook →
# UTF-8 stdout. MUST run before the pandas import below (import hook).
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from _common.db_commons import (
    copy_or_upsert_split_async,
    copy_insert_async,
)

import pandas as pd

from analyze._common import (
    sanitize_for_db_insert,
)
from analyze.futures.config import (
    TABLE_NAME,
    ANALYSIS_NAME,
    DESCRIPTION,
    NUMERIC_COLS,
    BOND_PRODUCT_TENOR,
    INDEX_PRODUCT_UNDERLYING,
    QUINTILE_TABLE_NAME,
    QUINTILE_ANALYSIS_NAME,
    QUINTILE_DESCRIPTION,
    QUINTILE_NUMERIC_COLS,
)
from analyze.futures.fetch import (
    fetch_futures_data,
    fetch_futures_identity_dates,
)
from analyze.futures.compute import (
    compute_futures_ext,
    compute_gap_quintile_summary,
)

from _common.log_setup import setup_logging
from _common.data_analysis import DataAnalysis

logger = setup_logging("futures")


_CHUNK_SIZE = 10000

# PK columns for ON CONFLICT in incremental upsert mode.
_PK_COLUMNS = ["date", "code"]


async def _find_missing_dates(conn) -> list:
    """Return list of (date, code) tuples that need computation.

    A (date, code) needs computation when it exists in
    stats.futures_identity but not in analysis.futures_ext.
    """
    return await fetch_futures_identity_dates(conn)


async def _write_rows(
    conn,
    result_df: pd.DataFrame,
    *,
    force: bool,
    target_pairs: set | None,
) -> int:
    """Write futures_ext rows to the DB.

    - force: DELETE all + chunked COPY-insert.
    - incremental: filter to target_pairs + chunked upsert.

    Returns:
        Number of rows written.
    """
    if result_df.empty:
        logger.info("  no rows to write")
        return 0

    if force:
        logger.info(f"  Deleting existing rows from {TABLE_NAME}...")
        await conn.execute(f"DELETE FROM {TABLE_NAME}")
    else:
        if target_pairs is not None and len(target_pairs) == 0:
            logger.info("  up to date; nothing to insert.")
            return 0

        if target_pairs is not None:
            # Filter rows to only those in target_pairs
            n_before = len(result_df)
            result_df = result_df[
                result_df.apply(
                    lambda r: (r["date"], r["code"]) in target_pairs,
                    axis=1,
                )
            ].reset_index(drop=True)
            logger.info(f"  Incremental filter: {len(result_df):,} of "
                  f"{n_before:,} rows are in target dates")

    if result_df.empty:
        logger.info("  no rows to write after filter")
        return 0

    # Chunked write
    n_chunks = (len(result_df) + _CHUNK_SIZE - 1) // _CHUNK_SIZE
    total = 0

    logger.info(f"  {'COPY' if force else 'Upsert'}ing {len(result_df):,} rows "
          f"in {n_chunks} chunks...")

    for i in range(n_chunks):
        chunk = result_df.iloc[
            i * _CHUNK_SIZE : (i + 1) * _CHUNK_SIZE
        ].copy()

        rows = sanitize_for_db_insert(
            chunk,
            numeric_cols=NUMERIC_COLS,
            round_to=4,
        )
        if not rows:
            continue

        if force:
            n = await copy_insert_async(conn, TABLE_NAME, rows)
        else:
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, TABLE_NAME, rows, key_columns=_PK_COLUMNS,
            )
            n = n_copied + n_upserted
        total += n
        via = "COPY" if force else (
            "COPY" if n_copied > 0 and n_upserted == 0 else
            f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else
            "upsert"
        )
        logger.info(f"    chunk {i + 1}/{n_chunks}: "
              f"{via} {n:,} rows "
              f"(cumulative {total:,})")

    logger.info(f"  wrote {total:,} rows total")
    return total


async def _write_quintile_summary(
    conn,
    summary_df: pd.DataFrame,
) -> int:
    """Upsert the basis-convergence quintile summary snapshot.

    The table PK is (asof_date, contract_type, horizon_days, quintile),
    where asof_date stamps the run — re-running on the same day
    upserts in place, a new day appends a fresh snapshot (small
    history of the convergence stats). The table is tiny (≤ ~20 rows
    per run), so a single upsert call suffices.
    """
    if summary_df.empty:
        logger.info("  no quintile summary rows to write")
        return 0

    rows = sanitize_for_db_insert(
        summary_df,
        numeric_cols=QUINTILE_NUMERIC_COLS,
        round_to=4,
    )
    if not rows:
        return 0

    n_copied, n_upserted = await copy_or_upsert_split_async(
        conn, QUINTILE_TABLE_NAME, rows,
        key_columns=["asof_date", "contract_type",
                     "horizon_days", "quintile"],
        date_column="asof_date",
    )
    n = n_copied + n_upserted
    logger.info(f"  upserted {n:,} quintile summary rows into "
          f"{QUINTILE_TABLE_NAME} (asof_date={rows[0]['asof_date']})")
    return n


class FuturesExtAnalysis(DataAnalysis):
    """``python -m analyze.futures`` — basis + correlation vs underlying.

    The DB connection is owned by the DataAnalysis amain() template
    (self.conn): opened before run(), timed-closed after.
    """

    title = "ANALYZE FUTURES EXT (basis + correlation vs underlying)"
    component = "futures"

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)

    def header_fields(self) -> dict:
        return {
            "table": TABLE_NAME,
            "bond_products": ", ".join(BOND_PRODUCT_TENOR.keys()),
            "index_products": ", ".join(INDEX_PRODUCT_UNDERLYING.keys()),
            "mode": "FORCE (full recompute)" if self.args.force
                    else "incremental (missing dates only)",
        }

    async def run(self) -> None:
        conn = self.conn
        force = self.args.force

        # ---- Detect missing dates (incremental mode) --------------------
        target_pairs: set | None = None
        if not force:
            logger.info("\n  Detecting missing (date, code) pairs...")
            missing_list = await _find_missing_dates(conn)
            target_pairs = set(missing_list)
            logger.info(f"    -> {len(target_pairs):,} missing (date, code) pairs")
            if len(target_pairs) == 0:
                logger.info("    -> DB is up to date; nothing to do.")
                return

        # ---- Step 1: fetch futures + underlying data ---------------------
        logger.info("\n  [1/4] Fetching futures + underlying data...")
        df = await fetch_futures_data(conn)
        logger.info(f"    {len(df):,} (date, code) rows with valid underlying data")
        if df.empty:
            logger.info("    no data; skipping.")
            return

        # ---- Step 2: compute metrics ------------------------------------
        logger.info("\n  [2/4] Computing futures_ext metrics...")
        result_df = compute_futures_ext(df)
        logger.info(f"    {len(result_df):,} result rows")

        # ---- Step 3: write detail rows to DB ----------------------------
        logger.info("\n  [3/4] Writing detail rows to DB...")
        n = await _write_rows(
            conn, result_df, force=force, target_pairs=target_pairs,
        )

        # ---- Step 4: write quintile summary + identity registry ---------
        logger.info("\n  [4/4] Writing basis-convergence quintile summary...")
        # The summary pools the FULL history (both modes fetch the full
        # underlying data), so it is rebuilt on every run regardless of
        # the incremental detail filter.
        summary_df = compute_gap_quintile_summary(result_df, df)
        logger.info(f"    {len(summary_df):,} quintile summary rows "
              f"(asof {summary_df['asof_date'].max() if not summary_df.empty else 'n/a'})")
        n_q = await _write_quintile_summary(conn, summary_df)

        # ---- Upsert analysis_identity -----------------------------------
        logger.info("\n  -> Upserting analysis.analysis_identity registry...")
        await self.upsert_identity(
            ANALYSIS_NAME, "futures_ext", DESCRIPTION,
        )
        await self.upsert_identity(
            QUINTILE_ANALYSIS_NAME, QUINTILE_TABLE_NAME, QUINTILE_DESCRIPTION,
        )

        logger.info(f"\n  TOTAL: {n:,} detail rows + {n_q:,} quintile "
              f"summary rows written")


if __name__ == "__main__":
    FuturesExtAnalysis().execute()
