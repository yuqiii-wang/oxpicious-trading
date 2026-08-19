"""Entry point for analyze.margins.

Run via ``python -m analyze.margins``.

Pipeline (default: incremental — skip dates already in the DB;
``--force``: truncate-then-recompute on every run).

  1. Determine ``ref_date`` = MAX(date) across the selected source
     tables. The universe filter (securities with non-zero rz_balance
     in the last 30 calendar days) is evaluated against this date, NOT
     ``datetime.date.today()`` — the source tables may lag by 1-2 days
     (weekend / holiday), so using today would wrongly exclude the
     most recent partial-day rows.

  2. For each sec_type in the selected set (etf / stock / both):
       a. fetch_active_rongzi_codes  — universe filter (rz_balance > 0
          in the last 30 calendar days on or before ref_date).
       b. fetch_margin_history       — full per-(code, date) rz_balance
          + rz_buy for the filtered codes. FULL history is always
          fetched (MA60 needs 60 prior days); the incremental filter
          is applied AFTER computation, not at fetch time.
       c. fetch_industry_mapping     — code -> industry_id.
       d. compute_tech_stats         — ma5/ma20/ma60 + slope per code
          (computed on FULL history for MA correctness + hypes episode
          detection).
       e. Write rows to margin_tech_stats.

  3. compute_industry_stats — per-(date, industry_id) SUM aggregation
     of rz_balance / rz_buy across stocks AND ETFs, with stock/etf
     components stored separately.

  4. run_correlations — pairwise rolling Pearson correlation of
     securities' rongzi series within each industry ->
     margin_industry_correlation.

  5. run_margin_changes — detect sustained UP/DOWN TREND episodes on
     the RONGZI margin-balance curve and populate margin_changes.

  6. Register the two non-internal analyses in
     analysis.analysis_identity.

Incremental mode rationale
  The universe filter (active rongzi in last 30d) shifts daily, but
  existing rows for past dates remain valid — rz_balance is a STOCK
  (cumulative balance) that doesn't change retroactively. New dates
  simply get appended via upsert; stale codes drop off naturally for
  new dates (their rows for past dates are retained as historical
  record). The MA computation still uses FULL history (fetched
  unconditionally) so MA60 windows are correct even for the first
  newly-added date.

Testing
  ``python -m analyze.margins --sec-type etf`` runs the pipeline with
  ETF data only (smaller dataset, ~1K rows vs ~600K for stocks).
  ``python -m analyze.margins --sec-type index`` runs only the
  index-level aggregation from the margin_index_series VIEW + hypes
  detection (skips stock/etf/industry/correlation steps).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import os
import sys
import time

# Ensure project root is on sys.path so ``_common`` is importable when run
# directly via ``python -m analyze.margins`` or as a script.
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
from analyze._common import (  # noqa: E402
    upsert_analysis_identity,
)
from analyze.margins.config import (  # noqa: E402
    TABLE_TECH_STATS,
    TABLE_INDUSTRY_STATS,
    SEC_TYPES,
    TECH_STATS_DESCRIPTION,
    INDUSTRY_STATS_DESCRIPTION,
)

setup_utf8_stdout()

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

# Now safe to import modules that use pandas
import pandas as pd  # noqa: E402

from analyze.margins.pipeline import (  # noqa: E402
    fetch_latest_source_date,
    detect_missing_dates,
    run_sec_type,
    run_index_tech_stats,
    insert_industry_stats,
)
from analyze.margins.compute import compute_industry_stats  # noqa: E402
from analyze.margins.correlations import run_correlations  # noqa: E402
from analyze.margins.changes import run_margin_changes  # noqa: E402


# ---------------------------------------------------------------------------
#  Main orchestration
# ---------------------------------------------------------------------------

async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Margin analysis: per-code tech stats + per-industry "
                    "SUM aggregation + within-industry security-pair "
                    "correlation. RONGZI (融资) only — RONQIN (融券) excluded."
    )
    ap.add_argument(
        "--sec-type",
        choices=["etf", "stock", "index", "both"],
        default="both",
        help="Which sec_type to process. 'both' (default) runs the full "
             "pipeline. 'etf' or 'stock' runs only that sec_type (useful "
             "for testing with a smaller dataset). 'index' runs only the "
             "index-level aggregation from the margin_index_series VIEW "
             "+ hypes detection (skips stock/etf/industry/correlation steps; "
             "useful for testing VIEW/MA/hype changes in isolation).",
    )
    add_force_arg(ap)
    args = ap.parse_args()
    force = args.force

    sec_types = SEC_TYPES if args.sec_type == "both" else (
        [] if args.sec_type == "index" else [args.sec_type]
    )
    run_index = args.sec_type in ("both", "index")
    is_index_only = args.sec_type == "index"

    t0 = time.time()
    print_build_header(
        "ANALYZE MARGINS (rongzi-only tech stats + industry SUM + corr)",
        index_table=TABLE_TECH_STATS,
        sec_type=args.sec_type,
        mode="FORCE (full recompute)" if force
             else "incremental (missing dates only)",
    )

    conn = await get_db_connection_async()
    try:
        # ---- Step 0: determine ref_date + missing dates ---------------
        print("\n[0/6] Determining ref_date (MAX date across source tables)...",
              flush=True)
        ref_date = await fetch_latest_source_date(conn, sec_types)
        print(f"    -> ref_date = {ref_date}", flush=True)

        if not force:
            print("\n    Detecting missing dates per table (incremental mode)...",
                  flush=True)
        (
            target_dates_tech, target_dates_index,
            target_dates_industry, target_dates_corr,
        ) = await detect_missing_dates(
            conn, sec_types, run_index, is_index_only, force,
        )

        # Early exit if everything is up to date (incremental mode only).
        if not force:
            total_missing = (
                sum(len(s) for s in target_dates_tech.values())
                + len(target_dates_index)
                + len(target_dates_industry)
                + len(target_dates_corr)
            )
            if total_missing == 0:
                print("    -> DB is up to date; nothing to do.", flush=True)
                print_wall_time(t0)
                return

        # ---- Step 1: per-sec-type tech stats ----------------------------
        print(f"\n[1/6] Per-sec-type tech stats (sec_types={sec_types})...",
              flush=True)
        # Force mode: truncate the whole tech_stats table up front when
        # processing both sec_types (faster than 2 separate DELETEs).
        if force and args.sec_type == "both":
            print("    Truncating margin_tech_stats (all sec_types)...",
                  flush=True)
            await truncate_table_async(conn, TABLE_TECH_STATS)

        histories: dict[str, pd.DataFrame] = {}
        maps: dict[str, pd.DataFrame] = {}
        tech_stats_by_sec_type: dict[str, pd.DataFrame] = {}
        for st in sec_types:
            td = target_dates_tech.get(st)
            if td is not None and len(td) == 0 and not force:
                print(f"\n  [{st}] up to date; skipping.", flush=True)
                continue
            hist, imap, tech = await run_sec_type(
                conn, st, ref_date, force=force, target_dates=td,
            )
            histories[st] = hist
            maps[st] = imap
            tech_stats_by_sec_type[st] = tech

        # ---- Step 1b: index-level tech stats (aggregated from VIEW) ----
        if run_index:
            td_idx = target_dates_index
            if td_idx is not None and len(td_idx) == 0 and not force:
                print("\n  [index] up to date; skipping.", flush=True)
            else:
                idx_hist, idx_tech = await run_index_tech_stats(
                    conn, force=force, target_dates=td_idx,
                )
                histories["index"] = idx_hist
                tech_stats_by_sec_type["index"] = idx_tech

        # ---- Step 2: industry SUM aggregation + insert + correlations ---
        if is_index_only:
            print("\n[2/6] Per-(date, industry_id) SUM aggregation "
                  "-- SKIPPED (index-only test run)", flush=True)
        else:
            print("\n[2/6] Per-(date, industry_id) SUM aggregation "
                  "(stock + etf)...", flush=True)

        etf_hist = histories.get("etf", pd.DataFrame(
            columns=["code", "date", "rz_balance", "rz_buy"]
        ))
        stock_hist = histories.get("stock", pd.DataFrame(
            columns=["code", "date", "rz_balance", "rz_buy"]
        ))
        etf_map = maps.get("etf", pd.DataFrame(
            columns=["code", "industry_id", "industry_label",
                     "parent_index_weight"]
        ))
        stock_map = maps.get("stock", pd.DataFrame(
            columns=["code", "industry_id", "industry_label",
                     "parent_index_weight"]
        ))

        if is_index_only:
            # Index-only test run: skip industry aggregation + insert +
            # correlation steps (no stock/etf histories to aggregate).
            industry_stats = pd.DataFrame()
        else:
            # ---- Step 2: compute industry SUM aggregation -------------
            print("\n[2/6] Per-(date, industry_id) SUM aggregation "
                  "(stock + etf)...", flush=True)

            # Pre-compute industry stats (will be inserted in step 3)
            industry_stats = pd.DataFrame()  # placeholder for type
            industry_stats = compute_industry_stats(
                etf_tech=etf_hist,
                stock_tech=stock_hist,
                etf_industry_map=etf_map,
                stock_industry_map=stock_map,
            )
            n_industries = (
                industry_stats["industry_id"].nunique()
                if not industry_stats.empty else 0
            )
            print(f"    -> {len(industry_stats):,} rows across "
                  f"{n_industries} industries", flush=True)

            # ---- Step 3: insert industry_stats ------------------------
            print(f"\n[3/6] Inserting into {TABLE_INDUSTRY_STATS}...",
                  flush=True)
            await insert_industry_stats(
                conn, industry_stats,
                force=force, target_dates=target_dates_industry,
            )

            # ---- Step 4: within-industry security-pair correlations ---
            # run_correlations upserts its OWN analysis_identity row
            # (margin_industry_correlation) internally, so __main__ only
            # upserts the other two identity rows in step 6.
            print("\n[4/6] Within-industry security-pair correlations "
                  "(internal step)...", flush=True)
            await run_correlations(
                conn, force=force, target_dates=target_dates_corr,
            )

        # ---- Step 5: margin changes detection ---------------------------
        # run_margin_changes upserts its OWN analysis_identity row
        # (margin_changes) internally, reusing the in-memory tech_stats +
        # raw histories collected in step 1 (no DB round-trip for source
        # data). Always truncates + recomputes when called — new dates
        # can change trend boundaries.
        print("\n[5/6] Margin changes detection (internal step)...",
              flush=True)
        await run_margin_changes(
            conn,
            histories=histories,
            tech_stats_by_sec_type=tech_stats_by_sec_type,
            force=True,
        )

        # ---- Step 6: register in analysis_identity ----------------------
        # (correlation + changes identity rows are upserted by their own
        # internal steps above)
        print("\n[6/6] Registering in analysis.analysis_identity...",
              flush=True)
        await upsert_analysis_identity(
            conn,
            name="margin_tech_stats",
            detail_name="margin_tech_stats",
            description=TECH_STATS_DESCRIPTION,
        )
        if not is_index_only:
            # industry_stats identity row only upserted when the industry
            # aggregation step ran (skipped for index-only test runs).
            await upsert_analysis_identity(
                conn,
                name="margin_industry_stats",
                detail_name="margin_industry_stats",
                description=INDUSTRY_STATS_DESCRIPTION,
            )
            print("    -> upserted 2 identity rows (+1 from correlations, "
                  "+1 from changes step)", flush=True)
        else:
            print("    -> upserted 1 identity row (+1 from changes step)",
                  flush=True)

        print_wall_time(t0)
    finally:
        # Close with a timeout — after heavy bulk inserts the PostgreSQL
        # server can be saturated with WAL checkpoint I/O, making
        # conn.close() stall on the Terminate message + TCP teardown.
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    asyncio.run(main())