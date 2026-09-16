"""builds.index.baseline — Build CSIndex daily history to DATABASE
(missing-data-only, no intermediate CSV).

Reads the history CSV archive produced by download_csindex.py:
  • {code}_history.csv        (daily OHLCV + PE + amount)
  • {code}_1m.csv             (recent 1-month export, bilingual headers)

Also reads SZSE index daily CSVs (399001 / 399006), SSE index
trend snapshots (~200 SSE indices), and CNINDEX history (399303 / 399310 /
399311). All sources are concatenated, deduplicated (priority: CNINDEX >
SSE trend > SZSE > 1m > history), then PE is backfilled from CSIndex rows
that lost the dedup. Missing trading days are filled with estimated close
prices using the best proxy index (> 60% composition shared weight).

Computes moving averages (ma5, ma20, ma60, ma120, ma255) from daily close.

NOTE: 5-minute intraday bars (stats.index_intraday_5min) are NO LONGER built
here. Intraday is now streamed in real time by stream_sse_price.py. The
former tick-file resample / 5min build / has_intraday_5mins sync helpers
have been removed; the flag is now synced by stream_sse_price after each
index bar lands.

Missing-data detection flow (DAILY, latest-missing-dates):
  1. Query stats.index_tech_stats for one MAX(date) per code (single GROUP
     BY — rows are inserted per table in one transaction, so max >= d
     implies the row at d exists).
  2. Peek every source file's last date (byte-level; snapshot filename
     dates for SZSE/SSE) → the source grid latest date. A code is read at
     all only when it has no DB rows, carries stale rebuild keys, or its
     latest DB date is behind the grid latest.

Usage:
  python -m builds.index.baseline
  python -m builds.index.baseline --force   (rebuild all daily tables)
  python -m builds.index.baseline --date 2026-08-14   (force one date)

The :class:`BaselineBuild` entry class (a :class:`DataBuild` subclass)
owns the runtime lifecycle + common argparse. It is ALSO embedded by the
builds.index orchestrator: construct it with a preset ``args`` namespace
and ``await child.amain()`` — no sys.argv mutation. Bootstrap-first
layout: the runtime setup runs before this module's imports.
"""

from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

import sys
import time

from _common.build_commons import (
    print_build_header, print_wall_time, TODAY_STR,
    get_latest_dates_async, truncate_table_async,
)
from builds.index.baseline.paths import CSINDEX_DIR
from _common.log_setup import setup_logging

from _common.data_build import DataBuild

logger = setup_logging("baseline")


