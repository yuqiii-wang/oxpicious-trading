"""Entry point for analyze.analysis_signals.

Run via ``python -m analyze.analysis_signals``.

Signal STRATEGIES + history signals computed IN-ENGINE off the RSI
percentile data (see database/sql/analysis/analysis_signals/
00_schema.sql and 01_signals.sql). The forecast layer was REMOVED
2026-10-05 (tables dropped, package + SQL files deleted) — the signals
tier computes its own rsi-pct buckets from analysis.mov_ave_rsi +
prices, and the plain gate at the per-side robustness floors is the
whole gate:

  - A bucket (code × stat_date snapshot M × config, trailing
    10-year window (M - 10y, M]) whose DELAY-0 blended mixed-row stats
    pass the plain rule — the sign-aligned blended mean forward change
    (dir_ave) > 0.75% over >= 4 (buy) / 3 (sell) of the window's
    qualifying streaks — IS a signal strategy: ONE
    analysis_signals.signal_thresholds row over the bucket's window
    period (start_date .. end_date = the snapshot key — the ROLLING
    LATEST snapshot is keyed at the sec_type's latest available RSI
    data date). The trigger is each qualifying streak's FIRST day
    (delay 0 — the 0..5 delay ladder + the optimal-entry selection
    were REMOVED 2026-10-05); the row carries the bucket's
    confidence-scale model (confidence_base = the sign-aligned dir_ave
    — the expected favorable blended move ON the bar — plus
    scale_method/scale_base/scale_factor: the breach depth amplifies
    the base) and the breach bar in the RSI's own space (the window's
    top/bottom-pct%
    percentile bar; the live tier's threshold set) and the bucket's
    OWN side as a STORED column.

  - The bucket's trigger days (the qualifying streaks' first days)
    INSIDE the snapshot key's calendar year M are its history signals: ONE
    analysis_signals.history_signals row per day (the day's value, the
    bar it crossed, the excess — a structural clone of
    live.live_signals; one snapshot owns each date, so no
    cross-snapshot PK conflicts).

  - Emission slice: mov_rsi pct in {1, 2, 3} (top → sell / bottom →
    buy), every regime split. NO CASE/WHEN anywhere in the SQL: side
    is stored, and every other conditional lives in this package's
    vectorized cudf.pandas engines.

Pipeline per sec_type (index / etf / stock), every step owned by the
family's engine (engines/signal_families/mov_rsi/ — dispatched through
the registry; see engines/_base):
  1. Resolve the target snapshots over the engine's OWN snapshot grid
     (the last N year-ends + the ROLLING LATEST key, config.grid):
     those missing from signal_thresholds, plus the MUTABLE SCOPE
     (config.mutable_dates — the rolling latest, always re-emitted;
     the newest year-end while its 20d forward windows realize).
     Retired keys (yesterday's rolling-latest date) are SWEPT from
     both tables before the resolution. ``--force`` purges the
     sec_type's family rows and re-emits every present snapshot.
  2. Per run: fetch the input frame ONCE (price + RSI windows, the
     first dates, the market-regime labels; in-engine forward
     changes) → per snapshot: compute the rsi-pct buckets + the
     delay-0 mixed stats (in-buckets) → plain gate at the sample
     floor → vectorized
     strategy/history frames → ONE transaction: purge the snapshot,
     COPY both tables.
  3. After every run: refresh is_active (each config's latest
     end_date — run/pipeline.refresh_is_active) and the engine upserts
     its own analysis.analysis_identity row.
"""
from __future__ import annotations

import argparse
import logging
import time

# cudf.pandas activation — must run before pandas first import. The
# _common helpers imported below (build_commons → db_commons →
# _batched_copy) transitively import pandas at module level, so activating
# any later would silently skip the cudf proxy ("pandas already imported").
from _common.df_utils._activate import activate  # noqa: E402
activate()

from _common.build_commons import (
    add_force_arg,
    get_db_connection_async,
    print_build_header,
    print_wall_time,
    setup_utf8_stdout,
)
from _common.db_commons import analyze_tables_async  # noqa: E402

setup_utf8_stdout()

from _common.log_setup import setup_logging  # noqa: E402

from analyze.analysis_signals.config import SEC_TYPES  # noqa: E402
from analyze.analysis_signals.config import (  # noqa: E402
    STAGE_NAMES,
)
from analyze.analysis_signals.engines import (  # noqa: E402
    RunStats,
    engines_for,
)
from analyze.analysis_signals.run import process_sec_type  # noqa: E402

