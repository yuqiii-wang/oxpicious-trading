"""Entry point for analyze.industry_sentiments (downstream analysis steps).

Run via ``python -m analyze.industry_sentiments``.

The per-industry BASELINE aggregation (formerly steps 1-6 of this module)
was MIGRATED (2026-08-24) to ``builds.industry`` → ``stats.industry_basic_stats``
(renamed from ``analysis.industry_sentiments``; mean_price rehooked to
mean_close + mean_open/high/low added). This module now runs ONLY the
downstream analysis steps that READ from the baseline table.

Correlations are DISABLED BY DEFAULT — run them separately via
``python -m analyze.industry_sentiments.corr`` (incremental / --force), or
opt back into the combined pipeline with ``--with-corr``.

Pipeline
  0. Determine target dates. --with-corr only: source dates that are
     POTENTIAL window END dates on the stats.industry_basic_stats calendar
     grid but NOT yet covered by a computed window end in
     analysis.industry_correlations (correlations.find_missing_corr_window_ends).
     Default (no corr): run perf_attribution first (it manages its OWN
     missing-date detection), then target dates = dates present in
     sec_alloc_perf_attribution but missing from industry_attributions
     (attributions.find_missing_attribution_dates).
     --force truncates attributions + hypes_and_drains first (plus
     correlations ONLY with --with-corr; the baseline table is owned by
     builds.industry and is NOT truncated here — use
     ``python -m builds.industry --force`` to rebuild it).
  1. INTERNAL STEP (--with-corr only): windowed MA-curve correlations of
     industries' mean_close series -> analysis.industry_correlations (see
     correlations.py). Reuses the same DB connection.
  2. INTERNAL STEP: populate analysis.sec_alloc_perf_attribution (see
     analyze.sec_alloc_perf_attribution.run.run_perf_attribution).
     Reuses the same DB connection. It manages its OWN target_dates
     (missing from sec_alloc_perf_attribution vs stats.index_identity)
     since its missing dates can differ from correlations' missing dates.
     (Skipped when it already ran in step 0 — default incremental mode.)
  3. INTERNAL STEP: aggregate analysis.sec_alloc_perf_attribution
     shared_weight to the industry level -> analysis.industry_attributions
     (see attributions.py). Depends on step 2 being populated first;
     exits gracefully if empty.
  4. INTERNAL STEP: aggregate code_etf_trading_amount to the industry
     level -> analysis.industry_etf_contribution (see etf_contribution.py).
     Depends on step 2 being populated first; exits gracefully if no
     index rows have non-NULL code_etf_trading_amount.
  5. INTERNAL STEP: pre-compute top-5 (HYPE) + bottom-5 (DRAIN)
     industries -> analysis.industry_hypes_and_drains (see
     hypes_and_drains.py). Depends on step 3 (attributions) being
     populated first. Always full recompute.

Default (incremental, no-corr) mode:
  Step 2 runs first and populates any missing perf dates; then only the
  dates missing from industry_attributions are (re)computed in steps 3/4.

--force mode:
  Truncates analysis.industry_attributions + industry_hypes_and_drains
  (plus analysis.industry_correlations with --with-corr), then recomputes
  all downstream rows (the baseline stats.industry_basic_stats is NOT
  truncated — it is rebuilt by ``python -m builds.industry --force``).
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
from typing import Optional, Set

import datetime  # noqa: F401  (type hints below)

# Ensure project root is on sys.path so ``_common`` is importable when run
# directly via ``python -m analyze.industry_sentiments`` or as a script.
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

setup_utf8_stdout()

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

from analyze.industry_sentiments.correlations import (  # noqa: E402
    run_correlations,
    find_missing_corr_window_ends,
    TABLE as CORRELATIONS_TABLE,
)
from analyze.industry_sentiments.attributions import (  # noqa: E402
    run_attributions,
    needs_rolling_backfill,
    find_missing_attribution_dates,
    TABLE as ATTRIBUTIONS_TABLE,
)
from analyze.industry_sentiments.etf_contribution import (  # noqa: E402
    run_etf_contribution,
    TABLE as ETF_CONTRIBUTION_TABLE,
)
from analyze.industry_sentiments.hypes_and_drains import (  # noqa: E402
    run_hypes_and_drains,
    TABLE as HYPES_DRAINS_TABLE,
)
from analyze.sec_alloc_perf_attribution.run import (  # noqa: E402
    run_perf_attribution,
)

# The industry BASELINE table (owned by builds.industry). This module only
# READS from it — the correlations step consumes its mean_close series.
BASELINE_TABLE = "stats.industry_basic_stats"


async def _up_to_date_checks(conn, t0: float) -> None:
    """Post-detection checks for the 'no missing target dates' branch.

    Even when no incremental target dates are missing, the attributions
    table might have rows with NULL rolling price columns (e.g. after
    adding benchmark_non_this_industry_rolling_* columns via ALTER TABLE,
    or an interrupted pre-transaction run) — detected by
    needs_rolling_backfill and fixed with a full attributions recompute.
    The hypes_and_drains table might also be empty (first run after the
    SQL migration). Exits after the checks (prints wall time + returns).
    """
    if await needs_rolling_backfill(conn):
        print("    -> industry_attributions has NULL rolling price "
              "columns — running full attributions recompute...",
              flush=True)
        await run_attributions(conn, force=True)
        # Recompute refreshed ALL columns (incl. rolling prices) —
        # rebuild hypes_and_drains so rankings reflect the data.
        await run_hypes_and_drains(conn, force=True)
    else:
        n_hd = await conn.fetchval(
            "SELECT COUNT(*) FROM analysis.industry_hypes_and_drains"
        )
        if not n_hd:
            print("    -> hypes_and_drains table empty — "
                  "populating...", flush=True)
            await run_hypes_and_drains(conn, force=True)
        else:
            print("    -> DB is up to date; nothing to do.", flush=True)
    print_wall_time(t0)


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Industry sentiments downstream analysis (correlations "
                    "[opt-in], perf_attribution, attributions, "
                    "etf_contribution, hypes_and_drains) reading from "
                    "stats.industry_basic_stats."
    )
    add_force_arg(ap)
    ap.add_argument(
        "--with-corr", action="store_true",
        help="ALSO run the correlations step (analysis."
             "industry_correlations). Disabled by default — run "
             "'python -m analyze.industry_sentiments.corr' separately "
             "instead.",
    )
    ap.add_argument(
        "--etf-only", action="store_true",
        help="Run ONLY the etf_contribution step (force=True: truncate "
             "analysis.industry_etf_contribution and recompute all rows). "
             "Use after backfilling sec_alloc_perf_attribution ETF "
             "columns in-place (analyze.sec_alloc_perf_attribution --etf) "
             "— the incremental path would otherwise skip it because no "
             "attribution dates are missing.",
    )
    args = ap.parse_args()

    t0 = time.time()

    conn = await get_db_connection_async()
    if args.etf_only:
        print_build_header(
            "ANALYZE INDUSTRY SENTIMENTS — ETF CONTRIBUTION ONLY",
            source_table=BASELINE_TABLE,
            mode="etf-only (truncate + full recompute of "
                 "industry_etf_contribution)",
        )
        try:
            await run_etf_contribution(conn, force=True)
        finally:
            try:
                await asyncio.wait_for(conn.close(), timeout=10)
            except (asyncio.TimeoutError, Exception):
                pass
        print_wall_time(t0)
        return
    print_build_header(
        "ANALYZE INDUSTRY SENTIMENTS "
        "(downstream steps; baseline source: stats.industry_basic_stats)",
        source_table=BASELINE_TABLE,
        mode="FORCE (full downstream recompute)" if args.force
             else "incremental (missing dates only)",
    )

    try:
        ran_perf = False  # step 2 already executed in step 0?
        # ---- Step 0: determine target dates -------------------------------
        if args.force:
            tables = "attributions + hypes_and_drains"
            if args.with_corr:
                tables = "correlations + " + tables
            print(f"\n[0/5] Force mode: truncating downstream tables "
                  f"({tables})...", flush=True)
            if args.with_corr:
                await truncate_table_async(conn, CORRELATIONS_TABLE)
            await truncate_table_async(conn, ATTRIBUTIONS_TABLE)
            await truncate_table_async(conn, HYPES_DRAINS_TABLE)
            target_dates: Optional[Set[datetime.date]] = None
            print("    -> truncated; will recompute all downstream rows "
                  "(baseline table untouched — rebuild it via "
                  "'python -m builds.industry --force')", flush=True)
        elif args.with_corr:
            print("\n[0/5] Detecting missing corr windows "
                  "(source: industry_basic_stats vs correlations)...",
                  flush=True)
            # Window-end detection: the correlations table is keyed by
            # window START dates (which lag the source calendar by
            # design), so raw-date comparison would never converge.
            # find_missing_corr_window_ends compares POTENTIAL window
            # END dates on the calendar grid against covered ends
            # (start_date + W - 1 where the corr is non-NULL).
            target_dates = await find_missing_corr_window_ends(conn)
            print(f"    -> {len(target_dates)} corr windows missing from "
                  f"{CORRELATIONS_TABLE}", flush=True)
            if not target_dates:
                # Even when correlations is up to date,
                # sec_alloc_perf_attribution (an independent producer
                # sourcing from index_identity + sec_composition +
                # index_exts) may have missing dates. Run it FIRST so
                # the backfill + downstream aggregations read a current
                # sec_alloc_perf_attribution.
                await run_perf_attribution(conn, force=False)
                await _up_to_date_checks(conn, t0)
                return
        else:
            print("\n[0/5] Correlations SKIPPED (disabled by default — "
                  "run 'python -m analyze.industry_sentiments.corr' "
                  "separately, or pass --with-corr).", flush=True)
            # Step 2 runs FIRST here: it manages its own missing-date
            # detection, and the downstream target dates below are dates
            # present in sec_alloc_perf_attribution but not yet
            # aggregated into industry_attributions.
            await run_perf_attribution(conn, force=False)
            ran_perf = True
            target_dates = await find_missing_attribution_dates(conn)
            print(f"    -> {len(target_dates)} dates missing from "
                  f"{ATTRIBUTIONS_TABLE}", flush=True)
            if not target_dates:
                await _up_to_date_checks(conn, t0)
                return

        # ---- Step 1: INTERNAL correlations step (--with-corr only) ------
        # Pairwise windowed MA-curve correlation of industries'
        # mean_close series -> analysis.industry_correlations. Reuses
        # this same connection. See correlations.py for the full
        # pipeline. Passes target_dates so correlations are computed
        # only for missing dates (force flag cascades from the parent).
        if args.with_corr:
            await run_correlations(conn, target_dates=target_dates,
                                   force=args.force)
        else:
            print("\n[1/5] correlations step skipped (disabled).",
                  flush=True)

        # ---- Step 2: INTERNAL sec_alloc_perf_attribution producer --------
        # Populate analysis.sec_alloc_perf_attribution (composition overlap
        # + ETF-market liquidity + rolling close correlations, Index x Index).
        # Reuses this same connection. It manages its OWN target_dates (its
        # missing dates can differ from correlations' missing dates). Force
        # flag cascades from the parent (force mode truncates + fully
        # recomputes).
        if not ran_perf:
            await run_perf_attribution(conn, force=args.force)

        # ---- Step 3: INTERNAL attributions step -------------------------
        # Aggregate analysis.sec_alloc_perf_attribution shared_weight to the
        # industry level -> analysis.industry_attributions. Reuses this same
        # connection. See attributions.py for the full pipeline. Exits
        # gracefully if sec_alloc_perf_attribution is empty.
        await run_attributions(conn, target_dates=target_dates,
                               force=args.force)

        # ---- Step 4: INTERNAL etf_contribution step --------------------
        # Aggregate analysis.sec_alloc_perf_attribution.code_etf_trading_amount
        # to the industry level -> analysis.industry_etf_contribution. Reuses
        # this same connection. See etf_contribution.py for the full pipeline.
        # Exits gracefully if sec_alloc_perf_attribution has no index rows
        # with non-NULL code_etf_trading_amount.
        await run_etf_contribution(conn, target_dates=target_dates,
                                   force=args.force)

        # ---- Step 5: INTERNAL hypes_and_drains step --------------------
        # Pre-compute top-5 (HYPE) + bottom-5 (DRAIN) industries ranked by
        # attribution contribution to composite broad-market benchmarks
        # (MAIN=SS+SZ, INNOV=GEM+STAR) -> analysis.industry_hypes_and_drains.
        # Reuses this same connection. See hypes_and_drains.py. Depends on
        # step 3 (attributions, incl. the 120d column) being populated first.
        # Always runs full recompute (truncate-then-recompute) — the table
        # is small (~245K rows max) and rankings shift when any date changes.
        await run_hypes_and_drains(conn, force=True)

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
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
