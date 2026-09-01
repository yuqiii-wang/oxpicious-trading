"""Entry point for analyze.mov_ave_spread.

Run via ``python -m analyze.mov_ave_spread``.

Pipeline
  1. Fetch per-(sec_type, code, date) price + MAs from stats schema for
     every sec_type in SEC_TYPES (active-universe pre-filter applied).
  2. Compute 9 wide gap columns + 12 slope/curvature columns per row
     (filtered to target_dates in incremental mode).
  3. Upsert detail.
  4. Upsert analysis_identity.
  5. INTERNAL STEP: compute Wilder RSI (6/10/14/20/60/120/255/500d) + price gaps (2/3d)
     from the SAME source price data already loaded in Step 1 ->
     analysis.mov_ave_rsi (see rsi.py). Reuses the same DB connection +
     source DataFrame. This step used to be a standalone
     analyze.mov_ave_rsi package; it is now an internal step because it
     shares the same source price data and active-universe pre-filter.
  6. INTERNAL STEP: compute holiday / non-trading-day risk metrics
     (previous-day trading/weekend/holiday status + today's intraday
     gaps) from the SAME source data -> analysis.mov_ave_rsi_holiday
     (see holiday.py). Must run AFTER RSI due to FK.
  7. INTERNAL STEP: compute EMA spread detail (9 EMA gap pairs + 5 EMA
     slope + 5 EMA curvature) from the SAME source data (EMA columns +
     pre-computed EMA slopes/curvatures already in the parent DataFrame)
     -> analysis.mov_ave_spreads_detail_ema (see ema.py). Reuses the
     same DB connection + source DataFrame.
  8. INTERNAL STEP: compute rolling OHLC detail (today_close +
     open/high/low/second-high/second-low over 7 windows:
     20/60/120/255/500/750/1275 trading days)
     from the SAME source data -> analysis.mov_ave_spreads_detail_ohlc
     (see ohlc.py). Reuses the same DB connection + source DataFrame.
  9. INTERNAL STEP: compute trading-amount liquidity-impact ratios
     (6 slope ratios + range ratio + overnight-gap ratio + MA5 versions)
     from the SAME source data -> analysis.mov_ave_trading_amt_ratios
     (see trading_amt_ratios.py). Reuses the same DB connection +
     source DataFrame.
  10. INTERNAL STEP: compute market-hype EPISODES (one row per
     CONCATENATED episode per check-in window 5/20/60/120/255: the span
     around a maximal run of consecutive hyped dates, extended through
     the check-in evidence, bucketed by span into [W, next window) —
     W = min_checkin_period is the bucket MINIMUM, the next window the
     exclusive maximum (the 255d bucket spans up to the whole ±10y
     base); a date is hyped when more than
     min_checkin_satisfaction_threshold percent of the last W trading
     rows are check-ins — dates whose trading_amount AND std_{W}days
     both exceed their centered 20-year (±10 trading years around the
     audited date) percentile thresholds) from the SAME source data
     (trading_amount + std_{W}days columns) ->
     analysis.mov_ave_market_hypes (see market_hypes.py; also counts
     the per-leg check-in days trading_amt_hype_days / std_hype_days
     within each episode span). Reuses the same DB connection +
     source DataFrame. Episodes are rebuilt wholesale per sec_type on
     every run — new dates shift episode boundaries (margin_changes
     precedent).

Default (incremental) mode:
  Only dates present in source identity tables (stats.etf_identity +
  stats.index_identity + stats.stock_identity) but NOT yet in
  analysis.mov_ave_spreads_detail are (re)computed and upserted.

  The missing-date check is PER-sec_type: a date populated for ETF does
  NOT mask the same date being missing for index/stock. This matters
  because the analysis table PK is (sec_type, code, date) — a global
  date check would falsely skip a date for sec_type B just because
  sec_type A already had it.

--force mode:
  Truncate detail, then recompute and
  insert all rows for the active universe.

--sec-type mode:
  Process only the specified sec_type (for testing). Default: all.
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
# directly via ``python -m analyze.mov_ave_spread`` or as a script.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    get_db_pool_async,
    truncate_table_async,
    print_build_header,
    print_wall_time,
    find_missing_analysis_dates,
    add_force_arg,
)

setup_utf8_stdout()

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

from analyze._common import (  # noqa: E402
    build_and_insert_chunked_df,
    upsert_analysis_identity,
)
from analyze.mov_ave_spread.config import (  # noqa: E402
    ANALYSIS_NAME,
    DETAIL_TABLE,
    DESCRIPTION,
    EMA_DETAIL_TABLE,
    HOLIDAY_TABLE,
    MARKET_HYPES_TABLE,
    OHLC_TABLE,
    PAIRS,
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
    TRADING_AMT_ANALYSIS_NAME,
    TRADING_AMT_RATIOS_TABLE,
    TRADING_AMT_TABLE,
)
from analyze.mov_ave_spread.fetch import fetch_source_data  # noqa: E402
from analyze.mov_ave_spread.compute import build_detail_frame  # noqa: E402
from analyze.mov_ave_spread.rsi import run_rsi, RSI_TABLE  # noqa: E402
from analyze.mov_ave_spread.ema import run_ema  # noqa: E402
from analyze.mov_ave_spread.ohlc import run_ohlc, find_ohlc_repair_dates  # noqa: E402
from analyze.mov_ave_spread.trading_amt import run_trading_amt  # noqa: E402
from analyze.mov_ave_spread.trading_amt_ratios import run_trading_amt_ratios  # noqa: E402
from analyze.mov_ave_spread.market_hypes import run_market_hypes  # noqa: E402
from analyze.mov_ave_spread.holiday import run_holiday  # noqa: E402


async def _process_one_sec_type(
    conn, pool, st, target_dates_st, force, max_concurrent, t0,
):
    """Process a single sec_type end-to-end.

    Fetches the FULL per-code history for ``st``, builds + inserts
    detail rows (filtered to ``target_dates_st`` in incremental
    mode), then runs the RSI/holiday/EMA/OHLC/trading-amt/trading-amt-
    ratios/market-hypes steps on the same source data. The source
    DataFrame is freed before returning so the caller can process the
    next sec_type without cumulative memory growth.

    Splitting per-sec_type is essential for the stock universe: 11K+
    codes × 1.6K dates = ~17M rows (~6 GB in pandas). Loading all 3
    sec_types at once (18.5M rows) OOMs the host; processing one sec_type
    at a time bounds peak memory to a single sec_type's data.
    """
    # ---- Fetch FULL source data for this sec_type ----------------------
    print(f"\n  [{st}] Fetching FULL per-(code, date) price + MAs "
          f"from stats schema...", flush=True)
    df = await fetch_source_data(conn, st, target_dates=None)
    print(f"  [{st}]   {len(df):,} (code, date) source rows", flush=True)
    if df.empty:
        print(f"  [{st}]   no source data; skipping.", flush=True)
        return 0

    # ---- Build + insert detail (chunked by date) -----------------------
    if target_dates_st is not None and len(target_dates_st) == 0:
        # Empty set means force mode — compute ALL dates (no filtering).
        print(f"\n  [{st}] Computing + inserting detail rows (FORCE mode, all dates)...",
              flush=True)
        detail_df = df
        n_detail = await build_and_insert_chunked_df(
            conn, pool, detail_df,
            lambda sub: build_detail_frame(sub),
            table_name=DETAIL_TABLE,
            force=force,
            sec_types=(st,),
            chunk_target_rows=100_000,  # wide table — no melt, keep big chunks
            max_concurrent=max_concurrent,
            label=f"detail[{st}]",
        )
        print(f"  [{st}]   inserted {n_detail:,} detail rows", flush=True)
        del detail_df
    elif target_dates_st is None:
        print(f"\n  [{st}] Detail up-to-date; skipping detail "
              f"computation.", flush=True)
        n_detail = 0
        # Still need to run internal steps even if detail is up-to-date
    else:
        print(f"\n  [{st}] Computing + inserting detail rows in date-bounded "
              f"chunks (9 gap cols + 12 slope/curv cols per row)...",
              flush=True)
        detail_df = df
        if len(target_dates_st) > 0:
            n_before = len(detail_df)
            detail_df = detail_df[
                detail_df["date"].isin(target_dates_st)
            ].reset_index(drop=True)
            print(f"  [{st}]   incremental filter: {len(detail_df):,} of "
                  f"{n_before:,} rows are in target_dates", flush=True)
        print(f"  [{st}]   building {len(detail_df):,} detail rows "
              f"(COPY per chunk)", flush=True)

        n_detail = await build_and_insert_chunked_df(
            conn, pool, detail_df,
            lambda sub: build_detail_frame(sub),
            table_name=DETAIL_TABLE,
            force=force,
            sec_types=(st,),
            chunk_target_rows=100_000,  # wide table — no melt, keep big chunks
            max_concurrent=max_concurrent,
            label=f"detail[{st}]",
        )
        print(f"  [{st}]   inserted {n_detail:,} detail rows", flush=True)

        del detail_df

    # ---- RSI step (reuses same source DataFrame) -----------------------
    await run_rsi(conn, df, force=force, pool=pool,
                  max_concurrent=max_concurrent, sec_type=st)

    # ---- Holiday step (reuses same source DataFrame) ------------------
    await run_holiday(conn, df, force=force, pool=pool,
                      max_concurrent=max_concurrent, sec_type=st)

    # ---- EMA detail step (reuses same source DataFrame) ---------------
    await run_ema(conn, df, force=force, pool=pool,
                  max_concurrent=max_concurrent, sec_type=st)

    # ---- OHLC detail step (reuses same source DataFrame) -------------
    await run_ohlc(conn, df, force=force, pool=pool,
                   max_concurrent=max_concurrent, sec_type=st)

    # ---- Trading-amount detail step (reuses same source DataFrame) ---
    await run_trading_amt(conn, df, force=force, pool=pool,
                          max_concurrent=max_concurrent, sec_type=st)

    # ---- Trading-amount ratios step (reuses same source DataFrame) --
    await run_trading_amt_ratios(conn, df, force=force, pool=pool,
                                 max_concurrent=max_concurrent, sec_type=st)

    # ---- Market-hypes step (reuses same source DataFrame) ---------
    await run_market_hypes(conn, df, force=force, pool=pool,
                           max_concurrent=max_concurrent, sec_type=st)

    # Free the source DataFrame — full history no longer needed.
    del df

    return n_detail


async def _process_single_code(
    conn, pool, st, code, max_concurrent,
) -> int:
    """Recompute ALL analysis rows for ONE (sec_type, code) — ``--code`` mode.

    Used by the UI per-security build button when a security has no
    analysis data. Steps:
      1. DELETE the code's existing rows from every target table (FK-safe
         order: holiday references mov_ave_rsi, so it goes first).
      2. Fetch the code's FULL source history (bypassing the
         active-universe pre-filter).
      3. Recompute + COPY-insert detail, then run ALL internal steps
         (RSI / holiday / EMA / OHLC / trading-amt / ratios / hypes) with
         ``code_filter`` so each step skips its per-sec_type missing-date
         detection (dates covered by OTHER codes would mask this code's
         gaps) and bypasses the per-sec_type skip-filter
         (``sec_types=()`` — rows are pre-deleted, so COPY cannot
         conflict).

    Returns the number of detail rows inserted (0 when the sec_type has
    no source data for the code).
    """
    print(f"\n  [{st}] SINGLE-CODE mode: rebuilding all rows for {code}",
          flush=True)

    # ---- Step 1: delete the code's rows (FK-safe order) ------------------
    # mov_ave_rsi_holiday has a FK to mov_ave_rsi → delete holiday first.
    for table in (
        HOLIDAY_TABLE, RSI_TABLE, DETAIL_TABLE, EMA_DETAIL_TABLE,
        OHLC_TABLE, TRADING_AMT_TABLE, TRADING_AMT_RATIOS_TABLE,
        MARKET_HYPES_TABLE,
    ):
        status = await conn.execute(
            f"DELETE FROM {table} WHERE sec_type = $1 AND code = $2",
            st, code,
        )
        n_del = int(status.rsplit(" ", 1)[-1]) if status else 0
        print(f"  [{st}]   deleted {n_del:,} rows from {table}", flush=True)

    # ---- Step 2: fetch the code's FULL source history --------------------
    print(f"  [{st}] Fetching FULL per-(code, date) price + MAs "
          f"for {code}...", flush=True)
    df = await fetch_source_data(conn, st, target_dates=None,
                                 code_filter=code)
    print(f"  [{st}]   {len(df):,} (code, date) source rows", flush=True)
    if df.empty:
        print(f"  [{st}]   no source data for {code} in {st}; skipping.",
              flush=True)
        return 0

    # ---- Step 3: detail + all internal steps -----------------------------
    n_detail = await build_and_insert_chunked_df(
        conn, pool, df,
        lambda sub: build_detail_frame(sub),
        table_name=DETAIL_TABLE,
        force=False,
        sec_types=(),  # bypass per-sec_type skip-filter (rows pre-deleted)
        chunk_target_rows=100_000,  # wide table — no melt, keep big chunks
        max_concurrent=max_concurrent,
        label=f"detail[{st}:{code}]",
    )
    print(f"  [{st}]   inserted {n_detail:,} detail rows", flush=True)

    await run_rsi(conn, df, force=False, pool=pool,
                  max_concurrent=max_concurrent, sec_type=st,
                  code_filter=code)
    await run_holiday(conn, df, force=False, pool=pool,
                      max_concurrent=max_concurrent, sec_type=st,
                      code_filter=code)
    await run_ema(conn, df, force=False, pool=pool,
                  max_concurrent=max_concurrent, sec_type=st,
                  code_filter=code)
    await run_ohlc(conn, df, force=False, pool=pool,
                   max_concurrent=max_concurrent, sec_type=st,
                   code_filter=code)
    await run_trading_amt(conn, df, force=False, pool=pool,
                          max_concurrent=max_concurrent, sec_type=st,
                          code_filter=code)
    await run_trading_amt_ratios(conn, df, force=False, pool=pool,
                                 max_concurrent=max_concurrent, sec_type=st,
                                 code_filter=code)
    await run_market_hypes(conn, df, force=False, pool=pool,
                           max_concurrent=max_concurrent, sec_type=st,
                           code_filter=code)

    del df
    return n_detail


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Moving-average spread analysis (ETF + Index + Stock)."
    )
    add_force_arg(ap)
    ap.add_argument(
        "--sec-type", choices=("etf", "index", "stock"), default=None,
        help="Process only this sec_type (for testing). Default: all.",
    )
    ap.add_argument(
        "--max-concurrent", type=int, default=20,
        help="Maximum parallel upsert chunks. Each chunk acquires one "
             "Postgres backend connection from the pool, so this also "
             "sets the pool's max_size. Local dev DB has "
             "max_connections=100 with ~2 in use, so 20 is safe. "
             "Reduce if you see 'too many clients' errors. Default: 20.",
    )
    ap.add_argument(
        "--code", default=None,
        help="Recompute ALL analysis rows for this single security only "
             "(single-code mode; used by the UI per-security build "
             "button). Deletes the code's rows first, then rebuilds "
             "detail + every internal step for that code. Mutually "
             "exclusive with --force.",
    )
    args = ap.parse_args()

    if args.code and args.force:
        print("ERROR: --code and --force are mutually exclusive.",
              flush=True)
        sys.exit(2)

    sec_types = (args.sec_type,) if args.sec_type else SEC_TYPES
    max_concurrent = max(1, args.max_concurrent)

    t0 = time.time()
    print_build_header(
        "ANALYZE MA-SPREADS (ETF + INDEX + STOCK)",
        detail_table=DETAIL_TABLE,
        pairs=f"{len(PAIRS)} (5 Price/MA + 4 MA5/MA)",
        sec_types=", ".join(sec_types),
        mode=(
            f"SINGLE-CODE {args.code} (full recompute for this security)"
            if args.code else
            "FORCE (full recompute)" if args.force
            else "incremental (missing dates only)"
        ),
    )

    conn = await get_db_connection_async()
    pool = await get_db_pool_async(min_size=1, max_size=max_concurrent)
    try:
        # ---- Single-code mode (--code): rebuild ONE security -------------
        # Bypasses the per-sec_type missing-date detection entirely — the
        # UI fires this when a security has NO analysis rows while the
        # rest of the sec_type is up to date (date-level detection would
        # see nothing missing and skip it).
        if args.code:
            total_detail = 0
            for st in sec_types:
                total_detail += await _process_single_code(
                    conn, pool, st, args.code, max_concurrent,
                )
            print(f"\n  -> Upserting analysis.analysis_identity registry...",
                  flush=True)
            await upsert_analysis_identity(
                conn,
                name=ANALYSIS_NAME,
                detail_name="mov_ave_spreads_detail",
                description=DESCRIPTION,
            )
            print(f"\n  TOTAL: {total_detail:,} detail rows inserted",
                  flush=True)
            print_wall_time(t0)
            return

        # ---- Step 0: determine target dates (per-sec_type) --------------
        if args.force:
            print("\n[0/4] Force mode: truncating detail "
                  "tables...", flush=True)
            await truncate_table_async(conn, DETAIL_TABLE)
            await truncate_table_async(conn, TRADING_AMT_TABLE)
            await truncate_table_async(conn, TRADING_AMT_RATIOS_TABLE)
            await truncate_table_async(conn, HOLIDAY_TABLE)
            await truncate_table_async(conn, MARKET_HYPES_TABLE)
            # Use empty set (not None) so _process_one_sec_type knows to
            # compute ALL dates (no filtering) in force mode.
            target_dates_per_st = {st: set() for st in sec_types}
            ta_missing_per_st = {}
            ta_ratios_missing_per_st = {}
            ohlc_missing_per_st = {}
            hypes_missing_per_st = {}
            print("    -> truncated; will recompute all rows", flush=True)
        else:
            print("\n[0/4] Detecting missing dates PER-sec_type "
                  "(etf_identity vs detail[etf], index_identity vs "
                  "detail[index], stock_identity vs detail[stock])...",
                  flush=True)
            target_dates_per_st: dict = {}
            ta_missing_per_st: dict = {}
            ta_ratios_missing_per_st: dict = {}
            ohlc_missing_per_st: dict = {}
            hypes_missing_per_st: dict = {}
            for st in sec_types:
                src_tbl = SEC_TYPE_IDENTITY_TABLE[st]
                td_st = await find_missing_analysis_dates(
                    conn, DETAIL_TABLE, [src_tbl], sec_type=st,
                )
                target_dates_per_st[st] = td_st
                print(f"    -> {st}: detail {len(td_st)} missing dates",
                      flush=True)
                # Also check trading_amt table independently
                td_ta = await find_missing_analysis_dates(
                    conn, TRADING_AMT_TABLE, [src_tbl], sec_type=st,
                )
                ta_missing_per_st[st] = td_ta
                if td_ta:
                    print(f"    -> {st}: trading_amt {len(td_ta)} missing dates",
                          flush=True)
                # Also check trading_amt_ratios table independently
                td_tar = await find_missing_analysis_dates(
                    conn, TRADING_AMT_RATIOS_TABLE, [src_tbl], sec_type=st,
                )
                ta_ratios_missing_per_st[st] = td_tar
                if td_tar:
                    print(f"    -> {st}: trading_amt_ratios "
                          f"{len(td_tar)} missing dates", flush=True)
                # Also check the OHLC table independently (missing dates
                # + repair dates whose rows predate the DATE/2nd-extrema
                # columns — see ohlc.find_ohlc_repair_dates).
                td_oh = await find_missing_analysis_dates(
                    conn, OHLC_TABLE, [src_tbl], sec_type=st,
                )
                td_oh |= await find_ohlc_repair_dates(conn, st)
                ohlc_missing_per_st[st] = td_oh
                if td_oh:
                    print(f"    -> {st}: ohlc {len(td_oh)} missing/repair dates",
                          flush=True)
                # The market-hypes table stores EPISODES (runs of hyped
                # dates), so per-date coverage cannot be diffed against
                # it — it is rebuilt wholesale whenever a sec_type is
                # processed. Only flag COMPLETELY EMPTY scopes (fresh
                # schema / wiped table) so the pipeline runs at least
                # once to populate them.
                hypes_empty = not await conn.fetchval(
                    f"SELECT EXISTS (SELECT 1 FROM {MARKET_HYPES_TABLE} "
                    f"WHERE sec_type = $1)",
                    st,
                )
                hypes_missing_per_st[st] = hypes_empty
                if hypes_empty:
                    print(f"    -> {st}: market_hypes empty "
                          f"(no episodes yet)", flush=True)
            total_missing = sum(
                len(s) for s in target_dates_per_st.values()
            )
            total_ta_missing = sum(
                len(s) for s in ta_missing_per_st.values()
            )
            total_ta_ratios_missing = sum(
                len(s) for s in ta_ratios_missing_per_st.values()
            )
            total_ohlc_missing = sum(
                len(s) for s in ohlc_missing_per_st.values()
            )
            total_hypes_missing = sum(
                1 for s in hypes_missing_per_st.values() if s
            )
            if (
                total_missing == 0
                and total_ta_missing == 0
                and total_ta_ratios_missing == 0
                and total_ohlc_missing == 0
                and total_hypes_missing == 0
            ):
                print("    -> DB is up to date; nothing to do.", flush=True)
                print_wall_time(t0)
                return
            # For sec_types where only trading_amt / trading_amt_ratios /
            # OHLC needs updates or market_hypes is still empty, set
            # target_dates to None so the detail step is skipped but the
            # internal steps (which have their own checks) still run.
            for st in sec_types:
                if not target_dates_per_st.get(st) and (
                    ta_missing_per_st.get(st)
                    or ta_ratios_missing_per_st.get(st)
                    or ohlc_missing_per_st.get(st)
                    or hypes_missing_per_st.get(st)
                ):
                    target_dates_per_st[st] = None

        # ---- Steps 1-5: process each sec_type independently -------------
        # Each sec_type is processed end-to-end (fetch → compute → insert →
        # RSI → free memory) before the next starts. This bounds peak
        # memory to a single sec_type's data — critical for the stock
        # universe (11K+ codes × 1.6K dates ≈ 17M rows / ~6 GB in pandas).
        # Loading all 3 sec_types at once (18.5M rows) OOMs the host.
        total_detail = 0
        for st in sec_types:
            td_st = target_dates_per_st.get(st) if target_dates_per_st else None
            ta_st = ta_missing_per_st.get(st) if ta_missing_per_st else None
            ta_ratio_st = (
                ta_ratios_missing_per_st.get(st)
                if ta_ratios_missing_per_st
                else None
            )
            oh_st = (
                ohlc_missing_per_st.get(st) if ohlc_missing_per_st else None
            )
            hy_st = (
                hypes_missing_per_st.get(st) if hypes_missing_per_st else None
            )
            if td_st is not None and len(td_st) == 0 and not args.force:
                if not ta_st and not ta_ratio_st and not oh_st and not hy_st:
                    print(f"\n  [{st}] up to date; skipping.", flush=True)
                    continue
            n_detail = await _process_one_sec_type(
                conn, pool, st, td_st, args.force, max_concurrent, t0,
            )
            total_detail += n_detail

        # ---- Upsert analysis_identity ------------------------------------
        print(f"\n  -> Upserting analysis.analysis_identity registry...",
              flush=True)
        await upsert_analysis_identity(
            conn,
            name=ANALYSIS_NAME,
            detail_name="mov_ave_spreads_detail",
            description=DESCRIPTION,
        )

        print(f"\n  TOTAL: {total_detail:,} detail rows inserted", flush=True)
        print_wall_time(t0)
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass
        try:
            await asyncio.wait_for(pool.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pool.terminate()


if __name__ == "__main__":
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
