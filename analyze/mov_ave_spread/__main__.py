"""Entry point for analyze.mov_ave_spread.

Run via ``python -m analyze.mov_ave_spread``.

Pipeline
  1. Fetch per-(sec_type, code, date) price + MAs from stats schema for
     every sec_type in SEC_TYPES (active-universe pre-filter applied).
  2. Compute peaks_and_floors (monthly valley-low detection) from the FULL
     per-code history — belt detection needs the full history so belts
     spanning month boundaries are detected correctly.
  3. Upsert peaks_and_floors FIRST (so the detail FK resolves).
  4. Compute 9 wide gap columns + 12 slope/curvature columns per row
     (filtered to target_dates in incremental mode).
  5. Upsert detail.
  6. Upsert analysis_identity.
  7. INTERNAL STEP: compute Wilder RSI (6/10/14/20/60/120/255/500d) + price gaps (2/3d)
     from the SAME source price data already loaded in Step 1 ->
     analysis.mov_ave_rsi (see rsi.py). Reuses the same DB connection +
     source DataFrame. This step used to be a standalone
     analyze.mov_ave_rsi package; it is now an internal step because it
     shares the same source price data and active-universe pre-filter.
  8. INTERNAL STEP: compute EMA spread detail (9 EMA gap pairs + 5 EMA
     slope + 5 EMA curvature) from the SAME source data (EMA columns +
     pre-computed EMA slopes/curvatures already in the parent DataFrame)
     -> analysis.mov_ave_spreads_detail_ema (see ema.py). Reuses the
     same DB connection + source DataFrame.
  9. INTERNAL STEP: compute rolling OHLC detail (today_close +
     open/high/low over 6 windows: 20/60/120/255/500/750 trading days)
     from the SAME source data -> analysis.mov_ave_spreads_detail_ohlc
     (see ohlc.py). Reuses the same DB connection + source DataFrame.

Default (incremental) mode:
  Only dates present in source identity tables (stats.etf_identity +
  stats.index_identity + stats.stock_identity) but NOT yet in
  analysis.mov_ave_spreads_detail are (re)computed and upserted.
  peaks_and_floors is always recomputed for ALL months of affected codes
  (belts can change when new data arrives).

  The missing-date check is PER-sec_type: a date populated for ETF does
  NOT mask the same date being missing for index/stock. This matters
  because the analysis table PK is (sec_type, code, date) — a global
  date check would falsely skip a date for sec_type B just because
  sec_type A already had it.

--force mode:
  Truncate peaks_and_floors + detail, then recompute and
  insert all rows for the active universe.

--sec-type mode:
  Process only the specified sec_type (for testing). Default: all.
"""
from __future__ import annotations

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
    filter_rows_to_missing_dates_async,
    add_force_arg,
)

setup_utf8_stdout()

from analyze._common import (  # noqa: E402
    batched_upsert_by_date,
    build_and_insert_chunked,
    upsert_analysis_identity,
)
from analyze.mov_ave_spread.config import (  # noqa: E402
    ANALYSIS_NAME,
    DETAIL_TABLE,
    PEAKS_AND_FLOORS_TABLE,
    DESCRIPTION,
    PAIRS,
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
    TRADING_AMT_ANALYSIS_NAME,
    TRADING_AMT_TABLE,
)
from analyze.mov_ave_spread.fetch import fetch_source_data  # noqa: E402
from analyze.mov_ave_spread.compute import build_detail_rows  # noqa: E402
from analyze.mov_ave_spread.peaks_and_floors import (  # noqa: E402
    compute_peaks_and_floors,
)
from analyze.mov_ave_spread.rsi import run_rsi  # noqa: E402
from analyze.mov_ave_spread.ema import run_ema  # noqa: E402
from analyze.mov_ave_spread.ohlc import run_ohlc  # noqa: E402
from analyze.mov_ave_spread.trading_amt import run_trading_amt  # noqa: E402
from analyze.mov_ave_spread.rebounds import run_rebounds  # noqa: E402


