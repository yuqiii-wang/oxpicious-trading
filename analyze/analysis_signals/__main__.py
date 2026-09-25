"""Entry point for analyze.analysis_signals.

Run via ``python -m analyze.analysis_signals``.

Signal STRATEGIES + history signals over analysis_forecasts (see
database/sql/analysis/analysis_signals/00_schema.sql and
01_signals.sql):

  - A forecast bucket (code × stat_date snapshot M × config, trailing
    5-year window (M - 5y, M]) whose mixed forecast_results DELAY
    LADDER (rung d 0..5 is conditioned on the signal having persisted
    d days) has a gate-passing rung — the plain rule: the sign-aligned
    blended mean forward change (dir_ave) > 0.75% — IS a signal
    strategy: ONE
    analysis_signals.signal_strategies row over the bucket's forecast
    period (start_date .. end_date = the snapshot key — the
    ROLLING LATEST snapshot is keyed at the sec_type's latest
    available data date). The
    registered rung is the OPTIMAL ENTRY DELAY — the balance rule's
    argmax occurrence_count × sign-aligned dir_ave over the
    gate-passing rungs (the opportunity cost of waiting — fewer
    occurrences as the rung deepens — against the persistence-
    conditioned return), ties → the smallest delay — stored as
    signal_delay_days, carrying the chosen rung's confidence
    (the sign-aligned dir_ave — the expected favorable blended move)
    and breach bar in the underlying value's own space
    (the live tier's threshold set) and the bucket's OWN side as a
    STORED column.

  - The bucket's CHOSEN-RUNG trigger days INSIDE the snapshot key's
    calendar year M
    are its history signals: ONE analysis_signals.history_signals row
    per day (the day's value, the bar it crossed, the excess — a
    structural clone of live.live_signals; one snapshot owns each
    date, so no cross-snapshot PK conflicts). A delayed strategy's
    history days are its streaks' day-d anchors (signal_delay_days
    rides both tables).

  - Emission slices (current build): mov_rsi pct = 1 (top → sell /
    bottom → buy), mov_std MA/σ windows >= 20d at k >= 2.0σ
    (upper → sell / lower → buy), and the two pair-cross families —
    mov_pairs / mov_pairs_ema, each covering BOTH its fast legs
    (ma5/ema6 and the close price) — at slow-leg windows >= 60d,
    cross-down (bottom → buy) side only. All slices cover every regime
    splits. Other forecast families get strategies as their engines
    land.

  - NO CASE/WHEN anywhere in the SQL: side is stored, and every other
    conditional lives in this package's vectorized cudf.pandas engines
    (engines/ — one SignalEngine subclass per family, dispatched
    through the registry).

Pipeline per sec_type (index / etf / stock), every step owned by the
family's engine (engines/signal_families/<family>/ — one
SignalEngine subclass per family, dispatched through the registry;
see engines/_base):
  1. Resolve the target snapshots per family: the stat_dates PRESENT
     in analysis_forecasts.forecast_identities (bucket-filtered) that
     are missing from signal_strategies, plus the MUTABLE SCOPE
     (config.mutable_dates — the ROLLING LATEST snapshot keyed at the
     sec_type's latest available data date, always re-emitted; the
     newest completed year-end while its 20d forward windows
     realize). Retired keys (yesterday's rolling-latest date) are
     SWEPT from both tables before the resolution. ``--force`` purges
     the sec_type's family rows and re-emits every present snapshot.
  2. Per snapshot: fetch the buckets (long, carrying the quality
     periods' forward profiles) + the indicator values at exactly the
     trigger points (wide) → vectorized plain gate → the FINAL
     SignalQuality gate (engines/_quality — breach coherence +
     sign-aligned per-period forward means on every period, plus the
     0.75 risk cap as ONE weight-blended verdict over the quality
     periods 65% 5d / 10% 20d at bar 0.50 (the 5d bar
     carries the decision — long-horizon failures alone can't kill);
     only quality-passing buckets register as strategies) →
     vectorized strategy/history
     frames → ONE transaction: purge the snapshot, COPY both tables.
  3. After every run: refresh is_active (each config's latest
     end_date) and rank signal_order (confidence DESC within each
     (sec_type, end_date) pool) — 02_is_active.sql /
     03_signal_order_rank.sql — and each engine upserts its own
     analysis.analysis_identity row.
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

setup_utf8_stdout()

from _common.log_setup import setup_logging  # noqa: E402

from analyze.analysis_forecasts.config import SEC_TYPES  # noqa: E402
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
        description="Analysis Signals (signal strategies + history "
                    "signals over analysis_forecasts): one "
                    "signal_strategies row per forecast bucket whose "
                    "mixed forecast_results delay ladder has a rung "
                    "passing the plain gate (sign-aligned blended mean "
                    "forward change > 0.75%), at "
                    "the OPTIMAL ENTRY DELAY (occurrence_count × "
                    "aligned dir_ave argmax over the gate-passing "
                    "rungs — opportunity cost vs return), covering the "
                    "bucket's forecast period (the trailing 5-year "
                    "snapshot key), plus the chosen rung's trigger "
                    "days inside its own snapshot year as "
                    "history_signals rows. Emission slices: mov_rsi "
                    "pct = 1; mov_std >= 20d windows at k >= 2.0σ; "
                    "the two pair-cross families (mov_pairs / "
                    "mov_pairs_ema, both fast legs) at >= 60d slow "
                    "legs, cross-down side; every regime split.",
    )
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
        "--months", type=int, default=None, metavar="N",
        help="Cap the target stat_dates to the newest N present in "
             "analysis_forecasts (default: all present snapshots).",
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

    t0 = time.time()
    print_build_header(
        "ANALYZE SIGNALS (signal strategies + history signals over "
        "the analysis_forecasts buckets)",
        tables="analysis_signals.signal_strategies, "
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
        totals = {"strategies": 0, "history": 0}
        for st in sec_types:
            stats = await process_sec_type(
                conn, st, force=force, metrics=metrics, months=args.months,
            )
            for engine in engines_for(metrics):
                run = stats.get(engine.stage_key, RunStats(0, 0, 0))
                totals["strategies"] += run.strategies
                totals["history"] += run.history

        if totals["strategies"] == 0 and totals["history"] == 0 \
                and not force:
            logger.info("\n  DB is up to date; nothing to do.")
            print_wall_time(t0)
            return

        logger.info(
            f"\n  TOTAL: {totals['strategies']:,} signal_strategies + "
            f"{totals['history']:,} history_signals rows written "
            f"(is_active + signal_order refreshed per sec_type)",
        )
        print_wall_time(t0)
    finally:
        await conn.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
