"""Download E Fund fund NAV (基金净值) FULL history (成立以来) archive.

The efunds twin of ``downloads.etf.huatai_pb.archive``: one POST per fund
(``api.efunds.com.cn/xcowch/front/fund/nav`` — see ``_endpoints`` for the
response contract and the 2026-11-14 probe) fetches the complete NAV
history since inception (563030: 899 rows, 2023-02-17 → today). Default
universe: the curated ACTIVE ETF registry (``active_etf_universe_codes``
in ``_endpoints`` — the issuer's actively-managed ETFs; only they load
per-fund history since 2026-11-14). Output per fund:

  ``temps/efunds_nav/raw/efunds_nav_archive_<code>_<YYYYMMDD>.json``
      raw response archive (re-written on every successful download)
  ``temps/efunds_nav/efunds_nav_archive_<code>.csv``
      finished full-history CSV — date asc, canonical code

CSV schema (``date, code, nav, cum_nav, day_pct`` — the huatai_pb archive
schema, consumed by builds.etf.feeder_nav's listed backfill):
  * date      — ISO trade date (row navDate)
  * code      — BARE 6-digit code, listed ETF and OTC fund alike (no
               exchange suffix anywhere — the 2026-09-30 convention)
  * nav       — 单位净值 (row netValue)
  * cum_nav   — 累计净值 (row totalNetValue)
  * day_pct   — 日涨跌 in PERCENT (row growthRate, e.g. -2.35)

Skip logic mirrors huatai_pb.archive: a fund whose archive CSV is fresh
within 20 days is skipped (registered run-once; re-runs are cheap
no-ops); ``--force`` re-downloads everything. Consumed by
builds.etf.feeder_nav (listed active-ETF NAV-history backfill →
stats.etf_identity/etf_basic_stats with is_ohl_estimated; strictly
missing-only — real exchange OHLC rows are never overwritten).

Anti-bot cooldown contract: every per-fund POST is preceded by
``await_host_cooldown`` (see ``_endpoints``) — a blocked host is waited
out, never hammered.

Usage:
  python -m downloads.etf.efunds.archive
  python -m downloads.etf.efunds.archive --fund-codes 563030,581050
  python -m downloads.etf.efunds.archive --force
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd

from downloads._common import (
    DownloadJob,
    RunStats,
    build_default_session,
    is_fresh_within,
    resolve_out_dir,
    service_urls,
    setup_logger,
)
from downloads.etf.efunds._endpoints import (
    EFundsEndpoints,
    active_etf_universe_codes,
    await_host_cooldown,
    parse_comma_number,
)

logger = setup_logger("efunds_nav_archive")

SERVICE = "downloads.etf.efunds.archive"

# Endpoint URLs — the service's external_request_config JSON row (urls
# object) is the single source; a missing role raises at the point of use.
URLS = service_urls(SERVICE)
NAV_URL = URLS["nav"]

CSV_ENCODING = "utf-8-sig"
ARCHIVE_FRESH_HOURS = 20 * 24  # registered run-once; guard bounds re-hits

NAV_COLS = ["date", "code", "nav", "cum_nav", "day_pct"]


def nav_rows_to_frame(payload: Dict[str, Any], fund_code: str) -> pd.DataFrame:
    """fund/nav payload rows → canonical NAV frame (date asc).

    The endpoint returns rows date-DESC (newest first); the frame is
    sorted date asc and deduped per date (first occurrence wins) — the
    huatai_pb archive conventions. Codes are stored BARE.
    """
    data = payload.get("data") or {}
    rows = data.get("data") or []
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=NAV_COLS)
    code = str(fund_code).strip()
    out = pd.DataFrame({
        "date": df["navDate"].astype(str),
        "code": code,
        "nav": df["netValue"].map(parse_comma_number),
        "cum_nav": df["totalNetValue"].map(parse_comma_number),
        "day_pct": df["growthRate"].map(parse_comma_number),
    })
    out = out.dropna(subset=["date", "nav"])
    out = out[out["date"] != ""]
    out = out.drop_duplicates(subset=["date"], keep="first")
    return out.sort_values("date").reset_index(drop=True)


class EFundsArchiveDownload(DownloadJob):
    """DownloadJob for the E Fund full-history (成立以来) fund NAV archive
    fetch cycle — one cooldown-guarded POST per active-ETF registry fund."""

    description = ("Download E Fund full-history (成立以来) fund NAV "
                   "archive.")
    component = "efunds_nav_archive"

    def __init__(self, args=None) -> None:
        super().__init__(args)
        self.endpoints = EFundsEndpoints()

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--fund-codes", type=str, default=None,
                        help="Comma-separated fund codes (default: the curated "
                             "active-ETF registry — see _endpoints."
                             "active_etf_universe_codes)")
        parser.add_argument("--out-root", type=str, default=None,
                        help="Alternative output root directory")
        parser.add_argument("--sleep-sec", type=float, default=None,
                        help="Sleep seconds between requests "
                             f"(default: external_request_config JSON for {SERVICE})")
        parser.add_argument("--force", action="store_true",
                        help="Re-download even when the archive CSV is fresh.")

    def apply_args(self) -> None:
        fund_codes_arg: Optional[List[str]] = None
        if self.args.fund_codes:
            fund_codes_arg = [c.strip() for c in self.args.fund_codes.split(",") if c.strip()]
        self.args.fund_codes = fund_codes_arg

    def download_nav_archive(
        self,
        *,
        out_root: str | None = None,
        fund_codes: Optional[List[str]] = None,
        sleep_sec: float | None = None,
        force: bool = False,
    ) -> Dict[str, object]:
        out_dir = resolve_out_dir("", "efunds_nav", out_root)
        raw_dir = out_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        if fund_codes:
            codes = [c.strip() for c in fund_codes if c.strip()]
        else:
            # Default universe: the curated ACTIVE ETF registry ONLY (the
            # daily trend stays all-funds; passive ETFs load no per-fund
            # history since 2026-11-14).
            codes = list(active_etf_universe_codes())

        session = build_default_session()
        proxy = self.endpoints.build_proxy(SERVICE, sleep_sec)
        stats = RunStats()

        for code in codes:
            csv_path = out_dir / f"efunds_nav_archive_{code}.csv"
            if not force and is_fresh_within(csv_path, hours=ARCHIVE_FRESH_HOURS):
                logger.info("[archive %s] cached (fresh within %dh)", code,
                            ARCHIVE_FRESH_HOURS // 24)
                stats.skipped_cached += 1
                continue

            if not await_host_cooldown(proxy, NAV_URL, logger):
                summary = stats.to_dict(
                    out_dir=str(out_dir), funds=len(codes),
                    status="blocked", fund=code)
                logger.warning("[archive] aborted (host blocked): %s", summary)
                return summary

            payload = self.endpoints.fetch_nav_history(
                session, proxy, code, service=SERVICE, logger=logger)
            frame = nav_rows_to_frame(payload, code) if payload is not None \
                else None
            if frame is None or frame.empty:
                logger.warning("[archive %s] no NAV rows returned, skipping",
                               code)
                stats.failed += 1
                continue

            raw_path = raw_dir / f"efunds_nav_archive_{code}_{date.today():%Y%m%d}.json"
            raw_path.write_text(json.dumps(payload, ensure_ascii=False),
                                encoding="utf-8")
            frame.to_csv(csv_path, index=False, encoding=CSV_ENCODING)
            stats.downloaded += 1
            logger.info("[archive %s] saved %s (%d rows, %s → %s)",
                        code, csv_path.name, len(frame),
                        frame["date"].iloc[0], frame["date"].iloc[-1])

        summary = stats.to_dict(out_dir=str(out_dir), funds=len(codes))
        logger.info("Done NAV archive: %s", summary)
        return summary

    def run(self) -> None:
        self.download_nav_archive(
            out_root=self.args.out_root,
            fund_codes=self.args.fund_codes,
            sleep_sec=self.args.sleep_sec,
            force=self.args.force,
        )


if __name__ == "__main__":
    EFundsArchiveDownload().execute()
