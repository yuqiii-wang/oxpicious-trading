"""Entry point for analysis.volatility.

Run via ``python -m analysis.volatility``.

Volatility — per-(sec_type, code, date) realized-volatility panel from
daily bars (analysis.volatility; see
database/sql/analysis/volatility.sql): close-to-close historical vol
across 5/10/20/60/120/252-row windows, the RiskMetrics EWMA vol
(λ=0.94), the Parkinson / Garman-Klass / Rogers-Satchell / Yang-Zhang
20-row OHLC estimators, the overnight-vs-intraday vol split, Wilder
ATR(14)/NATR(14), Bollinger band width, vol-of-vol, the short/long vol
ratio, trailing-252 percentile ranks + z-scores, and the 5-minute
realized vol with bipower jump ratio where the sec_type's intraday
bars exist. Serves the Commons "Volatility" page
(/analysis/commons/volatility) through GET /api/analysis/volatility/*.

STRUCTURED for the security sec_types (etf / index / stock — per-sec_type
source mapping in config.SEC_TYPE_SOURCES; industry stays out — pool
grain), but the RUN SCOPE is index-only for now
(config.ENABLED_SEC_TYPES; index = the 10 study indices). --sec-type
selects one enabled sec_type; widening = the config, no schema change.

Incremental by missing dates scoped to the run's codes (the source side
is the sec_type's own basic-stats bars, NOT a whole identity-table
calendar — a date covered only by out-of-scope codes would never clear
and break the nightly no-op); --force deletes the resolved sec_types'
rows and regenerates.
"""
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

import datetime
import time

import numpy as np

from _common.labels import SEC_TYPE_CODE
from _common.log_setup import setup_logging

from _common.data_analysis import DataAnalysis
from _common.db_commons import (
    analyze_tables_async,
    bulk_upsert_async,
    copy_frame_chunked_async,
)
from analysis._common import sanitize_for_db_insert
from analysis.volatility import config
from analysis.volatility.compute import VolatilityFrame
from analysis.volatility.data_definitions import (
    OUT_COLUMNS,
    OUT_NUMERIC_COLUMNS,
)
from analysis.volatility.fetch import VolatilitySources

logger = setup_logging("volatility")


TABLE = "analysis.volatility"
ANALYSIS_NAME = "volatility"
ANALYSIS_DESCRIPTION = (
    "Per-security daily realized-volatility panel (run scope: the 10 "
    "study indices): close-to-close historical vol across six windows, "
    "the RiskMetrics EWMA vol (λ=0.94), the Parkinson / Garman-Klass / "
    "Rogers-Satchell / Yang-Zhang 20-day OHLC estimators, the "
    "overnight-vs-intraday split, ATR(14)/NATR(14), Bollinger band "
    "width, vol-of-vol, the short/long vol ratio, trailing-year "
    "percentile ranks + z-scores, and the 5-minute realized vol with "
    "bipower jump ratio where intraday bars exist."
)


