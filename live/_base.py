"""live._base — shared pipeline base for the live.* entry points.

``LivePipeline`` is the ``DataAnalysis`` flavor for the three live-schema
pipelines (``live_signals``, ``options_intraday_skewness``,
``sec_alloc_live_attribution``). It absorbs the scaffolding those entry
points previously duplicated:

  - bootstrap: GPU-less (``pre_check(require_gpu=False)`` — the processes
    are asyncpg-only; the API-invoked spawns must not abort on the heavy
    cudf pipelines' 36 GiB floor) with the lowered ``ANALYZE_MIN_SYS_GB``
    floor, plus the standard cudf.pandas hook + UTF-8 stdout.
  - exit codes: the pipelines communicate API-facing outcomes (0 ok /
    2 usage / 404 nothing computable) through ``self.exit_code`` — the
    ``__main__`` block ends with ``sys.exit(job.exit_code)``.
  - shared live-schema steps (formerly near-identical module functions in
    every entry point):
      * ``upsert_live_identity``  — the pipeline registration row
        (``live.live_identity``; ``summary_name`` NULL-able).
      * ``maybe_prune_for_new_day`` / ``upsert_prune_bookkeeping_row`` —
        the once-per-new-trading-day retention prune guard (plain PK probe
        on the pipeline's ``live_identity`` bookkeeping row) + the row
        upsert itself; each pipeline implements ``prune_old_dates`` (the
        cutoff discovery + purge targets differ per pipeline).

Headers / wall time stay printed inside ``run()`` at their original
positions (``title`` stays empty — the base header suppresses itself, and
the per-mode headers differ per pipeline).
"""
from __future__ import annotations

import datetime
import os
import time

from _common.data_analysis import DataAnalysis
from _common.data_pipeline import bootstrap_runtime
from _common.sql_writes import render_sql_write

__all__ = ["LivePipeline"]

# The live processes peak well under 1 GiB RSS; the default 36 GiB floor
# guards the heavy cudf pipelines and would abort the API-invoked on-demand
# spawns whenever the WSL VM reports < 36 GiB. setdefault: an explicit
# ANALYZE_MIN_SYS_GB in the environment still wins.
_LIVE_MIN_SYS_GB = "8"


class LivePipeline(DataAnalysis):
    """Runtime lifecycle + shared steps for live.* entry points."""

    # Set by run() for API-facing exit codes (0 ok / 2 usage / 404 miss);
    # the __main__ block ends with sys.exit(job.exit_code).
    exit_code: int = 0

    # Retention-prune bookkeeping row (pipelines with a prune set these).
    prune_identity_name: str = ""
    prune_identity_description: str = ""

    def __init__(self, args=None) -> None:
        super().__init__(args)
        self.exit_code = 0

    def execute(self, argv: list[str] | None = None) -> None:
        os.environ.setdefault("ANALYZE_MIN_SYS_GB", _LIVE_MIN_SYS_GB)
        # The GPU-less bootstrap must run before super().execute()'s parse
        # and heavy imports; the inner bootstrap_runtime() call is a no-op
        # (flag-guarded) so nothing double-runs.
        bootstrap_runtime(require_gpu=False)
        super().execute(argv)

    async def amain(self) -> None:
        """Header-free amain variant for the live entries.

        Unlike DataAnalysis.amain (which prints the ``title`` header and
        wall time unconditionally), the live pipelines print their OWN
        per-mode headers and wall time inside run() — at their original
        positions (after usage validation; sec_alloc's header differs per
        --mode; the options entry's skip path prints none). The base
        template keeps only the DB connection lifecycle.
        """
        from _common.db_commons import get_db_connection_async

        self.t0 = time.time()
        self.conn = await get_db_connection_async()
        try:
            await self.run()
        finally:
            await self.close_conn(self.conn)
            if self.pool is not None:
                await self.close_pool(self.pool)

    # ------------------------------------------------------------------
    # Shared live-schema steps
    # ------------------------------------------------------------------
    async def upsert_live_identity(
        self,
        *,
        name: str,
        detail_name: str,
        summary_name: str | None,
        description: str,
    ) -> None:
        """Upsert the pipeline registration into live.live_identity."""
        # live/_base/live_identity/upsert — the sql_writes action file
        write = render_sql_write(
            "live/_base/live_identity/upsert",
            name, detail_name, summary_name, description,
        )
        await self.conn.execute(write.sql, *write.params)

    async def upsert_prune_bookkeeping_row(self) -> None:
        """Touch the pipeline's prune-bookkeeping live_identity row — its
        last_run_datetime marks the trading date the rolling-window prune
        last ran for (the once-per-new-day guard's probe target)."""
        # live/_base/live_identity/upsert_bookkeeping — the sql_writes
        # action file (last_run_datetime only — the once-per-new-day guard's
        # probe target)
        write = render_sql_write(
            "live/_base/live_identity/upsert_bookkeeping",
            self.prune_identity_name, self.prune_identity_description,
        )
        await self.conn.execute(write.sql, *write.params)

    async def prune_old_dates(self) -> int:
        """The pipeline's retention prune (cutoff discovery + purge targets
        differ per pipeline; see the concrete entries). Returns the number
        of rows deleted. Must end with upsert_prune_bookkeeping_row()."""
        raise NotImplementedError

    async def maybe_prune_for_new_day(self, latest_date: datetime.date) -> None:
        """Run the retention prune only when a NEW trading day is seen.

        The guard is a plain PK probe on the prune bookkeeping live_identity
        row: when its last_run_datetime::date already covers the latest
        intraday date, the prune is skipped — the unindexed DELETE date scan
        must not run on every 5-min tick.
        """
        last_prune_date = await self.conn.fetchval(
            """
            SELECT last_run_datetime::date FROM live.live_identity
            WHERE name = $1
            """,
            self.prune_identity_name,
        )
        if last_prune_date is not None and last_prune_date >= latest_date:
            return
        await self.prune_old_dates()
