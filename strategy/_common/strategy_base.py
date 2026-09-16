"""StrategyPipeline — ABC for strategy.* entry points.

Extends :class:`_common.data_pipeline.DataPipeline` with the DB lifecycle
shared by the strategy runners (singleton_trading, _optm_engine):

  - connection via ``get_db_or_exit()`` (transient-error retry, exit(1)
    on permanent failure) opened by the ``amain()`` template before
    ``run()`` and timed-closed after — same WAL-checkpoint-stall guard
    as the build/analysis entries;
  - optional connection pool (``pool_size`` derived from the parsed
    args, e.g. the mixed-mode sub-algo pool) with ``terminate()``
    fallback;
  - wall time printed after the close (the strategy convention).

An empty ``title`` suppresses the header bar — both current strategy
runners log without one (pipeline-specific ``===`` banners instead).

Typical subclass (bootstrap-first layout — strategy modules import
pandas at module level, so the runtime setup runs first)::

    from _common.data_pipeline import bootstrap_runtime
    bootstrap_runtime()          # pre-check → cudf.pandas hook → UTF-8

    from strategy._common.strategy_base import StrategyPipeline

    class MyStrategy(StrategyPipeline):
        component = "my_strategy"

        def add_arguments(self, parser): ...
        def apply_args(self): ...
        async def run(self) -> None:
            ...  # uses self.conn / self.args; close handled by amain()

    if __name__ == "__main__":
        MyStrategy().execute()
"""
from __future__ import annotations

import argparse
import time

from _common.data_pipeline import DataPipeline

__all__ = ["StrategyPipeline"]


class StrategyPipeline(DataPipeline):
    """Runtime lifecycle + DB connection lifecycle for strategy scripts."""

    # When set, amain() opens a pool sized to this in addition to conn.
    pool_size: int | None = None

    def __init__(self, args: argparse.Namespace | None = None) -> None:
        super().__init__(args)
        self.conn = None
        self.pool = None

    async def amain(self) -> None:
        """(header when titled) → connect (conn + optional pool) → run()
        → timed close → wall time.

        The finally-close runs on exceptions too — a strategy runner that
        dies mid-run must not leak its backend connection.
        """
        from strategy._common.db import print_wall_time

        self.t0 = time.time()
        if self.title:
            from _common.build_commons import print_build_header

            print_build_header(self.title, **self.header_fields())

        self.conn = await self.connect_db()
        if self.pool_size:
            from _common.db_commons import get_db_pool_async

            self.pool = await get_db_pool_async(
                min_size=1, max_size=self.pool_size
            )
        try:
            await self.run()
        finally:
            if self.pool is not None:
                await self.close_pool(self.pool)
            await self.close_conn(self.conn)
        print_wall_time(self.t0)

    async def connect_db(self):
        """Connect or sys.exit(1) — transient-error retry built in."""
        from strategy._common.db import get_db_or_exit

        return await get_db_or_exit()
