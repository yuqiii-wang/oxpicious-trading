"""Entry point for analysis.analysis_signals.signal_amplification.

Run via ``python -m analysis.analysis_signals.signal_amplification``.

The SIGNAL AMPLIFICATION tier (2026-11-18): the close-only signal
families' day-anatomy strengthener — the amplifiers AMPLIFY the
existing families' signals, never create signals of their own. Builds
analysis.ohlcv_ext (the extended per-day tech stats from
stats.*_basic_stats) + attaches the margin anatomy (融资余额 quantile
bars) and the news anatomy (the daily news-text MARKET state, the
2026-11-22 migrated news_wash rules), detects the amplifier days (the
ohlcv sub types flush_washout / gap_reclaim / breakout_confirm /
spike_blowoff / exhaustion_gap / dry_rebound + the margin sub types
margin_washout / margin_crowding + the news sub types — the config
AMP_BINDINGS matrix binds them to the signal families they
strengthen/discount), measures the per-binding evidence against the
bound families' own history_signals, and writes

  analysis_signals.signal_amplification_rules    the per-(code,
                                                 signal_type) binding
                                                 config (wiped +
                                                 reinserted per run)
  analysis_signals.signal_amplification_history  the ORIGINAL RECORD
                                                 KEEPER — one row per
                                                 amplifier day

Modes: incremental (default — the ohlcv_ext per-code horizon check +
the tier's own source-horizon skip) and --force (purge the sec_type's
rows in all three tables and rebuild).

Universe: --codes 'all' (default) resolves the sec_type's FULL code
universe from its base stats table (close IS NOT NULL) — the
signals tier's daily scope convention; the 10 study indices stay
available via --codes <list>.

This module is also run INSIDE the analysis_signals daily pipeline
(run after the families — the evidence reads their history_signals
events).
Bootstrap-first layout: the runtime setup runs before this module's
imports.
"""
from __future__ import annotations

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate

activate()

import argparse  # noqa: E402
import asyncio  # noqa: E402

from _common.data_pipeline import bootstrap_runtime  # noqa: E402

bootstrap_runtime()

from _common.log_setup import setup_logging  # noqa: E402

from analysis.analysis_signals.signal_amplification.run import (  # noqa: E402
    HISTORY_TABLE,
    RULES_TABLE,
    run_amplification,
)

logger = setup_logging("signal_amplification")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m analysis.analysis_signals.signal_amplification",
        description="Populate the signal amplification tier "
                    "(ohlcv_ext + signal_amplification_rules + "
                    "signal_amplification_history).",
    )
    p.add_argument("--force", action="store_true",
                   help="purge the sec_type's rows in all three tables "
                        "and rebuild")
    p.add_argument("--sec-type", default="index",
                   choices=("index", "etf", "stock"),
                   help="security type scope (default: index)")
    p.add_argument("--codes", default="all",
                   help="comma-separated codes, or 'all' (default): the "
                        "sec_type's full universe resolved from its base "
                        "stats table (close IS NOT NULL)")
    p.add_argument("--analyze", action="store_true",
                   help="run ANALYZE on the three tables after the writes")
    return p.parse_args()


async def amain() -> None:
    args = _parse_args()
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    all_codes = codes == ["all"]

    from _common.db_commons import (
        analyze_tables_async,
        get_db_connection_async,
    )
    conn = await get_db_connection_async()
    try:
        if all_codes:
            from analysis.analysis_signals.fetch.inputs import (
                resolve_sec_type_universe,
            )
            codes = await resolve_sec_type_universe(conn, args.sec_type)
            if not codes:
                logger.warning(f"[universe] no {args.sec_type} codes with "
                               f"close data — nothing to do")
                return
        n_rules, n_history = await run_amplification(
            conn, args.sec_type, codes, force=args.force,
        )
        if args.analyze and (n_rules or n_history):
            from analysis.analysis_signals.signal_amplification.ohlcv_ext import (
                OHLCV_EXT_TABLE,
            )
            await analyze_tables_async(
                conn, [OHLCV_EXT_TABLE, RULES_TABLE, HISTORY_TABLE])
        logger.info(f"done: {n_rules} rules rows, {n_history:,} history "
                    f"rows")
    finally:
        await conn.close()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
