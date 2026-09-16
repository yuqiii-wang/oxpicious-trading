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

The :class:`CorrAnalysis` entry class (a :class:`DataAnalysis` subclass)
owns the runtime lifecycle + DB connection lifecycle; the
``--code → industry_ids`` resolution is the shared
``DataAnalysis.resolve_codes_to_industries`` (was duplicated here and in
analysis_composites). Bootstrap-first layout: the runtime setup runs
before this module's imports.
"""
from __future__ import annotations

from typing import Optional, Set

from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from _common.log_setup import setup_logging

from _common.data_analysis import DataAnalysis

from analyze.industry_sentiments.correlations import (
    run_correlations,
    find_missing_corr_window_ends,
    TABLE as CORRELATIONS_TABLE,
)

logger = setup_logging("corr")

BASELINE_TABLE = "stats.industry_basic_stats"


def _parse_csv(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


class CorrAnalysis(DataAnalysis):
    """``python -m analyze.industry_sentiments.corr`` — correlations only."""

    title = ("ANALYZE INDUSTRY CORRELATIONS (standalone; source: "
             "stats.industry_basic_stats)")
    component = "corr"

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)
        parser.add_argument(
            "--industry", default="", metavar="ID[,ID...]",
            help="Filtered mode: recompute + upsert ALL windows for the pairs "
                 "among these industry_ids (e.g. BANKS,AI). No truncate.",
        )
        parser.add_argument(
            "--code", default="", metavar="CODE[,CODE...]",
            help="Filtered mode: member index codes (e.g. 000004,000005) "
                 "resolved to industry_ids via stats.sec_classification and "
                 "unioned with --industry.",
        )

    def apply_args(self) -> None:
        self.industry_args = _parse_csv(self.args.industry)
        self.code_args = _parse_csv(self.args.code)
        if self.args.force and (self.industry_args or self.code_args):
            self.parser.error("--force cannot be combined with --industry/--code "
                              "(filtered runs never truncate the table)")

    def header_fields(self) -> dict:
        return {
            "source_table": BASELINE_TABLE,
            "mode": "FORCE (full recompute)" if self.args.force
                    else "FILTERED (chosen industries, recompute + upsert)"
                    if (self.industry_args or self.code_args)
                    else "incremental (missing windows only)",
        }

    async def run(self) -> None:
        conn = self.conn

        if self.industry_args or self.code_args:
            # ---- Filtered mode: recompute + upsert chosen industries ----
            industry_ids: Optional[Set[str]] = set(self.industry_args)
            if self.code_args:
                resolved = await self.resolve_codes_to_industries(
                    self.code_args,
                )
                unmapped = sorted(
                    set(self.code_args)
                    - {
                        r["code"] for r in await conn.fetch(
                            "SELECT code FROM stats.sec_classification "
                            "WHERE type = 'index' AND code = ANY($1)",
                            self.code_args,
                        )
                    }
                )
                if unmapped:
                    logger.warning(f"    -> WARNING: codes with no index "
                          f"classification (ignored): "
                          f"{', '.join(unmapped)}")
                industry_ids |= resolved
            logger.info(f"\n[0/1] Filtered mode: {len(industry_ids)} industries "
                  f"({', '.join(sorted(industry_ids))})")
            if len(industry_ids) < 2:
                logger.info("    -> fewer than 2 industries — no pairs to "
                      "compute; nothing to do.")
                return
            await run_correlations(conn, industry_ids=industry_ids)
        elif self.args.force:
            await run_correlations(conn, force=True)
        else:
            logger.info("\n[0/1] Detecting missing corr windows "
                  "(source: industry_basic_stats vs correlations)...")
            target_dates = await find_missing_corr_window_ends(conn)
            logger.info(f"    -> {len(target_dates)} corr windows missing from "
                  f"{CORRELATIONS_TABLE}")
            if not target_dates:
                logger.info("    -> correlations are up to date; nothing to do.")
                return
            await run_correlations(conn, target_dates=target_dates)


if __name__ == "__main__":
    CorrAnalysis().execute()
