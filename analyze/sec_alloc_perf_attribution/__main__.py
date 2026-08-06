"""Entry point for analyze.sec_alloc_perf_attribution.

Run via ``python -m analyze.sec_alloc_perf_attribution``.

Pipeline
  1. Fetch all index closes (used as benchmarks) + recent-data pre-filter.
  2. Fetch composition shared weights (all subject x benchmark pairs) +
     codes-with-composition filter set + aggregate ETF amount per
     (date, tracking_index).
  3. Truncate analysis.sec_alloc_perf_attribution (force mode only).
  3a. Build + insert Index subjects (indices with composition vs all
      indices) — excludes self-pairs.
  4. Upsert analysis.analysis_identity registry.

Default (incremental) mode:
  Only dates present in stats.index_identity but NOT yet in
  analysis.sec_alloc_perf_attribution are (re)computed and upserted.
  Rolling correlations and the MA5 ratio are computed over the full
  per-(subject, benchmark) history for correctness, but only target-date
  rows are upserted.

--force mode:
  Truncate analysis.sec_alloc_perf_attribution first, then recompute and
  insert all rows.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import os
import sys
import time
from typing import Optional, Set

# Ensure project root is on sys.path so ``utils`` is importable when run
# directly via ``python -m analyze.sec_alloc_perf_attribution`` or as a script.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from utils.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    truncate_table_async,
    print_build_header,
    print_wall_time,
    find_missing_analysis_dates,
    add_force_arg,
    fetch_codes_with_recent_data_async,
    RECENT_TRADING_DAYS,
    recent_trading_day_cutoff,
)

setup_utf8_stdout()

from analyze.sec_alloc_perf_attribution.config import (  # noqa: E402
    ANALYSIS_NAME,
    TABLE,
    TOP_N_NON_BROAD,
    DESCRIPTION,
)
from analyze._common import upsert_analysis_identity  # noqa: E402
from analyze.sec_alloc_perf_attribution.fetch import (  # noqa: E402
    fetch_codes_with_composition,
    fetch_shared_weights,
    fetch_index_closes,
    fetch_etf_amount_by_index,
)
from analyze.sec_alloc_perf_attribution.compute import build_and_insert  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Sec alloc perf attribution analysis (Index x Index)."
    )
    add_force_arg(ap)
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "ANALYZE SEC ALLOC PERF ATTRIBUTION (INDEX x INDEX)",
        table=TABLE,
        sec_types="index",
        top_n_non_broad=f"{TOP_N_NON_BROAD}",
        mode="FORCE (full recompute)" if args.force
             else "incremental (missing dates only)",
    )

    conn = await get_db_connection_async()
    try:
        # ---- Step 0: determine target dates -------------------------------
        if args.force:
            print(f"\n[0/6] Force mode: truncating {TABLE}...", flush=True)
            await truncate_table_async(conn, TABLE)
            target_dates: Optional[Set[datetime.date]] = None
            print("    -> truncated; will recompute all rows", flush=True)
        else:
            print("\n[0/6] Detecting missing dates "
                  "(source: index_identity vs perf_attribution table)...",
                  flush=True)
            target_dates = await find_missing_analysis_dates(
                conn, TABLE, ["stats.index_identity"],
            )
            print(f"    -> {len(target_dates)} dates missing from {TABLE}",
                  flush=True)
            if not target_dates:
                print("    -> DB is up to date; nothing to do.", flush=True)
                print_wall_time(t0)
                return

        # ---- Step 1: fetch ALL index closes (used as benchmarks) -----
        print("\n[1/6] Fetching all index closes (benchmarks)...",
              flush=True)
        index_closes = await fetch_index_closes(conn)
        n_indices = index_closes["benchmark_code"].nunique() if not index_closes.empty else 0
        print(f"    -> {len(index_closes):,} index rows across {n_indices} indices",
              flush=True)

        if index_closes.empty:
            print("    -> no index data; exiting.", flush=True)
            return

        # ---- Step 1b: recent-data pre-filter ----------------------------
        # Drop any index (benchmark OR subject candidate) whose latest
        # stats.index_identity row is older than the cutoff — i.e. NO data
        # in the last RECENT_TRADING_DAYS trading days. Such indices are
        # delisted / suspended / never-traded and would contribute empty
        # subject rows. Filtering here covers BOTH the benchmark universe
        # and index subjects (subjects are derived from this same
        # index_closes frame below).
        cutoff = recent_trading_day_cutoff(RECENT_TRADING_DAYS)
        active_index_codes = await fetch_codes_with_recent_data_async(
            conn, "stats.index_identity", n_trading_days=RECENT_TRADING_DAYS,
        )
        before = int(index_closes["benchmark_code"].nunique())
        index_closes = index_closes[
            index_closes["benchmark_code"].isin(active_index_codes)
        ].copy()
        after = int(index_closes["benchmark_code"].nunique())
        print(f"    -> recent-data pre-filter (cutoff={cutoff.isoformat()}, "
              f"{RECENT_TRADING_DAYS} trading days): kept {after} of {before} "
              f"indices (dropped {before - after} with no recent data)",
              flush=True)
        if index_closes.empty:
            print("    -> no indices with recent data; exiting.", flush=True)
            return

        # ---- Step 2: fetch composition shared weights + codes-with-comp --
        print("\n[2/6] Fetching composition shared weights (ALL pairs) + "
              "codes-with-composition filter set...", flush=True)
        shared_weights = await fetch_shared_weights(conn)
        print(f"    -> {len(shared_weights):,} (subject, benchmark) pairs with shared weights",
              flush=True)
        codes_with_comp = await fetch_codes_with_composition(conn)
        print(f"    -> {len(codes_with_comp):,} codes have composition data "
              f"(used to filter subjects)", flush=True)

        # ---- Step 2b: fetch aggregate ETF amount per (date, index) ----
        # Reads precomputed total_etf_trading_amount from stats.index_exts (built by
        # build_index_exts.py). Used to populate benchmark_etf_trading_amount AND
        # code_etf_trading_amount for index subjects (both keyed on the tracked index
        # code via stats.sec_classification.parent_index_code).
        print("\n[2b/6] Fetching total_etf_trading_amount from stats.index_exts per "
              "(date, tracking_index)...", flush=True)
        etf_amount_by_index = await fetch_etf_amount_by_index(conn)
        if not etf_amount_by_index.empty:
            n_idx_with_etf = etf_amount_by_index["index_code"].nunique()
            print(f"    -> {len(etf_amount_by_index):,} rows across "
                  f"{n_idx_with_etf} indices that have tracking ETFs",
                  flush=True)
        else:
            print("    -> no ETF->index mapping data; benchmark_etf_trading_amount "
                  "and code_etf_trading_amount (for index subjects) will be NULL.",
                  flush=True)

        total = 0

        # ---- Step 3a: Index subjects ---------------------------------
        print("\n[3a/6] Building Index subjects (indices with composition vs all "
              "indices)...", flush=True)
        # Index subjects: rename columns from benchmark_* to subject_*.
        # NOTE: benchmark_etf_trading_amount is NOT carried in this rename — it is
        # fetched separately via etf_amount_by_index and merged inside
        # build_and_insert (keyed on subject_code as the tracked index).
        index_subject_closes = index_closes.rename(columns={
            "benchmark_code": "code",
            "benchmark_close": "subject_close",
        })
        # Filter: only include index subjects that have composition data.
        # Many indices (SSE/SZSE-published 000xxx/399xxx, BeSec 899xxx) have
        # NO published composition (only 44 CSI indices have closeweight CSVs).
        # Without this filter, every (subject, benchmark) pair for those
        # indices would have NULL shared_weight, rendering the chart empty.
        before_idx = index_subject_closes["code"].nunique()
        index_subject_closes = index_subject_closes[
            index_subject_closes["code"].isin(codes_with_comp)
        ].copy()
        after_idx = index_subject_closes["code"].nunique()
        print(f"    -> {after_idx} of {before_idx} indices have composition data "
              f"(dropped {before_idx - after_idx} without composition)", flush=True)
        if not index_subject_closes.empty:
            n = await build_and_insert(conn, index_subject_closes, index_closes,
                                       shared_weights,
                                       etf_amount_by_index,
                                       sec_type="index",
                                       target_dates=target_dates)
            total += n
            print(f"    -> Index total: {n:,} rows", flush=True)

        print(f"\n    -> grand total: {total:,} rows", flush=True)

        # ---- Step 4: upsert analysis_identity -------------------------
        print(f"\n[4/6] Upserting analysis.analysis_identity registry...",
              flush=True)
        await upsert_analysis_identity(
            conn,
            name=ANALYSIS_NAME,
            detail_name="sec_alloc_perf_attribution",
            description=DESCRIPTION,
        )

        print_wall_time(t0)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
