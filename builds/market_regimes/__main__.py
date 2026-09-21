"""Entry point for ``python -m builds.market_regimes``.

Thin CLI wrapper around ``run_build`` (runner.py). Populates
stats.market_regimes: one row per (sec_type, code, TRADING date) —
the DAILY 4-state market-regime label (calm / hot / panic / quiet)
every downstream consumer splits by (forecast bucket axes, signal
strategies / history rows, live rows, the UI regime shading) — and
materializes the derived stats.market_regime_spans shading table
(contiguous same-regime runs; the /api/analysis/market-regimes
endpoint's row-read source). REPLACES ``builds.market_hypes`` (the
boolean episode detector).

Rebuild semantics (margin_changes precedent): trailing-window stats
never change past rows, but ETF adj_close back-adjustments DO rewrite
history, so every run recomputes each requested sec_type's rows
wholesale from the FULL per-code history. --force additionally
truncates both tables upfront (full-universe force only).

Usage:
  python -m builds.market_regimes                   rebuild all sec_types
  python -m builds.market_regimes --sec-type index  rebuild one sec_type
  python -m builds.market_regimes --code 159673.SZ  rebuild one security
  python -m builds.market_regimes --force           truncate + full recompute

Prerequisite: stats.{etf,index,stock}_{identity,basic_stats} +
{etf,stock}_liquidity_margin (+ stats.etf_adjustment) populated by the
builds; then re-run analyze.analysis_forecasts / analysis_signals to
re-emit the regime-split rows.

The :class:`MarketRegimesBuild` entry class (a :class:`DataBuild`
subclass) owns the runtime lifecycle + the DB connection/pool lifecycle
(the market_hypes entry precedent). The runner / compute modules are
imported lazily (they import pandas — the cudf.pandas hook must be
installed first).
"""
from __future__ import annotations

import sys

from _common.data_build import DataBuild


class MarketRegimesBuild(DataBuild):
    """``python -m builds.market_regimes`` — lifecycle + delegation."""

    title = "BUILD MARKET REGIMES (ETF + INDEX + STOCK)"
    component = "market_regimes"
    max_concurrent: int = 20

    def add_arguments(self, parser) -> None:
        # Safe heavy import: add_arguments runs after bootstrap_runtime()
        # inside execute() (post pre_check/activate).
        from builds.market_regimes.config import SEC_TYPES

        self.sec_types = tuple(SEC_TYPES)
        self.add_force_arg(parser)
        parser.add_argument(
            "--sec-type", choices=SEC_TYPES, default=None,
            help="Rebuild only this sec_type (for testing). Default: all.",
        )
        self.add_max_concurrent_arg(parser)
        parser.add_argument(
            "--code", default=None,
            help="Rebuild the daily states of this single security only "
                 "(bypasses the active-universe pre-filter). Mutually "
                 "exclusive with --force.",
        )

    def apply_args(self) -> None:
        if self.args.code and self.args.force:
            print("ERROR: --code and --force are mutually exclusive.")
            sys.exit(2)
        self.max_concurrent = max(1, self.args.max_concurrent)

    def header_fields(self) -> dict:
        from builds.market_regimes import SPANS_TABLE, TABLE

        sec_types = self.resolve_sec_types()
        return {
            "table": TABLE,
            "derived": SPANS_TABLE,
            "sec_types": ", ".join(sec_types),
            "mode": (
                f"SINGLE-CODE {self.args.code} (full recompute for this security)"
                if self.args.code else
                "FORCE (truncate + full recompute)" if self.args.force
                else "wholesale per-sec_type recompute"
            ),
        }

    async def run(self) -> None:
        from builds.market_regimes import run_build

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
    MarketRegimesBuild().execute()
