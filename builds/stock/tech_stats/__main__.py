"""Entry point for builds.stock.tech_stats (standalone mode).

Run via ``python -m builds.stock.tech_stats`` (add ``--date YYYY-MM-DD``
to force a single-date refresh; add ``--force`` for a full recompute).

For integrated mode (called from builds.stock), construct TechStatsRunner:
    from builds.stock.tech_stats import TechStatsRunner

The :class:`StockTechStatsBuild` entry class (a :class:`StockBuild`
subclass — inheritance mirrors the directory layout) owns the runtime
lifecycle + common argparse (--force/--date)
+ header/wall time; the runner module is imported lazily inside run()
(this package's __init__ re-exports the pandas-using runner).
"""
from __future__ import annotations

from datetime import date

from builds.stock.__main__ import StockBuild


class StockTechStatsBuild(StockBuild):
    """``python -m builds.stock.tech_stats`` — lifecycle + delegation."""

    title = ("BUILD STOCK TECH_STATS  ·  "
             "MA5/20/60/120/255 + EMA6/12/20/26/60/120/255 from close")
    component = "tech_stats"
    # StockBuild delegates argv to its pipeline.cli (allow_unknown_args
    # True); this child owns its argparse — restore the strict default.
    allow_unknown_args = False

    def add_arguments(self, parser) -> None:
        from builds.stock.tech_stats import SOURCE_TABLE, TABLE

        self.TABLE = TABLE
        self.SOURCE_TABLE = SOURCE_TABLE
        parser.add_argument(
            "--chunk-size", type=int, default=500,
            help="Number of codes per chunk (default 500).",
        )
        self.add_force_arg(parser)
        self.add_date_arg(parser)

    def apply_args(self) -> None:
        self.forced_date = self.apply_date_force_args()

    def header_fields(self) -> dict:
        if self.forced_date is not None:
            mode = f"DATE MODE (single-date refresh: {self.forced_date})"
        elif self.args.force:
            mode = "FORCE (full recompute)"
        else:
            mode = "incremental (missing pairs only)"
        return {"table": self.TABLE, "source": self.SOURCE_TABLE, "mode": mode}

    async def run(self) -> None:
        from _common.build_commons import forced_date_scope
        from builds.stock.tech_stats.runner import TechStatsRunner

        conn = await self.connect_db()
        try:
            target_dates: set[date] | None = None
            if self.forced_date is not None:
                # Uniform --date semantics: exit(1) when the forced date has
                # no rows in the source table; otherwise the runner's
                # target_dates mechanism bypasses the max-date skip and
                # recomputes ONLY this date (upsert refresh, no truncation).
                rows = await conn.fetch(
                    f"SELECT DISTINCT date FROM {self.SOURCE_TABLE}"
                )
                target_dates = forced_date_scope(
                    {r["date"] for r in rows}, self.forced_date,
                    source_label=str(self.SOURCE_TABLE),
                )
            total = await TechStatsRunner(conn).run(
                force=self.args.force,
                chunk_size=self.args.chunk_size, verbose=True,
                target_dates=target_dates,
            )
            self.logger.info(
                f"\n[DONE] Total rows upserted into {self.TABLE}: {total:,}"
            )
        finally:
            await self.close_conn(conn)


if __name__ == "__main__":
    StockTechStatsBuild().execute()
