"""builds/options/__main__.py — Build both SZSE and CFFEX options data.

Orchestrates the two sub-builders sequentially:
  1. builds.options.szse  — SZSE ETF options (native ETF codes, 1599xx)
  2. builds.options.cffex — CFFEX index options (IO/HO/MO/CO → index codes)

Both write into the same 7 options_* tables. SZSE and CFFEX are
separated by underlying_code space: SZSE keeps native ETF codes
(e.g. 159919), CFFEX uses index codes (e.g. 000300), distinguished
further by underlying_target_type ('ETF' vs 'INDEX').

With --force: truncates all 7 tables ONCE upfront, then runs both
builders in normal (non-force) mode so they repopulate from scratch.
With --force --code <underlying>: only that underlying's rows are
deleted instead of truncating.

With --date YYYY-MM-DD: both builders are scoped to that single date
and the DB missing-date skip is bypassed — the date is always
(re)processed and rows already in the DB are refreshed through the
upsert write paths (no truncation, no deletes). Mutually exclusive
with --force.

Usage:
  python -m builds.options
  python -m builds.options --start-date 2026-07-01 --end-date 2026-07-31
  python -m builds.options --force
  python -m builds.options --code 159915              (single-underlying test filter)
  python -m builds.options --date 2026-08-28          (force single-date rebuild, no DB skip)

The :class:`OptionsBuild` entry class (a :class:`DataBuild` subclass)
composes the :class:`SzseOptionsBuild` / :class:`CffexOptionsBuild`
child pipelines directly (preset args + ``await child.amain()``) — the
old sys.argv mutation + child-argparse round-trip is gone. Child
modules are imported lazily (they run their own bootstrap, then import
pandas).
"""
from __future__ import annotations

import argparse
import sys

from _common.data_build import DataBuild
from _common.log_setup import setup_logging

logger = setup_logging("options")


async def _truncate_all_tables() -> None:
    """Truncate all 7 options_* tables (called once before force rebuild)."""
    from _common.build_commons import get_db_or_exit, truncate_table_async

    tables = (
        "stats.options_aggregate",
        "stats.options_volume_oi",
        "stats.options_greeks",
        "stats.options_settlement",
        "stats.options_strike",
        "stats.options_terms",
        "stats.options_identity",
    )
    conn = await get_db_or_exit()
    try:
        for tbl in tables:
            await truncate_table_async(conn, tbl)
    finally:
        await conn.close()


async def _purge_for_force(underlying: str | None) -> None:
    """--force: truncate all 7 options_* tables, or delete only the
    --code underlying's rows when a code filter is set."""
    if underlying:
        from builds.options.tables import delete_underlying_rows_async
        from _common.build_commons import get_db_or_exit

        conn = await get_db_or_exit()
        try:
            n = await delete_underlying_rows_async(conn, underlying)
            logger.info(f"    Deleted {n:,} (date, contract_code) rows of underlying {underlying}")
        finally:
            await conn.close()
    else:
        await _truncate_all_tables()


class OptionsBuild(DataBuild):
    """``python -m builds.options`` — SZSE + CFFEX options orchestrator."""

    title = "BUILD OPTIONS (SZSE + CFFEX)  ·  missing-data-only → DATABASE"
    component = "options"

    def add_arguments(self, parser) -> None:
        self.add_date_range_args(parser)
        self.add_code_arg(parser)

    def apply_args(self) -> None:
        # --date / --force are mutually exclusive; parse the forced single date.
        # When set, the date also supersedes any explicit --start/--end range so
        # downstream discovery/loading is scoped to this single day.
        self.forced_date = self.apply_date_force_args()
        if self.forced_date:
            self.args.start_date = self.forced_date.isoformat()
            self.args.end_date = self.forced_date.isoformat()

        # Normalized code filter (e.g. 159915 → 159915.SZ). Sub-builds compare
        # against the BARE underlying_code column, so forward the bare form.
        self.code_filter = self.resolve_code_filter(strip_suffix=True)

    def header_fields(self) -> dict:
        return {
            "Date range":   f"{self.args.start_date or '(all)'} → {self.args.end_date or '(all)'}",
            "Force rebuild": str(self.args.force),
            "Code filter":  self.code_filter or "(none — all underlyings)",
            "Today":        self._today(),
        }

    @staticmethod
    def _today() -> str:
        from _common.build_commons import TODAY_STR
        return TODAY_STR

    def _child_args(self) -> argparse.Namespace:
        """Preset argparse.Namespace forwarded to both child builders.

        Children run WITHOUT --force: the purge already happened here, so
        a child truncate would erase the other child's freshly written
        rows (they share the same 7 tables).
        """
        args = self.args
        return argparse.Namespace(
            start_date=args.start_date,
            end_date=args.end_date,
            force=False,
            date=args.date,
            code=args.code,
        )

    async def _run_child(self, child_cls, error_label: str) -> None:
        child = child_cls(args=self._child_args())
        child.apply_args()
        try:
            await child.amain()
        except SystemExit as e:
            if e.code not in (None, 0):
                logger.error(f"[ERROR] {error_label} exited with code {e.code}")

    async def run(self) -> None:
        if self.code_filter:
            logger.info(f"    [CODE FILTER] Restricting build to single underlying: {self.code_filter}")
        if self.forced_date:
            logger.info(f"[DATE MODE] Forced single-date build: {self.forced_date}")

        # --force: purge tables ONCE before running both builders
        if self.args.force:
            if self.code_filter:
                logger.info(f"\n[FORCE] Deleting rows of underlying {self.code_filter} from the 7 options_* tables …")
            else:
                logger.info("\n[FORCE] Truncating all 7 options_* tables …")
            await _purge_for_force(self.code_filter)
            logger.info("    Done.")

        # ---- 1. SZSE ETF options ----
        logger.info("\n" + "=" * 60)
        logger.info("PHASE 1: SZSE ETF OPTIONS")
        logger.info("=" * 60)
        from builds.options.szse.__main__ import SzseOptionsBuild
        await self._run_child(SzseOptionsBuild, "SZSE builder")

        # ---- 2. CFFEX index options ----
        logger.info("\n" + "=" * 60)
        logger.info("PHASE 2: CFFEX INDEX OPTIONS")
        logger.info("=" * 60)
        from builds.options.cffex.__main__ import CffexOptionsBuild
        await self._run_child(CffexOptionsBuild, "CFFEX builder")

        logger.info("\n" + "=" * 60)
        logger.info("OPTIONS BUILD COMPLETE (SZSE + CFFEX)")
        logger.info("=" * 60)


if __name__ == "__main__":
    OptionsBuild().execute()
