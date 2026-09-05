"""Entry point for analyze.analysis_composites.

Run via ``python -m analyze.analysis_composites``.

Composite analyses in the ``analysis_composites`` schema (see
database/sql/analysis/analysis_composites/):

  - industry_corr_benchmark_offsets: OPPOSITE industry correlations by
    benchmark offset. Each industry's MA trend (mean_close from
    stats.industry_basic_stats) is offset by a broad-market benchmark —
    the benchmark MA is rebased to the industry's MA level at each window
    start (k = MA_X[s] / MA_B[s]) and SUBTRACTED (common market factor
    removed); prices are recomputed from the offset trends (rebased to
    100 at the window start) and pairwise Pearson correlations are
    audited over 20/60/255 trading-day windows next to the RAW
    (overall) correlation, plus the derived opposite score
    (1 - offset_sub_corr) / 2 in [0, 1].

Modes (mirroring analyze.industry_sentiments.corr):

  incremental (default):
    Per benchmark, detects POTENTIAL window END dates on the
    stats.industry_basic_stats calendar grid not yet covered by a computed
    window end in analysis_composites.industry_corr_benchmark_offsets
    (find_missing_offset_window_ends) and (re)upserts only those windows.
    No truncate.

  --force:
    Truncates the table, then recomputes and inserts ALL rows (full
    history, every configured benchmark). Cannot be combined with
    --industry / --code.

  --industry ID[,ID...] and/or --code CODE[,CODE...] (filtered mode):
    Recomputes ALL windows for the pairs among the given industries and
    UPSERTS them (no truncate). ``--industry`` takes industry_ids directly
    (e.g. BANKS,AI); ``--code`` takes member index codes (e.g. 000004,
    000005) which are resolved to industry_ids via stats.sec_classification
    (type='index') and unioned with --industry. Driven by the UI refresh
    button, so a small selection recomputes in seconds.

  --benchmark CODE[,CODE...] (default 000300):
    Offset benchmark(s) to materialize. benchmark_code is part of the PK,
    so several benchmarks can coexist. Incremental mode runs the missing-
    window detection PER benchmark.
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
# directly via ``python -m analyze.analysis_composites`` or as a script.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

from analyze.analysis_composites.config import (  # noqa: E402
    BASELINE_TABLE,
    DEFAULT_BENCHMARKS,
    TABLE_OFFSETS,
)
from analyze.analysis_composites.opposite_correlations import (  # noqa: E402
    find_missing_offset_window_ends,
    run_opposite_correlations,
)


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
        description="Analysis Composites — opposite industry correlations "
                    "by benchmark offset (analysis_composites schema): "
                    "industry MA trends offset by a rebased broad-market "
                    "benchmark (subtracted — market factor removed), "
                    "prices recomputed, pairwise correlations audited over "
                    "20/60/255d windows with the raw overall correlation "
                    "and the opposite score (1 - sub)/2."
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
    ap.add_argument(
        "--benchmark", default=",".join(DEFAULT_BENCHMARKS),
        metavar="CODE[,CODE...]",
        help="Offset benchmark index code(s) to materialize (default "
             f"{','.join(DEFAULT_BENCHMARKS)}). Part of the table PK, so "
             "several benchmarks can coexist.",
    )
    args = ap.parse_args()

    industry_args = _parse_csv(args.industry)
    code_args = _parse_csv(args.code)
    benchmarks = _parse_csv(args.benchmark) or list(DEFAULT_BENCHMARKS)
    if args.force and (industry_args or code_args):
        ap.error("--force cannot be combined with --industry/--code "
                 "(filtered runs never truncate the table)")

    t0 = time.time()
    print_build_header(
        "ANALYZE COMPOSITES — opposite industry correlations by benchmark "
        "offset",
        source_table=f"{BASELINE_TABLE} + stats.index_basic_stats",
        tables=TABLE_OFFSETS,
        benchmarks=", ".join(benchmarks),
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
            await run_opposite_correlations(
                conn, industry_ids=industry_ids, benchmarks=benchmarks,
            )
        elif args.force:
            await run_opposite_correlations(
                conn, force=True, benchmarks=benchmarks,
            )
        else:
            for bench in benchmarks:
                print(f"\n[0/1] Detecting missing offset-corr windows for "
                      f"benchmark {bench}...", flush=True)
                target_dates = await find_missing_offset_window_ends(
                    conn, bench,
                )
                print(f"    -> {len(target_dates)} windows missing from "
                      f"{TABLE_OFFSETS} (benchmark={bench})", flush=True)
                if not target_dates:
                    print(f"    -> benchmark {bench} is up to date; "
                          f"nothing to do.", flush=True)
                    continue
                await run_opposite_correlations(
                    conn, target_dates=target_dates,
                    benchmarks=(bench,),
                )
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
