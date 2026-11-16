"""Fetch the LATEST missing trading date's PE per SSE stock into the archive.

Nightly companion to ``downloads.stock.sse.archive`` (which stays a manual /
once backfill): for each SSE stock the archive already tracks (every
``temps/sse_archive/{code}_pe.csv`` ∪ the DB's current ETF-held list),
request 静态市盈率(倍) + 总换手率(%) for the LATEST missing trading date
≤ the target from the SSE 成交概况 endpoint and append one row to that
stock's ``{code}_pe.csv``.

builds.stock's PE loader (``_read_sse_pe_files``) already auto-loads the
missing tail of those files, so a fresh daily anchor keeps the stock-PE
estimation ladder (``PE_ESTIMATE_MAX_MONTHS = 3``) permanently valid — the
2026-11-04 estimate expiry happened because the archive's quarterly-snapshot
+ big-jump request dates stopped being fetched when its "once" schedule
ended, and the SSE snapshot feed no longer carries 市盈率.

Latest-missing-date semantics: at most ONE request per stock per sweep —
the newest trading day ≤ target absent from the file (a fully current stock
costs only the in-memory read). A fetch failure (PE not yet published /
suspended stock / host block) leaves the stock missing; the next nightly
sweep retries the same date, so publication lag self-heals. The archive
module stays the on-demand backfill for gap anchors (quarterly snapshots +
big-jump dates).

Entry point: ``python -m downloads.stock.sse.pe_latest`` — a DownloadJob
reusing the archive module's 成交概况 fetch helpers (same import pattern as
``downloads.stock.sse.dividend``).
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from _common._holidays_and_weekdays import is_trading_day
from downloads._common import (
    AntiBotProxy,
    DownloadJob,
    antibot_config_for,
    last_business_day,
    resolve_out_dir,
)
from downloads._common.net import setup_logger
from downloads.stock.sse.archive.__main__ import (
    ARCHIVE_DIRNAME,
    CSV_ENCODING,
    PE_COLUMNS,
    SSE_TRADE_STATS_URL,
    _fetch_trade_stats_alt,
    _fetch_trade_stats_primary,
    _parse_trade_stats_row,
    _read_existing_trade_stats,
    _write_stats_csv,
    load_target_stocks,
)
from _common.db_commons import get_db_connection
from _common.log_setup import setup_logging  # noqa: E402

logger = setup_logging("pe_latest")

SERVICE = "downloads.stock.sse.pe_latest"

# Canonical on-disk schema of the archive's {code}_pe.csv files: the archive
# module's PE_COLUMNS plus the static classification columns the stock
# build's PE loader filters on (``sec_type == "stock"``; ``证券代码`` carries
# the ".SS" suffix). The bare 5-column PE_COLUMNS form predates those
# columns — rows written without them are silently dropped by the loader.
PE_FULL_COLUMNS: List[str] = PE_COLUMNS + ["exchange", "board", "sec_type"]


def _pe_csv_row(raw: Dict[str, Any], bare: str, name: str) -> Dict[str, str]:
    """成交概况 result dict → one full-schema PE CSV row (canonical 8 cols)."""
    row = _parse_trade_stats_row(raw, bare, name)
    row["证券代码"] = f"{bare}.SS"
    row["exchange"] = "SS"
    row["board"] = "STAR" if bare.startswith("68") else "MAIN"
    row["sec_type"] = "stock"
    return row


class SseStockPeLatestDownload(DownloadJob):
    """DownloadJob: append the latest missing biz date's PE row per stock."""

    description = __doc__
    service = SERVICE  # single-instance lock — overlapping sweeps would double-fetch the same (code, date)

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--target-date", type=str, default=None,
            help="PE date to fetch (YYYY-MM-DD; default: the latest "
                 "business day). Stocks already holding this date in "
                 "{code}_pe.csv are skipped without a request.",
        )
        parser.add_argument(
            "--sleep-sec", type=float, default=None,
            help="Override the trade_stats pacing sleep seconds "
                 "(default: external_request_config JSON for "
                 f"{SERVICE}).",
        )

    # ------------------------------------------------------------------
    # Fetch cycle
    # ------------------------------------------------------------------
    def fetch_latest_missing_pe(
        self,
        target_date: date,
        archive_dir: Path,
        sess: requests.Session,
        stats_proxy: AntiBotProxy,
        stocks: List[Tuple[str, str]],
    ) -> Dict[str, Any]:
        """One sweep: for every stock, append PE for the LATEST trading date
        ≤ *target_date* that its ``{code}_pe.csv`` still lacks.

        At most one request per stock per sweep (the anchor always advances
        to the newest missing date); a stock whose PE for the target date
        was already fetched costs only the in-memory file read. A failed
        fetch (PE not yet published / suspended / host block) leaves the
        stock missing — the next nightly sweep retries the same date.
        Returns the summary dict logged by ``run()``.
        """
        fetched = 0
        skipped_current = 0
        new_files = 0
        no_data: List[str] = []
        blocked_early = False

        for bare, name in stocks:
            pe_file = archive_dir / f"{bare}_pe.csv"
            existing = _read_existing_trade_stats(pe_file)

            # The newest trading day ≤ target the file still lacks — pure
            # calendar/set arithmetic, no requests.
            fetch_date: Optional[date] = None
            cursor = target_date
            while cursor >= date(target_date.year - 1, 1, 1):
                if is_trading_day(cursor) and cursor.isoformat() not in existing:
                    fetch_date = cursor
                    break
                cursor -= timedelta(days=1)
            if fetch_date is None:
                skipped_current += 1
                continue

            if stats_proxy.is_blocked(SSE_TRADE_STATS_URL):
                logger.warning(
                    "  [host-blocked] query.sse.com.cn blocked — stopping "
                    "sweep at %s (remaining stocks stay missing)",
                    bare,
                )
                blocked_early = True
                break

            # Alternative first, primary fallback (the archive's alternation
            # strategy mirrored): the primary queryNewAllQuatAbel.do stopped
            # serving usable rows in mid-2026 (empty shells for every date,
            # incl. known-good ones), so paying its request+sleep first
            # doubles the sweep time for nothing. When the primary recovers,
            # the fallback still catches it.
            raw = _fetch_trade_stats_alt(sess, bare, fetch_date, stats_proxy)
            used_alt = True
            if raw is None:
                raw = _fetch_trade_stats_primary(sess, bare, fetch_date, stats_proxy)
                used_alt = False
            if raw is None:
                # Both APIs returned nothing (PE not yet published for the
                # date, suspended stock, or fetch failure) — leave missing;
                # the next nightly sweep retries.
                no_data.append(bare)
                continue

            row = _pe_csv_row(raw, bare, name)
            if not pe_file.exists():
                # Create the file with its header before the first append
                # (mirrors the archive's resumable file bootstrap).
                _write_stats_csv(pe_file, [], PE_FULL_COLUMNS)
                new_files += 1
            with open(pe_file, "a", encoding=CSV_ENCODING, newline="") as f:
                writer = csv.DictWriter(
                    f, fieldnames=PE_FULL_COLUMNS, extrasaction="ignore"
                )
                writer.writerow(row)
            fetched += 1

            api_tag = "alt" if used_alt else "primary"
            logger.info(
                "  -> %s pe %s [%s]: PE=%s, turnover=%s",
                bare, fetch_date.isoformat(), api_tag,
                row.get("静态市盈率(倍)", ""),
                row.get("总换手率(%)", ""),
            )

        return {
            "target_date": target_date.isoformat(),
            "stocks": len(stocks),
            "fetched": fetched,
            "skipped_current": skipped_current,
            "new_files": new_files,
            "no_data": len(no_data),
            "blocked_early": blocked_early,
        }

    def run(self) -> None:
        """One fetch cycle (default target: the latest business day)."""
        if self.args.target_date:
            target = date.fromisoformat(self.args.target_date)
        else:
            target = last_business_day(date.today())

        archive_dir = resolve_out_dir(str(Path(__file__).resolve()), ARCHIVE_DIRNAME)
        sess = requests.Session()
        if self.args.sleep_sec is not None:
            stats_proxy = AntiBotProxy(replace(
                antibot_config_for(SERVICE, "trade_stats"),
                base_sleep_sec=self.args.sleep_sec,
            ))
        else:
            stats_proxy = AntiBotProxy(
                antibot_config_for(SERVICE, "trade_stats")
            )

        # Universe: every stock the archive already tracks ({code}_pe.csv
        # files — the coverage builds.stock actually loads) ∪ the DB's
        # current ETF-held SSE list (load_target_stocks, supplying proper
        # names). The ETF-held list alone has shrunk to ~226 codes while
        # ~1.4k archive files exist, so the file set is the base.
        universe: Dict[str, str] = {
            p.name[: -len("_pe.csv")]: ""
            for p in archive_dir.glob("*_pe.csv")
        }
        conn = get_db_connection()
        try:
            for bare, name in load_target_stocks(conn):
                universe[bare] = name or universe.get(bare, "")
        finally:
            conn.close()
        stocks = sorted(universe.items())
        if not stocks:
            logger.warning("No target SSE stocks resolved — nothing to do.")
            return

        logger.info(
            "SSE PE latest-missing-date sweep: target=%s, stocks=%d "
            "(archive files + ETF-held), archive_dir=%s",
            target.isoformat(), len(stocks), archive_dir,
        )
        logger.info(self.fetch_latest_missing_pe(
            target, archive_dir, sess, stats_proxy, stocks,
        ))


if __name__ == "__main__":
    SseStockPeLatestDownload().execute()
