"""Entry point for analyze.options.

Run via ``python -m analyze.options``.

Pipeline:
  1. Populate analysis.options_expiry_identity with distinct
     (date, option_type, underlying_code, expiry_date) tuples.
  2. Compute per-expiry-group rolling skewness (OI-weighted moneyness)
     stats and write to analysis.options_skewness_stats (PK
     (date, option_type, underlying_code, expiry_date), FK ->
     analysis.options_expiry_identity):
       - ``--force``: DELETE all + chunked COPY-insert.
       - default:     chunked upsert (ON CONFLICT DO UPDATE on PK).
       - Includes count_skewness_curve_crossed_spot: cumulative count of
         sign changes in (skewness − 1) per expiry group.
  3. Compute per-expiry-group OI stats and write to
     analysis.options_oi_stats (same PK/FK pattern).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# Ensure project root is on sys.path so ``_common`` is importable when run
# directly via ``python -m analyze.options`` or as a script.
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

import pandas as pd  # noqa: E402

from analyze._common import (  # noqa: E402
    sanitize_for_db_insert,
    upsert_analysis_identity,
)
from analyze.options.config import (  # noqa: E402
    SKEWNESS_TABLE_NAME,
    SKEWNESS_ANALYSIS_NAME,
    SKEWNESS_DESCRIPTION,
    SKEWNESS_NUMERIC_COLS,
    SKEWNESS_RESULT_COLUMNS,
    EXPIRY_IDENTITY_TABLE,
    EXPIRY_PK_COLUMNS,
    OI_TABLE_NAME,
    OI_ANALYSIS_NAME,
    OI_DESCRIPTION,
    OI_NUMERIC_COLS,
    OI_RESULT_COLUMNS,
)
from analyze.options.fetch import (  # noqa: E402
    fetch_options_skewness_rows,
    fetch_missing_skewness_groups,
    fetch_expiry_identity_rows,
)
from analyze.options.compute import (  # noqa: E402
    compute_options_skewness_stats,
)


_CHUNK_SIZE = 10000


async def _write_rows(
    conn,
    result_df: pd.DataFrame,
    *,
    table_name: str,
    numeric_cols: list[str],
    force: bool,
    target_pairs: set | None,
    pk_columns: list[str],
) -> int:
    """Write result rows to a target table.

    - force: DELETE all + chunked COPY-insert.
    - incremental: filter to target_pairs + chunked upsert.

    Returns:
        Number of rows written.
    """
    if result_df.empty:
        print("  no rows to write", flush=True)
        return 0

    if force:
        print(f"  Deleting existing rows from {table_name}...", flush=True)
        await conn.execute(f"DELETE FROM {table_name}")
    else:
        if target_pairs is not None and len(target_pairs) == 0:
            print("  up to date; nothing to insert.", flush=True)
            return 0

        if target_pairs is not None:
            n_before = len(result_df)
            result_df = result_df[
                result_df.apply(
                    lambda r: tuple(r[c] for c in pk_columns) in target_pairs,
                    axis=1,
                )
            ].reset_index(drop=True)
            print(f"  Incremental filter: {len(result_df):,} of "
                  f"{n_before:,} rows are in target pairs", flush=True)

    if result_df.empty:
        print("  no rows to write after filter", flush=True)
        return 0

    n_chunks = (len(result_df) + _CHUNK_SIZE - 1) // _CHUNK_SIZE
    total = 0

    print(f"  {'COPY' if force else 'Upsert'}ing {len(result_df):,} rows "
          f"in {n_chunks} chunks...", flush=True)

    for i in range(n_chunks):
        chunk = result_df.iloc[
            i * _CHUNK_SIZE : (i + 1) * _CHUNK_SIZE
        ].copy()

        rows = sanitize_for_db_insert(
            chunk,
            numeric_cols=numeric_cols,
            round_to=2,
        )
        if not rows:
            continue

        if force:
            n = await copy_insert_async(conn, table_name, rows)
        else:
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, table_name, rows, key_columns=pk_columns,
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

    print(f"  wrote {total:,} rows total", flush=True)
    return total


async def _run_expiry_identity_pipeline(
    conn,
    force: bool,
    sec_type: str | None = None,
) -> int:
    """Populate analysis.options_expiry_identity.

    When force=True, first delete all referencing tables to avoid FK
    constraint violations.

    Returns number of rows written.
    """
    print("\n  Populating expiry identity table...", flush=True)

    if force:
        print("    Force mode: clearing dependent tables first...", flush=True)
        await conn.execute("DELETE FROM analysis.options_skewness_stats")
        await conn.execute("DELETE FROM analysis.options_oi_stats")

    rows = await fetch_expiry_identity_rows(conn, sec_type)
    print(f"    {len(rows):,} distinct expiry groups", flush=True)

    if not rows:
        print("    no data; skipping.", flush=True)
        return 0

    # Create DataFrame from tuples
    df = pd.DataFrame(rows, columns=EXPIRY_PK_COLUMNS)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["expiry_date"] = pd.to_datetime(df["expiry_date"]).dt.date

    target_pairs = None
    if not force:
        existing = set()
        try:
            existing_rows = await conn.fetch(
                f"SELECT date, option_type, underlying_code, expiry_date "
                f"FROM {EXPIRY_IDENTITY_TABLE}"
            )
            existing = set(
                (r["date"], r["option_type"],
                 r["underlying_code"], r["expiry_date"])
                for r in existing_rows
            )
        except Exception:
            pass
        all_pairs = set(
            (r["date"], r["option_type"],
             r["underlying_code"], r["expiry_date"])
            for _, r in df.iterrows()
        )
        target_pairs = all_pairs - existing
        if len(target_pairs) == 0:
            print("    -> expiry_identity is up to date; nothing to do.",
                  flush=True)
            return 0

    n = await _write_rows(
        conn, df,
        table_name=EXPIRY_IDENTITY_TABLE,
        numeric_cols=[],
        force=force,
        target_pairs=target_pairs,
        pk_columns=EXPIRY_PK_COLUMNS,
    )
    return n


async def _run_skewness_pipeline(
    conn,
    force: bool,
    sec_type: str | None = None,
) -> int:
    """Run the options_skewness_stats pipeline.

    Returns number of rows written.
    """
    target_pairs: set | None = None
    if not force:
        print("\n  Detecting missing expiry groups "
              "for skewness stats...", flush=True)
        missing_list = await fetch_missing_skewness_groups(conn, sec_type)
        target_pairs = set(missing_list)
        print(f"    -> {len(target_pairs):,} missing expiry groups",
              flush=True)
        if len(target_pairs) == 0:
            print("    -> DB is up to date; nothing to do.", flush=True)
            return 0

    print("\n  [1/3] Fetching option contract rows for skewness...",
          flush=True)
    df = await fetch_options_skewness_rows(conn, sec_type)
    print(f"    {len(df):,} contract-date rows", flush=True)
    if df.empty:
        print("    no data; skipping.", flush=True)
        return 0

    print("\n  [2/3] Computing skewness rolling stats...", flush=True)
    result_df = compute_options_skewness_stats(df)
    print(f"    {len(result_df):,} expiry-group result rows", flush=True)

    print("\n  [3/3] Writing to DB...", flush=True)
    n = await _write_rows(
        conn, result_df,
        table_name=SKEWNESS_TABLE_NAME,
        numeric_cols=SKEWNESS_NUMERIC_COLS,
        force=force,
        target_pairs=target_pairs,
        pk_columns=EXPIRY_PK_COLUMNS,
    )

    print("\n  -> Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=SKEWNESS_ANALYSIS_NAME,
        detail_name="options_skewness_stats",
        description=SKEWNESS_DESCRIPTION,
    )

    return n


async def _run_oi_pipeline(
    conn,
    force: bool,
    sec_type: str | None = None,
) -> int:
    """Run the options_oi_stats pipeline.

    Computes put/call OI ratio correlation stats per expiry group.
    Returns number of rows written.
    """
    from analyze.options.fetch import fetch_oi_rows
    from analyze.options.compute import compute_options_oi_stats

    target_pairs: set | None = None
    if not force:
        print("\n  Detecting missing expiry groups "
              "for OI stats...", flush=True)
        # Same detection as skewness, but checked against the OI table
        missing_list = await fetch_missing_skewness_groups(
            conn, sec_type, table_name=OI_TABLE_NAME,
        )
        target_pairs = set(missing_list)
        print(f"    -> {len(target_pairs):,} missing expiry groups",
              flush=True)
        if len(target_pairs) == 0:
            print("    -> DB is up to date; nothing to do.", flush=True)
            return 0

    print("\n  [1/3] Fetching option contract rows for OI...",
          flush=True)
    df = await fetch_oi_rows(conn, sec_type)
    print(f"    {len(df):,} contract-date rows", flush=True)
    if df.empty:
        print("    no data; skipping.", flush=True)
        return 0

    print("\n  [2/3] Computing OI put/call ratio correlation stats...", flush=True)
    result_df = compute_options_oi_stats(df)
    print(f"    {len(result_df):,} expiry-group result rows", flush=True)

    print("\n  [3/3] Writing to DB...", flush=True)
    n = await _write_rows(
        conn, result_df,
        table_name=OI_TABLE_NAME,
        numeric_cols=OI_NUMERIC_COLS,
        force=force,
        target_pairs=target_pairs,
        pk_columns=EXPIRY_PK_COLUMNS,
    )

    print("\n  -> Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=OI_ANALYSIS_NAME,
        detail_name="options_oi_stats",
        description=OI_DESCRIPTION,
    )

    return n


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Options analysis pipelines. Computes per-expiry-group "
                    "rolling skewness (OI-weighted moneyness) stats and "
                    "per-expiry-group OI correlation stats.",
    )
    add_force_arg(ap)
    ap.add_argument(
        "--sec-type",
        choices=["index", "etf"],
        default=None,
        help="Filter by underlying target type (index or etf).",
    )
    args = ap.parse_args()
    force = args.force
    sec_type = args.sec_type

    t0 = time.time()
    print_build_header(
        "ANALYZE OPTIONS (expiry-group skewness + OI stats)",
        tables=f"{EXPIRY_IDENTITY_TABLE}, {SKEWNESS_TABLE_NAME}, {OI_TABLE_NAME}",
        sec_type=sec_type or "all",
        mode="FORCE (full recompute)" if force
             else "incremental (missing groups only)",
    )

    conn = await get_db_connection_async()
    try:
        # ---- Pipeline 0: populate options_expiry_identity (FK lookup) -----
        print("\n" + "=" * 60)
        print("PIPELINE 0: options_expiry_identity (FK lookup)")
        print("=" * 60)
        n_id = await _run_expiry_identity_pipeline(conn, force, sec_type)

        # ---- Pipeline 1: options_skewness_stats --------------------------
        print("\n" + "=" * 60)
        print("PIPELINE 1: options_skewness_stats (expiry-group skewness)")
        print("=" * 60)
        n1 = await _run_skewness_pipeline(conn, force, sec_type)

        # ---- Pipeline 2: options_oi_stats -------------------------------
        print("\n" + "=" * 60)
        print("PIPELINE 2: options_oi_stats (expiry-group OI)")
        print("=" * 60)
        n2 = await _run_oi_pipeline(conn, force, sec_type)

        total = n_id + n1 + n2
        print(f"\n  TOTAL: {total:,} rows written "
              f"(expiry_identity={n_id:,}, "
              f"skewness={n1:,}, oi={n2:,})", flush=True)
        print_wall_time(t0)
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    asyncio.run(main())
