"""Download the huatai-pb.com products index all-funds NAV snapshot — the
daily increment trend, ONE request for the whole fund family.

Source: ``https://www.huatai-pb.com/products/index.html`` — the
server-rendered 全部基金 table carries, for every non-money fund, the
基金名称/代码, 最新净值 (+ 净值日期) and 日涨跌 columns (see ``_endpoints``
study notes). Each run fetches the page once and writes one
run-date-stamped CSV:

  ``temps/huatai_pb_nav/huatai_pb_nav_trend_<YYYYMMDD>.csv``

CSV schema (``date, code, name, nav, day_pct``):
  * date     — the fund's own 净值日期 from the page (ISO; '' when the page
               shows "--" for not-yet-launched funds)
  * code     — BARE 6-digit code, listed ETFs and OTC funds alike (no
               exchange suffix — the 2026-09-30 convention)
  * name     — 基金名称 (shortname)
  * nav      — 最新净值 (单位净值)
  * day_pct  — 日涨跌 in PERCENT ("-0.51%" → -0.51)

Re-runs within the day overwrite the day's file with the fresh snapshot
(``--force`` re-fetches even when today's file exists). Money funds (货币型)
are excluded — the page shows 万份收益/7日年化 for them instead of
最新净值/日涨跌. NAV is fund-side data — builds.etf reads exchange OHLCV
for market trend; this CSV is the fund-side daily increment archive
(per-fund full history lives in downloads.etf.huatai_pb.archive).

Usage:
  python -m downloads.etf.huatai_pb.trend
  python -m downloads.etf.huatai_pb.trend --fund-codes 510880,510300
  python -m downloads.etf.huatai_pb.trend --force   (re-fetch even if today's file exists)
"""
from __future__ import annotations

import argparse
from datetime import date
from typing import Dict, List

import pandas as pd

from downloads._common import (
    DEFAULT_SLEEP_SEC,
    RunStats,
    build_default_session,
    is_valid_file,
    resolve_out_dir,
    setup_logger,
)
from downloads.etf.huatai_pb._endpoints import (
    build_proxy,
    fetch_products_index_html,
    parse_products_index_nav_rows,
)

logger = setup_logger("huatai_pb_nav_trend")

CSV_ENCODING = "utf-8-sig"

TREND_COLS = ["date", "code", "name", "nav", "day_pct"]


def canonical_trend_code(code: str) -> str:
    """Bare 6-digit code as-is (no exchange suffix — 2026-09-30
    convention; kept as a seam so legacy suffixed snapshots are easy to
    spot in one place)."""
    return code


def download_nav_trend(
    *,
    out_root: str | None = None,
    fund_codes: List[str] | None = None,
    sleep_sec: float = DEFAULT_SLEEP_SEC,
    force: bool = False,
) -> Dict[str, object]:
    out_dir = resolve_out_dir("", "huatai_pb_nav", out_root)
    today = date.today()
    out_path = out_dir / f"huatai_pb_nav_trend_{today:%Y%m%d}.csv"

    if not force and is_valid_file(out_path, min_bytes=64):
        logger.info("[trend] today's file exists: %s (use --force to re-fetch)",
                    out_path.name)
        return {"out_path": str(out_path), "status": "cached"}

    session = build_default_session()
    proxy = build_proxy(sleep_sec)
    stats = RunStats()

    html_text = fetch_products_index_html(session, proxy, logger=logger)
    if not html_text:
        logger.warning("[trend] products index fetch failed; nothing written")
        stats.failed += 1
        return stats.to_dict(out_dir=str(out_dir))

    rows = parse_products_index_nav_rows(html_text)
    if not rows:
        logger.warning("[trend] no fund rows parsed from products index page")
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
        "code": [canonical_trend_code(r["code"]) for r in rows],
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


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Download huatai-pb.com all-funds NAV snapshot (products index page)."
    )
    ap.add_argument("--fund-codes", type=str, default=None,
                    help="Comma-separated fund codes to keep (default: all funds on the page)")
    ap.add_argument("--out-root", type=str, default=None,
                    help="Alternative output root directory")
    ap.add_argument("--sleep-sec", type=float, default=DEFAULT_SLEEP_SEC,
                    help=f"Anti-bot sleep seconds (default: {DEFAULT_SLEEP_SEC})")
    ap.add_argument("--force", action="store_true",
                    help="Re-fetch even when today's trend file already exists.")
    args = ap.parse_args()

    fund_codes_arg: List[str] | None = None
    if args.fund_codes:
        fund_codes_arg = [c.strip() for c in args.fund_codes.split(",") if c.strip()]

    download_nav_trend(
        out_root=args.out_root,
        fund_codes=fund_codes_arg,
        sleep_sec=args.sleep_sec,
        force=args.force,
    )
