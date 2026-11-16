"""Download the E Fund fundlist all-funds NAV snapshot — the daily
increment trend, ONE request for the whole fund family.

Source: ``https://www.efunds.com.cn/fund/public/fundlist.json`` — the
fund supermarket page (``https://www.efundcf.com.cn/lm/jjcs/``) loads
exactly this file; it carries every E Fund fund (975 at 2026-11-14)
with the latest 单位净值 (netvalue), 日涨跌 (rzd, percent) and 净值日期
(tdate) per fund (see ``_endpoints`` study notes). Each run fetches it
once and writes one run-date-stamped CSV:

  ``temps/efunds_nav/efunds_nav_trend_<YYYYMMDD>.csv``

CSV schema (``date, code, name, nav, day_pct`` — the huatai_pb trend
schema):
  * date     — the fund's own 净值日期 from the list (ISO; '' when the
               entry carries no tdate)
  * code     — BARE 6-digit code, listed ETFs and OTC funds alike (no
               exchange suffix — the 2026-09-30 convention)
  * name     — 基金名称 (fundname)
  * nav      — 单位净值 (netvalue)
  * day_pct  — 日涨跌 in PERCENT ("-0.80" → -0.80)

Re-runs within the day overwrite the day's file with the fresh snapshot
(``--force`` re-fetches even when today's file exists). Money funds
(货币型, plus the bfFunds 保证金 set which is not parsed at all) are
excluded — they show 万份收益/7日年化 instead of NAV, the same
convention as the huatai products-index trend. Per-fund full NAV
history is NOT this service's job (the xcowch fund/nav POST endpoint
serves it — see ``_endpoints`` reference notes).

The entry point is the :class:`EfundsTrendDownload` DownloadJob.

Usage:
  python -m downloads.etf.efunds.trend
  python -m downloads.etf.efunds.trend --fund-codes 159915,510310
  python -m downloads.etf.efunds.trend --force   (re-fetch even if today's file exists)
"""
from __future__ import annotations

import argparse
from datetime import date
from typing import Dict, List

import pandas as pd

from downloads._common import (
    DownloadJob,
    RunStats,
    build_default_session,
    is_valid_file,
    resolve_out_dir,
    setup_logger,
)
from downloads.etf.efunds._endpoints import (
    EFundsEndpoints,
    parse_fundlist_nav_rows,
)

logger = setup_logger("efunds_nav_trend")

SERVICE = "downloads.etf.efunds.trend"

CSV_ENCODING = "utf-8-sig"

TREND_COLS = ["date", "code", "name", "nav", "day_pct"]


class EfundsTrendDownload(DownloadJob):
    """DownloadJob for the E Fund fundlist NAV trend fetch cycle — the
    huatai_pb trend shape over fundlist.json; pacing / filter / out_root
    stay per-call arguments."""

    description = ("Download the E Fund fundlist all-funds NAV snapshot "
                   "(fundlist.json, one request for the family).")
    component = "efunds_nav_trend"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--fund-codes", type=str, default=None,
                        help="Comma-separated fund codes to keep (default: all funds in the list)")
        parser.add_argument("--out-root", type=str, default=None,
                        help="Alternative output root directory")
        parser.add_argument("--sleep-sec", type=float, default=None,
                        help="Sleep seconds between requests "
                             f"(default: external_request_config JSON for {SERVICE})")
        parser.add_argument("--force", action="store_true",
                        help="Re-fetch even when today's trend file already exists.")

    def apply_args(self) -> None:
        fund_codes_arg: List[str] | None = None
        if self.args.fund_codes:
            fund_codes_arg = [c.strip() for c in self.args.fund_codes.split(",") if c.strip()]
        self.args.fund_codes = fund_codes_arg

    def download_nav_trend(
        self,
        *,
        out_root: str | None = None,
        fund_codes: List[str] | None = None,
        sleep_sec: float | None = None,
        force: bool = False,
    ) -> Dict[str, object]:
        out_dir = resolve_out_dir("", "efunds_nav", out_root)
        today = date.today()
        out_path = out_dir / f"efunds_nav_trend_{today:%Y%m%d}.csv"

        if not force and is_valid_file(out_path, min_bytes=64):
            logger.info("[trend] today's file exists: %s (use --force to re-fetch)",
                        out_path.name)
            return {"out_path": str(out_path), "status": "cached"}

        session = build_default_session()
        endpoints = EFundsEndpoints()
        proxy = endpoints.build_proxy(SERVICE, sleep_sec)
        stats = RunStats()

        payload = endpoints.fetch_fundlist(session, proxy, service=SERVICE, logger=logger)
        if payload is None:
            logger.warning("[trend] fundlist fetch failed; nothing written")
            stats.failed += 1
            return stats.to_dict(out_dir=str(out_dir))

        rows = parse_fundlist_nav_rows(payload)
        if not rows:
            logger.warning("[trend] no fund rows parsed from fundlist.json")
            stats.empty += 1
            return stats.to_dict(out_dir=str(out_dir))

        if fund_codes:
            wanted = {c for c in (s.strip() for s in fund_codes) if c}
            rows = [r for r in rows if r["code"] in wanted]
            if not rows:
                logger.warning("[trend] no funds matched the --fund-codes filter")
                stats.empty += 1
                return stats.to_dict(out_dir=str(out_dir))

        trend = pd.DataFrame({
            "date": [r["nav_date"] for r in rows],
            "code": [r["code"] for r in rows],
            "name": [r["name"] for r in rows],
            "nav": [r["nav"] for r in rows],
            "day_pct": [r["day_pct"] for r in rows],
        })
        trend = trend.drop_duplicates(subset=["code"], keep="first")
        trend = trend[TREND_COLS]
        trend = trend.sort_values("code").reset_index(drop=True)
        trend.to_csv(out_path, index=False, encoding=CSV_ENCODING)

        stats.downloaded += 1
        no_nav = int(trend["nav"].isna().sum())
        freshest = str(trend["date"].max()) if len(trend) else ""
        logger.info("[trend] saved %s (%d funds, %d without NAV, freshest %s)",
                    out_path.name, len(trend), no_nav, freshest or "--")
        return stats.to_dict(out_path=str(out_path), out_dir=str(out_dir),
                             funds=len(trend))

    def run(self) -> None:
        self.download_nav_trend(
            out_root=self.args.out_root,
            fund_codes=self.args.fund_codes,
            sleep_sec=self.args.sleep_sec,
            force=self.args.force,
        )


if __name__ == "__main__":
    EfundsTrendDownload().execute()
