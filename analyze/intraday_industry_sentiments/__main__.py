"""Entry point for analyze.intraday_industry_sentiments.

Run via ``python -m analyze.intraday_industry_sentiments``.

Pipeline (per (benchmark_code, tick_date) pair):
  1. Find missing (benchmark, date) pairs by comparing stats.index_intraday_5min
     against analysis.intraday_industry_market_movements. A benchmark is
     considered "in scope" iff it appears as a benchmark_code in
     analysis.sec_alloc_perf_attribution (i.e. has at least one member
     index we can aggregate). sec_type is hardcoded to 'index' only.
  2. For each (benchmark, date):
     a. Fetch benchmark 5-min bars + prev_day_close.
     b. Fetch member indices' 5-min bars + prev_day_close (joined to
        sec_classification for industry_id, is_industry_not_strategy;
        joined to sec_alloc_perf_attribution latest snapshot for the
        member universe; BROAD_* industry_ids excluded).
     c. Compute (in pandas):
        - benchmark_price_pct_relative_prev_date_close per tick
        - code_price_pct_relative_prev_date_close per (code, tick)
        - industry_price_pct_relative_prev_date_close per (industry, tick)
          as the simple mean of member code_price_pct across the industry
     d. Bulk-upsert PARENT first (industry aggregates), then CHILD
        (per-index rows). Order matters because the child has a strict
        composite FK to the parent.

Default scope (incremental, today + last biz day):
  By default the script only processes the latest 2 distinct dates in
  stats.index_intraday_5min (today's intraday bars being populated
  during market hours + the previous trading day for a complete
  reference session). This keeps market-hours re-runs fast — only the
  new ticks since the last run are inserted. sec_type is 'index' only.

--all-dates:
  Override the default 2-date scope to search ALL dates in
  stats.index_intraday_5min for missing pairs (full historical
  backfill). Use sparingly — the intraday 5-min table is large.

--force mode:
  Truncate BOTH tables first (child first because of the FK ON DELETE
  CASCADE — but we explicitly truncate child first to be safe across
  schema variants), then recompute and insert all rows for the
  in-scope (benchmark, date) pairs. Respects the same date scope as
  incremental mode (today + last biz day by default, --all-dates for
  full history).

--benchmark CODE[,CODE,...]:
  Comma-separated list of benchmark codes to limit scope to (e.g.
  --benchmark 000922 for the 中证红利 index). Without this flag, all
  benchmarks in sec_alloc_perf_attribution are considered.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from typing import Final

# Ensure project root is on sys.path so ``_common`` is importable when run
# directly via ``python -m analyze.intraday_industry_sentiments``.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    bulk_upsert_async,
    truncate_table_async,
    print_build_header,
    print_wall_time,
    add_force_arg,
)
from analyze._common import upsert_analysis_identity  # noqa: E402

from analyze.intraday_industry_sentiments.config import (  # noqa: E402
    INDUSTRY_TABLE,
    INDEX_TABLE,
    ANALYSIS_NAME,
    ANALYSIS_DESCRIPTION,
)
from analyze.intraday_industry_sentiments.fetch import (  # noqa: E402
    find_missing_pairs,
    fetch_latest_intraday_dates,
    fetch_benchmark_bars,
    fetch_member_bars,
)
from analyze.intraday_industry_sentiments.compute import (  # noqa: E402
    compute_movements,
)

setup_utf8_stdout()

# Per-(benchmark, date) timeout. Heavy benchmarks (e.g. 000300 沪深300 with
# ~150 member indices × 48 ticks × full history) take a few seconds per
# date; a 5-minute per-pair timeout is generous and prevents a stuck
# pair from blocking the whole run.
PER_PAIR_TIMEOUT_S: Final[int] = 300


async def _process_pair(
    conn,
    benchmark_code: str,
    tick_date,
) -> tuple[int, int]:
    """Compute + upsert one (benchmark, date) pair. Returns (n_parent, n_child)."""
    bench_bars = await fetch_benchmark_bars(conn, benchmark_code, tick_date)
    if not bench_bars:
        return 0, 0
    member_bars = await fetch_member_bars(conn, benchmark_code, tick_date)
    if not member_bars:
        return 0, 0

    industry_rows, index_rows = compute_movements(
        benchmark_bars=bench_bars,
        member_bars=member_bars,
        benchmark_code=benchmark_code,
        sec_type="index",
    )

    # Insert PARENT first (FK requirement). Use ON CONFLICT DO UPDATE so
    # re-runs of the same tick (e.g. during market hours when a 5-min bar
    # arrives after the previous computation) refresh the metrics.
    n_parent = await bulk_upsert_async(
        conn,
        INDUSTRY_TABLE,
        industry_rows,
        key_columns=["industry_id", "date", "time", "benchmark_code"],
        batch_size=2000,
    )
    # Insert CHILD after parent. ON CONFLICT DO UPDATE likewise.
    n_child = await bulk_upsert_async(
        conn,
        INDEX_TABLE,
        index_rows,
        key_columns=["code", "date", "time", "sec_type", "benchmark_code"],
        batch_size=5000,
    )
    return n_parent, n_child


async def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Intraday industry sentiments — per-5-min-tick % change vs "
            "previous trading day's close, decomposed to industry + "
            "individual-index level (parent + child strict FK tables)."
        )
    )
    add_force_arg(ap)
    ap.add_argument(
        "--benchmark",
        type=str,
        default="",
        help=(
            "Comma-separated benchmark codes to limit scope to "
            "(e.g. 000922). Without this flag, all benchmarks in "
            "sec_alloc_perf_attribution are considered."
        ),
    )
    ap.add_argument(
        "--all-dates",
        action="store_true",
        help=(
            "Search ALL dates in stats.index_intraday_5min for missing "
            "pairs (full historical backfill). By default the script only "
            "processes the latest 2 distinct intraday dates (today + last "
            "biz day) to keep market-hours re-runs fast."
        ),
    )
    args = ap.parse_args()

    benchmarks = [
        s.strip() for s in args.benchmark.split(",") if s.strip()
    ] or None

    t0 = time.time()
    scope_desc = "ALL dates" if args.all_dates else "today + last biz day"
    mode_desc = (
        f"FORCE (truncate + recompute, {scope_desc}"
        f"{f' for {len(benchmarks)} benchmark(s)' if benchmarks else ''})"
        if args.force
        else (
            f"incremental (missing pairs, {scope_desc}"
            f"{f' for {len(benchmarks)} benchmark(s)' if benchmarks else ''})"
        )
    )
    print_build_header(
        "ANALYZE INTRADAY INDUSTRY SENTIMENTS "
        "(per-5-min-tick % change vs prev day close, "
        "industry + index level)",
        index_table=INDUSTRY_TABLE,
        mode=mode_desc,
    )

    conn = await get_db_connection_async()
    try:
        # ---- Step 0: force mode → truncate both tables ----------------
        if args.force:
            print("\n[0/3] Force mode: truncating child first, then parent "
                  "(FK ON DELETE CASCADE makes this safe)...", flush=True)
            await truncate_table_async(conn, INDEX_TABLE)
            await truncate_table_async(conn, INDUSTRY_TABLE)
            print("    -> truncated; will recompute all rows", flush=True)

        # ---- Step 1: determine date scope + find missing pairs ---------
        # Default scope: latest 2 distinct intraday dates (today + last
        # biz day). --all-dates overrides to search the full history.
        print("\n[1/3] Detecting missing (benchmark, date) pairs "
              "(source: stats.index_intraday_5min WHERE code IN "
              "sec_alloc_perf_attribution.benchmark_code) ...",
              flush=True)
        if args.all_dates:
            print("    -> --all-dates: searching full history", flush=True)
            target_dates = None
        else:
            target_dates = await fetch_latest_intraday_dates(conn, n_dates=2)
            print(f"    -> default scope: latest 2 intraday dates = "
                  f"{[str(d) for d in target_dates]}", flush=True)

        pairs = await find_missing_pairs(
            conn, benchmarks, target_dates=target_dates
        )
        print(f"    -> {len(pairs)} (benchmark, date) pairs missing",
              flush=True)
        if not pairs:
            print("    -> DB is up to date; nothing to do.", flush=True)
            print_wall_time(t0)
            return

        # ---- Step 2: per-pair compute + upsert -------------------------
        print(f"\n[2/3] Computing + upserting {len(pairs)} pairs...",
              flush=True)
        n_total_parent = 0
        n_total_child = 0
        for idx, (bench, dt) in enumerate(pairs, start=1):
            t_pair = time.time()
            try:
                n_parent, n_child = await asyncio.wait_for(
                    _process_pair(conn, bench, dt),
                    timeout=PER_PAIR_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                print(f"    [{idx}/{len(pairs)}] {bench} @ {dt} "
                      f"TIMED OUT after {PER_PAIR_TIMEOUT_S}s — skipping",
                      flush=True)
                continue
            n_total_parent += n_parent
            n_total_child += n_child
            elapsed = time.time() - t_pair
            print(f"    [{idx}/{len(pairs)}] {bench} @ {dt}: "
                  f"{n_parent:,} parent + {n_child:,} child rows "
                  f"({elapsed:.1f}s)", flush=True)

        print(f"\n[2/3] TOTAL: {n_total_parent:,} parent + "
              f"{n_total_child:,} child rows upserted across {len(pairs)} "
              f"pairs", flush=True)

        # ---- Step 3: register in analysis.analysis_identity ------------
        print("\n[3/3] Registering in analysis.analysis_identity...",
              flush=True)
        await upsert_analysis_identity(
            conn,
            name=ANALYSIS_NAME,
            detail_name=ANALYSIS_NAME,
            description=ANALYSIS_DESCRIPTION,
        )
        print("    -> registered", flush=True)

        print_wall_time(t0)
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    asyncio.run(main())
