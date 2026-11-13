"""Entry point for analysis.market_participation.

Run via ``python -m analysis.market_participation``.

Market Participation — one row per trading day for the whole A-share
market's participation gauges (analysis.market_participation; see
database/sql/analysis/31_market_participation.sql): total SS+SZ proxy
turnover (+ MAs / vs-MA gaps / trailing-252 percentile), the RAW daily
SSE share of that turnover (+ MAs / vs-ma20 pp gap / 20-day OLS slope),
and the whole-universe advance/decline breadth (+ A/D ratio, median /
mean change, A/D MAs, trailing-252 percentile). Serves the Market Beta
page's "Market Participation" panel via
GET /api/analysis/market-beta/participation.

Incremental by missing dates against the source tables' own dates
(the view reads are cheap; the missing-date set is computed host-side
against the target table); --force truncates and regenerates. Market-
level grain — no --sec-type.
"""
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

import datetime
import time

import numpy as np
import pandas as pd

from _common.log_setup import setup_logging

from _common.data_analysis import DataAnalysis
from _common.db_commons import (
    analyze_tables_async,
    bulk_upsert_async,
    copy_frame_chunked_async,
    truncate_table_async,
)
from _common.sql_views import render_sql_view
from analysis._common import sanitize_for_db_insert
from analysis.market_participation.compute import ParticipationFrame
from analysis.market_participation.data_definitions import (
    OUT_COLUMNS,
    OUT_FLOAT_COLUMNS,
)
from analysis.market_participation.fetch import ParticipationSources

logger = setup_logging("market_participation")


TABLE = "analysis.market_participation"
ANALYSIS_NAME = "market_participation"
ANALYSIS_DESCRIPTION = (
    "One row per trading day for the whole A-share market's "
    "participation gauges: total SS+SZ proxy turnover (each exchange "
    "proxied by one representative index), the raw daily SSE share of "
    "that turnover, and the advance/decline breadth of the stock "
    "universe."
)


class MarketParticipationAnalysis(DataAnalysis):
    """``python -m analysis.market_participation`` — market-level
    participation gauges, one row per trading day."""

    title = ("ANALYZE MARKET PARTICIPATION — whole-market turnover, "
             "SSE share and advance/decline breadth")
    component = "market_participation"

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)

    def apply_args(self) -> None:
        pass

    def header_fields(self) -> dict:
        return {
            "tables": TABLE,
            "mode": ("FORCE (truncate + full recompute)"
                     if self.args.force
                     else "incremental (missing dates only)"),
        }

    async def run(self) -> None:
        t0 = time.time()
        force = bool(self.args.force)

        logger.info("=" * 78)
        logger.info("  MARKET PARTICIPATION  (%s mode)",
                    "FORCE" if force else "incremental")
        logger.info("=" * 78)

        # ---- [mp1/4] incremental scope --------------------------------
        lo: datetime.date | None = None
        hi: datetime.date | None = None
        if not force:
            missing = await self._missing_dates()
            if not missing:
                logger.info("[mp1/4] no missing dates — nothing to do.")
                await self.upsert_identity(
                    ANALYSIS_NAME, ANALYSIS_NAME, ANALYSIS_DESCRIPTION)
                return
            lo = min(missing)
            hi = max(missing)
            logger.info("[mp1/4] %d missing date(s): %s .. %s",
                        len(missing), lo, hi)

        # ---- [mp2/4] fetch + compute ----------------------------------
        sources = ParticipationSources(self.conn)
        totals = await sources.fetch_exchange_totals(None, None)
        breadth = await sources.fetch_breadth_daily(None, None)
        logger.info("[mp2/4] %s turnover row(s), %s breadth row(s).",
                    f"{len(totals):,}", f"{len(breadth):,}")
        if totals.empty:
            logger.info("[mp2/4] no turnover rows — nothing to compute.")
            return
        frame = ParticipationFrame(totals, breadth).build()
        logger.info("        -> %s participation row(s) (%.1fs)",
                    f"{len(frame):,}", time.time() - t0)
        if lo is not None:
            lo64 = np.datetime64(lo, "us")
            frame = frame[frame["date"] >= lo64]
        if hi is not None:
            hi64 = np.datetime64(hi, "us")
            frame = frame[frame["date"] <= hi64]

        # ---- [mp3/4] write ---------------------------------------------
        if force:
            await truncate_table_async(self.conn, TABLE)
            n = await copy_frame_chunked_async(
                self.conn, TABLE, frame,
                columns=OUT_COLUMNS,
                numeric_cols=OUT_FLOAT_COLUMNS,
                round_to=None,  # pre-rounded in the frame
                date_cols=["date"],
                label="[mp3/4]",
            )
            await analyze_tables_async(self.conn, [TABLE])
        else:
            rows = sanitize_for_db_insert(
                frame, numeric_cols=OUT_FLOAT_COLUMNS,
                round_to=None, date_cols=["date"])
            n = 0
            if rows:
                n = await bulk_upsert_async(
                    self.conn, TABLE, rows, key_columns=["date"])
        logger.info("[mp3/4] wrote %s row(s).", f"{n:,}")

        # ---- [mp4/4] identity + sanity ---------------------------------
        await self.upsert_identity(
            ANALYSIS_NAME, ANALYSIS_NAME, ANALYSIS_DESCRIPTION)
        summary = await self.conn.fetchrow(f"""
            SELECT COUNT(*) AS n_rows, MIN(date) AS first_date,
                   MAX(date) AS last_date
              FROM {TABLE}
        """)
        if summary:
            logger.info(
                "[mp4/4] %s row(s), %s .. %s.",
                f"{summary['n_rows']:,}", summary["first_date"],
                summary["last_date"])

    async def _missing_dates(self) -> set:
        """Dates the exchange-totals source covers but the target table
        does not (host-side set arithmetic over the small per-date
        reads — the table is one row per trading day)."""
        have = await self.conn.fetch(
            f"SELECT date FROM {TABLE}")
        have_dates = {r["date"] for r in have}
        source = await self.conn.fetch(
            "SELECT DISTINCT ss.date AS date "
            "FROM stats.exchange_trading_amt ss "
            "JOIN stats.exchange_trading_amt sz "
            "  ON sz.date = ss.date AND sz.exchange = 'SZ' "
            "WHERE ss.exchange = 'SS'")
        return ({r["date"] for r in source} - have_dates)


def main() -> None:
    MarketParticipationAnalysis().execute()


if __name__ == "__main__":
    main()
