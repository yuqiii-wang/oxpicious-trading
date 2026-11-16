"""Download E Fund fund intro (基金概况) for the listed-ETF universe.

The per-fund detail page ``https://www.efundcf.com.cn/fund/<p3>/<code>.shtml``
renders the 基本信息 table server-side (``_endpoints`` study notes) — one
GET per fund, parsed by regex (no XHR carries it). This module fetches
each page, archives the raw HTML and writes:

  ``temps/efunds_intro/raw/efunds_page_<code>_<YYYYMMDD>.html``
      raw page archive (per fund, per run date)
  ``temps/efunds_intro/efunds_intro_<YYYYMMDD>.csv``
      one row per fund — INTRO_COLS schema

CSV schema (INTRO_COLS — the huatai_pb intro vocabulary first, then the
E Fund page extras):
  code, name, full_name, fund_type, company, manager, trustee,
  setup_date, risk_level, benchmark, invest_target,   # huatai-compatible
  listed_name, exchange, category, sub_category, scale, target_index,
  invest_scope, invest_ratio, risk_feature

* code — BARE 6-digit (no exchange suffix — the 2026-09-30 convention).
* risk_level — fundlist ``properties.riskLevel`` (the page renders the
  chip client-side only for some funds); name/full_name/fund_type/
  manager/setup_date page cells win, fundlist fields fill gaps.
* category / sub_category — the baseinfo-table's data-level1desc /
  data-level2desc attrs (e.g. 被动股票指数 / 被动股票指数-ETF).

Default universe = the fundlist listed-ETF set (51x/56x/58x/158/159)
intersected with the curated ACTIVE ETF registry
(database/sec_statics/active_funds/e_fund.json — 7 funds at 2026-11-14;
only actively-managed ETFs get per-fund data); ``--all-funds`` widens to
every non-money fund, ``--fund-codes`` filters explicitly.

Quarter-start refresh (quarterly cadence since 2026-11 — the
downloads_quarterly tier): when a valid ``efunds_intro_<YYYYMMDD>.csv``
from the CURRENT quarter already exists, the run is a no-op; the first
run of a quarter-start month (Jan/Apr/Jul/Oct) re-fetches the per-fund
pages. Use ``--force`` to re-fetch on any day for testing.

Anti-bot
cooldown contract: every
per-fund GET is preceded by ``await_host_cooldown`` (see ``_endpoints``)
— a blocked host is waited out, never hammered, and the run aborts
cleanly past the wait budget (a re-run resumes; the quarter's CSV is only
written on a full pass).

Fund intro has no builds consumer yet — reference metadata.

Usage:
  python -m downloads.etf.efunds.intro
  python -m downloads.etf.efunds.intro --all-funds   (every non-money fund)
  python -m downloads.etf.efunds.intro --fund-codes 159915,510310
  python -m downloads.etf.efunds.intro --force
"""
from __future__ import annotations

import argparse
from datetime import date
from typing import Any, Dict, List, Optional

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
from downloads._common.monthly import current_quarter_csv
from downloads.etf.efunds._endpoints import (
    EFundsEndpoints,
    await_host_cooldown,
    filter_universe_to_active,
    parse_fundlist_etf_universe,
    parse_fundlist_intro_fallbacks,
    parse_fundlist_nav_rows,
    parse_intro_html,
)

logger = setup_logger("efunds_intro")

SERVICE = "downloads.etf.efunds.intro"

# Endpoint URLs — the service's external_request_config JSON row (urls
# object) is the single source; a missing role raises at the point of use.
URLS = service_urls(SERVICE)
FUND_PAGE_URL = URLS["fund_page"]

CSV_ENCODING = "utf-8-sig"

INTRO_COLS: List[str] = [
    # huatai_pb INTRO_FIELDS-compatible block first
    "code", "name", "full_name", "fund_type", "company", "manager",
    "trustee", "setup_date", "risk_level", "benchmark", "invest_target",
    # E Fund page extras
    "listed_name", "exchange", "category", "sub_category", "scale",
    "target_index", "invest_scope", "invest_ratio", "risk_feature",
]

# Page fields that a fundlist fallback may fill when the page row is
# absent (page cell wins; fallback only for '' cells).
_PAGE_FALLBACK_FIELDS = (
    "name", "full_name", "fund_type", "manager", "setup_date",
)


