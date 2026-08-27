"""Entry point for live.sec_alloc_live_attribution.

Run via ``python -m live.sec_alloc_live_attribution [--mode {all,live,ref}]``.

The pipeline is split into TWO INDEPENDENT PROCESSES (plus a back-compat
combined mode), each with its own PG advisory lock so they never block
each other:

  LIVE  (--mode live — the 5-min auto-refresh path, fired by the Market
        Movements UI on every route via the App-root keeper):
        Ref-less EQUAL-WEIGHT ticks only. For every tick-eligible
        (benchmark, date) pair, appends fallback rows
        (is_without_trading_amt = TRUE — prev close = prev-day LAST 5-min
        bar close from stats.index_intraday_5min itself, NO basic_stats /
        trading-amount dependency). The loader's anti-join skips (code,
        time) rows that already exist with ANY flag, so pairs covered by
        weighted rows are natural no-ops. If the live lock is held by a
        concurrent instance, exits fast (next 5-min run catches up).

  REF   (--mode ref — the manual yday-ref path, fired by the "Build Yday
        Ref" button on the Market Movements page):
        1. HEAVY once-per-date ref build for missing (benchmark, date)
           pairs (prev-day closes + trading amounts + normalized weights
           into live.sec_alloc_live_prev_ref).
        2. WEIGHTED tick pass: (re)fetch rows missing or present only as
           fallback (TRUE) and upsert them as weighted (FALSE) rows —
           upgrades fallback rows in place.
        Waits (bounded) for its lock instead of skipping.

  ALL   (--mode all — combined back-compat behavior for CLI runs): ref
        build + weighted ticks + fallback for zero-ref pairs, exactly as
        before the split.

--force: truncate tick table first, then ref table, then recompute (all/
ref modes; ignored in live mode). Respects the same latest-date scope.

--benchmark CODE[,CODE,...]: limit scope to specific benchmark codes.
"""
from __future__ import annotations


# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()
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
    REF_ADVISORY_LOCK_KEY,
)
from live.sec_alloc_live_attribution.fetch import (  # noqa: E402
    fetch_latest_intraday_dates,
    find_live_tick_pairs,
    find_missing_ref_pairs,
    find_pairs_with_missing_ticks,
)
from live.sec_alloc_live_attribution.ref import (  # noqa: E402
    ensure_ref,
    invalidate_ref_for_date,
)
from live.sec_alloc_live_attribution.ticks import (  # noqa: E402
    load_fallback_ticks,
    load_missing_ticks,
)

setup_utf8_stdout()

# How long --mode ref waits for its advisory lock before aborting (another
# ref build is still running; its work is idempotent so aborting is safe).
REF_LOCK_WAIT_S = 300


async def _upsert_live_identity(conn, mode: str) -> None:
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
        f"{PIPELINE_DESCRIPTION} [last mode: {mode}]",
    )
    print(f"    -> upserted live_identity (name='{PIPELINE_NAME}', "
          f"mode={mode})", flush=True)


async def _acquire_ref_lock_blocking(conn) -> bool:
    """Bounded-blocking acquire of the REF advisory lock.

    Sets a per-statement timeout so pg_advisory_lock() gives up after
    REF_LOCK_WAIT_S instead of hanging forever, then restores no timeout.
    Returns False when another ref run still holds the lock.
    """
    await conn.execute(f"SET statement_timeout = '{REF_LOCK_WAIT_S * 1000}ms'")
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", REF_ADVISORY_LOCK_KEY)
        return True
    except Exception as e:  # QueryCanceledError on timeout
        print(f"    -> ref lock not acquired within {REF_LOCK_WAIT_S}s "
              f"({type(e).__name__}); aborting this ref run.", flush=True)
        return False
    finally:
        await conn.execute("SET statement_timeout = '0'")


