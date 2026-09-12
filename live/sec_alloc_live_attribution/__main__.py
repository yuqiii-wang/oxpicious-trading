"""Entry point for live.sec_alloc_live_attribution.

Run via ``python -m live.sec_alloc_live_attribution [--mode {all,live,ref}]``.

The pipeline is split into TWO INDEPENDENT PROCESSES (plus a back-compat
combined mode), each with its own PG advisory lock so they never block
each other:

  LIVE  (--mode live — the 5-min auto-refresh path, fired by the Market
        Movements UI on every route via the App-root keeper):
        Self-contained EQUAL-WEIGHT ticks only. For every tick-eligible
        (benchmark, date) pair, appends fallback rows
        (is_without_trading_amt = TRUE — prev close = prev-day LAST 5-min
        bar close from stats.index_intraday_5min itself, NO basic_stats
        dependency). The loader's anti-join skips (code, time) rows that
        already exist with ANY flag, so pairs covered by daily-close-basis
        rows are natural no-ops. If the live lock is held by a concurrent
        instance, exits fast (next 5-min run catches up).

  REF   (--mode ref — the manual yday-ref path, fired by the "Build Yday
        Ref" button on the Market Movements page):
        WEIGHTED tick pass: (re)fetch rows missing or present only as
        fallback (TRUE) — prev closes are computed AT TICK TIME from
        stats.index_basic_stats (the former heavy
        live.sec_alloc_live_prev_ref reference table was consolidated
        away; trading-amount weights / composition-overlap shared weights
        are computed at READ time by the API service) — and upserted as
        daily-close-basis (FALSE) rows, upgrading fallback rows in place.
        Waits (bounded) for its lock instead of skipping.

  ALL   (--mode all — combined back-compat behavior for CLI runs):
        weighted ticks + fallback fill for any rows still missing,
        exactly as before the split.

--force: truncate tick table first, then recompute (all/ref modes;
ignored in live mode). Respects the same latest-date scope.

--benchmark CODE[,CODE,...]: limit scope to specific benchmark codes.
"""
from __future__ import annotations


# resource pre-check -- exit early when sys/GPU memory is insufficient.
# This pipeline is pure asyncpg DB I/O (no cudf/GPU work) — the GPU VRAM
# check is skipped so the 5-min UI-keeper run is never killed by heavy
# GPU-resident jobs holding the card (which silently starved the Market
# Movements page of live tick rows).
from _common.pre_check import pre_check

pre_check(require_gpu=False)
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
    TICK_TABLE,
    PIPELINE_NAME,
    PIPELINE_DESCRIPTION,
    RETENTION_DATES,
    ADVISORY_LOCK_KEY,
    REF_ADVISORY_LOCK_KEY,
)
from live.sec_alloc_live_attribution.fetch import (  # noqa: E402
    fetch_latest_intraday_dates,
    find_live_tick_pairs,
    find_pairs_with_missing_ticks,
)
from live.sec_alloc_live_attribution.ticks import (  # noqa: E402
    load_fallback_ticks,
    load_missing_ticks,
    invalidate_ticks_for_date,
)

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("sec_alloc_live_attribution")

setup_utf8_stdout()

# How long --mode ref waits for its advisory lock before aborting (another
# ref run is still running; its work is idempotent so aborting is safe).
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
        TICK_TABLE.split(".", 1)[1],
        f"{PIPELINE_DESCRIPTION} [last mode: {mode}]",
    )
    logger.info(f"    -> upserted live_identity (name='{PIPELINE_NAME}', "
          f"mode={mode})")


async def _prune_old_dates(conn) -> int:
    """Delete tick rows older than the RETENTION_DATES newest trading dates.

    The cutoff is the OLDEST of the RETENTION_DATES most recent distinct
    intraday dates in the SOURCE table (the pipeline writes the latest
    date only, so the tick table always tracks those dates).

    Runs in ref/all modes only (once-per-day cadence) — never in the
    5-min live mode, where the tick table's unindexed date scan would
    run on every tick.

    Returns tick_rows_deleted.
    """
    keep = await fetch_latest_intraday_dates(conn, n_dates=RETENTION_DATES)
    if len(keep) < RETENTION_DATES:
        # Less history exists than the window — nothing can be outside it.
        return 0
    cutoff = keep[-1]
    tick_status = await conn.execute(
        f"DELETE FROM {TICK_TABLE} WHERE date < $1", cutoff
    )
    # asyncpg execute() returns the command tag, e.g. "DELETE 12345".
    n_tick = int(tick_status.split()[-1])
    if n_tick:
        logger.info(f"    -> retention prune (keep newest {RETENTION_DATES} "
              f"dates, cutoff < {cutoff}): deleted {n_tick:,} tick rows")
    else:
        logger.info(f"    -> retention prune: within the newest "
              f"{RETENTION_DATES}-date window; nothing deleted")
    return n_tick


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
        logger.info(f"    -> ref lock not acquired within {REF_LOCK_WAIT_S}s "
              f"({type(e).__name__}); aborting this ref run.")
        return False
    finally:
        await conn.execute("SET statement_timeout = '0'")


