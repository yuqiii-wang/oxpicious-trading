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
     Default (no corr): run the cross-stats producer first (it manages its
     OWN missing-date detection), then target dates = dates present in
     stats.cross_stats but missing from industry_attributions
     (attributions.find_missing_attribution_dates).
     --force truncates attributions + hypes_and_drains first (plus
     correlations ONLY with --with-corr; the baseline table is owned by
     builds.industry and is NOT truncated here — use
     ``python -m builds.industry --force`` to rebuild it).
  1. INTERNAL STEP (--with-corr only): windowed MA-curve correlations of
     industries' mean_close series -> analysis.industry_correlations (see
     correlations.py). Reuses the same DB connection.
  2. INTERNAL STEP: populate the pair-grain rows of stats.cross_stats
     (builds.cross_stats.runner.run_cross_stats — composition overlap +
     ETF-market liquidity, Index x Index; corr via the --corr sub-command).
     Reuses the same DB connection. It manages its OWN target_dates
     (missing from stats.cross_stats vs stats.index_identity)
     since its missing dates can differ from correlations' missing dates.
     (Skipped when it already ran in step 0 — default incremental mode.)
  3. INTERNAL STEP: aggregate stats.cross_stats (sec_type='index')
     shared_weight to the industry level -> analysis.industry_attributions
     (see attributions.py). Depends on step 2 being populated first;
     exits gracefully if empty.
  4. INTERNAL STEP: aggregate stats.index_exts.total_etf_trading_amount
     to the industry level -> analysis.industry_etf_contribution (see
     etf_contribution.py). Depends on stats.index_exts (builds.index
     exts phase) being populated first; exits gracefully if it has no
     non-NULL ETF amounts.
  5. INTERNAL STEP: pre-compute top-5 (HYPE) + bottom-5 (DRAIN)
     industries -> analysis.industry_hypes_and_drains (see
     hypes_and_drains.py). Depends on step 3 (attributions) being
     populated first. Always full recompute.

Default (incremental, no-corr) mode:
  Step 2 runs first and populates any missing cross_stats pair dates; then
  only the dates missing from industry_attributions are (re)computed in
  steps 3/4.

--force mode:
  Truncates analysis.industry_attributions + industry_hypes_and_drains
  (plus analysis.industry_correlations with --with-corr), then recomputes
  all downstream rows (the baseline stats.industry_basic_stats is NOT
  truncated — it is rebuilt by ``python -m builds.industry --force``).
