"""Entry point for ``python -m builds.market_hypes``.

Thin CLI wrapper around ``run_build`` (runner.py). Populates
stats.mov_ave_market_hypes: one row per (sec_type, code,
min_checkin_period, hype EPISODE) — ETF + Index + Stock in one table,
``sec_type`` discriminates.

Rebuild semantics (margin_changes precedent): episodes SHIFT with new
data and non-hyped dates leave no footprint, so there is no per-date
incremental mode — every run recomputes each requested sec_type's
episodes wholesale from the FULL per-code history (the centered ±10y
percentile threshold windows need up to 2550 rows on EACH side of
every audited date anyway). --force additionally truncates the table
upfront (full-universe force only).

Usage:
  python -m builds.market_hypes                   rebuild all sec_types
  python -m builds.market_hypes --sec-type index  rebuild one sec_type
  python -m builds.market_hypes --code 159673.SZ  rebuild one security
  python -m builds.market_hypes --force           truncate + full recompute

Prerequisite: stats.{etf,index,stock}_{identity,basic_stats,tech_stats,
liquidity_margin} (+ stats.etf_adjustment) populated by the builds.

The :class:`MarketHypesBuild` entry class (a :class:`DataBuild`
subclass) owns the runtime lifecycle + the DB connection/pool lifecycle
(timed close — after heavy bulk writes the PostgreSQL server can be
saturated with WAL checkpoint I/O, making conn.close() stall). The
runner / compute modules are imported lazily (they import pandas — the
cudf.pandas hook must be installed first).
"""
from __future__ import annotations

import sys

from _common.data_build import DataBuild


class MarketHypesBuild(DataBuild):
    """``python -m builds.market_hypes`` — lifecycle + delegation."""

    title = "BUILD MARKET HYPES (ETF + INDEX + STOCK)"
    component = "market_hypes"
    max_concurrent: int = 20

    def add_arguments(self, parser) -> None:
        # Safe heavy import: add_arguments runs after bootstrap_runtime()
        # inside execute() (post pre_check/activate).
        from builds.market_hypes.config import SEC_TYPES

        self.sec_types = tuple(SEC_TYPES)
        self.add_force_arg(parser)
        parser.add_argument(
            "--sec-type", choices=SEC_TYPES, default=None,
            help="Rebuild only this sec_type (for testing). Default: all.",
        )
        self.add_max_concurrent_arg(parser)
        parser.add_argument(
            "--code", default=None,
            help="Rebuild the episodes of this single security only "
                 "(bypasses the active-universe pre-filter). Mutually "
                 "exclusive with --force.",
        )

    def apply_args(self) -> None:
        if self.args.code and self.args.force:
            print("ERROR: --code and --force are mutually exclusive.")
            sys.exit(2)
        self.max_concurrent = max(1, self.args.max_concurrent)

    def header_fields(self) -> dict:
        from builds.market_hypes import TABLE

        sec_types = self.resolve_sec_types()
        return {
            "table": TABLE,
            "sec_types": ", ".join(sec_types),
            "mode": (
                f"SINGLE-CODE {self.args.code} (full recompute for this security)"
                if self.args.code else
                "FORCE (truncate + full recompute)" if self.args.force
                else "wholesale per-sec_type recompute"
            ),
        }

    async def run(self) -> None:
        from builds.market_hypes import run_build

        conn = await self.open_conn()
        pool = await self.open_pool(max_size=self.max_concurrent)
        try:
            await run_build(
                conn,
                force=self.args.force,
                sec_types=self.resolve_sec_types(),
                code_filter=self.args.code,
                max_concurrent=self.max_concurrent,
                pool=pool,
            )
        finally:
            await self.close_conn(conn)
            await self.close_pool(pool)


if __name__ == "__main__":
    MarketHypesBuild().execute()
