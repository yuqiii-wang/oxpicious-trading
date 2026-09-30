"""Download huatai-pb.com ETF 分红信息 (dividend history) from product pages.

The 分红信息 section on each product page (e.g.
``https://www.huatai-pb.com/products/zhishu/510300/index.html``) is
server-rendered HTML: ``<div id="fhxx">`` wraps a ``<tbody id="fhTbody">``
whose rows carry ``class="hide"`` (client-side pagination shows 10 per
page — the markup still contains ALL rows). Row shape:

  分红年度 | 权益登记日 | 红利发放日/再投资日 | 每10份基金份额分红（元）

The last column is per 10 shares (1.430 = 0.143 元/份) — stored verbatim
as ``dividend_per_10_units`` to avoid the ÷10 rounding step. Funds with
no distributions render an ``info-noData`` 暂无内容 block instead of the
table (512890/561630/018173 today). The sibling 基金拆分记录 table is
display:none and empty — nothing to parse there.

Default universe: the curated checkin watchlist (CHECKIN_FUND_CODES in
``_endpoints``, from config.json). Output per fund (full-history refresh,
semi-annual cadence — registered in downloads_semiannually.sh):

  ``temps/huatai_pb_dividends/raw/huatai_pb_page_<code>_<YYYYMMDD>.html``
      raw page archive (re-written on every successful download)
  ``temps/huatai_pb_dividends/huatai_pb_dividends_<code>.csv``
      finished CSV — ``code, year, record_date, pay_date,
      dividend_per_10_units`` (code BARE 6-digit, listed ETFs and OTC
      funds alike — 2026-09-30 convention)

Dividend records have no builds/etf consumer yet (corp-action adjustment
in builds.etf is OHLCV-implied) — this is the reference archive the NAV
cum-vs-unit spread corroborates.

Usage:
  python -m downloads.etf.huatai_pb.dividends
  python -m downloads.etf.huatai_pb.dividends --fund-codes 510880,018173
  python -m downloads.etf.huatai_pb.dividends --force
"""
from __future__ import annotations

import argparse
import re
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from downloads._common import (
    DEFAULT_SLEEP_SEC,
    RunStats,
    build_default_session,
    is_fresh_within,
    resolve_out_dir,
    setup_logger,
)
from downloads.etf.huatai_pb._endpoints import (
    CHECKIN_FUND_CODES,
    PRODUCT_PAGE_URL,
    build_proxy,
)

logger = setup_logger("huatai_pb_dividends")

CSV_ENCODING = "utf-8-sig"
FRESH_HOURS = 20 * 24  # freshness guard (semi-annual cadence)

DIVIDEND_COLS = ["code", "year", "record_date", "pay_date",
                 "dividend_per_10_units"]

# 分红信息 markup: <div id="fhxx"> wraps the table (newer pages); the rows
# live in <tbody id="fhTbody"> on BOTH templates, each <tr> carrying
# class="hide" (client-side pagination — all rows stay in the markup).
_RE_FHTBODY = re.compile(
    r'<tbody\s+id="fhTbody">(.*?)</tbody>', re.S)
# Fallback for a page whose fhxx section renders the records table
# WITHOUT the fhTbody id (not seen live; kept for template drift).
_RE_FHXX = re.compile(r'<div\s+id="fhxx"[^>]*>(.*?)<!--', re.S)
_RE_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_RE_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def _clean_cell(html_fragment: str) -> str:
    """Strip tags/whitespace from a ``<td>`` fragment."""
    text = re.sub(r"<[^>]+>", "", html_fragment)
    return text.replace("&nbsp;", " ").strip()


