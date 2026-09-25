"""builds.index — Unified index build orchestrator.

Runs three sequential phases:

  1. Index composition (CSI + SZSE) → stats.sec_composition
  2. Index baseline (CSIndex daily OHLCV + PE + MAs)
  3. Index exts (stats.index_exts + stats.etf_trading_amt +
     stats.exchange_trading_amt + stats.sec_similars)

Composition must run before baseline because baseline's close-price
estimation relies on index composition shared weights. Exts must run
after baseline: exchange_trading_amt is driven by stats.index_basic_stats
dates (produced by baseline), and sec_similars is driven by
sec_composition snapshot dates (produced by phase 1).

Phase 2 embeds the :class:`BaselineBuild` child pipeline directly
(preset ``args`` + ``await child.amain()``) — no sys.argv mutation.

Usage:
  python -m builds.index
  python -m builds.index --force
  python -m builds.index --date 2026-08-14   (force single-date rebuild)
  python -m builds.index --start-date 2024-01-01 --end-date 2026-07-23
  python -m builds.index --code 000300               (single-index test filter)
"""
from __future__ import annotations

from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

import time

from _common.build_commons import (
    print_build_header, print_wall_time, TODAY_STR,
)
from builds._commons.paths import INDEX_COMP_DIR, SZSE_INDEX_COMP_DIR
from _common.log_setup import setup_logging

from _common.data_build import DataBuild

logger = setup_logging("index")


async def _run_composition(force: bool, code_filter: str | None = None,
                           forced_date=None) -> None:
    """Run index composition build (CSI + SZSE) → stats.sec_composition.

    Date-check fast-path: filenames are checked first to identify missing
    (code, snapshot_date) pairs before reading any CSV content. With
    code_filter, only that index's files/rows are processed. With
    forced_date (--date mode), the missing-pair skip is bypassed and only
    that snapshot date's rows are (re)built via the upsert path — the
    force-mode DELETE of sec_composition never runs.
    """
    from _common.build_commons import get_db_or_exit, copy_or_upsert_split_async
    from builds.index.composition import (
        build_index_composition_rows,
        build_szse_index_composition_rows,
    )

    if forced_date is not None:
        # --date availability gate: the forced snapshot date must exist
        # among the composition CSV filenames (CSI + SZSE union), else
        # exits(1) before any DB work.
        from _common.build_commons import forced_date_scope
        from builds.index.composition import available_snapshot_dates

        forced_date_scope(
            available_snapshot_dates(code_filter),
            forced_date,
            source_label="composition CSV filenames",
        )

    conn = await get_db_or_exit()
    try:
        logger.info("\n    Building CSI index composition rows …")
        index_comp_rows = await build_index_composition_rows(
            conn=conn, force=force, code_filter=code_filter,
            forced_date=forced_date,
        )

        logger.info("\n    Building SZSE index composition rows …")
        szse_index_comp_rows = await build_szse_index_composition_rows(
            conn=conn, force=force, code_filter=code_filter,
            forced_date=forced_date,
        )

        all_rows = index_comp_rows + szse_index_comp_rows
        # CSI and SZSE closeweight sets can overlap — e.g. 399812 is
        # published by BOTH for the same snapshot_date. A duplicate
        # (code, snapshot_date, rank) aborts the all-COPY write with a
        # UNIQUE violation; keep the FIRST occurrence (CSI — canonical
        # suffixed stock codes + normalized weights).
        _seen: set = set()
        _deduped = []
        for _r in all_rows:
            _k = (_r["code"], _r["snapshot_date"], _r["rank"])
            if _k in _seen:
                continue
            _seen.add(_k)
            _deduped.append(_r)
        if len(_deduped) != len(all_rows):
            logger.warning(f"    [DEDUP] dropped {len(all_rows) - len(_deduped):,} "
                  f"duplicate index composition rows overlapping between "
                  f"CSI and SZSE sources (CSI kept)")
        all_rows = _deduped
        logger.info(f"\n    → total: {len(all_rows):,} index composition rows "
              f"({len(index_comp_rows):,} CSI + {len(szse_index_comp_rows):,} SZSE)")

        if force:
            if code_filter:
                logger.info(f"    [DB] Force mode for code {code_filter}: deleting existing index composition rows")
                await conn.execute(
                    "DELETE FROM stats.sec_composition WHERE source_type = 'index' AND code = $1",
                    code_filter,
                )
            else:
                logger.info("    [DB] Force mode: deleting existing index composition rows")
                await chunked_purge_async(
                    conn, "stats.sec_composition",
                    where_sql="source_type = 'index'",
                )
        else:
            # Date-check fast-path already filtered CSVs before reading,
            # so only missing dates were processed. Skip the secondary
            # row-level filter — all remaining rows are genuinely new.
            pass

        if all_rows:
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, "stats.sec_composition", all_rows,
                ["code", "snapshot_date", "rank"],
                date_column="snapshot_date",
            )
            total = n_copied + n_upserted
            via = "COPY" if n_copied > 0 and n_upserted == 0 else \
                  f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
                  "upsert"
            logger.info(f"    [DB] Inserted {total:,} rows into stats.sec_composition via {via}")
        else:
            logger.info("    [DB] No new rows to insert")

        # Post-step: stats.sec_board_map index slice — composition-weighted
        # board mix per index. Must run AFTER the composition upsert: mixes
        # read the latest snapshots this phase may have just added.
        from builds._commons.board_map import stage_stock_boards, refresh_board_mix
        await stage_stock_boards(conn)
        await refresh_board_mix(conn, "index", force=force,
                                codes=[code_filter] if code_filter else None)
    finally:
        await conn.close()