class BaselineBuild(DataBuild):
    """``python -m builds.index.baseline`` — CSIndex daily → DB.

    Embeddable: builds.index constructs this class with preset args and
    awaits amain() directly (phase 2 of the index build).
    """

    title = "BUILD CSINDEX DAILY  ·  missing-data-only → DATABASE"
    component = "baseline"

    def add_arguments(self, parser) -> None:
        self.add_date_range_args(parser)
        self.add_code_arg(parser)
        parser.add_argument(
            "--refresh-estimated-days",
            type=int,
            default=0,
            metavar="N",
            help=(
                "Treat (date, code) rows newer than N days that are estimated "
                "(is_close_estimated = TRUE) or lack real OHLC (NULL/NaN open) "
                "as MISSING so they are rebuilt from the local CSVs (upsert is "
                "idempotent; 0 = off). Self-heals rows gap-filled by a build "
                "that ran before the EOD CSV publish landed. Used by the "
                "nightly builds.index run and the 'Build Yday Ref' chain."
            ),
        )

    def apply_args(self) -> None:
        # --date mode: mutual exclusion + parse (SystemExit 2 on bad input).
        self.forced_date = self.apply_date_force_args()
        if self.forced_date is not None:
            logger.info(f"[DATE MODE] Forced single-date build: {self.forced_date}")
        # Index codes are bare 6-digit codes (e.g. 000300) — strip the
        # exchange suffix normalize_code may have appended.
        self.code_filter = self.resolve_code_filter(strip_suffix=True)

    def header_fields(self) -> dict:
        return {
            "CSIndex dir": CSINDEX_DIR,
            "Code filter": self.code_filter or "(none — all indices)",
            "Forced date": str(self.forced_date) if self.forced_date else "(none)",
            "Today":       TODAY_STR,
        }

    async def run(self) -> None:
        from builds.index.baseline.shared_weights import fetch_index_shared_weights
        from builds.index.baseline.build_daily import build_daily_df
        from builds.index.baseline.db_insert import insert_daily_to_db

        args = self.args
        code_filter = self.code_filter
        forced = self.forced_date

        if code_filter:
            logger.info(f"    [CODE FILTER] Restricting build to single index: {code_filter}")

        # ------------------------------------------------------------------
        # 1. Connect to DB and query latest date per code
        # ------------------------------------------------------------------
        logger.info("\n[1/3] Connecting to database and querying latest dates …")
        conn = await self.connect_db()

        try:
            if args.force:
                if code_filter:
                    # Single-code force mode: DELETE only this index's rows
                    # (FK children first, identity last) instead of truncating.
                    logger.info(f"    [DB] Force mode for code {code_filter}: deleting existing rows for this code")
                    for tbl in ("stats.index_tech_stats",
                                "stats.index_valuation", "stats.index_basic_stats",
                                "stats.index_identity"):
                        await conn.execute(f"DELETE FROM {tbl} WHERE code = $1", code_filter)
                else:
                    logger.info("    [DB] Force mode: truncating existing daily tables")
                    # NOTE: stats.index_intraday_5min is owned by stream_sse_price.py
                    # (real-time SSE streaming) and is intentionally NOT truncated here.
                    for tbl in ("stats.index_tech_stats",
                                "stats.index_valuation", "stats.index_basic_stats",
                                "stats.index_identity"):
                        await truncate_table_async(conn, tbl)
                # Force mode: no DB rows → every code is fresh, everything loads.
                latest_dates: dict = {}
                stale_keys: set = set()
            elif code_filter:
                # Single-code mode: only check this index's latest date so dates
                # loaded for OTHER indices don't mask this code's gaps.
                row = await conn.fetchrow(
                    "SELECT max(date) AS max_date FROM stats.index_tech_stats WHERE code = $1",
                    code_filter,
                )
                latest_dates = {code_filter: row["max_date"].isoformat()} \
                    if row and row["max_date"] else {}
                stale_keys: set = set()
                logger.info(f"    [DB] code {code_filter} latest date in stats.index_tech_stats: "
                      f"{latest_dates.get(code_filter) or '(none)'}")
            else:
                # Latest-missing-dates check: one MAX(date) per code from
                # stats.index_tech_stats (the LAST table in the insert sequence)
                # instead of loading every (date, code) pair. Rows are inserted
                # per table in a single transaction, so a code's max date >= d
                # implies the row at d exists — only dates AFTER the max can be
                # missing. If tech_stats lacks a row entirely, all four tables
                # are re-upserted (upsert is idempotent).
                latest_dates = {
                    str(c): str(d)[:10]
                    for c, d in (await get_latest_dates_async(
                        conn, "stats.index_tech_stats", ["code"])).items()
                }
                n_dates = len(latest_dates)
                logger.info(f"    [DB] {n_dates:,} index codes in stats.index_tech_stats; "
                      f"latest {max(latest_dates.values()) if latest_dates else '(none)'}")

                # Self-heal: recent estimated/NULL-OHLC rows are stale keys —
                # they are rebuilt from the local CSVs. Covers rows gap-filled
                # by a build that ran before the EOD CSV publish landed; rows
                # whose CSVs still lack the data are re-estimated identically.
                # --date mode skips the self-heal: single-date scope only (the
                # forced date is always rebuilt regardless of its row state).
                stale_keys: set = set()
                if args.refresh_estimated_days > 0 and forced is None:
                    stale_rows = await conn.fetch(
                        """
                        SELECT date, code
                        FROM stats.index_basic_stats
                        WHERE date >= CURRENT_DATE - ($1::int)
                          AND (is_close_estimated = TRUE
                               OR open IS NULL
                               OR open::text = 'NaN')
                        """,
                        args.refresh_estimated_days,
                    )
                    stale_keys = {f"{str(r['date'])[:10]}|{str(r['code'])}"
                                  for r in stale_rows}
                    if stale_keys:
                        logger.info(
                            f"    [DB] refresh-estimated({args.refresh_estimated_days}d): "
                            f"{len(stale_keys):,} estimated/NULL-open keys marked for rebuild",
                        )
                    else:
                        logger.info(
                            f"    [DB] refresh-estimated({args.refresh_estimated_days}d): "
                            f"no estimated/NULL-open rows in window",
                        )

            # ------------------------------------------------------------------
            # 2. Build daily frame (latest-missing-dates only)
            # ------------------------------------------------------------------
            logger.info("\n[2/3] Building daily history frame (latest missing dates only) …")

            # Fetch shared weights for close-price estimation of missing dates
            shared_weights = await fetch_index_shared_weights(conn)
            logger.info(f"    [DB] {len(shared_weights):,} index shared-weight pairs loaded "
                  f"for close estimation")

            daily_df = await build_daily_df(conn, latest_dates,
                                            stale_keys=stale_keys,
                                            shared_weights=shared_weights,
                                            code_filter=code_filter,
                                            forced_date=forced.isoformat() if forced else None)

            # --date availability gate: no source CSV row exists at the forced
            # date (real or estimable) — same contract as forced_date_scope.
            if forced is not None and (daily_df is None or len(daily_df) == 0):
                logger.error(f"[FATAL] --date {forced}: no data for this date in "
                      f"index daily source CSVs")
                raise SystemExit(1)

            # (--code filtering is pushed down into build_daily_df / loaders —
            # only this code's source files are ever read.)

            # ------------------------------------------------------------------
            # 3. Insert to database
            # ------------------------------------------------------------------
            logger.info("\n[3/3] Inserting daily data to database …")
            new_daily = await insert_daily_to_db(conn, daily_df)

            logger.info(f"    → Total new daily rows inserted: {new_daily:,}")

        finally:
            await conn.close()


if __name__ == "__main__":
    BaselineBuild().execute()
