"""Entry point for analyze.mov_ave_spread.

Run via ``python -m analyze.mov_ave_spread``.

Pipeline
  1. Fetch per-(sec_type, code, date) price + MAs from stats schema for
     every sec_type in SEC_TYPES (active-universe pre-filter applied).
  2. Compute 9 wide gap columns + 12 slope/curvature columns per row.
  3. Upsert detail + upsert analysis_identity.

Default (incremental) mode:
  Only dates present in source identity tables (stats.etf_identity +
  stats.index_identity) but NOT yet in analysis.mov_ave_spreads_detail are
  (re)computed and upserted. Slope/curvature context is loaded from the
  source tables for correctness but only target-date rows are inserted.

--force mode:
  Truncate analysis.mov_ave_spreads_detail first, then recompute and
  insert all rows for the active universe.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# Ensure project root is on sys.path so ``utils`` is importable when run
# directly via ``python -m analyze.mov_ave_spread`` or as a script.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from utils.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    bulk_upsert_async,
    truncate_table_async,
    print_build_header,
    print_wall_time,
    find_missing_analysis_dates,
    add_force_arg,
)

setup_utf8_stdout()

import pandas as pd  # noqa: E402

from analyze.mov_ave_spread.config import (  # noqa: E402
    ANALYSIS_NAME,
    DETAIL_TABLE,
    DESCRIPTION,
    PAIRS,
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
)
from analyze.mov_ave_spread.fetch import fetch_source_data  # noqa: E402
from analyze.mov_ave_spread.compute import build_detail_rows  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Moving-average spread analysis (ETF + Index)."
    )
    add_force_arg(ap)
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "ANALYZE MA-SPREADS (ETF + INDEX)",
        detail_table=DETAIL_TABLE,
        pairs=f"{len(PAIRS)} (5 Price/MA + 4 MA5/MA)",
        sec_types=", ".join(SEC_TYPES),
        mode="FORCE (full recompute)" if args.force else "incremental (missing dates only)",
    )

    conn = await get_db_connection_async()
    try:
        # ---- Step 0: determine target dates -----------------------------
        if args.force:
            print("\n[0/3] Force mode: truncating detail table...", flush=True)
            await truncate_table_async(conn, DETAIL_TABLE)
            target_dates = None  # None = full recompute
            print("    -> truncated; will recompute all rows", flush=True)
        else:
            print("\n[0/3] Detecting missing dates "
                  "(source: etf_identity + index_identity vs detail table)...",
                  flush=True)
            source_tables = [
                SEC_TYPE_IDENTITY_TABLE[st] for st in SEC_TYPES
            ]
            target_dates = await find_missing_analysis_dates(
                conn, DETAIL_TABLE, source_tables,
            )
            print(f"    -> {len(target_dates)} dates missing from "
                  f"{DETAIL_TABLE}", flush=True)
            if not target_dates:
                print("    -> DB is up to date; nothing to do.", flush=True)
                print_wall_time(t0)
                return

        # ---- Step 1: fetch source data for every sec_type ---------------
        print("\n[1/3] Fetching per-(sec_type, code, date) price + MAs "
              "from stats schema...", flush=True)
        frames = []
        for at in SEC_TYPES:
            print(f"    -> fetching {at}...", flush=True)
            df_at = await fetch_source_data(conn, at, target_dates=target_dates)
            print(f"      {len(df_at):,} {at} (code, date) source rows",
                  flush=True)
            if not df_at.empty:
                frames.append(df_at)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        print(f"    -> {len(df):,} total (sec_type, code, date) source rows",
              flush=True)
        if df.empty:
            print("    -> no source data; exiting.", flush=True)
            return

        # ---- Step 2: build detail rows -------------------------------------
        print("\n[2/3] Computing 9 wide gap columns + 12 slope/curvature "
              "columns per (sec_type, code, date)...", flush=True)
        detail = build_detail_rows(df)
        print(f"    -> {len(detail):,} detail rows", flush=True)

        # ---- Step 3: upsert detail + upsert identity ----------------------
        print(f"\n[3/3] Upserting {len(detail):,} detail rows into "
              f"{DETAIL_TABLE}...", flush=True)
        n_detail = await bulk_upsert_async(
            conn, DETAIL_TABLE, detail,
            key_columns=["sec_type", "code", "date"],
            batch_size=1000,
        )
        print(f"    -> upserted {n_detail:,} rows", flush=True)

        # ---- Upsert analysis_identity ------------------------------------
        print(f"    -> Upserting analysis.analysis_identity registry...",
              flush=True)
        await conn.execute(
            """
            INSERT INTO analysis.analysis_identity
                (name, detail_name, last_run_datetime, description)
            VALUES ($1, $2, NOW(), $3)
            ON CONFLICT (name) DO UPDATE SET
                detail_name       = EXCLUDED.detail_name,
                last_run_datetime = NOW(),
                description       = EXCLUDED.description
            """,
            ANALYSIS_NAME,
            "mov_ave_spreads_detail",
            DESCRIPTION,
        )
        print(f"    -> upserted analysis_identity: name={ANALYSIS_NAME!r}, "
              f"detail_name='mov_ave_spreads_detail'", flush=True)

        print_wall_time(t0)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
