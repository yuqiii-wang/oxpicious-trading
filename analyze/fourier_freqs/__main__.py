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
     - default: chunked upsert only rows whose (code, last_date) is in
       the per-code missing-target set (ON CONFLICT DO UPDATE on PK).
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
    SEC_TYPE_CLOSE_TABLE,
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

async def _find_missing_targets(
    conn,
    sec_type: str,
    codes: list[str],
) -> dict[str, set]:
    """Return per-code date sets needing (re)computation.

    A (code, date, range_days) target needs computation when the code
    has a close on that date with enough prior close history for the
    window (its close-date rank >= range_days), but analysis.fourier_freqs
    has NO row with a non-NULL amplitude_spectrum at that exact PK.
    Legacy rows with a NULL spectrum therefore count as missing and are
    backfilled by the recompute-upsert.

    Detecting gaps at the FULL PK granularity (code × date × window) —
    not just distinct dates — catches per-code holes that global
    date-level detection masks forever: a single row for one code and
    one window on a date (e.g. a stray test write) makes that date look
    "done" for EVERY code, permanently freezing every other code's
    spectrum at the previous date.

    Scoped to the given active codes so delisted/suspended codes with
    unfillable gaps don't inflate the target set every run.

    Args:
        conn: asyncpg connection.
        sec_type: 'index', 'etf', or 'stock'.
        codes: active-code universe (from fetch_active_codes).

    Returns:
        dict code -> set of datetime.date needing (re)computation.
        Empty dict if up to date.
    """
    close_table = SEC_TYPE_CLOSE_TABLE[sec_type]

    rows = await conn.fetch(
        f"""
        WITH close_dates AS (
            SELECT DISTINCT code, date
            FROM {close_table}
            WHERE close IS NOT NULL AND code = ANY($2::text[])
        ),
        ranked AS (
            SELECT code, date,
                   ROW_NUMBER() OVER (PARTITION BY code ORDER BY date) AS rn
            FROM close_dates
        ),
        expected AS (
            SELECT r.code, r.date, rd.range_days
            FROM ranked r
            CROSS JOIN unnest($3::int[]) AS rd(range_days)
            WHERE r.rn >= rd.range_days
        )
        SELECT DISTINCT e.code, e.date
        FROM expected e
        LEFT JOIN {TABLE_NAME} f
            ON f.sec_type = $1
            AND f.code = e.code
            AND f.last_date = e.date
            AND f.range_days = e.range_days
            AND f.amplitude_spectrum IS NOT NULL
        WHERE f.code IS NULL
        """,
        sec_type, codes, list(RANGE_DAYS),
    )

    targets: dict[str, set] = {}
    for r in rows:
        targets.setdefault(r["code"], set()).add(r["date"])
    return targets


# ---------------------------------------------------------------------------
#  Write helpers (force = DELETE + chunked COPY; incremental = chunked upsert)
# ---------------------------------------------------------------------------