async def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Live sec-alloc attribution — per-5-min-tick member % vs prev "
            "day close (equal-weight live ticks + optional trading-amount "
            "weighted yday ref, under the live schema)."
        )
    )
    add_force_arg(ap)
    ap.add_argument(
        "--mode",
        type=str,
        choices=("all", "live", "ref"),
        default="all",
        help=(
            "live = 5-min equal-weight fallback ticks only (no yday ref "
            "dependency); ref = heavy yday ref build + weighted tick "
            "upgrades (manual button); all = combined (back-compat CLI)."
        ),
    )
    ap.add_argument(
        "--rebuild-latest-date",
        action="store_true",
        help=(
            "Before the heavy pass, DELETE this date's existing ref + "
            "tick rows so they are rebuilt from scratch. Set by the "
            "'Build Yday Ref' chain (the chain refreshes CSVs + daily "
            "stats first, so any refs built from stale/estimated closes "
            "must be invalidated). Ref/all modes only."
        ),
    )
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

    # ---- Resolve the live date -------------------------------------
    target_dates = await fetch_latest_intraday_dates(conn, n_dates=1)
    if not target_dates:
        print("\n    -> no intraday dates in stats.index_intraday_5min; "
              "nothing to do.", flush=True)
        print_wall_time(t0)
        await asyncio.wait_for(conn.close(), timeout=10)
        return
    latest_date = target_dates[0]

    # ======================== LIVE mode ==============================
    # Equal-weight fallback ticks only — the 5-min auto-refresh path.
    # =================================================================
    if args.mode == "live":
        lock_acquired = await conn.fetchval(
            "SELECT pg_try_advisory_lock($1)", ADVISORY_LOCK_KEY
        )
        print_build_header(
            "LIVE SEC ALLOC ATTRIBUTION — LIVE TICKS "
            "(per-5-min-tick % vs prev day close, equal-weight fallback)",
            index_table=TICK_TABLE,
            mode=(
                "LIVE (equal-weight ticks, no yday-ref dependency)"
                if lock_acquired
                else "SKIPPED (another live instance holds the advisory lock)"
            ),
        )
        print(f"\n[1/2] Latest intraday date = {latest_date}", flush=True)
        if not lock_acquired:
            print("[2/2] Lock held — exiting fast; the next 5-min run "
                  "catches up.", flush=True)
            print_wall_time(t0)
            try:
                await asyncio.wait_for(conn.close(), timeout=10)
            except (asyncio.TimeoutError, Exception):
                pass
            return

        pairs = await find_live_tick_pairs(conn, benchmarks)
        print(f"[2/2] LIVE fallback tick pass: {len(pairs)} tick-eligible "
              "(benchmark, date) pairs...", flush=True)
        if pairs:
            n_fb = await load_fallback_ticks(conn, pairs)
            print(f"    -> fallback total: {n_fb:,} TRUE tick rows",
                  flush=True)
        else:
            print("    -> no tick-eligible pairs; nothing to do.", flush=True)
        await _upsert_live_identity(conn, "live")
        print_wall_time(t0)
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)",
                               ADVISORY_LOCK_KEY)
        except Exception:
            pass  # connection close releases the lock anyway
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass
        return

    # ======================== REF / ALL modes ========================
    # Heavy yday ref build + weighted tick upgrades (ref), optionally
    # combined with the fallback pass (all).
    # =================================================================
    lock_acquired = await _acquire_ref_lock_blocking(conn)
    if not lock_acquired:
        print_wall_time(t0)
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass
        return

    print_build_header(
        "LIVE SEC ALLOC ATTRIBUTION — YDAY REF "
        "(heavy prev-date ref + trading-amount weighted ticks)"
        if args.mode == "ref"
        else "LIVE SEC ALLOC ATTRIBUTION "
             "(per-5-min-tick % vs prev day close, trading-amount weighted)",
        index_table=TICK_TABLE,
        mode=(
            "REF (heavy yday ref + weighted tick upgrades)"
            if args.mode == "ref"
            else "INCREMENTAL (ref skip-if-present + weighted ticks + fallback)"
        ),
    )

    # ---- Force mode → truncate child first, then parent ------------
    if args.force:
        print("\n[0/3] Force mode: truncating tick table first, then "
              "ref...", flush=True)
        await truncate_table_async(conn, TICK_TABLE)
        await truncate_table_async(conn, REF_TABLE)
        print("    -> truncated; will recompute all rows", flush=True)

    # ---- Step 0: invalidate this date's ref/ticks (--rebuild-latest-date)
    # The "Build Yday Ref" chain refreshes CSVs + daily stats BEFORE this
    # process runs; any existing refs for the date may have been built
    # from stale/estimated closes → delete so the heavy pass rebuilds.
    if args.rebuild_latest_date:
        n_ref_del, n_tick_del = await invalidate_ref_for_date(conn, latest_date)
        print(f"\n[0/3] --rebuild-latest-date: deleted {n_ref_del:,} ref + "
              f"{n_tick_del:,} tick rows for {latest_date} — rebuilding",
              flush=True)

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
    # (all mode only — in the split design the LIVE process owns
    # fallback ticks; ref mode stops after the weighted upgrades.)
    if args.mode == "all" and zero_ref_pairs:
        print(f"    -> fallback for {len(zero_ref_pairs)} ref-less "
              "pairs (is_without_trading_amt = TRUE)...", flush=True)
        n_fb = await load_fallback_ticks(conn, zero_ref_pairs)
        print(f"    -> fallback total: {n_fb:,} TRUE tick rows",
              flush=True)

    # ---- Step 3: register in live.live_identity --------------------
    print("\n[3/3] Registering in live.live_identity...", flush=True)
    await _upsert_live_identity(conn, args.mode)

    print_wall_time(t0)
    try:
        await conn.execute("SELECT pg_advisory_unlock($1)",
                           REF_ADVISORY_LOCK_KEY)
    except Exception:
        pass  # connection close releases the lock anyway
    try:
        await asyncio.wait_for(conn.close(), timeout=10)
    except (asyncio.TimeoutError, Exception):
        pass


if __name__ == "__main__":
    asyncio.run(main())
