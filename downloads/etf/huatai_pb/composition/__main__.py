"""Download huatai-pb.com ETF 申购赎回清单 (PCF) as builds.etf composition CSVs.

Studies ``https://www.huatai-pb.com/products/zhishu/510880/index.html`` whose
申购赎回清单 tab POSTs ``/etf-web/etf/index.json`` (see ``_endpoints`` for
the full response contract). The endpoint serves the PCF of the EXACT
trading day requested (empty ``dayInfo`` on non-trading days) with history
back to inception, so this module both:

  * latest-snapshot mode (registered monthly in downloads_monthly.sh —
    mirrors the SSE/SZSE composition cadence; default universe = the
    curated checkin watchlist, CHECKIN_FUND_CODES in ``_endpoints`` —
    PCF exists only for the LISTED members of the list, OTC codes
    simply yield no snapshot) — fetches the latest
    published PCF (today, falling back to the previous business day when
    today's is not yet published), skipping any (date, code) snapshot
    already cached on disk;
  * ``--backfill-start YYYY-MM-DD`` — walks every trading day from that
    date to today and fetches each missing snapshot (history archive).

Output per snapshot (COMBINED_COLS schema — byte-compatible with the
szse/sse composition CSVs so ``builds.etf.pipeline`` loads them directly):

  ``temps/huatai_etf_composition/huatai_etf_comp_<YYYYMMDD>_<code>.csv``
      per-file finished CSV — the ONLY artifact persisted, matching the
      szse/sse composition modules whose build-loaded artifact is likewise
      the per-file CSV (the former raw ``.json`` response archive was
      removed 2026-10-12: nothing consumed it, and the endpoint serves the
      exact-day PCF back to inception, so any re-derivation can re-fetch).

Column mapping (COMBINED_COLS):
  trade_date     ← maxDate (the PCF's trading day, ISO)
  etf_code       ← fundcode, BARE 6-digit (no exchange suffix — 2026-09-30
                   convention; stock codes below keep theirs)
  etf_name       ← fundname
  fund_type      ← "" (not published by the PCF endpoint)
  target_index   ← dayInfo.underlyingindex (e.g. "000015" 上证红利) — a
                   field the exchange PCF exports do NOT carry
  nav_per_unit   ← dayInfo.nav (单位净值)
  min_unit_nav   ← dayInfo.navpercu (最小申赎单位净值)
  stock_code     ← stockList[].stockcode, suffixed via gpsc
                   (XSHG→.SS / XSHE→.SZ / XHKG→.HK; older records lack gpsc
                   and fall back to the A-share code prefix)
  stock_name     ← stockList[].stockshort
  shares         ← stockList[].number (股数 per creation unit)
  cash_sub_flag  ← stockList[].tdbz mapped to the SZSE/SSE Chinese labels
                   (0禁止/1允许/2必须/3-8 退补类)
  market         ← gpsc/prefix-resolved market label

The entry point is the :class:`HuataiPbCompositionDownload` DownloadJob —
the former ``__main__`` body is its ``run()`` (the --fund-codes comma
split moved to ``apply_args``) and the ``download_composition``
orchestration function became the method of the same name (same signature
and body); ``pcf_to_combined_rows`` / ``_save_snapshot`` and the constants
stay module-level.

Usage:
  python -m downloads.etf.huatai_pb.composition
  python -m downloads.etf.huatai_pb.composition --fund-codes 510880,512890
  python -m downloads.etf.huatai_pb.composition --backfill-start 2026-01-01
  python -m downloads.etf.huatai_pb.composition --sleep-sec 1.5
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

from downloads._common import (
    DownloadJob,
    RunStats,
    business_days,
    build_default_session,
    is_valid_file,
    resolve_out_dir,
    service_urls,
    setup_logger,
)
from downloads.etf.huatai_pb._endpoints import (
    CHECKIN_FUND_CODES,
    TDBZ_LABEL_MAP,
    build_proxy,
    fetch_pcf_payload,
    parse_comma_number,
    resolve_stock_market,
)

logger = setup_logger("huatai_pb_composition")

SERVICE = "downloads.etf.huatai_pb.composition"

# Endpoint URLs — the service's external_request_config JSON row (urls
# object) is the single source; a missing role raises at the point of use.
URLS = service_urls(SERVICE)
PCF_URL = URLS["pcf"]

CSV_ENCODING = "utf-8-sig"
# How many business days back the daily mode probes for a published PCF.
FALLBACK_DAYS = 6

# Identical to builds.etf.composition.COMBINED_COLS / the szse+sse per-file
# CSV schema — the build reads these files without any surgery.
COMBINED_COLS: List[str] = [
    "trade_date", "etf_code", "etf_name", "fund_type", "target_index",
    "nav_per_unit", "min_unit_nav",
    "stock_code", "stock_name", "shares", "cash_sub_flag", "market",
]


def pcf_to_combined_rows(payload: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    """PCF payload → (trade_date, COMBINED_COLS rows).

    Returns ("", []) when the response carries no PCF (non-trading day /
    not yet published) or no components.
    """
    day_info = payload.get("dayInfo") or {}
    stock_list = payload.get("stockList") or []
    if not day_info or not stock_list:
        return "", []

    trade_day = str(payload.get("maxDate", "")).strip()
    try:
        datetime.strptime(trade_day, "%Y-%m-%d")
    except ValueError:
        return "", []

    fund_code = str(payload.get("fundcode", "")).strip()
    etf_code = fund_code
    etf_name = str(payload.get("fundname", "") or "")

    rows: List[Dict[str, Any]] = []
    for comp in stock_list:
        stock_code_raw = str(comp.get("stockcode", "")).strip()
        market_label, suffix = resolve_stock_market(
            str(comp.get("gpsc", "") or "").strip(), stock_code_raw)
        stock_code = f"{stock_code_raw}.{suffix}" if suffix else stock_code_raw

        tdbz = str(comp.get("tdbz", "")).strip()
        shares_tok = comp.get("number")

        rows.append({
            "trade_date": trade_day,
            "etf_code": etf_code,
            "etf_name": etf_name,
            "fund_type": "",
            "target_index": str(day_info.get("underlyingindex", "") or ""),
            "nav_per_unit": parse_comma_number(day_info.get("nav")),
            "min_unit_nav": parse_comma_number(day_info.get("navpercu")),
            "stock_code": stock_code,
            "stock_name": str(comp.get("stockshort", "") or ""),
            "shares": int(shares_tok) if shares_tok not in (None, "", "-") else None,
            "cash_sub_flag": TDBZ_LABEL_MAP.get(tdbz, "退补"),
            "market": market_label,
        })
    return trade_day, rows


def _snapshot_csv_path(out_dir: Path, trade_ymd: str, code: str) -> Path:
    return out_dir / f"huatai_etf_comp_{trade_ymd}_{code}.csv"


def _save_snapshot(
    out_dir: Path, payload: Dict[str, Any], code: str,
) -> Tuple[bool, str]:
    """Persist one PCF payload as the per-file finished CSV.

    CSV-only (no raw JSON archive) — same final-artifact contract as the
    szse/sse composition modules, whose build-loaded artifact is likewise
    the per-file CSV.

    Returns (ok, status) with status ∈ {"downloaded", "empty"}.
    """
    trade_day, rows = pcf_to_combined_rows(payload)
    if not rows:
        return False, "empty"
    trade_ymd = trade_day.replace("-", "")
    csv_path = _snapshot_csv_path(out_dir, trade_ymd, code)

    out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=COMBINED_COLS)
    df.to_csv(csv_path, index=False, encoding=CSV_ENCODING)
    return True, "downloaded"


class HuataiPbCompositionDownload(DownloadJob):
    """DownloadJob for the huatai-pb.com PCF composition fetch cycle — the
    former ``__main__`` body is ``run()`` and the ``download_composition``
    function is the method of the same name (same signature and body
    verbatim); pacing / filter / out_root stay per-call arguments."""

    description = ("Download huatai-pb.com ETF PCF (申购赎回清单) "
                   "composition CSVs.")
    component = "huatai_pb_composition"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--fund-codes", type=str, default=None,
                        help="Comma-separated fund codes (default: the checkin "
                             "watchlist — see _endpoints.CHECKIN_FUND_CODES)")
        parser.add_argument("--out-root", type=str, default=None,
                        help="Alternative output root directory")
        parser.add_argument("--sleep-sec", type=float, default=None,
                        help="Sleep seconds between requests "
                             f"(default: external_request_config JSON for {SERVICE})")
        parser.add_argument("--backfill-start", type=str, default=None,
                        metavar="YYYY-MM-DD",
                        help="Backfill trading days from this date to today "
                             "(skip snapshots already cached on disk).")

    def apply_args(self) -> None:
        # Former --fund-codes comma split (post-parse derivation).
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
    ) -> Dict[str, Any]:
        out_dir = resolve_out_dir("", "huatai_etf_composition", out_root)
        codes = [c.strip() for c in (fund_codes or list(CHECKIN_FUND_CODES)) if c.strip()]
        today = date.today()

        session = build_default_session()
        proxy = build_proxy(SERVICE, sleep_sec)
        stats = RunStats()

        if backfill_start:
            start = datetime.strptime(backfill_start, "%Y-%m-%d").date()
            days = business_days(start, today, reverse=False)
            logger.info("[backfill] %s → %s: %d trading days", start, today, len(days))
            for code in codes:
                for day in days:
                    if proxy.is_blocked(PCF_URL):
                        logger.warning("[backfill %s] host blocked, stopping", code)
                        break
                    ymd = day.strftime("%Y%m%d")
                    csv_cached = _snapshot_csv_path(out_dir, ymd, code)
                    if is_valid_file(csv_cached, min_bytes=64):
                        stats.skipped_cached += 1
                        continue
                    payload = fetch_pcf_payload(
                        session, proxy, code, day.isoformat(), service=SERVICE,
                        logger=logger)
                    if payload is None:
                        stats.failed += 1
                        continue
                    ok, _status = _save_snapshot(out_dir, payload, code)
                    if ok:
                        stats.downloaded += 1
                    else:
                        stats.empty += 1
            summary = stats.to_dict(out_dir=str(out_dir), funds=len(codes))
            logger.info("Done PCF backfill: %s", summary)
            return summary

        # --- daily mode: latest published PCF per fund ---
        probe_days = business_days(today - timedelta(days=12), today)[:FALLBACK_DAYS + 1]
        for code in codes:
            got = False
            for day in probe_days:
                payload = fetch_pcf_payload(
                    session, proxy, code, day.isoformat(), service=SERVICE,
                    logger=logger)
                if payload is None:
                    stats.failed += 1
                    break
                trade_day, rows = pcf_to_combined_rows(payload)
                if not rows:
                    continue
                csv_cached = _snapshot_csv_path(
                    out_dir, trade_day.replace("-", ""), code)
                if is_valid_file(csv_cached, min_bytes=64):
                    stats.skipped_cached += 1
                    got = True
                    logger.info("[pcf %s] cached snapshot %s", code, trade_day)
                    break
                ok, _status = _save_snapshot(out_dir, payload, code)
                if ok:
                    stats.downloaded += 1
                    got = True
                    logger.info("[pcf %s] saved snapshot %s (%d holdings)",
                                code, trade_day, len(rows))
                    break
            if not got:
                logger.warning("[pcf %s] no published PCF found in last %d business days",
                               code, len(probe_days))
                stats.empty += 1

        summary = stats.to_dict(out_dir=str(out_dir), funds=len(codes))
        logger.info("Done PCF daily: %s", summary)
        return summary

    def run(self) -> None:
        self.download_composition(
            out_root=self.args.out_root,
            fund_codes=self.args.fund_codes,
            sleep_sec=self.args.sleep_sec,
            backfill_start=self.args.backfill_start,
        )


if __name__ == "__main__":
    HuataiPbCompositionDownload().execute()
