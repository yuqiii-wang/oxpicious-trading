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
  7. INTERNAL STEP: compute Wilder RSI (6/10/14/20d) + price gaps (2/3d)
     from the SAME source price data already loaded in Step 1 ->
     analysis.mov_ave_rsi (see rsi.py). Reuses the same DB connection +
     source DataFrame. This step used to be a standalone
     analyze.mov_ave_rsi package; it is now an internal step because it
     shares the same source price data and active-universe pre-filter.

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

import pandas as pd  # noqa: E402

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
)
from analyze.mov_ave_spread.fetch import fetch_source_data  # noqa: E402
from analyze.mov_ave_spread.compute import build_detail_rows  # noqa: E402
from analyze.mov_ave_spread.peaks_and_floors import (  # noqa: E402
    compute_peaks_and_floors,
)
from analyze.mov_ave_spread.rsi import run_rsi  # noqa: E402


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
    # Pool for parallel upsert chunks. Each connection is a Postgres backend
    # process; max_size matches --max-concurrent (default 20). Local dev DB
    # has max_connections=100 with ~2 in use, so 20 leaves comfortable
    # headroom. More concurrent chunks = faster upserts, but WAL flushing
    # becomes the bottleneck past ~8-12 on a single SSD.
    pool = await get_db_pool_async(min_size=1, max_size=max_concurrent)
    try:
        # ---- Step 0: determine target dates (per-sec_type) --------------
        if args.force:
            print("\n[0/4] Force mode: truncating detail + peaks_and_floors "
                  "tables...", flush=True)
            # TRUNCATE detail FIRST (it has FK to peaks_and_floors), then
            # peaks_and_floors.
            await truncate_table_async(conn, DETAIL_TABLE)
            await truncate_table_async(conn, PEAKS_AND_FLOORS_TABLE)
            # target_dates_per_st is None in force mode → no incremental
            # filter applied to detail rows (full recompute).
            target_dates_per_st = None
            target_dates_union = None
            print("    -> truncated; will recompute all rows", flush=True)
        else:
            print("\n[0/4] Detecting missing dates PER-sec_type "
                  "(etf_identity vs detail[etf], index_identity vs "
                  "detail[index], stock_identity vs detail[stock])...",
                  flush=True)
            # Per-sec_type missing-date check. Without per-sec_type scoping,
            # a date populated for ETF would mask the same date being
            # missing for index/stock because the existing-date query
            # spans all sec_types.
            target_dates_per_st: dict = {}
            for st in sec_types:
                src_tbl = SEC_TYPE_IDENTITY_TABLE[st]
                td_st = await find_missing_analysis_dates(
                    conn, DETAIL_TABLE, [src_tbl], sec_type=st,
                )
                target_dates_per_st[st] = td_st
                print(f"    -> {st}: detail {len(td_st)} missing dates",
                      flush=True)
            # Union across sec_types — used to filter the concatenated
            # source DataFrame before computing detail rows. A date is
            # "to do" if ANY sec_type is missing it.
            target_dates_union = set()
            for s in target_dates_per_st.values():
                target_dates_union |= s
            print(f"    -> union across sec_types: "
                  f"{len(target_dates_union)} dates to (re)compute",
                  flush=True)
            if not target_dates_union:
                print("    -> DB is up to date; nothing to do.", flush=True)
                print_wall_time(t0)
                return

        # ---- Step 1: fetch FULL source data for every sec_type -----------
        # Always fetch the FULL per-code history (target_dates=None) so belt
        # detection for peaks_and_floors works correctly across month
        # boundaries. The target_dates filter is applied AFTER
        # peaks_and_floors computation (Step 3 below) for the detail rows.
        print("\n[1/4] Fetching FULL per-(sec_type, code, date) price + MAs "
              "from stats schema (needed for belt detection)...",
              flush=True)
        frames = []
        for at in sec_types:
            print(f"    -> fetching {at}...", flush=True)
            df_at = await fetch_source_data(conn, at, target_dates=None)
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

        # ---- Step 2: compute + upsert peaks_and_floors -------------------
        print("\n[2/4] Computing peaks_and_floors (per-extreme-date "
              "detection from full history)...", flush=True)
        pf_rows = compute_peaks_and_floors(df)
        print(f"    -> {len(pf_rows):,} peaks_and_floors rows "
              f"(one per detected extreme/trend)",
              flush=True)

        # Save the FULL list of peaks_and_floors rows for the detail FK
        # mapping (nearest-preceding-extreme). Must be saved BEFORE the
        # incremental skip-filter so detail rows can map to ALL extremes,
        # not just newly-added ones.
        all_pf_rows = pf_rows

        print(f"    -> Upserting {len(pf_rows):,} peaks_and_floors rows "
              f"into {PEAKS_AND_FLOORS_TABLE} "
              f"(chunked by date to bound memory)...", flush=True)

        # Pre-check: skip already-present extreme dates, scoped per-sec_type
        # so an ETF extreme date doesn't mask the same date being missing
        # for index/stock. In incremental mode, most extreme dates are
        # already populated.
        n_pf_before = len(pf_rows)
        pf_rows = await _filter_per_sec_type_async(
            conn, PEAKS_AND_FLOORS_TABLE, pf_rows,
        )
        n_pf_skipped = n_pf_before - len(pf_rows)
        if n_pf_skipped > 0:
            print(f"    -> skip check (per-sec_type): {n_pf_skipped:,} of "
                  f"{n_pf_before:,} peaks_and_floors rows already present "
                  f"(skipped)",
                  flush=True)

        n_pf = await batched_upsert_by_date(
            conn, PEAKS_AND_FLOORS_TABLE, pf_rows,
            key_columns=["sec_type", "code", "date"],
            label="peaks_and_floors",
            pool=pool,
            max_concurrent=max_concurrent,
        )
        print(f"    -> upserted {n_pf:,} peaks_and_floors rows", flush=True)

        # ---- Step 3+4: build + insert detail (chunked by date) -----------
        # Build detail rows in date-bounded chunks and insert each chunk
        # immediately, so the full dict list is never materialized at
        # once. sanitize_for_db_insert converts each numeric column to
        # object dtype (~4x float64) and to_dict creates one Python dict
        # per row (~1.6 KB for a 45-key dict); for the full stock universe
        # (6.7M rows) that is ~10+ GB of dicts alone and OOMs. Chunking
        # bounds peak memory to one chunk's dicts (~100K rows ≈ 160 MB).
        #
        # The full all_pf_rows is passed to every chunk (via the build_fn
        # closure) so _compute_pf_date_mapping's nearest-preceding-extreme
        # asof picks up ALL extremes, not just the chunk's dates — the
        # chunk's sub-frame + the full pf_df in the combined timeline
        # forward-fills the correct extreme per detail row.
        print("\n[3/4] Computing + inserting detail rows in date-bounded "
              "chunks (9 gap cols + 12 slope/curv cols per row)...",
              flush=True)
        detail_df = df
        if target_dates_union is not None and len(target_dates_union) > 0:
            n_before = len(detail_df)
            detail_df = detail_df[
                detail_df["date"].isin(target_dates_union)
            ].reset_index(drop=True)
            print(f"    -> incremental filter: {len(detail_df):,} of "
                  f"{n_before:,} rows are in target_dates_union", flush=True)
        print(f"    -> building {len(detail_df):,} detail rows "
              f"({'COPY' if args.force else 'upsert'} per chunk)",
              flush=True)

        n_detail = await build_and_insert_chunked(
            conn, pool, detail_df,
            lambda sub: build_detail_rows(sub, pf_rows=all_pf_rows),
            table_name=DETAIL_TABLE,
            key_columns=["sec_type", "code", "date"],
            force=args.force,
            sec_types=sec_types,
            max_concurrent=max_concurrent,
            label="detail",
        )
        print(f"    -> inserted {n_detail:,} detail rows", flush=True)

        del detail_df

        # ---- Upsert analysis_identity ------------------------------------
        print(f"    -> Upserting analysis.analysis_identity registry...",
              flush=True)
        await upsert_analysis_identity(
            conn,
            name=ANALYSIS_NAME,
            detail_name="mov_ave_spreads_detail",
            description=DESCRIPTION,
        )

        # ---- Step 5: INTERNAL rsi step -----------------------------------
        # Compute Wilder RSI (6/10/14/20d) + short-term price gaps (2/3d)
        # from the SAME source price data already loaded in Step 1 ->
        # analysis.mov_ave_rsi. Reuses the same DB connection + pool.
        # See rsi.py for the full pipeline. The source DataFrame ``df``
        # (full per-code history) is passed so the RSI step can reuse the
        # price column without a second DB fetch.
        await run_rsi(conn, df, force=args.force, pool=pool,
                      max_concurrent=max_concurrent)

        # Free the source DataFrame now that detail + RSI rows are built —
        # the full history is no longer needed.
        del df

        print_wall_time(t0)
    finally:
        # Close with a timeout — after heavy parallel bulk inserts the
        # PostgreSQL server can be saturated with WAL checkpoint I/O,
        # making conn.close() / pool.close() stall on the Terminate
        # message + TCP teardown. Without a timeout this hangs forever.
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass
        try:
            await asyncio.wait_for(pool.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            # Graceful close timed out — force-terminate all connections.
            pool.terminate()


if __name__ == "__main__":
    asyncio.run(main())
