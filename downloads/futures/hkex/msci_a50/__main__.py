"""downloads.futures.hkex.msci_a50 — Download HKEX MSCI China A 50 Connect
(USD) Index Futures daily bars, per contract month.

Source: the exchange's Daily Market Report (FINAL) archives under
``https://www.hkex.com.hk/eng/stat/dmstat/dayrpt/`` — one zip per exchange
trading date, one row per listed contract month (the same "By Contract
Month" data the product page's Prices & Statistics view serves):

  * ``mcaf{YYMMDD}.zip`` — day session OHLC + volume, settlement price and
    change, contract high/low, open interest and OI change.
  * ``mcafa{YYMMDD}.zip`` — the after-hours (T+1) session that ran the
    EVENING of that trading date (published ~03:15 HKT the next morning).

Written to the SHARED futures tables — ``stats.futures_identity`` +
``stats.futures_basic_stats`` (the family in
``database/sql/stats/12_futures.sql``; this family's codes all match
``MCA%``) — plus a CFFEX-format CSV audit drop
(``temps/hkex_mca_daily/YYYYMM/YYYYMMDD_futures.csv`` — same layout as
``temps/cffex_trend/``, see ``csv_transform.py``).

Bar date semantics: a trading date T owns its day session AND the
after-hours session starting that evening. ``volume`` (day + after-hours)
stays NULL until the after-hours report is out; the next run refreshes the
latest stored date to complete it. The exchange keeps ~1 year of report
archives, so the initial backfill starts at most ``--start-date`` /
today - 400 days back.

Incremental behaviour:
  * Without ``--force``: fetch every weekday from the last stored date
    (inclusive — after-hours completion) through today; 404 dates
    (weekends apart) are non-trading days or not-yet-final reports and are
    simply skipped, a later run picks them up.
  * ``--force``: purge the family's rows (``code LIKE 'MCA%'``) from both
    futures tables, then backfill the whole retained history.

The entry point is the :class:`MsciA50Download` DownloadJob — the former
run_scheduled body is its ``run()``, get_stored_dates / plan_dates became
methods and daterange stays a module-level generator.

Usage:
  python -m downloads.futures.hkex.msci_a50
  python -m downloads.futures.hkex.msci_a50 --start-date 2026-10-01  # scope
  python -m downloads.futures.hkex.msci_a50 --force
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

# Project root setup — MUST be before any project imports
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from _common.build_commons import print_build_header, print_wall_time
from _common.db_commons import bulk_upsert, get_db_connection

from downloads._common import DownloadJob, resolve_out_dir, setup_logger
from downloads.futures.hkex.msci_a50.config import (
    FAMILY_CODE_LIKE,
    OUT_DIRNAME,
    RETENTION_DAYS,
    SERVICE,
)
from downloads.futures.hkex.msci_a50.csv_transform import write_cffex_style_files
from downloads.futures.hkex.msci_a50.downloader import (
    MsciA50Downloader,
    db_rows_for,
    identity_rows_for,
)

logger = setup_logger("hkex_mca")

TARGET_TABLE: str = "stats.futures_basic_stats"
IDENTITY_TABLE: str = "stats.futures_identity"


class MsciA50Download(DownloadJob):
    """DownloadJob for the HKEX MSCI A50 fetch cycle — the former
    run_scheduled body is ``run()`` verbatim, get_stored_dates / plan_dates
    became methods and daterange stays a module-level generator; the fetch
    layer is the MsciA50Downloader instance held in ``__init__``."""

    description = ("Download HKEX MSCI China A 50 Connect (USD) Index "
                   "Futures daily bars per contract month "
                   "(Daily Market Report archives).")
    component = "hkex_mca"
    service = SERVICE

    def __init__(self) -> None:
        super().__init__()
        self.downloader = MsciA50Downloader()

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--force", action="store_true",
            help=f"Purge the family's rows ({FAMILY_CODE_LIKE.replace('%', '%%')}) from the shared "
                 "futures tables and backfill the whole retained history "
                 "(~1 year of archives).",
        )
        parser.add_argument(
            "--start-date", default=None,
            help="Backfill start (YYYY-MM-DD). Default: the last stored date "
                 "(inclusive, for after-hours completion), or today minus the "
                 "archive retention when nothing is stored.",
        )
        parser.add_argument(
            "--out-root", default=None,
            help="Override the CSV audit directory root.",
        )
        parser.add_argument(
            "--sleep", type=float, default=None,
            help="Sleep seconds between requests (default: the service's "
                 f"external_request_config row for {SERVICE!r}).",
        )

    def apply_args(self) -> None:
        if self.args.start_date:
            self.args.start_date = date.fromisoformat(self.args.start_date)

    def get_stored_dates(self, conn) -> Set[date]:
        """Trading dates already stored (any MCA contract) — the incremental unit."""
        rows = conn.execute(
            f"SELECT DISTINCT date FROM {TARGET_TABLE} WHERE code LIKE %s ORDER BY date",
            (FAMILY_CODE_LIKE,),
        ).fetchall()
        return {row[0] for row in rows}

    def plan_dates(self, stored: Set[date], start: Optional[date], force: bool,
                   today: date) -> List[date]:
        """Weekday dates to fetch: every missing date in range, plus the latest
        stored one (its after-hours session may have completed since)."""
        if force or not stored:
            begin: date = start or (today - timedelta(days=RETENTION_DAYS))
        else:
            begin = start or min(max(stored), today)
        plan: Set[date] = {
            d for d in daterange(begin, today)
            if d.weekday() < 5 and (force or d not in stored)
        }
        if not force and stored:
            latest = max(stored)
            if begin <= latest and latest not in plan:
                plan.add(latest)  # after-hours completion refresh
        return sorted(plan)

    def run(self) -> None:
        """One fetch cycle (single-instance lock held by caller)."""
        t0 = self.t0
        today: date = date.today()
        out_dir: Path = resolve_out_dir(__file__, OUT_DIRNAME, self.args.out_root)

        print_build_header(
            "DOWNLOAD HKEX MSCI A50 FUTURES DAILY  ·  Daily Market Report archives",
            **{
                "Report base": "www.hkex.com.hk/eng/stat/dmstat/dayrpt (mcaf/mcafa zips)",
                "Tables": f"{IDENTITY_TABLE} + {TARGET_TABLE} (codes {FAMILY_CODE_LIKE})",
                "CSV dir": str(out_dir),
                "Start": str(self.args.start_date or "(auto)"),
                "Force": self.args.force,
            },
        )

        # ------------------------------------------------------------------
        # Step 1: DB state — stored dates, purge on --force, the fetch plan
        # ------------------------------------------------------------------
        logger.info("[1/3] Checking stored dates ...")
        conn = get_db_connection()
        if self.args.force:
            deleted_stats = conn.execute(
                f"DELETE FROM {TARGET_TABLE} WHERE code LIKE %s",
                (FAMILY_CODE_LIKE,),
            ).rowcount
            deleted_identity = conn.execute(
                f"DELETE FROM {IDENTITY_TABLE} WHERE code LIKE %s",
                (FAMILY_CODE_LIKE,),
            ).rowcount
            conn.commit()
            logger.info("  --force: purged %d basic_stats + %d identity rows (%s)",
                        deleted_stats, deleted_identity, FAMILY_CODE_LIKE)
            stored: Set[date] = set()
        else:
            stored = self.get_stored_dates(conn)
        if stored:
            logger.info("  %d dates stored (%s .. %s)",
                        len(stored), min(stored), max(stored))
        else:
            logger.info("  nothing stored yet")

        plan = self.plan_dates(stored, self.args.start_date, self.args.force, today)
        logger.info("[2/3] Planning fetches ...")
        logger.info("  %d dates to fetch (%s .. %s)",
                    len(plan), plan[0] if plan else "-", plan[-1] if plan else "-")
        if not plan:
            logger.info("Nothing to fetch.")
            conn.close()
            print_wall_time(t0)
            return

        # ------------------------------------------------------------------
        # Step 2: fetch + upsert, one trading date at a time; the CFFEX-format
        # CSV audit drop is written afterwards from all rows of this run
        # ------------------------------------------------------------------
        logger.info("[3/3] Fetching and upserting ...")
        fetched_rows = 0
        run_rows: List[Dict[str, object]] = []
        dates_upserted = 0
        dates_missing = 0
        ah_pending = 0
        for i, d in enumerate(plan):
            rows: Optional[List[Dict[str, object]]] = self.downloader.fetch_daily_rows(
                d, sleep_sec=self.args.sleep,
            )
            if rows is None:
                dates_missing += 1
                logger.info("[%d/%d] %s: no report (non-trading day / not final yet)",
                            i + 1, len(plan), d)
                continue
            if rows:
                inserted: int = bulk_upsert(
                    conn, IDENTITY_TABLE, identity_rows_for(rows),
                    key_columns=["code", "date"],
                )
                inserted += bulk_upsert(
                    conn, TARGET_TABLE, db_rows_for(rows),
                    key_columns=["code", "date"],
                )
                fetched_rows += inserted
                dates_upserted += 1
                run_rows.extend(rows)
                ah_pending += sum(1 for r in rows if r.get("volume") is None)
            logger.info("[%d/%d] %s: %d contract rows", i + 1, len(plan), d, len(rows))

        conn.close()

        if run_rows:
            logger.info("Writing CFFEX-format CSV audit files ...")
            write_cffex_style_files(run_rows, out_dir)

        # ------------------------------------------------------------------
        # Summary
        # ------------------------------------------------------------------
        logger.info("Done: %d rows across %d dates upserted, %d dates without "
                    "report, %d rows awaiting after-hours completion",
                    fetched_rows, dates_upserted, dates_missing, ah_pending)
        print_wall_time(t0)


def daterange(begin: date, end: date):
    d = begin
    while d <= end:
        yield d
        d += timedelta(days=1)


if __name__ == "__main__":
    MsciA50Download().execute()
