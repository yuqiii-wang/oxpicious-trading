"""Download huatai-pb.com fund intro (基金概况) for the firm's listed ETFs.

The 基金概况 tab is filled client-side from ``var FundArr = [...]`` embedded
in ``/common/index.html.js`` (one GET covers every fund of the firm — see
``_endpoints`` study notes; the fetch itself stays whole-family, like the
daily trend). This module fetches that JS once, parses FundArr and writes
the curated ACTIVE ETF rows only (ACTIVE_ETF_CODES in ``_endpoints`` —
the issuer's actively-managed ETFs, 2026-11-14 decision); ``--all-funds``
widens back to every fund of the family:

  ``temps/huatai_pb_intro/raw/huatai_pb_common_<YYYYMMDD>.js``
      raw JS archive
  ``temps/huatai_pb_intro/huatai_pb_intro_<YYYYMMDD>.csv``
      one row per ETF fund — INTRO_COLS schema

Quarter-start refresh (quarterly cadence since 2026-11 — the
downloads_quarterly tier):
  One GET covers the whole family, so the run only needs one snapshot
  per quarter. When a valid ``huatai_pb_intro_<YYYYMMDD>.csv`` from the
  CURRENT quarter already exists, the run is a no-op; the first run of
  a quarter-start month (Jan/Apr/Jul/Oct) re-fetches. Use ``--force``
  to re-fetch on any day for testing.

CSV schema (``code, name, full_name, fund_type, company, manager, trustee,
setup_date, risk_level, benchmark, invest_target``): code is the BARE
6-digit ETF code (no exchange suffix — 2026-09-30 convention); manager is
基金经理 (brokername) while company is the fund manager firm (managerName).
Fund intro has no builds/etf consumer yet — reference metadata.

The entry point is the :class:`HuataiPbIntroDownload` DownloadJob — the
former ``__main__`` body is its ``run()`` and the ``download_intro``
orchestration function became the method of the same name (same signature
and body); ``fund_arr_to_frame`` and the constants stay module-level.

Usage:
  python -m downloads.etf.huatai_pb.intro
  python -m downloads.etf.huatai_pb.intro --all-funds   (include non-ETF funds)
  python -m downloads.etf.huatai_pb.intro --force
"""
from __future__ import annotations

import argparse
from datetime import date
from typing import Dict, List, Optional, Tuple

import pandas as pd

from downloads._common import (
    DownloadJob,
    RunStats,
    build_default_session,
    is_valid_file,
    resolve_out_dir,
    service_urls,
    setup_logger,
)
from downloads._common.monthly import current_quarter_csv
from downloads.etf.huatai_pb._endpoints import (
    ACTIVE_ETF_CODES,
    INTRO_FIELDS,
    build_proxy,
    parse_fund_arr,
)

logger = setup_logger("huatai_pb_intro")

SERVICE = "downloads.etf.huatai_pb.intro"

# Endpoint URLs — the service's external_request_config JSON row (urls
# object) is the single source; a missing role raises at the point of use.
URLS = service_urls(SERVICE)
COMMON_JS_URL = URLS["common_js"]

CSV_ENCODING = "utf-8-sig"

# INTRO_FIELDS maps FundArr keys → these CSV columns (order = column order).
INTRO_COLS: List[str] = list(INTRO_FIELDS.values())
# FundArr key of the bare fund code.
CODE_KEY = "fundcode"


def fund_arr_to_frame(
    funds: List[Dict[str, str]],
    *,
    codes: Optional[Tuple[str, ...]] = ACTIVE_ETF_CODES,
) -> pd.DataFrame:
    """FundArr entries → intro frame with BARE 6-digit codes.

    ``codes`` filters the frame to that curated set (the default: the
    ACTIVE ETF registry); ``None`` keeps every entry of the family.
    """
    if codes is not None:
        wanted = set(codes)
        funds = [f for f in funds if str(f.get(CODE_KEY, "")).strip() in wanted]
    rows: List[Dict[str, str]] = []
    for f in funds:
        row: Dict[str, str] = {}
        for src_key, col in INTRO_FIELDS.items():
            row[col] = f.get(src_key, "")
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=INTRO_COLS)
    return pd.DataFrame(rows, columns=INTRO_COLS).sort_values("code").reset_index(drop=True)


class HuataiPbIntroDownload(DownloadJob):
    """DownloadJob for the huatai-pb.com fund intro fetch cycle — the former
    ``__main__`` body is ``run()`` and the ``download_intro`` function is
    the method of the same name (same signature and body verbatim);
    pacing / out_root stay per-call arguments."""

    description = "Download huatai-pb.com fund intro (基金概况) from FundArr."
    component = "huatai_pb_intro"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--out-root", type=str, default=None,
                        help="Alternative output root directory")
        parser.add_argument("--sleep-sec", type=float, default=None,
                        help="Sleep seconds between requests "
                             f"(default: external_request_config JSON for {SERVICE})")
        parser.add_argument("--all-funds", action="store_true",
                        help="Write every fund of the family (default: the curated "
                             "active-ETF registry — see _endpoints.ACTIVE_ETF_CODES).")
        parser.add_argument("--force", action="store_true",
                        help="Re-fetch even when a current-quarter intro file exists.")

    def download_intro(
        self,
        *,
        out_root: str | None = None,
        sleep_sec: float | None = None,
        all_funds: bool = False,
        force: bool = False,
    ) -> Dict[str, object]:
        out_dir = resolve_out_dir("", "huatai_pb_intro", out_root)
        raw_dir = out_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        today = date.today()
        out_path = out_dir / f"huatai_pb_intro_{today:%Y%m%d}.csv"

        # Quarterly guard: a valid current-quarter intro snapshot skips the
        # run (the first run of a quarter-start month fetches, later runs
        # in the same quarter are no-ops); --force bypasses.
        if not force:
            cached = current_quarter_csv(out_dir, "huatai_pb_intro_*.csv", today)
            if cached is not None and is_valid_file(cached, min_bytes=256):
                logger.info("[intro] current-quarter file exists: %s (use --force to re-fetch)",
                            cached.name)
                return {"out_path": str(cached), "status": "cached"}

        session = build_default_session()
        proxy = build_proxy(SERVICE, sleep_sec)
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

        frame = fund_arr_to_frame(funds, codes=None if all_funds else ACTIVE_ETF_CODES)
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

    def run(self) -> None:
        self.download_intro(
            out_root=self.args.out_root,
            sleep_sec=self.args.sleep_sec,
            all_funds=self.args.all_funds,
            force=self.args.force,
        )


if __name__ == "__main__":
    HuataiPbIntroDownload().execute()