async def _filter_per_sec_type_async(conn, table, rows):
    """Filter rows to missing dates, scoping the existing-date check
    per-sec_type.

    ``rows`` is a list of dicts each carrying a ``sec_type`` field. We
    split them by sec_type and call
    :func:`filter_rows_to_missing_dates_async` once per group with the
    matching ``sec_type`` argument. Without per-sec_type scoping, a date
    already populated for ETF would mask the same date being missing for
    index/stock (PK is (sec_type, code, date)).
    """
    if not rows:
        return []
    by_st: dict = {}
    for r in rows:
        by_st.setdefault(r.get("sec_type"), []).append(r)
    out: list = []
    for st, group in by_st.items():
        filtered = await filter_rows_to_missing_dates_async(
            conn, table, group, sec_type=st,
        )
        out.extend(filtered)
    return out


async def _process_one_sec_type(
    conn, pool, st, target_dates_st, force, max_concurrent, t0,
):
    """Process a single sec_type end-to-end.

    Fetches the FULL per-code history for ``st`` (needed for belt
    detection), computes peaks_and_floors, upserts them, then builds +
    inserts detail rows (filtered to ``target_dates_st`` in incremental
    mode), then runs the RSI step on the same source data. The source
    DataFrame is freed before returning so the caller can process the
    next sec_type without cumulative memory growth.

    Splitting per-sec_type is essential for the stock universe: 11K+
    codes × 1.6K dates = ~17M rows (~6 GB in pandas). Loading all 3
    sec_types at once (18.5M rows) OOMs the host; processing one sec_type
    at a time bounds peak memory to a single sec_type's data.
    """
    # ---- Fetch FULL source data for this sec_type ----------------------
    # Always fetch the FULL per-code history (target_dates=None) so belt
    # detection for peaks_and_floors works correctly across month
    # boundaries. The target_dates filter is applied AFTER
    # peaks_and_floors computation for the detail rows.
    print(f"\n  [{st}] Fetching FULL per-(code, date) price + MAs "
          f"from stats schema (needed for belt detection)...", flush=True)
    df = await fetch_source_data(conn, st, target_dates=None)
    print(f"  [{st}]   {len(df):,} (code, date) source rows", flush=True)
    if df.empty:
        print(f"  [{st}]   no source data; skipping.", flush=True)
        return 0, 0

    # ---- Compute + upsert peaks_and_floors -----------------------------
    print(f"\n  [{st}] Computing peaks_and_floors (per-extreme-date "
          f"detection from full history)...", flush=True)
    pf_rows = compute_peaks_and_floors(df)
    print(f"  [{st}]   {len(pf_rows):,} peaks_and_floors rows",
          flush=True)

    # Save the FULL list of peaks_and_floors rows for the detail FK
    # mapping (nearest-preceding-extreme). Must be saved BEFORE the
    # incremental skip-filter so detail rows can map to ALL extremes,
    # not just newly-added ones.
    all_pf_rows = pf_rows

    print(f"  [{st}]   Upserting {len(pf_rows):,} peaks_and_floors rows "
          f"into {PEAKS_AND_FLOORS_TABLE} "
          f"(chunked by date to bound memory)...", flush=True)

    # Pre-check: skip already-present extreme dates, scoped per-sec_type.
    n_pf_before = len(pf_rows)
    pf_rows = await _filter_per_sec_type_async(
        conn, PEAKS_AND_FLOORS_TABLE, pf_rows,
    )
    n_pf_skipped = n_pf_before - len(pf_rows)
    if n_pf_skipped > 0:
        print(f"  [{st}]   skip check (per-sec_type): {n_pf_skipped:,} of "
              f"{n_pf_before:,} peaks_and_floors rows already present "
              f"(skipped)",
              flush=True)

    n_pf = await batched_upsert_by_date(
        conn, PEAKS_AND_FLOORS_TABLE, pf_rows,
        key_columns=["sec_type", "code", "date"],
        label=f"peaks_and_floors[{st}]",
        pool=pool,
        max_concurrent=max_concurrent,
    )
    print(f"  [{st}]   upserted {n_pf:,} peaks_and_floors rows", flush=True)

    # Free the filtered pf_rows list (all_pf_rows is retained for detail).
    del pf_rows

    # ---- Build + insert detail (chunked by date) -----------------------
    if target_dates_st is not None and len(target_dates_st) == 0:
        # Empty set means force mode — compute ALL dates (no filtering).
        print(f"\n  [{st}] Computing + inserting detail rows (FORCE mode, all dates)...",
              flush=True)
        detail_df = df
        n_detail = await build_and_insert_chunked(
            conn, pool, detail_df,
            lambda sub: build_detail_rows(sub, pf_rows=all_pf_rows),
            table_name=DETAIL_TABLE,
            key_columns=["sec_type", "code", "date"],
            force=force,
            sec_types=(st,),
            max_concurrent=max_concurrent,
            label=f"detail[{st}]",
        )
        print(f"  [{st}]   inserted {n_detail:,} detail rows", flush=True)
        del detail_df
    elif target_dates_st is None:
        print(f"\n  [{st}] Detail up-to-date; skipping detail "
              f"computation.", flush=True)
        n_detail = 0
        return n_pf, n_detail
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

        n_detail = await build_and_insert_chunked(
            conn, pool, detail_df,
            lambda sub: build_detail_rows(sub, pf_rows=all_pf_rows),
            table_name=DETAIL_TABLE,
            key_columns=["sec_type", "code", "date"],
            force=force,
            sec_types=(st,),
            max_concurrent=max_concurrent,
            label=f"detail[{st}]",
        )
        print(f"  [{st}]   inserted {n_detail:,} detail rows", flush=True)

        del detail_df

    # ---- RSI step (reuses same source DataFrame) -----------------------
    await run_rsi(conn, df, force=force, pool=pool,
                  max_concurrent=max_concurrent, sec_type=st)

    # ---- EMA detail step (reuses same source DataFrame) ---------------
    # Computes 9 EMA gap (vs) columns + selects pre-computed EMA slope/
    # curvature columns into analysis.mov_ave_spreads_detail_ema (see
    # ema.py). The EMA columns (ema{6,20,60,120,255}) + EMA slope/
    # curvature are already in the parent DataFrame (fetched + computed
    # by fetch_source_data / compute_ema_slopes_curvatures), so this step
    # needs no second DB fetch.
    await run_ema(conn, df, force=force, pool=pool,
                  max_concurrent=max_concurrent, sec_type=st)

    # ---- OHLC detail step (reuses same source DataFrame) -------------
    # Computes today_close + rolling open/high/low over 6 windows
    # (20/60/120/255/500/750 trading days) into analysis.mov_ave_spreads_
    # detail_ohlc (see ohlc.py). The price/open/high/low columns are
    # already in the parent DataFrame, so this step needs no second
    # DB fetch.
    await run_ohlc(conn, df, force=force, pool=pool,
                   max_concurrent=max_concurrent, sec_type=st)

    # ---- Trading-amount detail step (reuses same source DataFrame) ---
    # Computes rolling max/min of trading_amt_ma5 + ratio columns into
    # analysis.mov_ave_trading_amt (see trading_amt.py). The trading_amt_*
    # columns are already computed by the parent fetch step (helpers),
    # so this step only adds the new max/min + ratio columns.
    await run_trading_amt(conn, df, force=force, pool=pool,
                          max_concurrent=max_concurrent, sec_type=st)

    # ---- Rebounds step (reuses same source DataFrame) ---------------
    # Computes double-top (rebound) detection metrics into
    # analysis.mov_ave_rebounds (see rebounds.py). The price and
    # trading_amount columns are already in the parent DataFrame,
    # so this step needs no second DB fetch.
    await run_rebounds(conn, df, force=force, pool=pool,
                       max_concurrent=max_concurrent, sec_type=st)

    # Free the source DataFrame — full history no longer needed.
    del df
    del all_pf_rows

    return n_pf, n_detail


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
    args = ap.parse_args()

    sec_types = (args.sec_type,) if args.sec_type else SEC_TYPES
    max_concurrent = max(1, args.max_concurrent)

    t0 = time.time()
    print_build_header(
        "ANALYZE MA-SPREADS (ETF + INDEX + STOCK)",
        detail_table=DETAIL_TABLE,
        pairs=f"{len(PAIRS)} (5 Price/MA + 4 MA5/MA)",
        sec_types=", ".join(sec_types),
        mode="FORCE (full recompute)" if args.force else "incremental (missing dates only)",
    )

    conn = await get_db_connection_async()
    pool = await get_db_pool_async(min_size=1, max_size=max_concurrent)
    try:
        # ---- Step 0: determine target dates (per-sec_type) --------------
        if args.force:
            print("\n[0/4] Force mode: truncating detail + peaks_and_floors "
                  "tables...", flush=True)
            await truncate_table_async(conn, DETAIL_TABLE)
            await truncate_table_async(conn, PEAKS_AND_FLOORS_TABLE)
            await truncate_table_async(conn, TRADING_AMT_TABLE)
            # Use empty set (not None) so _process_one_sec_type knows to
            # compute ALL dates (no filtering) in force mode.
            target_dates_per_st = {st: set() for st in sec_types}
            ta_missing_per_st = {}
            print("    -> truncated; will recompute all rows", flush=True)
        else:
            print("\n[0/4] Detecting missing dates PER-sec_type "
                  "(etf_identity vs detail[etf], index_identity vs "
                  "detail[index], stock_identity vs detail[stock])...",
                  flush=True)
            target_dates_per_st: dict = {}
            ta_missing_per_st: dict = {}
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
            total_missing = sum(
                len(s) for s in target_dates_per_st.values()
            )
            total_ta_missing = sum(
                len(s) for s in ta_missing_per_st.values()
            )
            if total_missing == 0 and total_ta_missing == 0:
                print("    -> DB is up to date; nothing to do.", flush=True)
                print_wall_time(t0)
                return
            # For sec_types where only trading_amt needs updates,
            # set target_dates to None so detail step is skipped but
            # internal steps (which have their own checks) still run.
            for st in sec_types:
                if not target_dates_per_st.get(st) and ta_missing_per_st.get(st):
                    target_dates_per_st[st] = None

        # ---- Steps 1-5: process each sec_type independently -------------
        # Each sec_type is processed end-to-end (fetch → compute → insert →
        # RSI → free memory) before the next starts. This bounds peak
        # memory to a single sec_type's data — critical for the stock
        # universe (11K+ codes × 1.6K dates ≈ 17M rows / ~6 GB in pandas).
        # Loading all 3 sec_types at once (18.5M rows) OOMs the host.
        total_pf = 0
        total_detail = 0
        for st in sec_types:
            td_st = target_dates_per_st.get(st) if target_dates_per_st else None
            ta_st = ta_missing_per_st.get(st) if ta_missing_per_st else None
            if td_st is not None and len(td_st) == 0 and not args.force:
                if not ta_st:
                    print(f"\n  [{st}] up to date; skipping.", flush=True)
                    continue
            n_pf, n_detail = await _process_one_sec_type(
                conn, pool, st, td_st, args.force, max_concurrent, t0,
            )
            total_pf += n_pf
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

        print(f"\n  TOTAL: {total_pf:,} peaks_and_floors + "
              f"{total_detail:,} detail rows inserted", flush=True)
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
    asyncio.run(main())