def parse_dividend_rows(html_text: str) -> List[Dict[str, object]]:
    """Parse the 分红信息 table rows → [{year, record_date, pay_date,
    dividend_per_10_units}].

    Row shape on both page templates: 分红年度 | 权益登记日 |
    红利发放日/再投资日 | 每10份基金份额分红（元）— the last value is
    per 10 shares (1.430 = 0.143 元/份), kept verbatim.
    """
    m = _RE_FHTBODY.search(html_text)
    if not m:
        m = _RE_FHXX.search(html_text)
    if not m:
        return []
    rows: List[Dict[str, object]] = []
    for tr in _RE_TR.finditer(m.group(1)):
        cells = [_clean_cell(td.group(1)) for td in _RE_TD.finditer(tr.group(1))]
        if len(cells) < 4 or not cells[1]:
            continue
        per_10: Optional[float]
        try:
            per_10 = float(cells[3]) if cells[3] else None
        except ValueError:
            per_10 = None
        rows.append({
            "year": cells[0],
            "record_date": cells[1],
            "pay_date": cells[2],
            "dividend_per_10_units": per_10,
        })
    return rows


def download_dividends(
    *,
    out_root: str | None = None,
    fund_codes: List[str] | None = None,
    sleep_sec: float = DEFAULT_SLEEP_SEC,
    force: bool = False,
) -> Dict[str, object]:
    out_dir = resolve_out_dir("", "huatai_pb_dividends", out_root)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    codes = [c.strip() for c in (fund_codes or list(CHECKIN_FUND_CODES)) if c.strip()]

    session = build_default_session()
    proxy = build_proxy(sleep_sec)
    stats = RunStats()

    for code in codes:
        csv_path = out_dir / f"huatai_pb_dividends_{code}.csv"
        if not force and is_fresh_within(csv_path, hours=FRESH_HOURS):
            logger.info("[div %s] cached (fresh within %dh)", code, FRESH_HOURS // 24)
            stats.skipped_cached += 1
            continue

        resp = proxy.get(
            session, PRODUCT_PAGE_URL.format(code=code),
            headers=None, logger=logger, log_tag=f"[div {code}]")
        if resp is None or resp.status_code != 200:
            logger.warning("[div %s] page fetch failed, skipping", code)
            stats.failed += 1
            continue

        # The response carries no charset — resp.text would decode as
        # ISO-8859-1 mojibake; the pages are UTF-8.
        page_html = resp.content.decode("utf-8", errors="replace")
        raw_path = raw_dir / f"huatai_pb_page_{code}_{date.today():%Y%m%d}.html"
        raw_path.write_text(page_html, encoding="utf-8")

        rows = parse_dividend_rows(page_html)
        if not rows:
            logger.warning("[div %s] no 分红 rows parsed from page", code)
            stats.empty += 1
            continue

        frame = pd.DataFrame({
            "code": code,
            "year": [r["year"] for r in rows],
            "record_date": [r["record_date"] for r in rows],
            "pay_date": [r["pay_date"] for r in rows],
            "dividend_per_10_units": [r["dividend_per_10_units"] for r in rows],
        })
        frame = frame[DIVIDEND_COLS]
        frame.to_csv(csv_path, index=False, encoding=CSV_ENCODING)
        stats.downloaded += 1
        logger.info("[div %s] saved %s (%d records, %s → %s)",
                    code, csv_path.name, len(frame),
                    frame["record_date"].iloc[-1],
                    frame["record_date"].iloc[0])

    summary = stats.to_dict(out_dir=str(out_dir), funds=len(codes))
    logger.info("Done dividends: %s", summary)
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Download huatai-pb.com fund dividend history (分红信息)."
    )
    ap.add_argument("--fund-codes", type=str, default=None,
                    help="Comma-separated fund codes (default: the checkin "
                         "watchlist — see _endpoints.CHECKIN_FUND_CODES)")
    ap.add_argument("--out-root", type=str, default=None,
                    help="Alternative output root directory")
    ap.add_argument("--sleep-sec", type=float, default=DEFAULT_SLEEP_SEC,
                    help=f"Sleep seconds between requests (default: {DEFAULT_SLEEP_SEC})")
    ap.add_argument("--force", action="store_true",
                    help="Re-download even when the CSV is fresh.")
    args = ap.parse_args()

    fund_codes_arg = None
    if args.fund_codes:
        fund_codes_arg = [c.strip() for c in args.fund_codes.split(",") if c.strip()]

    download_dividends(
        out_root=args.out_root,
        fund_codes=fund_codes_arg,
        sleep_sec=args.sleep_sec,
        force=args.force,
    )