class VolatilityAnalysis(DataAnalysis):
    """``python -m analysis.volatility`` — per-security realized-vol
    panel, one row per (sec_type, code, date)."""

    title = ("ANALYZE VOLATILITY — realized-volatility panel "
             "(index scope for now)")
    component = "volatility"
    sec_types = config.ENABLED_SEC_TYPES

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)
        self.add_sec_type_arg(parser)
        self.add_code_arg(parser)

    def apply_args(self) -> None:
        pass

    def header_fields(self) -> dict:
        return {
            "tables": TABLE,
            "mode": ("FORCE (scoped delete + full recompute)"
                     if self.args.force
                     else "incremental (missing dates only)"),
        }

    async def run(self) -> None:
        t0 = time.time()
        force = bool(self.args.force)
        sec_types = self.resolve_sec_types()

        logger.info("=" * 78)
        logger.info("  VOLATILITY  (%s mode)  sec_types=%s",
                    "FORCE" if force else "incremental",
                    ",".join(sec_types))
        logger.info("=" * 78)

        # A --code run recomputes ALL of that security's rows in ONE
        # sec_type (mutually exclusive with --force per the base help).
        single_code = self.args.code
        if force:
            # Scoped force clear: DELETE only the resolved sec_types'
            # rows (a TRUNCATE would wipe sec_types this run does not
            # rebuild — the multi-sec_type contract).
            await self.delete_sec_type_rows(
                TABLE, [SEC_TYPE_CODE[st] for st in sec_types])

        n_total = 0
        for sec_type in sec_types:
            n_total += await self._run_sec_type(
                sec_type, force, single_code, t0)

        if not single_code:
            await self.upsert_identity(
                ANALYSIS_NAME, ANALYSIS_NAME, ANALYSIS_DESCRIPTION)
        logger.info("[done] %s row(s) written across %d sec_type(s) "
                    "(%.1fs).", f"{n_total:,}", len(sec_types),
                    time.time() - t0)

    async def _run_sec_type(
        self, sec_type: str, force: bool, single_code: str | None,
        t0: float,
    ) -> int:
        """One sec_type's slice of the run: universe → missing-date
        scope → fetch + compute → write. Returns the rows written."""
        sec_code = SEC_TYPE_CODE[sec_type]
        sources = VolatilitySources(self.conn, sec_type, codes=[])
        codes = ([single_code] if single_code
                 else await sources.resolve_universe())
        sources.codes = codes
        if not codes:
            logger.info("[%s] no codes with bars — skipping.", sec_type)
            return 0
        tag = f"iv:{sec_type}"

        # ---- [1/4] incremental scope -----------------------------------
        lo: datetime.date | None = None
        hi: datetime.date | None = None
        if not force:
            missing = await self._missing_dates(sources, sec_code)
            if not missing:
                logger.info("[%s] no missing dates — nothing to do.",
                            tag)
                return 0
            lo = min(missing)
            hi = max(missing)
            logger.info("[%s] %d missing date(s): %s .. %s",
                        tag, len(missing), lo, hi)

        # ---- [2/4] fetch + compute -------------------------------------
        daily = await sources.fetch_daily_bars()
        intraday = await sources.fetch_intraday_closes()
        logger.info("[%s] %s daily bar(s), %s intraday close(s).",
                    tag, f"{len(daily):,}",
                    f"{len(intraday):,}" if intraday is not None else "0")
        if daily.empty:
            logger.info("[%s] no daily bars — nothing to compute.", tag)
            return 0
        frame = VolatilityFrame(daily, intraday, sec_code).build()
        frame = frame[OUT_COLUMNS]
        logger.info("[%s]        -> %s vol row(s) (%.1fs)",
                    tag, f"{len(frame):,}", time.time() - t0)
        if lo is not None:
            lo64 = np.datetime64(lo, "us")
            frame = frame[frame["date"] >= lo64]
        if hi is not None:
            hi64 = np.datetime64(hi, "us")
            frame = frame[frame["date"] <= hi64]
        frame = frame.sort_values(["date", "code"]).reset_index(drop=True)

        # ---- [3/4] write ------------------------------------------------
        if force:
            n = await copy_frame_chunked_async(
                self.conn, TABLE, frame,
                columns=OUT_COLUMNS,
                numeric_cols=OUT_NUMERIC_COLUMNS,
                round_to=4,
                date_cols=["date"],
                label=f"[{tag}]",
            )
            await analyze_tables_async(self.conn, [TABLE])
        else:
            rows = sanitize_for_db_insert(
                frame, numeric_cols=OUT_NUMERIC_COLUMNS,
                round_to=4, date_cols=["date"])
            n = 0
            if rows:
                n = await bulk_upsert_async(
                    self.conn, TABLE, rows,
                    key_columns=["sec_type", "code", "date"])
        logger.info("[%s] wrote %s row(s).", tag, f"{n:,}")

        # ---- [4/4] sanity -----------------------------------------------
        summary = await self.conn.fetchrow(f"""
            SELECT COUNT(*) AS n_rows, MIN(date) AS first_date,
                   MAX(date) AS last_date
              FROM {TABLE}
             WHERE sec_type = $1
        """, sec_code)
        if summary:
            logger.info(
                "[%s] %s row(s) now, %s .. %s.",
                tag, f"{summary['n_rows']:,}", summary["first_date"],
                summary["last_date"])
        return n

    async def _missing_dates(
        self, sources: VolatilitySources, sec_code: int,
    ) -> set:
        """Dates the run's codes have daily bars for but the target
        table does not (host-side python-date set arithmetic over the
        two small DISTINCT-date reads; scoped to the run's codes — NOT
        a whole identity-table calendar)."""
        source = await sources.fetch_source_dates()
        have = await sources.fetch_target_dates(sec_code)
        return source - have


def main() -> None:
    VolatilityAnalysis().execute()


if __name__ == "__main__":
    main()
