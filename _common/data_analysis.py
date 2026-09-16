"""DataAnalysis — ABC for analyze.* entry points (stats.* → analysis.*).

Extends :class:`_common.data_pipeline.DataPipeline` with the lifecycle
and steps shared by every analysis script:

  - DB lifecycle: every analysis opens ONE ``get_db_connection_async()``
    connection in main() and closes it in a ``finally`` (timed close —
    graceful close can stall minutes behind a heavy WAL checkpoint);
    analyses with parallel chunked writes additionally open an asyncpg
    pool sized to ``--max-concurrent``. Both moved here into the
    ``amain()`` template: header → connect → run() → timed close →
    wall time.
  - sec_type scaffolding: ``--sec-type`` choice + ``resolve_sec_types()``
    (one sec_type when given, all configured ones otherwise).
  - shared steps as methods:
      truncate_tables / delete_sec_type_rows   force-mode table clears
      find_missing_dates                       per-sec_type date diff
      upsert_identity                          analysis_identity registry
      resolve_codes_to_industries              --code → industry_ids map
        (was byte-for-byte duplicated in analysis_composites and
        industry_sentiments.corr)

Typical subclass (bootstrap-first layout for big inline pipelines)::

    from _common.data_analysis import DataAnalysis, bootstrap_runtime
    bootstrap_runtime()          # cudf.pandas hook BEFORE pandas imports

    import pandas as pd          # safe: hook installed
    from analyze.x.config import ANALYSIS_NAME, DESCRIPTION, SEC_TYPES

    class XAnalysis(DataAnalysis):
        title = "ANALYZE X"
        sec_types = SEC_TYPES
        component = "x"

        def add_arguments(self, ap):
            self.add_sec_type_arg(ap)
            self.add_force_arg(ap)

        async def run(self) -> None:
            ...  # uses self.conn / self.args; close handled by amain()

    if __name__ == "__main__":
        XAnalysis().execute()
"""
from __future__ import annotations

import argparse
import time

from _common.data_pipeline import DataPipeline

__all__ = ["DataAnalysis"]


class DataAnalysis(DataPipeline):
    """Runtime lifecycle + shared steps for analysis scripts."""

    # All sec_types this analysis covers (subclass: config.SEC_TYPES).
    sec_types: tuple = ()
    # When set, amain() opens a pool sized to this in addition to conn.
    pool_size: int | None = None

    def __init__(self, args: argparse.Namespace | None = None) -> None:
        super().__init__(args)
        self.conn = None
        self.pool = None

    # ------------------------------------------------------------------
    # argparse helpers
    # ------------------------------------------------------------------
    def add_force_arg(self, parser: argparse.ArgumentParser) -> None:
        """--force (full recompute; semantics are per-analysis)."""
        from _common.build_commons import add_force_arg

        add_force_arg(parser)

    def add_sec_type_arg(
        self,
        parser: argparse.ArgumentParser,
        choices=None,
    ) -> None:
        """--sec-type (process a single sec_type; default: all)."""
        parser.add_argument(
            "--sec-type",
            choices=list(choices or self.sec_types) or None,
            default=None,
            help="Process only this sec_type (for testing). Default: all.",
        )

    def add_max_concurrent_arg(
        self,
        parser: argparse.ArgumentParser,
        default: int = 20,
    ) -> None:
        """--max-concurrent — inherited from DataPipeline; kept here for
        import-site backwards compatibility."""
        super().add_max_concurrent_arg(parser, default=default)

    def add_code_arg(
        self,
        parser: argparse.ArgumentParser,
        help_text: str = "Recompute ALL analysis rows for this single "
        "security only (single-code mode). Mutually exclusive with --force.",
    ) -> None:
        """--code (single-security recompute; UI per-security build button)."""
        parser.add_argument("--code", default=None, help=help_text)

    # ------------------------------------------------------------------
    # sec_type resolution (see DataPipeline.resolve_sec_types)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # amain override: shared DB connection lifecycle
    # ------------------------------------------------------------------
    async def amain(self) -> None:
        """header → connect (conn + optional pool) → run() → timed close
        → wall time.

        The finally-close runs on exceptions too — an analysis that dies
        mid-run must not leak its backend connection.
        """
        from _common.build_commons import print_build_header, print_wall_time
        from _common.db_commons import get_db_connection_async

        self.t0 = time.time()
        print_build_header(self.title, **self.header_fields())
        self.conn = await get_db_connection_async()
        if self.pool_size:
            from _common.db_commons import get_db_pool_async

            self.pool = await get_db_pool_async(
                min_size=1, max_size=self.pool_size
            )
        try:
            await self.run()
        finally:
            await self.close_conn(self.conn)
            if self.pool is not None:
                await self.close_pool(self.pool)
        print_wall_time(self.t0)

    # ------------------------------------------------------------------
    # Shared analysis steps
    # ------------------------------------------------------------------
    async def truncate_tables(self, tables) -> None:
        """TRUNCATE each table (full-universe force mode)."""
        from _common.db_commons import truncate_table_async

        for table in tables:
            await truncate_table_async(self.conn, table)

    async def delete_sec_type_rows(self, tables, sec_types) -> None:
        """Scoped force-mode clear: DELETE only the given sec_types' rows.

        Used when the run rebuilds only SOME sec_types — a TRUNCATE would
        wipe the other sec_types' data, which this run does NOT rebuild.
        """
        if isinstance(tables, str):
            tables = (tables,)
        for table in tables:
            status = await self.conn.execute(
                f"DELETE FROM {table} WHERE sec_type = ANY($1::text[])",
                list(sec_types),
            )
            n_del = int(status.rsplit(" ", 1)[-1]) if status else 0
            if self.logger:
                self.logger.info(
                    f"    -> deleted {n_del:,} sec_type rows from {table}"
                )

    async def find_missing_dates(self, analysis_table, source_identity_tables, *, sec_type=None):
        """Dates present in the source identity tables but missing from the
        analysis table (per-sec_type when ``sec_type`` is given)."""
        from _common.pre_check_and_load import find_missing_analysis_dates

        return await find_missing_analysis_dates(
            self.conn, analysis_table, source_identity_tables,
            sec_type=sec_type,
        )

    async def upsert_identity(
        self,
        name: str,
        detail_name: str,
        description: str,
        *,
        summary_name: str | None = None,
    ) -> None:
        """Upsert the analysis_identity registry row for one analysis."""
        from analyze._common import upsert_analysis_identity

        await upsert_analysis_identity(
            self.conn,
            name=name,
            detail_name=detail_name,
            description=description,
            summary_name=summary_name,
        )

    async def resolve_codes_to_industries(self, codes: list[str]) -> set:
        """Map member index codes -> their industry_ids (sec_classification).

        Shared by analysis_composites and industry_sentiments.corr
        filtered mode (--code CODE[,CODE...]).
        """
        rows = await self.conn.fetch(
            """
            SELECT DISTINCT industry_id
            FROM stats.sec_classification
            WHERE type = 'index'
              AND industry_id IS NOT NULL
              AND industry_id <> ''
              AND code = ANY($1)
            """,
            codes,
        )
        return {r["industry_id"] for r in rows}
