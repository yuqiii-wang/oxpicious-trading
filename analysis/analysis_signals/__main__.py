"""Entry point for analysis.analysis_signals.

Run via ``python -m analysis.analysis_signals``.

The redesigned SIGNAL RULES tier (2026-10-14): six detection
families, each a set of RuleEntry engines (engines/signal_families) —
they measure SELF-ADAPTIVE per-(code, sub signal type, trend, regime,
action) cells on each code's own history and write

  analysis_signals.signal_rules     the rule cells (the engine entry +
                                    the prepared rule_params; the family
                                    knowledge lives in
                                    static.analysis_signal_docs)
  analysis_signals.history_signals  the history populated by REPLAYING
                                    those rules (episode first days)

Modes:
  incremental (default): a family whose every requested code already
    reaches its data horizon with a live rules scope is skipped.
  --force: purges the sec_type's rows in both tables and re-emits.

Universe: --sec-type index (default) with the 10 study indices; the
options families run their OWN universe — the sec_type's option
underlyings from stats.options_terms
(fetch/inputs.resolve_options_universe; options_wall drops the SSE ETF
underlyings, whose chain OI is an accumulated-volume estimator).
Bootstrap-first layout: the runtime setup runs before this module's
imports.
"""
from __future__ import annotations

import argparse
import asyncio

from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from _common.log_setup import setup_logging

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.signal_families import (
    OPTIONS_FAMILIES,
    SIGNAL_FAMILY_ENTRIES,
    family_label,
)
from analysis.analysis_signals.fetch import resolve_options_universe
from analysis.analysis_signals.run import (
    HISTORY_TABLE,
    RULES_TABLE,
    run_family,
)

logger = setup_logging("analysis_signals")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m analysis.analysis_signals",
        description="Populate the signal rules tier (signal_rules + "
                    "history_signals) for the detection families.",
    )
    p.add_argument("--force", action="store_true",
                   help="purge the sec_type's rows in both tables and "
                        "re-emit everything")
    p.add_argument("--sec-type", default="index",
                   choices=("index", "etf", "stock"),
                   help="security type scope (default: index)")
    p.add_argument("--families", default=",".join(SIGNAL_FAMILY_ENTRIES),
                   help="comma-separated family labels (default: all)")
    p.add_argument("--codes", default=",".join(cfg.STUDY_INDEX_CODES),
                   help="comma-separated codes (default: the 10 study "
                        "indices; ignored for the options families — "
                        "their universe resolves per sec_type from "
                        "stats.options_terms)")
    p.add_argument("--analyze", action="store_true",
                   help="run ANALYZE on the two tables after the writes")
    p.add_argument("--no-boost", action="store_true",
                   help="force the ohlcv boost off (the A/B switch; "
                        "default = each family's OHLCV_BOOST class flag)")
    return p.parse_args()


async def amain() -> None:
    args = _parse_args()
    labels = [family_label(s.strip()) for s in args.families.split(",")
              if s.strip()]
    unknown = [l for l in labels if l not in SIGNAL_FAMILY_ENTRIES]
    if unknown:
        raise SystemExit(f"unknown families: {unknown} "
                         f"(known: {sorted(SIGNAL_FAMILY_ENTRIES)})")
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]

    from _common.db_commons import (
        analyze_tables_async,
        get_db_connection_async,
    )
    conn = await get_db_connection_async()
    try:
        totals = {"rules": 0, "history": 0}
        for label in labels:
            if label in OPTIONS_FAMILIES:
                fam_codes = await resolve_options_universe(
                    conn, label, args.sec_type,
                )
            else:
                fam_codes = codes
            if not fam_codes:
                logger.warning(f"[{label}] empty code scope — skip")
                continue
            n_rules, n_hist = await run_family(
                conn, label, args.sec_type, fam_codes, force=args.force,
                boost=False if args.no_boost else None,
            )
            totals["rules"] += n_rules
            totals["history"] += n_hist
        if args.analyze and (totals["rules"] or totals["history"]):
            await analyze_tables_async(conn, [RULES_TABLE, HISTORY_TABLE])
        logger.info(f"done: {totals['rules']} rule cells, "
                    f"{totals['history']} history events")
    finally:
        await conn.close()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
