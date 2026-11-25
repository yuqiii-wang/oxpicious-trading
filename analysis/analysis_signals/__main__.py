"""Entry point for analysis.analysis_signals.

Run via ``python -m analysis.analysis_signals``.

The redesigned SIGNAL RULES tier (2026-10-14): six detection
families, each a set of RuleEntry engines (engines/signal_families) —
they measure SELF-ADAPTIVE per-(code, sub signal type, trend, regime,
action) cells on each code's own history and write

  analysis_signals.signal_rules     the rule cells (the engine entry +
                                    the CONSTANT rule_params; the family
                                    knowledge lives in
                                    static.signal_rules_docs)
  analysis_signals.history_signals  the history populated by REPLAYING
                                    those rules (episode first days)
  analysis_signals.history_signal_performance
                                    the DATED daily snapshot of every
                                    MEASURED item per rule cell (the
                                    evidence measure block, the pooled
                                    tier, the gate verdict, the boost
                                    conf — the 2026-10-19 split; never
                                    purged)

Modes:
  incremental (default): a family whose every requested code already
    reaches its data horizon with a live rules scope is skipped.
  --force: purges the sec_type's rows in both tables and re-emits
    (history_signal_performance is NEVER purged — past snapshots are
    the point of the table).

Universe: --codes 'all' (default) resolves the sec_type's FULL code
universe from its base stats table (close IS NOT NULL) — every index
since 2026-11-07 (user-directed; was the 10 study indices, still
available via --codes <list>); the options families run their OWN
universe — the sec_type's option underlyings from stats.options_terms
(fetch/inputs.resolve_options_universe; options_wall drops the SSE ETF
underlyings, whose chain OI is an accumulated-volume estimator) —
crowdedness runs the codes with crowdedness rows at the sec_type
(fetch/inputs.resolve_crowdedness_universe; index only),
volatility runs the codes with realized-vol rows at the sec_type
(fetch/inputs.resolve_volatility_universe; index only while the vol
panel's run scope is the study indices), and
hedge_pairs runs the ALL-INDUSTRY-PAIRS universe
(fetch/inputs.resolve_hedge_pair_universe; --codes is ignored for
all three).
Bootstrap-first layout: the runtime setup runs before this module's
imports.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from _common.log_setup import setup_logging

from analysis.analysis_signals.engines.signal_families import (
    OPTIONS_FAMILIES,
    SIGNAL_FAMILY_ENTRIES,
    family_label,
)
from analysis.analysis_signals.fetch import (
    resolve_options_universe,
)
from analysis.analysis_signals.run import (
    HISTORY_TABLE,
    NO_ACTION_TABLE,
    PERF_TABLE,
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
    p.add_argument("--codes", default="all",
                    help="comma-separated codes, or 'all' (default): the "
                         "sec_type's full universe resolved from its base "
                         "stats table (close IS NOT NULL) — the daily run "
                         "covers every index since 2026-11-07 (was the 10 "
                         "study indices); ignored for the options families "
                         "(stats.options_terms), crowdedness (the codes "
                         "with crowdedness rows), volatility (the codes "
                         "with realized-vol rows) and hedge_pairs (its "
                         "all-industry-pairs universe resolves from the "
                         "composites + offsets)")
    p.add_argument("--analyze", action="store_true",
                   help="run ANALYZE on the two tables after the writes")
    p.add_argument("--no-boost", action="store_true",
                   help="force the ohlcv boost off (the A/B switch; "
                        "default = each family's OHLCV_BOOST class flag)")
    p.add_argument("--no-no-action", action="store_true",
                   help="disable the NO-ACTION overlay tier (the A/B "
                        "isolation switch; default ON — with it off "
                        "the positive tables must stay bit-identical)")
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
        totals = {"rules": 0, "history": 0}
        for label in labels:
            if label in OPTIONS_FAMILIES:
                fam_codes = await resolve_options_universe(
                    conn, label, args.sec_type,
                )
            elif label == "crowdedness":
                # the crowdedness universe is DATA-DRIVEN (the codes
                # with crowdedness rows at the sec_type — index; etf/
                # stock have no crowdedness data, the family skips) —
                # --codes is ignored
                from analysis.analysis_signals.fetch import (
                    resolve_crowdedness_universe,
                )
                fam_codes = await resolve_crowdedness_universe(
                    conn, args.sec_type,
                )
            elif label == "volatility":
                # the volatility universe is DATA-DRIVEN (the codes
                # with realized-vol rows at the sec_type — index while
                # the vol panel's run scope is the study indices;
                # etf/stock have no vol rows yet, the family skips) —
                # --codes is ignored
                from analysis.analysis_signals.fetch import (
                    resolve_volatility_universe,
                )
                fam_codes = await resolve_volatility_universe(
                    conn, args.sec_type,
                )
            elif label == "hedge_pairs":
                # the pair universe is DATA-DRIVEN (the offsets table's
                # disjoint offset-opposite industry pairs) — the codes
                # are the 'A~B' pair ids; --codes is ignored
                from analysis.analysis_signals.fetch.inputs import (
                    resolve_hedge_pair_universe,
                )
                fam_codes = [p["code"]
                             for p in await resolve_hedge_pair_universe(
                                 conn)]
            else:
                fam_codes = codes
            if not fam_codes:
                logger.warning(f"[{label}] empty code scope — skip")
                continue
            n_rules, n_hist = await run_family(
                conn, label, args.sec_type, fam_codes, force=args.force,
                boost=False if args.no_boost else None,
                no_action=not args.no_no_action,
            )
            totals["rules"] += n_rules
            totals["history"] += n_hist
        # the SIGNAL AMPLIFICATION tier (2026-11-18) — runs AFTER the
        # families: its evidence measures the amplifier days against
        # the families' own fresh history_signals events
        from analysis.analysis_signals.signal_amplification.run import (
            HISTORY_TABLE as AMP_HISTORY_TABLE,
            RULES_TABLE as AMP_RULES_TABLE,
            run_amplification,
        )
        amp_rules, amp_history = await run_amplification(
            conn, args.sec_type, codes, force=args.force,
        )
        totals["amplification_rules"] = amp_rules
        totals["amplification_history"] = amp_history
        if args.analyze and (totals["rules"] or totals["history"]
                             or amp_rules or amp_history):
            await analyze_tables_async(
                conn, [RULES_TABLE, HISTORY_TABLE, PERF_TABLE,
                       NO_ACTION_TABLE, AMP_RULES_TABLE,
                       AMP_HISTORY_TABLE])
        logger.info(f"done: {totals['rules']} rule cells, "
                    f"{totals['history']} history events")
    finally:
        await conn.close()


def main() -> None:
    # The fleet's audit life — DataPipeline.execute()'s boundary,
    # mirrored for this hand-rolled main (the 2026-10-14 redesign
    # dropped the base-class wrapper and the orchestrated runs went
    # INVISIBLE: no monitor.service_heartbeat row, no service_audit
    # exit, so the arranger's last_success froze at 10-14 and step 9
    # re-arranged due every pass). Register the heartbeat + run-audit
    # row, report the Phase 4 progress states around the async body,
    # and hold any claimed PAUSE at this boundary. Registration is
    # fail-soft — monitoring never kills a run.
    from _common.service_heartbeat import autoregister

    try:
        autoregister()
    except Exception as e:  # noqa: BLE001 — monitoring never kills a run
        print(
            f"[service_heartbeat] registration failed for "
            f"analysis.analysis_signals: {e}",
            file=sys.stderr, flush=True,
        )
    from _common.post_check import post_check
    from _common.service_heartbeat import pause_checkpoint, report_progress

    try:
        report_progress(state="running", force=True)
        pause_checkpoint()
        asyncio.run(amain())
    finally:
        report_progress(state="done", force=True)
        post_check()


if __name__ == "__main__":
    main()
