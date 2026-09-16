"""DataBuild — ABC for builds.* entry points (source CSV → stats.*).

Extends :class:`_common.data_pipeline.DataPipeline` with the argument
and DB plumbing shared by every build script:

  - argparse clusters (all build scripts use one of three):
      date-range builds   --start-date/--end-date/--force/--date (+--code)
      force-only builds   --force (+script-specific flags)
      single-code filter  --code with normalize_code() suffix inference
  - post-parse validation (--date ⊕ --force) and derivation
    (forced_date, suffixed/bare code filter)
  - DB acquisition: ``connect_db()`` (get_db_or_exit: retry transient
    connection errors, sys.exit(1) on permanent failure) and
    ``open_conn()`` / ``open_pool()`` for scripts that manage the
    lifecycle themselves.

Typical lazy-import subclass (thin delegate)::

    from _common.data_build import DataBuild

    class StockBuild(DataBuild):
        title = "BUILD STOCK ..."
        allow_unknown_args = True   # pipeline.main parses sys.argv itself

        async def run(self) -> None:
            from builds.stock.pipeline.main import main
            await main()

    if __name__ == "__main__":
        StockBuild().execute()

Typical argparse subclass::

    class EtfBuild(DataBuild):
        def add_arguments(self, ap):
            self.add_date_range_args(ap)
            self.add_code_arg(ap)

        def apply_args(self):
            self.forced_date = self.apply_date_force_args()
            self.code_filter = self.resolve_code_filter()

        async def run(self) -> None:
            from builds.etf.pipeline.main import run
            await run(self.args)
"""
from __future__ import annotations

import argparse
import datetime

from _common.data_pipeline import DataPipeline

__all__ = ["DataBuild"]


class DataBuild(DataPipeline):
    """Runtime lifecycle + shared argparse/DB plumbing for build scripts."""

    # Derived by apply_args() hooks where relevant:
    forced_date: datetime.date | None = None
    code_filter: str | None = None

    # ------------------------------------------------------------------
    # argparse helpers (lazy imports keep this module pandas-free)
    # ------------------------------------------------------------------
    def add_date_range_args(self, parser: argparse.ArgumentParser) -> None:
        """--start-date/--end-date/--force/--date (the full date-driven set)."""
        from _common.build_commons import add_common_build_args

        add_common_build_args(parser)

    def add_force_arg(self, parser: argparse.ArgumentParser) -> None:
        """Only --force (no date range)."""
        from _common.build_commons import add_force_arg

        add_force_arg(parser)

    def add_date_arg(self, parser: argparse.ArgumentParser) -> None:
        """Only --date (single-date forced rebuild)."""
        from _common.build_commons import add_date_arg

        add_date_arg(parser)

    def add_code_arg(self, parser: argparse.ArgumentParser) -> None:
        """--code single-security filter (e.g. 000001.SZ)."""
        from builds._commons.code_filter import add_code_arg

        add_code_arg(parser)

    # ------------------------------------------------------------------
    # Post-parse helpers
    # ------------------------------------------------------------------
    def apply_date_force_args(self) -> datetime.date | None:
        """Validate + derive the --date flag (SystemExit 2 on misuse).

        --date and --force are mutually exclusive; a malformed --date is
        an argparse-style usage error. Returns the parsed forced date
        (None in normal incremental/force mode).
        """
        from _common.build_commons import enforce_date_force_exclusion, parse_date_arg

        enforce_date_force_exclusion(self.args)
        forced = parse_date_arg(getattr(self.args, "date", None))
        self.forced_date = forced
        return forced

    def resolve_code_filter(self, strip_suffix: bool = False) -> str | None:
        """Normalize --code → suffixed code (000001.SZ); optionally strip
        the exchange suffix for the bare-code tables (index/futures/
        options: 000300, IF2606, 10008657)."""
        from builds._commons.code_filter import normalize_code

        code = normalize_code(getattr(self.args, "code", None))
        if code and strip_suffix:
            code = code.split(".")[0]
        self.code_filter = code
        return code

    # ------------------------------------------------------------------
    # DB acquisition (lifecycle stays with the subclass's run())
    # ------------------------------------------------------------------
    async def connect_db(self):
        """Connect or sys.exit(1) — transient-error retry built in."""
        from _common.build_commons import get_db_or_exit

        return await get_db_or_exit()

    async def open_conn(self):
        """Connect WITHOUT the exit/retry wrapper (raises on failure)."""
        from _common.db_commons import get_db_connection_async

        return await get_db_connection_async()

    async def open_pool(self, max_size: int, min_size: int = 1):
        """Connection pool for parallel chunked writes."""
        from _common.db_commons import get_db_pool_async

        return await get_db_pool_async(min_size=min_size, max_size=max_size)