class EfundsIntroDownload(DownloadJob):
    """DownloadJob for the E Fund fund intro fetch cycle — one page GET
    per fund of the universe, cooldown-guarded."""

    description = ("Download E Fund fund intro (基金概况) from the "
                   "per-fund detail pages.")
    component = "efunds_intro"

    def __init__(self, args=None) -> None:
        super().__init__(args)
        self.endpoints = EFundsEndpoints()

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--out-root", type=str, default=None,
                        help="Alternative output root directory")
        parser.add_argument("--sleep-sec", type=float, default=None,
                        help="Sleep seconds between requests "
                             f"(default: external_request_config JSON for {SERVICE})")
        parser.add_argument("--fund-codes", type=str, default=None,
                        help="Comma-separated fund codes (default: the curated "
                             "active-ETF registry ∩ the fundlist listed-ETF set)")
        parser.add_argument("--all-funds", action="store_true",
                        help="Widen to every non-money fund in fundlist.json "
                             "(default: listed ETF codes only).")
        parser.add_argument("--force", action="store_true",
                        help="Re-fetch even when a current-quarter intro file exists.")

    def apply_args(self) -> None:
        fund_codes_arg: Optional[List[str]] = None
        if self.args.fund_codes:
            fund_codes_arg = [c.strip() for c in self.args.fund_codes.split(",") if c.strip()]
        self.args.fund_codes = fund_codes_arg

    def _resolve_universe(
        self, session: requests.Session, proxy: AntiBotProxy, *,
        fund_codes: Optional[List[str]], all_funds: bool,
    ) -> tuple[Optional[List[Dict[str, str]]], Optional[Dict[str, Dict[str, str]]]]:
        """(universe, fundlist fallbacks) from one fundlist GET — None
        universe means the fetch failed and the run cannot proceed."""
        payload = self.endpoints.fetch_fundlist(
            session, proxy, service=SERVICE, logger=logger)
        if payload is None:
            logger.error("[intro] fundlist fetch failed — cannot resolve the universe")
            return None, None
        fallbacks = parse_fundlist_intro_fallbacks(payload)
        if all_funds:
            universe = [
                {"code": r["code"], "name": r["name"], "setup_date":
                 fallbacks.get(r["code"], {}).get("setup_date", "")}
                for r in parse_fundlist_nav_rows(payload)
            ]
        else:
            universe = filter_universe_to_active(
                parse_fundlist_etf_universe(payload), logger)
        if fund_codes:
            wanted = set(fund_codes)
            universe = [f for f in universe if f["code"] in wanted]
        return universe, fallbacks

    def download_intro(
        self,
        *,
        out_root: str | None = None,
        sleep_sec: float | None = None,
        fund_codes: Optional[List[str]] = None,
        all_funds: bool = False,
        force: bool = False,
    ) -> Dict[str, Any]:
        out_dir = resolve_out_dir("", "efunds_intro", out_root)
        raw_dir = out_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        today = date.today()
        out_path = out_dir / f"efunds_intro_{today:%Y%m%d}.csv"

        # Quarterly guard: a valid current-quarter intro snapshot skips the
        # run (the first run of a quarter-start month fetches the per-fund
        # pages, later runs in the same quarter are no-ops); --force bypasses.
        if not force:
            cached = current_quarter_csv(out_dir, "efunds_intro_*.csv", today)
            if cached is not None and is_valid_file(cached, min_bytes=256):
                logger.info("[intro] current-quarter file exists: %s (use --force to re-fetch)",
                            cached.name)
                return {"out_path": str(cached), "status": "cached"}

        session = build_default_session()
        proxy = self.endpoints.build_proxy(SERVICE, sleep_sec)
        stats = RunStats()

        universe, fallbacks = self._resolve_universe(
            session, proxy, fund_codes=fund_codes, all_funds=all_funds)
        if universe is None or fallbacks is None:
            stats.failed += 1
            return stats.to_dict(out_dir=str(out_dir))
        if not universe:
            logger.warning("[intro] empty universe (filter matched nothing)")
            stats.empty += 1
            return stats.to_dict(out_dir=str(out_dir), funds=0)

        rows: List[Dict[str, str]] = []
        for fund in universe:
            code = fund["code"]
            if not await_host_cooldown(proxy, FUND_PAGE_URL, logger):
                summary = stats.to_dict(
                    out_dir=str(out_dir), funds=len(universe),
                    status="blocked", fund=code)
                logger.warning("[intro] aborted (host blocked): %s", summary)
                return summary
            html_text = self.endpoints.fetch_fund_intro_html(
                session, proxy, code, service=SERVICE, logger=logger)
            if html_text is None:
                stats.failed += 1
                logger.warning("[intro %s] page fetch failed — fallback row only",
                               code)
                page_fields: Dict[str, str] = {}
            else:
                raw_path = raw_dir / f"efunds_page_{code}_{today:%Y%m%d}.html"
                raw_path.write_text(html_text, encoding="utf-8", errors="replace")
                page_fields = parse_intro_html(html_text)
                if not page_fields:
                    stats.failed += 1
                    logger.warning("[intro %s] baseinfo-table not parsed", code)

            fb = fallbacks.get(code, {})
            row: Dict[str, str] = {col: "" for col in INTRO_COLS}
            for col in INTRO_COLS:
                if col == "code":
                    continue  # set from the universe below — never page/fb-filled
                row[col] = page_fields.get(col, "") or (
                    fb.get(col, "") if col in _PAGE_FALLBACK_FIELDS else "")
            row["code"] = code
            row["risk_level"] = fb.get("risk_level", "")
            if not page_fields and not fb:
                continue  # unknown code entirely — no row
            rows.append(row)

        if not rows:
            stats.empty += 1
            return stats.to_dict(out_dir=str(out_dir), funds=len(universe))

        frame = pd.DataFrame(rows, columns=INTRO_COLS)
        frame = frame.drop_duplicates(subset=["code"], keep="first")
        frame = frame.sort_values("code").reset_index(drop=True)
        frame.to_csv(out_path, index=False, encoding=CSV_ENCODING)
        stats.downloaded += 1
        logger.info("[intro] saved %s (%d funds of %d in universe)",
                    out_path.name, len(frame), len(universe))
        return stats.to_dict(out_path=str(out_path), out_dir=str(out_dir),
                             funds=len(frame), universe=len(universe))

    def run(self) -> None:
        self.download_intro(
            out_root=self.args.out_root,
            sleep_sec=self.args.sleep_sec,
            fund_codes=self.args.fund_codes,
            all_funds=self.args.all_funds,
            force=self.args.force,
        )


if __name__ == "__main__":
    EfundsIntroDownload().execute()