"""
from __future__ import annotations

import datetime  # noqa: F401  (type hints below)
from typing import Optional, Set

# Runtime bootstrap — pre-check → silence warnings → cudf.pandas hook →
# UTF-8 stdout. MUST run before the pandas-importing modules below.
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from _common.build_commons import (
    truncate_table_async,
)

from analyze.industry_sentiments.correlations import (
    run_correlations,
    find_missing_corr_window_ends,
    TABLE as CORRELATIONS_TABLE,
)
from analyze.industry_sentiments.attributions import (
    run_attributions,
    needs_rolling_backfill,
    find_missing_attribution_dates,
    TABLE as ATTRIBUTIONS_TABLE,
)
from analyze.industry_sentiments.etf_contribution import (
    run_etf_contribution,
    TABLE as ETF_CONTRIBUTION_TABLE,
)
from analyze.industry_sentiments.hypes_and_drains import (
    run_hypes_and_drains,
    TABLE as HYPES_DRAINS_TABLE,
)
from builds.cross_stats.runner import (
    run_cross_stats,
)

from _common.log_setup import setup_logging
from _common.data_analysis import DataAnalysis

logger = setup_logging("industry_sentiments")

# The industry BASELINE table (owned by builds.industry). This module only
# READS from it — the correlations step consumes its mean_close series.
BASELINE_TABLE = "stats.industry_basic_stats"


async def _up_to_date_checks(analysis: DataAnalysis, conn) -> None:
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
        logger.info("    -> industry_attributions has NULL rolling price "
              "columns — running full attributions recompute...")
        await run_attributions(conn, force=True)
        # Recompute refreshed ALL columns (incl. rolling prices) —
        # rebuild hypes_and_drains so rankings reflect the data.
        await run_hypes_and_drains(conn, force=True)
    else:
        n_hd = await conn.fetchval(
            "SELECT COUNT(*) FROM analysis.industry_hypes_and_drains"
        )
        if not n_hd:
            logger.info("    -> hypes_and_drains table empty — "
                  "populating...")
            await run_hypes_and_drains(conn, force=True)
        else:
            logger.info("    -> DB is up to date; nothing to do.")


class IndustrySentimentsAnalysis(DataAnalysis):
    """``python -m analyze.industry_sentiments`` — downstream steps.

    The old conn-before-header + two-branch structure is normalized by
    the DataAnalysis amain() template: one header (title switches for
    --etf-only via header_fields()), one connection, then run().
    """

    component = "industry_sentiments"

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)
        parser.add_argument(
            "--with-corr", action="store_true",
            help="ALSO run the correlations step (analysis."
                 "industry_correlations). Disabled by default — run "
                 "'python -m analyze.industry_sentiments.corr' separately "
                 "instead.",
        )
        parser.add_argument(
            "--etf-only", action="store_true",
            help="Run ONLY the etf_contribution step (force=True: truncate "
                 "analysis.industry_etf_contribution and recompute all rows). "
                 "Use after rebuilding stats.index_exts (builds.index exts "
                 "phase) when ETF amounts changed — the incremental path "
                 "would otherwise skip it because no attribution dates are "
                 "missing.",
        )

    def apply_args(self) -> None:
        # Title depends on the mode — set BEFORE amain() prints the header.
        if self.args.etf_only:
            self.title = "ANALYZE INDUSTRY SENTIMENTS — ETF CONTRIBUTION ONLY"
        else:
            self.title = ("ANALYZE INDUSTRY SENTIMENTS "
                          "(downstream steps; baseline source: "
                          "stats.industry_basic_stats)")

    def header_fields(self) -> dict:
        return {
            "source_table": BASELINE_TABLE,
            "mode": "etf-only (truncate + full recompute of "
                    "industry_etf_contribution)" if self.args.etf_only
                    else "FORCE (full downstream recompute)" if self.args.force
                    else "incremental (missing dates only)",
        }

    async def run(self) -> None:
        conn = self.conn
        args = self.args

        if args.etf_only:
            await run_etf_contribution(conn, force=True)
            return

        ran_perf = False  # step 2 already executed in step 0?
        # ---- Step 0: determine target dates -------------------------------
        if args.force:
            tables = "attributions + hypes_and_drains"
            if args.with_corr:
                tables = "correlations + " + tables
            logger.info(f"\n[0/5] Force mode: truncating downstream tables "
                  f"({tables})...")
            if args.with_corr:
                await truncate_table_async(conn, CORRELATIONS_TABLE)
            await truncate_table_async(conn, ATTRIBUTIONS_TABLE)
            await truncate_table_async(conn, HYPES_DRAINS_TABLE)
            target_dates: Optional[Set[datetime.date]] = None
            logger.info("    -> truncated; will recompute all downstream rows "
                  "(baseline table untouched — rebuild it via "
                  "'python -m builds.industry --force')")
        elif args.with_corr:
            logger.info("\n[0/5] Detecting missing corr windows "
                  "(source: industry_basic_stats vs correlations)...")
            # Window-end detection: the correlations table is keyed by
            # window START dates (which lag the source calendar by
            # design), so raw-date comparison would never converge.
            # find_missing_corr_window_ends compares POTENTIAL window
            # END dates on the calendar grid against covered ends
            # (start_date + W - 1 where the corr is non-NULL).
            target_dates = await find_missing_corr_window_ends(conn)
            logger.info(f"    -> {len(target_dates)} corr windows missing from "
                  f"{CORRELATIONS_TABLE}")
            if not target_dates:
                # Even when correlations is up to date,
                # stats.cross_stats (an independent producer
                # sourcing from index_identity + sec_composition +
                # index_exts) may have missing dates. Run it FIRST so
                # the backfill + downstream aggregations read a current
                # stats.cross_stats.
                await run_cross_stats(conn, force=False)
                await _up_to_date_checks(self, conn)
                return
        else:
            logger.info("\n[0/5] Correlations SKIPPED (disabled by default — "
                  "run 'python -m analyze.industry_sentiments.corr' "
                  "separately, or pass --with-corr).")
            # Step 2 runs FIRST here: it manages its own missing-date
            # detection, and the downstream target dates below are dates
            # present in stats.cross_stats but not yet
            # aggregated into industry_attributions.
            await run_cross_stats(conn, force=False)
            ran_perf = True
            target_dates = await find_missing_attribution_dates(conn)
            logger.info(f"    -> {len(target_dates)} dates missing from "
                  f"{ATTRIBUTIONS_TABLE}")
            if not target_dates:
                await _up_to_date_checks(self, conn)
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
            logger.info("\n[1/5] correlations step skipped (disabled).")

        # ---- Step 2: INTERNAL cross-stats producer ----------------------
        # Populate the pair-grain rows of stats.cross_stats (composition
        # overlap + ETF-market liquidity, Index x Index). Reuses this same
        # connection. It manages its OWN target_dates (its missing dates can
        # differ from correlations' missing dates). Force flag cascades from
        # the parent (force mode truncates + fully recomputes).
        if not ran_perf:
            await run_cross_stats(conn, force=args.force)

        # ---- Step 3: INTERNAL attributions step -------------------------
        # Aggregate stats.cross_stats (sec_type='index') shared_weight to the
        # industry level -> analysis.industry_attributions. Reuses this same
        # connection. See attributions.py for the full pipeline. Exits
        # gracefully if stats.cross_stats has no index rows.
        await run_attributions(conn, target_dates=target_dates,
                               force=args.force)

        # ---- Step 4: INTERNAL etf_contribution step --------------------
        # Aggregate stats.index_exts.total_etf_trading_amount to the
        # industry level -> analysis.industry_etf_contribution. Reuses
        # this same connection. See etf_contribution.py for the full
        # pipeline. Exits gracefully if index_exts has no non-NULL ETF
        # amounts.
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


if __name__ == "__main__":
    IndustrySentimentsAnalysis().execute()