class IndexBuild(DataBuild):
    """``python -m builds.index`` — 3-phase orchestrator (composition →
    baseline → exts)."""

    title = ("BUILD INDEX  ·  composition (CSI+SZSE) → baseline (CSIndex daily) "
             "→ exts (ETF/exchange amt + sec similars)")
    component = "index"

    def add_arguments(self, parser) -> None:
        self.add_date_range_args(parser)
        self.add_code_arg(parser)

    def apply_args(self) -> None:
        # --date mode: mutual exclusion + parse (SystemExit 2 on bad input).
        self.forced_date = self.apply_date_force_args()
        if self.forced_date is not None:
            logger.info(f"[DATE MODE] Forced single-date build: {self.forced_date}")
        # Index codes in the DB are bare 6-digit codes (e.g. 000300) — strip
        # the exchange suffix normalize_code may have appended.
        self.code_filter = self.resolve_code_filter(strip_suffix=True)

    def header_fields(self) -> dict:
        return {
            "CSI comp dir":  INDEX_COMP_DIR,
            "SZSE comp dir": SZSE_INDEX_COMP_DIR,
            "Date range":    f"{self.args.start_date or '(all)'} → {self.args.end_date or '(all)'}",
            "Forced date":   str(self.forced_date) if self.forced_date else "(none)",
            "Code filter":   self.code_filter or "(none — all indices)",
            "Today":         TODAY_STR,
        }

    def _child_baseline_args(self):
        """Preset argparse.Namespace for the embedded BaselineBuild (phase 2).

        --refresh-estimated-days: nightly self-heal — rebuild recent daily
        rows gap-filled as ESTIMATED by a build that raced ahead of the EOD
        CSV publish; idempotent when the CSVs already agree. Skipped in
        --date mode: single-date scope only (the forced date is always
        rebuilt regardless of its row state).
        """
        import argparse as _ap

        args = self.args
        return _ap.Namespace(
            start_date=args.start_date,
            end_date=args.end_date,
            force=args.force,
            date=args.date,
            code=self.code_filter,
            refresh_estimated_days=0 if (args.force or args.date) else 10,
        )

    async def run(self) -> None:
        from builds._commons.code_filter import normalize_code  # noqa: F401
        from builds.index.composition import (
            build_index_composition_rows,
            build_szse_index_composition_rows,
            available_snapshot_dates,
        )
        from builds.index._index_exts import build_index_exts
        from builds.index._exchange_trading_amt import build_exchange_trading_amt
        from builds.index._sec_similars import build_sec_similars

        args = self.args
        code_filter = self.code_filter
        forced = self.forced_date

        if code_filter:
            logger.info(f"    [CODE FILTER] Restricting build to single index: {code_filter}")

        # ---- Phase 1: Index composition (CSI + SZSE) ----------------------
        logger.info("\n" + "=" * 78)
        logger.info("  PHASE 1: INDEX COMPOSITION (CSI + SZSE)")
        logger.info("=" * 78)
        t1 = time.time()
        await _run_composition(force=args.force, code_filter=code_filter,
                               forced_date=forced)
        logger.info(f"\n  Composition phase done ({int(time.time() - t1)}s)")

        # ---- Phase 2: Index baseline (CSIndex daily) ----------------------
        logger.info("\n" + "=" * 78)
        logger.info("  PHASE 2: INDEX BASELINE (CSIndex daily OHLCV + PE + MAs)")
        logger.info("=" * 78)
        t2 = time.time()

        # Embed the BaselineBuild child pipeline directly: preset args,
        # derive the child's forced_date/code_filter via its apply_args()
        # hook, then await amain() in THIS event loop — no sys.argv mutation.
        from builds.index.baseline import BaselineBuild

        baseline = BaselineBuild(args=self._child_baseline_args())
        baseline.apply_args()
        await baseline.amain()

        logger.info(f"\n  Baseline phase done ({int(time.time() - t2)}s)")

        # ---- Phase 3: Index exts (index_exts + etf/exchange amt + similars)
        # Each step has its own missing-date skip check, so in incremental mode
        # each only (re)computes the dates it is missing. The steps are
        # independent (different sources, different date grains) and do not
        # take a code filter.
        logger.info("\n" + "=" * 78)
        logger.info("  PHASE 3: INDEX EXTS (ETF metrics + exchange trading amt + sec similars)")
        logger.info("=" * 78)
        t3 = time.time()

        conn = await self.open_conn()
        try:
            # Step A: per-(date, index) + per-(date, industry) ETF metrics.
            # Driven by etf_liquidity_margin. --date mode recomputes the forced
            # date even when already present (upsert refresh, no truncation).
            await build_index_exts(conn, force=args.force, forced_date=forced)

            # Step B: per-(date, exchange) trading amount proxied by a
            # representative broad-market index per exchange (SZ->399001,
            # SS->000001). Driven by index_basic_stats (phase 2 output).
            await build_exchange_trading_amt(conn, force=args.force, forced_date=forced)

            # Step C: per-(composition-date, code) top-5 similar codes +
            # similar/dissimilar industries. Driven by sec_composition
            # snapshot dates (phase 1 output).
            await build_sec_similars(conn, force=args.force, forced_date=forced)
        finally:
            await self.close_conn(conn)

        logger.info(f"\n  Exts phase done ({int(time.time() - t3)}s)")


if __name__ == "__main__":
    IndexBuild().execute()
