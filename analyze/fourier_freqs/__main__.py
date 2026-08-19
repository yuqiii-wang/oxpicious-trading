"""Entry point for analyze.fourier_freqs.

Run via ``python -m analyze.fourier_freqs``.

Pipeline (default: incremental — skip dates already in the DB;
``--force``: truncate-then-recompute per sec_type on every run).

Per sec_type (index / etf / stock):
  1. Fetch active-universe codes from the identity table (recent-data
     pre-filter: only codes with data in the last RECENT_TRADING_DAYS
     trading days are included).
  2. Fetch FULL per-(code, date) close price history from the stats
     schema (FFT windows need up to 750 prior trading days, so full
     history is always fetched regardless of incremental/force mode).
  3. Compute dominant Fourier frequency per (code, last_date, range_days)
     via sliding-window real FFT. In incremental mode, target_dates
     filters which windows to OUTPUT (the FFT still uses the full history
     for each window).
  4. Write rows to analysis.fourier_freqs:
     - ``--force``: DELETE sec_type rows + chunked COPY-insert.
     - default: chunked upsert only rows whose last_date is in the
       missing-dates set (ON CONFLICT DO UPDATE on PK).
  5. Upsert analysis_identity.

Incremental mode rationale
  The dominant frequency for a past date doesn't change retroactively
  (close prices are historical facts), so existing rows are valid. New
  dates get appended via upsert. The FFT computation still uses FULL
  history (fetched unconditionally) so the sliding window is correct
  even for the first newly-added date.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# Ensure project root is on sys.path so ``_common`` is importable when run
# directly via ``python -m analyze.fourier_freqs`` or as a script.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    print_build_header,
    print_wall_time,
    add_force_arg,
)
from _common.db_commons import (  # noqa: E402
    copy_or_upsert_split_async,
    copy_insert_async,
)

setup_utf8_stdout()

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from analyze._common import (  # noqa: E402
    sanitize_for_db_insert,
    upsert_analysis_identity,
)
from analyze.fourier_freqs.config import (  # noqa: E402
    TABLE_NAME,
    ANALYSIS_NAME,
    DESCRIPTION,
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
    RANGE_DAYS,
)
from analyze.fourier_freqs.fetch import (  # noqa: E402
    fetch_active_codes,
    fetch_close_prices,
)
from analyze.fourier_freqs.compute import (  # noqa: E402
    compute_fourier_freqs,
    NUMERIC_COLS,
)


# Chunk size for COPY / upsert. Reduced from 100K → 10K because each row
# now carries an amplitude_spectrum array (up to 375 doubles for
# range_days=750). At 10K rows × ~9 KB/row-dict ≈ 90 MB per chunk — safe
# alongside the ~4 GB result_df held during the write.
_CHUNK_SIZE = 10_000

# PK columns for ON CONFLICT in incremental upsert mode.
_PK_COLUMNS = ["sec_type", "code", "last_date", "range_days"]


# ---------------------------------------------------------------------------
#  Missing-date detection (incremental mode)
# ---------------------------------------------------------------------------

async def _find_missing_dates(
    conn,
    sec_type: str,
) -> set:
    """Return dates that need (re)computation for the given sec_type.

    A date needs computation when EITHER:
      1. It is present in the source identity table but NOT yet in
         analysis.fourier_freqs (a genuinely new trading day), OR
      2. It IS in fourier_freqs but ``amplitude_spectrum`` is NULL
         (legacy rows written before the spectrum column was added —
         these need a backfill recompute so the per-date spectrum bar
         charts have data).

    The analysis table uses ``last_date`` as its date column (not
    ``date``), so the standard ``find_missing_analysis_dates`` helper
    can't be used directly (it assumes the same date_column name in both
    the analysis and source tables).

    Args:
        conn: asyncpg connection.
        sec_type: 'index', 'etf', or 'stock'.

    Returns:
        Set of datetime.date values needing (re)computation. Empty set
        if up to date.
    """
    identity_table = SEC_TYPE_IDENTITY_TABLE[sec_type]

    # Source dates from identity table (column name = "date").
    source_rows = await conn.fetch(
        f"SELECT DISTINCT date FROM {identity_table}"
    )
    source_dates = {
        r["date"] for r in source_rows if r["date"] is not None
    }

    # Existing dates in fourier_freqs (column name = "last_date").
    existing_rows = await conn.fetch(
        f"SELECT DISTINCT last_date FROM {TABLE_NAME} WHERE sec_type = $1",
        sec_type,
    )
    existing_dates = {
        r["last_date"] for r in existing_rows
        if r["last_date"] is not None
    }

    missing_dates = source_dates - existing_dates

    # Also flag dates where amplitude_spectrum is NULL — legacy rows that
    # pre-date the spectrum column. These get upserted (ON CONFLICT DO
    # UPDATE) to backfill the array without touching freq/amplitude.
    null_spectrum_rows = await conn.fetch(
        f"SELECT DISTINCT last_date FROM {TABLE_NAME} "
        f"WHERE sec_type = $1 AND amplitude_spectrum IS NULL",
        sec_type,
    )
    null_spectrum_dates = {
        r["last_date"] for r in null_spectrum_rows
        if r["last_date"] is not None
    }

    return missing_dates | null_spectrum_dates


# ---------------------------------------------------------------------------
#  Write helpers (force = DELETE + chunked COPY; incremental = chunked upsert)
# ---------------------------------------------------------------------------

async def _write_rows(
    conn,
    sec_type: str,
    result_df: pd.DataFrame,
    *,
    force: bool,
    target_dates: set | None,
) -> int:
    """Write fourier_freqs rows to the DB.

    - force: DELETE sec_type rows + chunked COPY-insert (no conflicts
      because the table was just cleared for this sec_type).
    - incremental: filter to target_dates rows + chunked upsert on the
      full PK (ON CONFLICT DO UPDATE). Skipped entirely when target_dates
      is empty.

    Args:
        conn: asyncpg connection.
        sec_type: 'index', 'etf', or 'stock'.
        result_df: DataFrame from compute_fourier_freqs.
        force: when True, DELETE + COPY-insert. When False, upsert.
        target_dates: set of missing dates (incremental mode). Ignored
            when force is True.

    Returns:
        Number of rows written.
    """
    if result_df.empty:
        print(f"  [{sec_type}]   no rows to write", flush=True)
        return 0

    if force:
        print(f"  [{sec_type}] Deleting existing {sec_type} rows from "
              f"{TABLE_NAME}...", flush=True)
        await conn.execute(
            f"DELETE FROM {TABLE_NAME} WHERE sec_type = $1", sec_type
        )
    else:
        # Incremental: filter to target_dates only.
        if target_dates is not None:
            if len(target_dates) == 0:
                print(f"  [{sec_type}]   up to date; skipping insert.",
                      flush=True)
                return 0
            n_before = len(result_df)
            result_df = result_df[
                result_df["last_date"].isin(target_dates)
            ].reset_index(drop=True)
            print(f"  [{sec_type}] Incremental filter: {len(result_df):,} "
                  f"of {n_before:,} rows are in target_dates", flush=True)

    if result_df.empty:
        print(f"  [{sec_type}]   no rows to write after filter",
              flush=True)
        return 0

    # Chunked insert to bound peak memory (~100K rows per chunk).
    n_chunks = (len(result_df) + _CHUNK_SIZE - 1) // _CHUNK_SIZE
    total = 0

    if force:
        print(f"  [{sec_type}] COPY-inserting {len(result_df):,} rows "
              f"in {n_chunks} chunks...", flush=True)
    else:
        print(f"  [{sec_type}] Upserting {len(result_df):,} rows "
              f"in {n_chunks} chunks...", flush=True)

    for i in range(n_chunks):
        chunk = result_df.iloc[
            i * _CHUNK_SIZE : (i + 1) * _CHUNK_SIZE
        ].copy()
        # Convert amplitude_spectrum from numpy 1-D arrays to Python lists.
        # asyncpg encodes Python lists as Postgres double-precision arrays
        # but has no codec for numpy arrays. sanitize_for_db_insert only
        # handles scalar numeric cols, so this array→list conversion must
        # happen here (before sanitize). tolist() is C-level — fast.
        if "amplitude_spectrum" in chunk.columns:
            chunk["amplitude_spectrum"] = chunk["amplitude_spectrum"].apply(
                lambda v: v.tolist() if isinstance(v, np.ndarray) else v
            )
        rows = sanitize_for_db_insert(
            chunk,
            numeric_cols=NUMERIC_COLS,
            round_to=10,
        )
        if not rows:
            continue

        if force:
            n = await copy_insert_async(conn, TABLE_NAME, rows)
        else:
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, TABLE_NAME, rows, key_columns=_PK_COLUMNS,
                date_column="last_date",
            )
            n = n_copied + n_upserted
        total += n
        via = "COPY" if force else (
            "COPY" if n_copied > 0 and n_upserted == 0 else
            f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else
            "upsert"
        )
        print(f"    chunk {i + 1}/{n_chunks}: "
              f"{via} {n:,} rows "
              f"(cumulative {total:,})", flush=True)

    print(f"  [{sec_type}]   wrote {total:,} rows total", flush=True)
    return total


# ---------------------------------------------------------------------------
#  Per-sec_type pipeline
# ---------------------------------------------------------------------------

async def _process_sec_type(
    conn,
    sec_type: str,
    *,
    force: bool,
    target_dates: set | None,
) -> int:
    """Process one sec_type end-to-end.

    1. Fetch active codes (recent-data pre-filter).
    2. Fetch full close-price history.
    3. Compute dominant Fourier frequency per (code, last_date, range_days).
    4. Write to DB (force: DELETE + COPY; incremental: upsert).

    Returns the number of rows written.
    """
    print(f"\n  [{sec_type}] Fetching active codes...", flush=True)
    codes = await fetch_active_codes(conn, sec_type)
    code_list = sorted(codes)
    print(f"  [{sec_type}]   {len(code_list):,} active codes", flush=True)
    if not code_list:
        print(f"  [{sec_type}]   no active codes; skipping.", flush=True)
        return 0

    # ---- Fetch close prices (full history) -------------------------------
    print(f"  [{sec_type}] Fetching full close-price history for "
          f"{len(code_list):,} codes...", flush=True)
    close_df = await fetch_close_prices(conn, sec_type, code_list)
    print(f"  [{sec_type}]   {len(close_df):,} (code, date) rows",
          flush=True)

    if close_df.empty:
        print(f"  [{sec_type}]   no close-price data; skipping.",
              flush=True)
        return 0

    # ---- Compute dominant Fourier frequency ------------------------------
    print(f"  [{sec_type}] Computing dominant Fourier frequency "
          f"(range_days={list(RANGE_DAYS)})...", flush=True)
    result_df = compute_fourier_freqs(
        close_df, sec_type, RANGE_DAYS,
        target_dates=target_dates if not force else None,
    )
    print(f"  [{sec_type}]   {len(result_df):,} fourier-freqs rows",
          flush=True)

    # ---- Write to DB -----------------------------------------------------
    n = await _write_rows(
        conn, sec_type, result_df,
        force=force, target_dates=target_dates,
    )
    return n


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Fourier Frequency Analysis (ETF + Index + Stock). "
                    "Computes dominant cycle period and amplitude from "
                    "real FFT of close prices per (sec_type, code, "
                    "last_date, range_days)."
    )
    ap.add_argument(
        "--sec-type", choices=("index", "etf", "stock"), default=None,
        help="Process only this sec_type (for testing). Default: all.",
    )
    add_force_arg(ap)
    args = ap.parse_args()
    force = args.force

    sec_types = (args.sec_type,) if args.sec_type else SEC_TYPES

    t0 = time.time()
    print_build_header(
        "ANALYZE FOURIER FREQS (dominant cycle via real FFT on close)",
        table=TABLE_NAME,
        sec_types=", ".join(sec_types),
        mode="FORCE (full recompute per sec_type)" if force
             else "incremental (missing dates only)",
    )

    conn = await get_db_connection_async()
    try:
        # ---- Detect missing dates (incremental mode) --------------------
        target_dates_by_sec_type: dict[str, set] = {}
        if not force:
            print("\n  Detecting missing dates per sec_type "
                  "(incremental mode)...", flush=True)
            for st in sec_types:
                missing = await _find_missing_dates(conn, st)
                target_dates_by_sec_type[st] = missing
                print(f"    -> [{st}]: {len(missing)} missing dates",
                      flush=True)

            # Early exit if everything is up to date.
            total_missing = sum(
                len(td) for td in target_dates_by_sec_type.values()
            )
            if total_missing == 0:
                print("    -> DB is up to date; nothing to do.",
                      flush=True)
                print_wall_time(t0)
                return

        # ---- Process each sec_type --------------------------------------
        total = 0
        for st in sec_types:
            td = None if force else target_dates_by_sec_type.get(st, set())
            if not force and td is not None and len(td) == 0:
                print(f"\n  [{st}] up to date; skipping.", flush=True)
                continue
            n = await _process_sec_type(
                conn, st, force=force, target_dates=td,
            )
            total += n

        # ---- Upsert analysis_identity -----------------------------------
        print(f"\n  -> Upserting analysis.analysis_identity registry...",
              flush=True)
        await upsert_analysis_identity(
            conn,
            name=ANALYSIS_NAME,
            detail_name="fourier_freqs",
            description=DESCRIPTION,
        )

        print(f"\n  TOTAL: {total:,} rows written", flush=True)
        print_wall_time(t0)
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    asyncio.run(main())
