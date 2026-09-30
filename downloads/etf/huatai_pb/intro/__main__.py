"""Download huatai-pb.com fund intro (基金概况) for the firm's listed ETFs.

The 基金概况 tab is filled client-side from ``var FundArr = [...]`` embedded
in ``/common/index.html.js`` (one GET covers every fund of the firm — see
``_endpoints`` study notes). This module fetches that JS once, parses
FundArr, filters it to exchange-listed ETF codes (51x/56x/58x SSE, 159 SZSE)
and writes:

  ``temps/huatai_pb_intro/raw/huatai_pb_common_<YYYYMMDD>.js``
      raw JS archive
  ``temps/huatai_pb_intro/huatai_pb_intro_<YYYYMMDD>.csv``
      one row per ETF fund — INTRO_COLS schema

CSV schema (``code, name, full_name, fund_type, company, manager, trustee,
setup_date, risk_level, benchmark, invest_target``): code is the BARE
6-digit ETF code (no exchange suffix — 2026-09-30 convention); manager is
基金经理 (brokername) while company is the fund manager firm (managerName).
Fund intro has no builds/etf consumer yet — reference metadata.

Usage:
  python -m downloads.etf.huatai_pb.intro
  python -m downloads.etf.huatai_pb.intro --all-funds   (include non-ETF funds)
  python -m downloads.etf.huatai_pb.intro --force
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
    COMMON_JS_URL,
    INTRO_FIELDS,
    build_proxy,
    is_listed_etf_code,
    parse_fund_arr,
)

logger = setup_logger("huatai_pb_intro")

CSV_ENCODING = "utf-8-sig"

# INTRO_FIELDS maps FundArr keys → these CSV columns (order = column order).
INTRO_COLS: List[str] = list(INTRO_FIELDS.values())
# FundArr key of the bare fund code.
CODE_KEY = "fundcode"


def fund_arr_to_frame(funds: List[Dict[str, str]], *, etf_only: bool) -> pd.DataFrame:
    """FundArr entries → intro frame with BARE 6-digit codes."""
    if etf_only:
        funds = [f for f in funds if is_listed_etf_code(f.get(CODE_KEY, ""))]
    rows: List[Dict[str, str]] = []
    for f in funds:
        row: Dict[str, str] = {}
        for src_key, col in INTRO_FIELDS.items():
            row[col] = f.get(src_key, "")
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=INTRO_COLS)
    return pd.DataFrame(rows, columns=INTRO_COLS).sort_values("code").reset_index(drop=True)


def download_intro(
    *,
    out_root: str | None = None,
    sleep_sec: float = DEFAULT_SLEEP_SEC,
    all_funds: bool = False,
    force: bool = False,
) -> Dict[str, object]:
    out_dir = resolve_out_dir("", "huatai_pb_intro", out_root)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    out_path = out_dir / f"huatai_pb_intro_{today:%Y%m%d}.csv"

    if not force and is_valid_file(out_path, min_bytes=256):
        logger.info("[intro] today's file exists: %s (use --force to re-fetch)",
                    out_path.name)
        return {"out_path": str(out_path), "status": "cached"}

    session = build_default_session()
    proxy = build_proxy(sleep_sec)
    stats = RunStats()

    resp = proxy.get(session, COMMON_JS_URL, headers=None, logger=logger,
                     log_tag="[intro]")
    if resp is None or resp.status_code != 200:
        logger.error("[intro] failed to fetch %s", COMMON_JS_URL)
        return stats.to_dict(out_dir=str(out_dir))

    raw_path = raw_dir / f"huatai_pb_common_{today:%Y%m%d}.js"
    raw_path.write_text(resp.text, encoding="utf-8", errors="replace")

    funds = parse_fund_arr(resp.text)
    if not funds:
        logger.error("[intro] FundArr parse yielded 0 funds")
        stats.failed += 1
        return stats.to_dict(out_dir=str(out_dir))

    frame = fund_arr_to_frame(funds, etf_only=not all_funds)
    if frame.empty:
        logger.warning("[intro] 0 funds after filter (all_funds=%s)", all_funds)
        stats.empty += 1
        return stats.to_dict(out_dir=str(out_dir))

    frame.to_csv(out_path, index=False, encoding=CSV_ENCODING)
    stats.downloaded += 1
    logger.info("[intro] saved %s (%d funds from %d FundArr entries)",
                out_path.name, len(frame), len(funds))
    return stats.to_dict(out_path=str(out_path), out_dir=str(out_dir),
                         funds_parsed=len(funds), funds_written=len(frame))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Download huatai-pb.com fund intro (基金概况) from FundArr."
    )
    ap.add_argument("--out-root", type=str, default=None,
                    help="Alternative output root directory")
    ap.add_argument("--sleep-sec", type=float, default=DEFAULT_SLEEP_SEC,
                    help=f"Sleep seconds between requests (default: {DEFAULT_SLEEP_SEC})")
    ap.add_argument("--all-funds", action="store_true",
                    help="Include non-ETF funds (default: listed ETF codes only).")
    ap.add_argument("--force", action="store_true",
                    help="Re-fetch even when today's intro file exists.")
    args = ap.parse_args()

    download_intro(
        out_root=args.out_root,
        sleep_sec=args.sleep_sec,
        all_funds=args.all_funds,
        force=args.force,
    )
