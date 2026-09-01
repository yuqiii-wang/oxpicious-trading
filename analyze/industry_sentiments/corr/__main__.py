"""Entry point for analyze.industry_sentiments.corr — correlations ONLY.

Run via ``python -m analyze.industry_sentiments.corr``.

The correlations step is NOT part of the default
``python -m analyze.industry_sentiments`` pipeline (opt-in there via
``--with-corr``); this standalone entry point runs it in isolation:

  incremental (default):
    Detects POTENTIAL window END dates on the stats.industry_basic_stats
    calendar grid not yet covered by a computed window end in
    analysis.industry_correlations (find_missing_corr_window_ends) and
    (re)upserts only those windows. No truncate.

  --force:
    Truncates analysis.industry_correlations, then recomputes and
    inserts ALL rows (full history). Cannot be combined with --industry
    / --code (filtered runs must never truncate the whole table).

  --industry ID[,ID...] and/or --code CODE[,CODE...] (filtered mode):
    Recomputes ALL windows for the pairs among the given industries and
    UPSERTS them (no truncate). ``--industry`` takes industry_ids
    directly (e.g. BANKS,AI); ``--code`` takes member index codes
    (e.g. 000004,000005) which are resolved to their industry_ids via
    stats.sec_classification (type='index') and unioned with
    --industry. Driven by the UI refresh button on the Pairwise
    Correlation chart, so a small selection recomputes in seconds.
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

# Ensure project root is on sys.path so ``_common`` is importable when run
# directly via ``python -m analyze.industry_sentiments.corr`` or as a script.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    print_build_header,
    print_wall_time,
    add_force_arg,
)

setup_utf8_stdout()

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate  # noqa: E402
activate()

from analyze.industry_sentiments.correlations import (  # noqa: E402
    run_correlations,
    find_missing_corr_window_ends,
    TABLE as CORRELATIONS_TABLE,
)

BASELINE_TABLE = "stats.industry_basic_stats"


def _parse_csv(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


async def _resolve_codes_to_industries(
    conn, codes: list[str],
) -> Set[str]:
    """Map member index codes -> their industry_ids (sec_classification)."""
    rows = await conn.fetch("""
        SELECT DISTINCT industry_id
        FROM stats.sec_classification
        WHERE type = 'index'
          AND industry_id IS NOT NULL
          AND industry_id <> ''
          AND code = ANY($1)
    """, codes)
    return {r["industry_id"] for r in rows}


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Industry correlations ONLY (windowed MA-curve "
                    "pairwise Pearson correlation) reading from "
                    "stats.industry_basic_stats."
    )
    add_force_arg(ap)
    ap.add_argument(
        "--industry", default="", metavar="ID[,ID...]",
        help="Filtered mode: recompute + upsert ALL windows for the pairs "
             "among these industry_ids (e.g. BANKS,AI). No truncate.",
    )
    ap.add_argument(
        "--code", default="", metavar="CODE[,CODE...]",
        help="Filtered mode: member index codes (e.g. 000004,000005) "
             "resolved to industry_ids via stats.sec_classification and "
             "unioned with --industry.",
    )
    args = ap.parse_args()

    industry_args = _parse_csv(args.industry)
    code_args = _parse_csv(args.code)
    if args.force and (industry_args or code_args):
        ap.error("--force cannot be combined with --industry/--code "
                 "(filtered runs never truncate the table)")

    t0 = time.time()
    print_build_header(
        "ANALYZE INDUSTRY CORRELATIONS (standalone; source: "
        "stats.industry_basic_stats)",
        source_table=BASELINE_TABLE,
        mode="FORCE (full recompute)" if args.force
             else "FILTERED (chosen industries, recompute + upsert)"
             if (industry_args or code_args)
             else "incremental (missing windows only)",
    )

    conn = await get_db_connection_async()
    try:
        if industry_args or code_args:
            # ---- Filtered mode: recompute + upsert chosen industries ----
            industry_ids: Optional[Set[str]] = set(industry_args)
            if code_args:
                resolved = await _resolve_codes_to_industries(
                    conn, code_args,
                )
                unmapped = sorted(
                    set(code_args)
                    - {
                        r["code"] for r in await conn.fetch(
                            "SELECT code FROM stats.sec_classification "
                            "WHERE type = 'index' AND code = ANY($1)",
                            code_args,
                        )
                    }
                )
                if unmapped:
                    print(f"    -> WARNING: codes with no index "
                          f"classification (ignored): "
                          f"{', '.join(unmapped)}", flush=True)
                industry_ids |= resolved
            print(f"\n[0/1] Filtered mode: {len(industry_ids)} industries "
                  f"({', '.join(sorted(industry_ids))})", flush=True)
            if len(industry_ids) < 2:
                print("    -> fewer than 2 industries — no pairs to "
                      "compute; nothing to do.", flush=True)
                print_wall_time(t0)
                return
            await run_correlations(conn, industry_ids=industry_ids)
        elif args.force:
            await run_correlations(conn, force=True)
        else:
            print("\n[0/1] Detecting missing corr windows "
                  "(source: industry_basic_stats vs correlations)...",
                  flush=True)
            target_dates = await find_missing_corr_window_ends(conn)
            print(f"    -> {len(target_dates)} corr windows missing from "
                  f"{CORRELATIONS_TABLE}", flush=True)
            if not target_dates:
                print("    -> correlations are up to date; nothing to do.",
                      flush=True)
                print_wall_time(t0)
                return
            await run_correlations(conn, target_dates=target_dates)
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
