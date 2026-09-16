"""Entry point for ``python -m builds.cross_stats``.

Thin CLI wrapper around ``run_cross_stats`` / ``run_corr_update``
(runner.py). The pipeline logic lives in runner.py so it can ALSO be
called as an internal step of a downstream build (which passes its own
connection down). Running this module standalone opens its own
connection, runs the pipeline, and closes it.

Populates stats.cross_stats (+ stats.cross_stats_dates):
  • PAIR grain (sec_type='index')  — pandas/cudf pipeline, chunked COPY.
  • INDUSTRY grain (sec_type='industry') — single INSERT...SELECT.

Usage:
  python -m builds.cross_stats            incremental (missing dates only)
  python -m builds.cross_stats --force    truncate + full recompute
  python -m builds.cross_stats --corr     corr-only upsert on stride-20
                                          grid dates (base cols untouched)
  python -m builds.cross_stats --backfill-offsets   one-off backfill

Prerequisite (preflight gate exits(1) otherwise): stats.sec_composition
index holdings — run ``python -m builds.index`` first.

The :class:`CrossStatsBuild` entry class (a :class:`DataBuild` subclass)
owns the runtime lifecycle + per-branch DB connection lifecycle (timed
close — after heavy bulk writes the PostgreSQL server can be saturated
with WAL checkpoint I/O). Runner modules are imported lazily (they
import pandas — the cudf.pandas hook must be installed first).
"""
from __future__ import annotations

from _common.data_build import DataBuild


class CrossStatsBuild(DataBuild):
    """``python -m builds.cross_stats`` — lifecycle + 3-branch dispatch."""

    component = "cross_stats"

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)
        parser.add_argument(
            "--corr", action="store_true",
            help="Corr-only build: recompute corr_20d/60d/255d on stride-20 "
                 "grid dates and upsert them onto existing rows (the main run "
                 "writes rows with corr OFF by default).",
        )
        parser.add_argument(
            "--backfill-offsets", action="store_true",
            help="One-off: backfill code_price_with_benchmark_offset and its "
                 "amount-weighted variant onto EXISTING pair rows (deployments "
                 "created before the columns existed). New dates are covered "
                 "by the regular pipeline.",
        )

    def header_fields(self) -> dict:
        from builds.cross_stats.config import TOP_N_NON_BROAD, TABLE

        if self.args.backfill_offsets:
            self.title = "BUILD CROSS STATS — OFFSET BACKFILL (existing pair rows)"
            return {
                "table": TABLE,
                "sec_types": "index",
                "mode": "backfill code_price_with_benchmark_offset(_by_weighted_amt)",
            }
        if self.args.corr:
            self.title = "BUILD CROSS STATS — CORR BUILD (stride-20 grid dates)"
            return {
                "table": TABLE,
                "sec_types": "index",
                "top_n_non_broad": f"{TOP_N_NON_BROAD}",
                "mode": "corr-only (upsert corr_20d/60d/255d on grid dates)",
            }
        self.title = "BUILD CROSS STATS (PAIR + INDUSTRY GRAIN)"
        return {
            "table": TABLE,
            "sec_types": "index, industry",
            "top_n_non_broad": f"{TOP_N_NON_BROAD}",
            "mode": "FORCE (full recompute)" if self.args.force
                    else "incremental (missing dates only)",
        }

    async def run(self) -> None:
        from builds.cross_stats.runner import (
            run_corr_update,
            run_cross_stats,
            run_offset_backfill,
        )

        conn = await self.open_conn()
        try:
            if self.args.backfill_offsets:
                await run_offset_backfill(conn)
            elif self.args.corr:
                await run_corr_update(conn)
            else:
                await run_cross_stats(conn, force=self.args.force)
        finally:
            await self.close_conn(conn)


if __name__ == "__main__":
    CrossStatsBuild().execute()
