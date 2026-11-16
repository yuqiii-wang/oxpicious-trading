"""Download E Fund quarterly-report stock composition (季报十大重仓股)
as per-(fund, quarter) CSVs.

Studies ``api.efunds.com.cn/xcowch/front/efund/assetinfo`` (POST
``{fundCode, xDate}`` — see ``_endpoints`` for the full response
contract): the EXACT quarter-end (03-31/06-30/09-30/12-31) top-10 stock
holdings of the fund's quarterly report, with history back to the
fund's data beginning (empty assets/investList before that). This
module both:

  * quarterly mode (registered quarterly — the tier runs daily, the
    quarter guard below makes non-quarter-start days no-ops): for each fund of
    the listed-ETF universe (fundlist.json filtered to 51x/56x/58x/158/
    159 codes, intersected with the curated ACTIVE ETF registry — 7
    funds at 2026-11-14; only actively-managed ETFs get per-fund data),
    probe the latest quarter-end ≤
    today and, when its report is not yet published, the previous
    quarter-end; a fund whose latest-quarter snapshot is already cached
    is skipped (``--force-quarter-start`` bypasses);
  * ``--backfill-start YYYY-MM-DD`` — walks every quarter-end from that
    date (bounded per fund by the fundlist setupdate, so pre-inception
    quarters are never requested) to today and fetches each missing
    snapshot (history archive; cached files skipped).

Output per snapshot (COMP_COLS schema):

  ``temps/efunds_composition/efunds_comp_<YYYYMMDD>_<code>.csv``
      per-file finished CSV — report_date is the QUARTER-END date (the
      report's own investReportDate echo), one row per top-10 holding.

Column mapping (COMP_COLS):
  report_date     ← data.investReportDate (== the requested quarter-end)
  fund_code       ← the requested code, BARE 6-digit (no suffix)
  fund_name       ← fundlist fundname (the payload carries no fund name)
  rank            ← investInfoData[].norder (1..10)
  stock_code      ← investInfoData[].symbol, suffixed via mktcode
                    (XSHG→.SS / XSHE→.SZ)
  stock_name      ← investInfoData[].name
  weight_pct      ← formatratio (percent; ratio fraction ×100 fallback)
  fair_value_yuan ← investInfoData[].money (公允价值, 元)
  shares          ← investInfoData[].amount (持仓股数)
  market          ← mktcode-resolved market label

Anti-bot cooldown contract: every request is preceded by
``await_host_cooldown`` — while the AntiBotProxy host tracker has
api.efunds.com.cn blocked (403/429), the loop waits out the cooldown
window in slices (the tracker auto-unblocks and logs it) instead of
hammering the host or burning the remaining funds as failures; if the
host is still blocked after the wait budget the run aborts cleanly and
a re-run resumes from the cached files.

The entry point is the :class:`EfundsCompositionDownload` DownloadJob.

Usage:
  python -m downloads.etf.efunds.composition
  python -m downloads.etf.efunds.composition --fund-codes 159915,510310
  python -m downloads.etf.efunds.composition --backfill-start 2006-01-01
  python -m downloads.etf.efunds.composition --force-quarter-start
  python -m downloads.etf.efunds.composition --sleep-sec 1.0
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

from downloads._common import (
    AntiBotProxy,
    DownloadJob,
    RunStats,
    build_default_session,
    is_valid_file,
    resolve_out_dir,
    service_urls,
    setup_logger,
)
from downloads.etf.efunds._endpoints import (
    EFundsEndpoints,
    assetinfo_to_rows,
    await_host_cooldown,
    filter_universe_to_active,
    latest_quarter_end,
    parse_fundlist_etf_universe,
    quarter_ends,
)

logger = setup_logger("efunds_composition")

SERVICE = "downloads.etf.efunds.composition"

# Endpoint URLs — the service's external_request_config JSON row (urls
# object) is the single source; a missing role raises at the point of use.
URLS = service_urls(SERVICE)
ASSETINFO_URL = URLS["assetinfo"]

CSV_ENCODING = "utf-8-sig"

COMP_COLS: List[str] = [
    "report_date", "fund_code", "fund_name",
    "rank", "stock_code", "stock_name", "weight_pct",
    "fair_value_yuan", "shares", "market",
]

_UNIVERSE_FALLBACK_NOTE = (
    "fundlist fetch failed — pass --fund-codes explicitly or retry later")


def _snapshot_csv_path(out_dir: Path, report_ymd: str, code: str) -> Path:
    return out_dir / f"efunds_comp_{report_ymd}_{code}.csv"


def _save_snapshot(
    out_dir: Path, fund: Dict[str, str], report_date: str,
    rows: List[Dict[str, Any]],
) -> Path:
    """Persist one quarter's holdings as the per-file finished CSV.

    Returns the written path (the caller counts downloaded).
    """
    csv_path = _snapshot_csv_path(
        out_dir, report_date.replace("-", ""), fund["code"])
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=[
        "rank", "stock_code", "stock_name", "weight_pct",
        "fair_value_yuan", "shares", "market"])
    frame.insert(0, "report_date", report_date)
    frame.insert(1, "fund_code", fund["code"])
    frame.insert(2, "fund_name", fund["name"])
    frame = frame[COMP_COLS]
    frame.to_csv(csv_path, index=False, encoding=CSV_ENCODING)
    return csv_path


def _resolve_universe(
    session: requests.Session, endpoints: EFundsEndpoints,
    proxy: AntiBotProxy, fund_codes: Optional[List[str]],
) -> Tuple[List[Dict[str, str]], bool]:
    """The composition universe — the curated ACTIVE ETFs from a fresh
    fundlist GET (filtered by --fund-codes when given). Returns
    (universe, ok)."""
    payload = endpoints.fetch_fundlist(session, proxy, service=SERVICE, logger=logger)
    if payload is None:
        if fund_codes:
            logger.warning("[universe] fundlist fetch failed — proceeding "
                           "with --fund-codes (%d codes, names unknown)",
                           len(fund_codes))
            return ([{"code": c, "name": "", "setup_date": ""}
                     for c in fund_codes], True)
        logger.error("[universe] %s", _UNIVERSE_FALLBACK_NOTE)
        return [], False
    universe = filter_universe_to_active(
        parse_fundlist_etf_universe(payload), logger)
    if fund_codes:
        wanted = set(fund_codes)
        universe = [f for f in universe if f["code"] in wanted]
    return universe, True


class EfundsCompositionDownload(DownloadJob):
    """DownloadJob for the E Fund quarterly composition fetch cycle —
    quarterly latest-quarter refresh by default, ``--backfill-start``
    for the history archive; both loops are cooldown-guarded."""

    description = ("Download E Fund quarterly-report stock composition "
                   "(assetinfo top-10 holdings) CSVs.")
    component = "efunds_composition"

    def __init__(self, args=None) -> None:
        super().__init__(args)
        self.endpoints = EFundsEndpoints()

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--fund-codes", type=str, default=None,
                        help="Comma-separated fund codes (default: the curated "
                             "active-ETF registry ∩ the fundlist listed-ETF set)")
        parser.add_argument("--out-root", type=str, default=None,
                        help="Alternative output root directory")
        parser.add_argument("--sleep-sec", type=float, default=None,
                        help="Sleep seconds between requests "
                             f"(default: external_request_config JSON for {SERVICE})")
        parser.add_argument("--backfill-start", type=str, default=None,
                        metavar="YYYY-MM-DD",
                        help="Backfill quarter-ends from this date to today "
                             "(per-fund walk bounded by the fundlist setupdate; "
                             "snapshots already cached on disk are skipped).")
        parser.add_argument("--force-quarter-start", action="store_true", default=False,
                        help="Bypass the quarter guard: probe the latest quarter "
                             "even when its snapshot is already cached.")

    def apply_args(self) -> None:
        fund_codes_arg = None
        if self.args.fund_codes:
            fund_codes_arg = [c.strip() for c in self.args.fund_codes.split(",") if c.strip()]
        self.args.fund_codes = fund_codes_arg

    def download_composition(
        self,
        *,
        out_root: str | None = None,
        fund_codes: List[str] | None = None,
        sleep_sec: float | None = None,
        backfill_start: str | None = None,
        force_quarter_start: bool = False,
    ) -> Dict[str, Any]:
        out_dir = resolve_out_dir("", "efunds_composition", out_root)
        today = date.today()

        session = build_default_session()
        proxy = self.endpoints.build_proxy(SERVICE, sleep_sec)
        stats = RunStats()

        universe, ok = _resolve_universe(
            session, self.endpoints, proxy, fund_codes)
        if not ok:
            stats.failed += 1
            return stats.to_dict(out_dir=str(out_dir))
        if not universe:
            logger.warning("[composition] empty universe (filter matched nothing)")
            stats.empty += 1
            return stats.to_dict(out_dir=str(out_dir), funds=0)

        if backfill_start:
            return self._backfill(out_dir, universe, today, backfill_start,
                                  proxy, session, stats)
        return self._quarterly(out_dir, universe, today, force_quarter_start,
                              proxy, session, stats)

    # -- backfill: every quarter-end from max(arg, setup) to today -------
    def _backfill(
        self,
        out_dir: Path,
        universe: List[Dict[str, str]],
        today: date,
        backfill_start: str,
        proxy: Any,
        session: Any,
        stats: RunStats,
    ) -> Dict[str, Any]:
        start = datetime.strptime(backfill_start, "%Y-%m-%d").date()
        total_quarters = 0
        for fund in universe:
            fund_start = start
            setup = fund.get("setup_date") or ""
            if setup:
                try:
                    fund_start = max(start, datetime.strptime(
                        setup, "%Y-%m-%d").date())
                except ValueError:
                    pass
            for qe in quarter_ends(fund_start, today):
                total_quarters += 1
                csv_cached = _snapshot_csv_path(
                    out_dir, qe.strftime("%Y%m%d"), fund["code"])
                if is_valid_file(csv_cached, min_bytes=64):
                    stats.skipped_cached += 1
                    continue
                if not await_host_cooldown(proxy, ASSETINFO_URL, logger):
                    summary = stats.to_dict(
                        out_dir=str(out_dir), funds=len(universe),
                        status="blocked", fund=fund["code"], quarter=str(qe))
                    logger.warning("Backfill aborted (host blocked): %s", summary)
                    return summary
                payload = self.endpoints.fetch_assetinfo(
                    session, proxy, fund["code"], qe.isoformat(),
                    service=SERVICE, logger=logger)
                if payload is None:
                    stats.failed += 1
                    continue
                report_date, rows = assetinfo_to_rows(payload)
                if not rows:
                    stats.empty += 1
                    continue
                path = _save_snapshot(out_dir, fund, report_date, rows)
                stats.files.append(str(path))
                stats.downloaded += 1
        summary = stats.to_dict(
            out_dir=str(out_dir), funds=len(universe), quarters=total_quarters)
        logger.info("Done composition backfill: %s", summary)
        return summary

    # -- quarterly: latest published report per fund, once per quarter -----
    def _quarterly(
        self,
        out_dir: Path,
        universe: List[Dict[str, str]],
        today: date,
        force_quarter_start: bool,
        proxy: Any,
        session: Any,
        stats: RunStats,
    ) -> Dict[str, Any]:
        latest_qe = latest_quarter_end(today)
        prev_qe = latest_quarter_end(latest_qe - timedelta(days=1))
        for fund in universe:
            got = False
            had_failure = False
            latest_cached = _snapshot_csv_path(
                out_dir, latest_qe.strftime("%Y%m%d"), fund["code"])
            if not force_quarter_start and is_valid_file(latest_cached, min_bytes=64):
                stats.skipped_cached += 1
                continue
            for qe in (latest_qe, prev_qe):
                csv_cached = _snapshot_csv_path(
                    out_dir, qe.strftime("%Y%m%d"), fund["code"])
                if is_valid_file(csv_cached, min_bytes=64):
                    stats.skipped_cached += 1
                    got = True
                    logger.info("[comp %s] cached snapshot %s", fund["code"], qe)
                    break
                if not await_host_cooldown(proxy, ASSETINFO_URL, logger):
                    summary = stats.to_dict(
                        out_dir=str(out_dir), funds=len(universe),
                        status="blocked", fund=fund["code"], quarter=str(qe))
                    logger.warning("Quarterly pass aborted (host blocked): %s", summary)
                    return summary
                payload = self.endpoints.fetch_assetinfo(
                    session, proxy, fund["code"], qe.isoformat(),
                    service=SERVICE, logger=logger)
                if payload is None:
                    stats.failed += 1
                    had_failure = True
                    break
                report_date, rows = assetinfo_to_rows(payload)
                if not rows:
                    continue  # latest report not yet published → try prev
                path = _save_snapshot(out_dir, fund, report_date, rows)
                stats.files.append(str(path))
                stats.downloaded += 1
                got = True
                logger.info("[comp %s] saved snapshot %s (%d holdings)",
                            fund["code"], report_date, len(rows))
                break
            if not got and not had_failure:
                # neither the latest nor the previous quarter has a
                # published report (new fund / nothing disclosed yet)
                logger.info("[comp %s] no published report for %s..%s",
                            fund["code"], prev_qe, latest_qe)
                stats.empty += 1
        summary = stats.to_dict(out_dir=str(out_dir), funds=len(universe))
        logger.info("Done composition quarterly: %s", summary)
        return summary

    def run(self) -> None:
        self.download_composition(
            out_root=self.args.out_root,
            fund_codes=self.args.fund_codes,
            sleep_sec=self.args.sleep_sec,
            backfill_start=self.args.backfill_start,
            force_quarter_start=self.args.force_quarter_start,
        )


if __name__ == "__main__":
    EfundsCompositionDownload().execute()