async def _write_rows(
    conn,
    sec_type: str,
    result_df: pd.DataFrame,
    *,
    force: bool,
    target_dates: dict[str, set] | None,
    code: str | None = None,
) -> int:
    """Write fourier_freqs rows to the DB.

    - force: DELETE sec_type rows + chunked COPY-insert (no conflicts
      because the table was just cleared for this sec_type).
    - single-code mode (``code``): DELETE the code's rows + chunked
      COPY-insert (scoped rebuild for the UI per-security build button).
    - incremental: filter to target rows + chunked upsert on the full
      PK (ON CONFLICT DO UPDATE). Skipped entirely when target_dates
      is empty.

    Args:
        conn: asyncpg connection.
        sec_type: 'index', 'etf', or 'stock'.
        result_df: DataFrame from compute_fourier_freqs.
        force: when True, DELETE + COPY-insert. When False, upsert.
        target_dates: per-code missing date sets (code -> set of dates,
            incremental mode). Ignored when force is True.
        code: single-code mode — recompute ALL windows for this one
            code (DELETE its rows first, then COPY-insert). Mutually
            exclusive with force.

    Returns:
        Number of rows written.
    """
    if result_df.empty:
        print(f"  [{sec_type}]   no rows to write", flush=True)
        return 0

    if code is not None:
        print(f"  [{sec_type}] SINGLE-CODE mode: deleting existing rows "
              f"for {code} from {TABLE_NAME}...", flush=True)
        await conn.execute(
            f"DELETE FROM {TABLE_NAME} WHERE sec_type = $1 AND code = $2",
            sec_type, code,
        )
    elif force:
        print(f"  [{sec_type}] Deleting existing {sec_type} rows from "
              f"{TABLE_NAME}...", flush=True)
        await conn.execute(
            f"DELETE FROM {TABLE_NAME} WHERE sec_type = $1", sec_type
        )
    else:
        # Incremental: filter to target dates only. compute_fourier_freqs
        # already applied per-code target masks before the FFT, so
        # result_df contains ONLY target rows — this union-of-dates isin
        # is a defensive no-op that also handles the empty-target skip.
        if target_dates is not None:
            all_target_dates: set = (
                set().union(*target_dates.values()) if target_dates
                else set()
            )
            if not all_target_dates:
                print(f"  [{sec_type}]   up to date; skipping insert.",
                      flush=True)
                return 0
            n_before = len(result_df)
            result_df = result_df[
                result_df["last_date"].isin(all_target_dates)
            ].reset_index(drop=True)
            print(f"  [{sec_type}] Incremental filter: {len(result_df):,} "
                  f"of {n_before:,} rows are in target dates", flush=True)

    if result_df.empty:
        print(f"  [{sec_type}]   no rows to write after filter",
              flush=True)
        return 0

    # Chunked insert to bound peak memory (~100K rows per chunk).
    n_chunks = (len(result_df) + _CHUNK_SIZE - 1) // _CHUNK_SIZE
    total = 0

    if force or code is not None:
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

        if force or code is not None:
            n = await copy_insert_async(conn, TABLE_NAME, rows)
        else:
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, TABLE_NAME, rows, key_columns=_PK_COLUMNS,
                date_column="last_date",
            )
            n = n_copied + n_upserted
        total += n
        via = "COPY" if (force or code is not None) else (
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
    code: str | None = None,
) -> int:
    """Process one sec_type end-to-end.

    1. Fetch active codes (recent-data pre-filter). Single-code mode
       (``code``) bypasses the pre-filter and processes just that code.
    2. Incremental mode: detect missing (code, date, range_days) targets
       scoped to those codes; skip early when up to date. Single-code
       mode skips detection (recompute ALL windows for the code).
    3. Fetch full close-price history.
    4. Compute dominant Fourier frequency per (code, last_date, range_days).
    5. Write to DB (force / single-code: DELETE + COPY; incremental:
       upsert).

    Returns the number of rows written.
    """
    if code is not None:
        # Single-code mode (--code): bypass the active-universe pre-filter
        # and the incremental missing-target detection — the UI fires this
        # when a security has NO rows while the rest of the sec_type is up
        # to date (date-level detection would see nothing missing).
        code_list = [code]
        print(f"\n  [{sec_type}] SINGLE-CODE mode: processing {code}",
              flush=True)
    else:
        print(f"\n  [{sec_type}] Fetching active codes...", flush=True)
        codes = await fetch_active_codes(conn, sec_type)
        code_list = sorted(codes)
        print(f"  [{sec_type}]   {len(code_list):,} active codes", flush=True)
        if not code_list:
            print(f"  [{sec_type}]   no active codes; skipping.", flush=True)
            return 0

    # ---- Detect missing targets (incremental mode) ------------------------
    target_dates: dict[str, set] | None = None
    if code is None and not force:
        print(f"  [{sec_type}] Detecting missing (code, date, window) "
              f"targets...", flush=True)
        target_dates = await _find_missing_targets(
            conn, sec_type, code_list
        )
        n_gap_codes = len(target_dates)
        n_target_dates = (
            len(set().union(*target_dates.values())) if target_dates else 0
        )
        print(f"  [{sec_type}]   {n_gap_codes:,} codes with gaps; "
              f"{n_target_dates:,} distinct missing dates", flush=True)
        if not target_dates:
            print(f"  [{sec_type}]   up to date; skipping.", flush=True)
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
        target_dates=target_dates,
    )
    print(f"  [{sec_type}]   {len(result_df):,} fourier-freqs rows",
          flush=True)

    # ---- Write to DB -----------------------------------------------------
    n = await _write_rows(
        conn, sec_type, result_df,
        force=force, target_dates=target_dates, code=code,
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
    ap.add_argument(
        "--code", default=None,
        help="Recompute ALL windows for this single security only "
             "(single-code mode; used by the UI per-security build "
             "button). Deletes the code's rows first, then rebuilds "
             "them. Mutually exclusive with --force.",
    )
    add_force_arg(ap)
    args = ap.parse_args()
    force = args.force

    if args.code and args.force:
        print("ERROR: --code and --force are mutually exclusive.",
              flush=True)
        sys.exit(2)

    sec_types = (args.sec_type,) if args.sec_type else SEC_TYPES

    t0 = time.time()
    print_build_header(
        "ANALYZE FOURIER FREQS (dominant cycle via real FFT on close)",
        table=TABLE_NAME,
        sec_types=", ".join(sec_types),
        mode=(
            f"SINGLE-CODE {args.code} (full recompute for this security)"
            if args.code else
            "FORCE (full recompute per sec_type)" if force
            else "incremental (missing dates only)"
        ),
    )

    conn = await get_db_connection_async()
    try:
        # ---- Process each sec_type --------------------------------------
        # Incremental missing-target detection happens inside
        # _process_sec_type, scoped to that sec_type's active-code
        # universe (codes are fetched once and shared by detection
        # and compute). Single-code mode (--code) bypasses the detection.
        total = 0
        for st in sec_types:
            total += await _process_sec_type(
                conn, st, force=force, code=args.code,
            )

        # Early exit if everything was up to date (nothing written).
        if total == 0 and not force and not args.code:
            print("\n  DB is up to date; nothing to do.", flush=True)
            print_wall_time(t0)
            return

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