logger = setup_logging("analysis_signals")


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Analysis Signals (rsi-pct signal strategies + history "
                    "signals, computed in-engine over analysis.mov_ave_rsi): "
                    "one signal_thresholds row per RSI percentile bucket "
                    "whose delay-0 blended mixed-row stats pass the "
                    "plain gate (sign-aligned blended mean forward "
                    "change > 0.75%) at the sample floor (4 buys / "
                    "3 sells), covering the "
                    "bucket's window period (the trailing 10-year "
                    "snapshot key), plus the bucket's trigger days "
                    "(each qualifying streak's first day) inside its "
                    "own snapshot year as "
                    "history_signals rows. Emission slice: mov_rsi "
                    "pct in {1, 2, 3}; every regime split.",    )
    ap.add_argument(
        "--sec-type", choices=SEC_TYPES, default=None,
        help="Process only this sec_type (for testing). Default: all.",
    )
    ap.add_argument(
        "--metrics", type=str, default=None,
        help="Comma-separated signal families to run (choices: "
             f"{', '.join(STAGE_NAMES)}). Default: all.",
    )
    ap.add_argument(
        "--snapshots", type=int, default=None, metavar="N",
        help="Cap the target stat_dates to the newest N on the "
             "snapshot grid (default: all present snapshots).",
    )
    ap.add_argument(
        "--codes", type=str, default=None, metavar="CSV",
        help="Comma-separated codes scoping the universe (the study-"
             "scope rule: analysis runs write only the 10 study "
             "indices unless explicitly widened). Default: the "
             "sec_type's full active universe.",
    )
    ap.add_argument(
        "--analyze", action="store_true",
        help="ANALYZE the written tables after the build (planner-"
             "statistics refresh; debugging / performance testing only "
             "— default OFF: bulk builds skip it and leave the tables "
             "to autovacuum.)",
    )
    add_force_arg(ap)
    args = ap.parse_args()
    force = args.force

    if args.metrics:
        requested = {s.strip() for s in args.metrics.split(",")
                     if s.strip()}
        unknown = requested - set(STAGE_NAMES)
        if unknown:
            ap.error(f"--metrics: unknown family(ies) {sorted(unknown)} "
                     f"(choices: {', '.join(STAGE_NAMES)})")
        metrics = frozenset(requested)
    else:
        metrics = None

    sec_types = (args.sec_type,) if args.sec_type else SEC_TYPES
    codes = (
        frozenset(c.strip() for c in args.codes.split(",") if c.strip())
        if args.codes else None
    )

    t0 = time.time()
    print_build_header(
        "ANALYZE SIGNALS (rsi-pct signal strategies + history signals, "
        "computed in-engine over analysis.mov_ave_rsi)",
        tables="analysis_signals.signal_thresholds, "
               "analysis_signals.history_signals",
        sec_types=", ".join(sec_types),
        metrics=", ".join(sorted(metrics)) if metrics else "all",
        mode="FORCE (purge + re-emit every present snapshot)" if force
        else "incremental (missing snapshots + the mutable scope: "
             "the rolling latest snapshot and the newest year-end "
             "while its 20d forward windows realize)",
    )

    conn = await get_db_connection_async()
    try:
        totals = {"thresholds": 0, "history": 0, "curves": 0}
        for st in sec_types:
            stats = await process_sec_type(
                conn, st, force=force, metrics=metrics,
                snapshots=args.snapshots, codes=codes,
            )
            for engine in engines_for(metrics):
                run = stats.get(engine.stage_key, RunStats(0, 0, 0))
                totals["thresholds"] += run.thresholds
                totals["history"] += run.history
                totals["curves"] += run.curves

        if totals["thresholds"] == 0 and totals["history"] == 0 \
                and totals["curves"] == 0 and not force:
            logger.info("\n  DB is up to date; nothing to do.")
            print_wall_time(t0)
            return

        logger.info(
            f"\n  TOTAL: {totals['thresholds']:,} signal_thresholds + "
            f"{totals['history']:,} history_signals + "
            f"{totals['curves']:,} signal_curves rows written "
            f"(is_active refreshed per sec_type)",
        )
        # Post-bulk-write ANALYZE — same rationale as the forecasts
        # build: stale primary statistics after a rewrite turn every
        # value-blind prepared-statement read (node-pg) into whole-
        # table seq scans until autovacuum gets to the table.
        # OPT-IN (--analyze): debugging / performance testing only —
        # routine builds skip it.
        if args.analyze:
            await analyze_tables_async(conn, (
                "analysis_signals.signal_thresholds",
                "analysis_signals.history_signals",
                "analysis_signals.signal_curves",
            ))
        else:
            logger.info(
                "  skipping post-build ANALYZE (--analyze to refresh "
                "planner statistics — debugging / performance testing "
                "only)")
        print_wall_time(t0)
    finally:
        await conn.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