async def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Live sec-alloc attribution — per-5-min-tick member % vs prev "
            "day close (equal-weight live ticks + daily-close-basis "
            "weighted upgrades, under the live schema)."
        )
    )
    add_force_arg(ap)
    ap.add_argument(
        "--mode",
        type=str,
        choices=("all", "live", "ref"),
        default="all",
        help=(
            "live = 5-min equal-weight fallback ticks only (self-contained, "
            "no daily-stats dependency); ref = weighted tick upgrades "
            "(prev closes from index_basic_stats at tick time; manual "
            "button); all = combined (back-compat CLI)."
        ),
    )
    ap.add_argument(
        "--rebuild-latest-date",
        action="store_true",
        help=(
            "Before the weighted pass, DELETE this date's existing tick "
            "rows so they are rebuilt from scratch. Set by the 'Build Yday "
            "Ref' chain (the chain refreshes CSVs + daily stats first, so "
            "any ticks computed from stale/estimated closes must be "
            "invalidated). Ref/all modes only."
        ),
    )
    ap.add_argument(
        "--benchmark",
        type=str,
        default="",
        help=(
            "Comma-separated benchmark codes to limit scope to. "
            "Without this flag, all indices with 5-min tick data "
            "(stats.index_intraday_5min) are considered."
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
        logger.info("\n    -> no intraday dates in stats.index_intraday_5min; "
              "nothing to do.")
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
                "LIVE (equal-weight ticks, self-contained prev closes)"
                if lock_acquired
                else "SKIPPED (another live instance holds the advisory lock)"
            ),
        )
        logger.info(f"\n[1/2] Latest intraday date = {latest_date}")
        if not lock_acquired:
            logger.info("[2/2] Lock held — exiting fast; the next 5-min run "
                  "catches up.")
            print_wall_time(t0)
            try:
                await asyncio.wait_for(conn.close(), timeout=10)
            except (asyncio.TimeoutError, Exception):
                pass
            return

        pairs = await find_live_tick_pairs(conn, benchmarks)
        logger.info(f"[2/2] LIVE fallback tick pass: {len(pairs)} tick-eligible "
              "(benchmark, date) pairs...")
        if pairs:
            n_fb = await load_fallback_ticks(conn, pairs)
            logger.info(f"    -> fallback total: {n_fb:,} TRUE tick rows")
        else:
            logger.info("    -> no tick-eligible pairs; nothing to do.")
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
    # Weighted (daily-close-basis) tick upgrades (ref), optionally
    # combined with the fallback fill (all).
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
        "(trading-amount weighted, daily-close basis)"
        if args.mode == "ref"
        else "LIVE SEC ALLOC ATTRIBUTION "
             "(per-5-min-tick % vs prev day close, trading-amount weighted)",
        index_table=TICK_TABLE,
        mode=(
            "REF (weighted tick upgrades, tick-time prev closes)"
            if args.mode == "ref"
            else "INCREMENTAL (weighted ticks + fallback fill)"
        ),
    )

    # ---- Force mode → truncate the tick table -----------------------
    if args.force:
        logger.info("\n[0/3] Force mode: truncating tick table...")
        await truncate_table_async(conn, TICK_TABLE)
        logger.info("    -> truncated; will recompute all rows")

    # ---- Step 0: invalidate this date's ticks (--rebuild-latest-date)
    # The "Build Yday Ref" chain refreshes CSVs + daily stats BEFORE this
    # process runs; any existing ticks for the date may have been computed
    # from stale/estimated closes → delete so both passes rebuild.
    if args.rebuild_latest_date:
        n_tick_del = await invalidate_ticks_for_date(conn, latest_date)
        logger.info(f"\n[0/3] --rebuild-latest-date: deleted {n_tick_del:,} "
              f"tick rows for {latest_date} — rebuilding")

    # ---- Step 1: WEIGHTED tick pass (incremental + upgrades) --------
    logger.info(f"\n[1/3] Weighted tick pass (latest intraday date = "
          f"{latest_date}; pairs with missing or fallback-only ticks)...")
    tick_pairs = await find_pairs_with_missing_ticks(conn, benchmarks)
    logger.info(f"    -> {len(tick_pairs)} (benchmark, date) pairs pending")
    if tick_pairs:
        n_ticks = await load_missing_ticks(conn, tick_pairs)
        logger.info(f"    -> weighted total: {n_ticks:,} rows")
    else:
        logger.info("    -> all weighted ticks up to date.")

    # ---- Step 2: FALLBACK tick fill (all mode only) -----------------
    # The weighted loader's anti-join covers missing (code, time) rows
    # per pair, but pairs whose prev-day basic_stats rows are missing
    # entirely (fetch_missing_ticks returns nothing) would keep no data —
    # the fallback fill gives them equal-weight rows. In the split design
    # the LIVE process owns fallback ticks; all mode just catches up.
    if args.mode == "all":
        logger.info("\n[2/3] Fallback tick fill (pairs still missing rows)...")
        n_fb = await load_fallback_ticks(conn, tick_pairs)
        logger.info(f"    -> fallback total: {n_fb:,} TRUE tick rows")

    # ---- Step 3: retention prune + register in live_identity ---------
    # Skipped on --force (the truncate above already emptied the table)
    # and in live mode (the 5-min path stays scan-free; the ref/all runs
    # fire at least once per trading day via the Build Yday Ref chain).
    if not args.force:
        await _prune_old_dates(conn)

    logger.info("\n[3/3] Registering in live.live_identity...")
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
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
