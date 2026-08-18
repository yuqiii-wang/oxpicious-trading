"""Entry point for live.sec_alloc_live_attribution.

Run via ``python -m live.sec_alloc_live_attribution``.

Designed to be triggered every 5 minutes during trading hours by the
Market Movements UI (POST /api/live-data/sec-alloc-live/run), but is
safe to run any time — every pass is incremental.

CONCURRENCY (advisory lock):
  A PG advisory lock (config.ADVISORY_LOCK_KEY) serializes the HEAVY
  work. If the lock is held — a previous ``python -m
  live.sec_alloc_live_attribution`` is still running — this instance
  degrades to the FALLBACK-ONLY pass: it skips the heavy ref build and
  the weighted pass entirely and only appends ref-less fallback ticks
  (is_without_trading_amt = TRUE) for pairs whose ref is not ready, so
  live data keeps flowing WITHOUT duplicating the running instance's
  work. PK upserts make every path idempotent.

NORMAL mode pipeline:
  1. HEAVY ref pass (once per date — skipped when today's ref exists):
     build live.sec_alloc_live_prev_ref for missing (benchmark, date)
     pairs (member universe incl. stocks for share weights).
  2a. WEIGHTED tick pass: for pairs WITH ref, fetch rows missing or
     present only as fallback (TRUE) and upsert them as weighted (FALSE)
     rows — the upsert upgrades fallback rows in place. Tick rows only
     for index/etf members.
  2b. FALLBACK tick pass: for pairs whose ref build produced 0 rows
     (prev-day basic_stats lagging etc.), append ref-less TRUE rows
     (prev close = prev-day last 5-min bar close).
  3. Register the run in live.live_identity.

--force:
  Truncate tick table first, then ref table, then recompute. Respects
  the same latest-date ref scope.

--benchmark CODE[,CODE,...]:
  Limit scope to specific benchmark codes.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# Ensure project root is on sys.path so ``_common`` is importable when run
# via ``python -m live.sec_alloc_live_attribution``.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    truncate_table_async,
    print_build_header,
    print_wall_time,
    add_force_arg,
)

from live.sec_alloc_live_attribution.config import (  # noqa: E402
    REF_TABLE,
    TICK_TABLE,
    PIPELINE_NAME,
    PIPELINE_DESCRIPTION,
    ADVISORY_LOCK_KEY,
)
from live.sec_alloc_live_attribution.fetch import (  # noqa: E402
    fetch_latest_intraday_dates,
    find_missing_ref_pairs,
    find_pairs_with_missing_ticks,
)
from live.sec_alloc_live_attribution.ref import ensure_ref  # noqa: E402
from live.sec_alloc_live_attribution.ticks import (  # noqa: E402
    load_fallback_ticks,
    load_missing_ticks,
)

setup_utf8_stdout()


async def _upsert_live_identity(conn) -> None:
    """Upsert the pipeline registration into live.live_identity."""
    await conn.execute(
        """
        INSERT INTO live.live_identity
            (name, detail_name, summary_name, last_run_datetime, description)
        VALUES ($1, $2, $3, NOW(), $4)
        ON CONFLICT (name) DO UPDATE SET
            detail_name       = EXCLUDED.detail_name,
            summary_name      = EXCLUDED.summary_name,
            last_run_datetime = NOW(),
            description       = EXCLUDED.description
        """,
        PIPELINE_NAME,
        TICK_TABLE.split(".", 1)[1],
        REF_TABLE.split(".", 1)[1],
        PIPELINE_DESCRIPTION,
    )
    print(f"    -> upserted live_identity (name='{PIPELINE_NAME}')", flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Live sec-alloc attribution — per-5-min-tick member % vs prev "
            "day close, trading-amount weighted (heavy once-per-date ref + "
            "light incremental ticks + ref-less fallback under the live "
            "schema)."
        )
    )
    add_force_arg(ap)
    ap.add_argument(
        "--benchmark",
        type=str,
        default="",
        help=(
            "Comma-separated benchmark codes to limit scope to. "
            "Without this flag, all benchmarks in "
            "sec_alloc_perf_attribution are considered."
        ),
    )
    args = ap.parse_args()

    benchmarks = [
        s.strip() for s in args.benchmark.split(",") if s.strip()
    ] or None

    t0 = time.time()

    conn = await get_db_connection_async()
    lock_acquired = False
    try:
        # ---- Step 0: single-instance advisory lock ---------------------
        # If held, a previous instance is still running: degrade to the
        # FALLBACK-ONLY pass (no heavy ref build, no weighted upgrades)
        # so live data keeps flowing without duplicating its work.
        lock_acquired = await conn.fetchval(
            "SELECT pg_try_advisory_lock($1)", ADVISORY_LOCK_KEY
        )
        mode_desc = (
            "INCREMENTAL (ref skip-if-present + weighted ticks + fallback)"
            if lock_acquired
            else "FALLBACK-ONLY (another instance holds the advisory lock)"
        )
        if not lock_acquired and args.force:
            print("    -> --force ignored in fallback-only mode "
                  "(another instance is running).", flush=True)

        print_build_header(
            "LIVE SEC ALLOC ATTRIBUTION "
            "(per-5-min-tick % vs prev day close, trading-amount weighted)",
            index_table=TICK_TABLE,
            mode=mode_desc,
        )

        # ---- Force mode → truncate child first, then parent ------------
        if args.force and lock_acquired:
            print("\n[0/3] Force mode: truncating tick table first, then "
                  "ref...", flush=True)
            await truncate_table_async(conn, TICK_TABLE)
            await truncate_table_async(conn, REF_TABLE)
            print("    -> truncated; will recompute all rows", flush=True)

        # ---- Resolve the live date -------------------------------------
        target_dates = await fetch_latest_intraday_dates(conn, n_dates=1)
        if not target_dates:
            print("\n    -> no intraday dates in stats.index_intraday_5min; "
                  "nothing to do.", flush=True)
            print_wall_time(t0)
            return
        latest_date = target_dates[0]

        if not lock_acquired:
            # ---- FALLBACK-ONLY pass (advisory lock held elsewhere) ------
            print(f"\n[1/3] Latest intraday date = {latest_date}; skipping "
                  "heavy ref pass (lock held).", flush=True)
            fb_pairs = await find_missing_ref_pairs(conn, benchmarks)
            print(f"\n[2/3] FALLBACK tick pass: {len(fb_pairs)} ref-less "
                  "(benchmark, date) pairs...", flush=True)
            if fb_pairs:
                n_fb = await load_fallback_ticks(conn, fb_pairs)
                print(f"    -> fallback total: {n_fb:,} TRUE tick rows",
                      flush=True)
            else:
                print("    -> no ref-less pairs; nothing to do.", flush=True)
            print("\n[3/3] Registering in live.live_identity...", flush=True)
            await _upsert_live_identity(conn)
            print_wall_time(t0)
            return

        # ---- Step 1: HEAVY ref pass (once per date) --------------------
        print(f"\n[1/3] Heavy ref pass (latest intraday date = {latest_date}; "
              "pairs with existing ref rows are skipped)...", flush=True)
        missing_ref = await find_missing_ref_pairs(conn, benchmarks)
        print(f"    -> {len(missing_ref)} (benchmark, date) ref pairs missing",
              flush=True)
        zero_ref_pairs: list[tuple[str, object]] = []
        if missing_ref:
            n_ref, zero_ref_pairs = await ensure_ref(conn, missing_ref)
            print(f"    -> ref total: {n_ref:,} rows; "
                  f"{len(zero_ref_pairs)} pairs remain ref-less", flush=True)
        else:
            print("    -> ref up to date for the latest date; heavy pass "
                  "skipped.", flush=True)

        # ---- Step 2a: WEIGHTED tick pass (incremental + upgrades) ------
        print("\n[2/3] Weighted tick pass (pairs with missing or "
              "fallback-only ticks)...", flush=True)
        tick_pairs = await find_pairs_with_missing_ticks(conn, benchmarks)
        print(f"    -> {len(tick_pairs)} (benchmark, date) pairs pending",
              flush=True)
        if tick_pairs:
            n_ticks = await load_missing_ticks(conn, tick_pairs)
            print(f"    -> weighted total: {n_ticks:,} rows", flush=True)
        else:
            print("    -> all weighted ticks up to date.", flush=True)

        # ---- Step 2b: FALLBACK tick pass for ref-less pairs ------------
        if zero_ref_pairs:
            print(f"    -> fallback for {len(zero_ref_pairs)} ref-less "
                  "pairs (is_without_trading_amt = TRUE)...", flush=True)
            n_fb = await load_fallback_ticks(conn, zero_ref_pairs)
            print(f"    -> fallback total: {n_fb:,} TRUE tick rows",
                  flush=True)

        # ---- Step 3: register in live.live_identity --------------------
        print("\n[3/3] Registering in live.live_identity...", flush=True)
        await _upsert_live_identity(conn)

        print_wall_time(t0)
    finally:
        if lock_acquired:
            try:
                await conn.execute("SELECT pg_advisory_unlock($1)",
                                   ADVISORY_LOCK_KEY)
            except Exception:
                pass  # connection close releases the lock anyway
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    asyncio.run(main())
