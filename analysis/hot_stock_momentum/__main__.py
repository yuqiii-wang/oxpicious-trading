"""Entry point for analysis.hot_stock_momentum.

Run via ``python -m analysis.hot_stock_momentum``.

Hot Stock Momentum — per-(sec_type, code, date) hot-stock momentum curve
for indices and composite industries (analysis.hot_stock_momentum; see
database/sql/analysis/30_hot_stock_momentum.sql). Serves the Commons
"Hot Stock Momentum" page (/analysis/commons/hot-stock-momentum) via
/api/analysis/hot-stock-momentum/*.

IMPLEMENTATION PLACEHOLDER (2026-10-31): the service is registered
(heartbeat via DataPipeline.execute, service_config JSON, orchestration
step in scripts/orchestration/analysis_daily.json) and the target table
is live, but run() is intentionally empty — it reports the scoped
sec_types and exits without writing. Fill in the compute (source fetch →
hot-stock momentum curve → upsert; incremental vs --force per the house
convention) when the metric definition lands.

The :class:`HotStockMomentumAnalysis` entry class (a
:class:`DataAnalysis` subclass) owns the runtime lifecycle + DB
connection lifecycle (opened by amain() before run(), timed-closed
after). Bootstrap-first layout: the runtime setup runs before this
module's imports.
"""
from __future__ import annotations

from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from _common.log_setup import setup_logging

from _common.data_analysis import DataAnalysis

logger = setup_logging("hot_stock_momentum")


class HotStockMomentumAnalysis(DataAnalysis):
    """``python -m analysis.hot_stock_momentum`` — hot stock momentum."""

    title = ("ANALYZE HOT STOCK MOMENTUM — per-index/industry "
             "hot-stock momentum curve")
    component = "hot_stock_momentum"
    # The page toggle exposes index + industry only; the pipeline is
    # scoped the same way.
    sec_types = ("index", "industry")

    def add_arguments(self, parser) -> None:
        self.add_sec_type_arg(parser)
        self.add_force_arg(parser)

    def apply_args(self) -> None:
        # --force is accepted for the house entry-point convention; it is
        # a no-op until the compute pipeline is implemented.
        pass

    def header_fields(self) -> dict:
        return {
            "tables": "analysis.hot_stock_momentum",
            "sec_types": ", ".join(self.resolve_sec_types()),
            "mode": "FORCE (full recompute)" if self.args.force
                    else "incremental (unimplemented — no-op)",
        }

    async def run(self) -> None:
        # IMPLEMENTATION PLACEHOLDER — nothing to compute yet. The run
        # still opens/closes the DB connection and registers the service
        # heartbeat (via execute()), so the monitor shows the pipeline.
        logger.info(
            "    -> hot stock momentum compute is not implemented yet; "
            "nothing to do (sec_types: %s)",
            ", ".join(self.resolve_sec_types()),
        )


if __name__ == "__main__":
    HotStockMomentumAnalysis().execute()
