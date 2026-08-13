"""Download gov-family policy news title lists + crawl matched article details.

This is the single entry point for the gov.cn macro-news pipeline. Running
``python -m downloads.macro.gov.news`` executes three internal steps in order,
reusing the same ``--start-date`` / ``--sleep-sec`` / ``--force`` / ``--out-root``
flags for all of them (mirroring the convention used by ``pboc.repo_news`` and
``analyze.industry_sentiments``, where internal steps share one CLI surface):

  1. **gov.news** — fetch the 政策解读 title list from gov.cn (single
     ``ZCJD_QZ.json`` fetch; full backfill to 2020-01-01 in one shot) and
     write ``temps/gov_news/gov_news_titles.csv``.
  2. **gov.ndrc** — paginate the ndrc.gov.cn 新闻发布 list (HTML pagination,
     stops at the start-date floor) and write ``temps/ndrc_news/ndrc_news_titles.csv``.
  3. **gov.articles** — scan the gov.news titles CSV against
     ``downloads/macro/gov/keywords.json`` (reverse-map keyword matching),
     crawl matched article detail pages (anti-bot + sleep, .md caching), and
     write ``temps/gov_news/articles_index.csv`` + per-article ``.md`` files.

Each step can be skipped via ``--skip-news`` / ``--skip-ndrc`` / ``--skip-articles``.
``--max-articles N`` caps the article crawl (for testing).

Usage::

    python -m downloads.macro.gov.news                          # full pipeline
    python -m downloads.macro.gov.news --start-date 2024-01-01
    python -m downloads.macro.gov.news --skip-articles          # titles only
    python -m downloads.macro.gov.news --max-articles 5         # test crawl
    python -m downloads.macro.gov.news --force                  # re-fetch all
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make the project root importable when this module is executed directly.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from downloads._common.core import COMMON_BASE_HEADERS, DEFAULT_START_DATE, LONG_SLEEP_INTERVAL  # noqa: E402
from downloads.macro.gov.main_gov import (  # noqa: E402
    SourceConfig,
    download_source,
    parse_date_str,
    setup_logger,
)
from downloads.macro.gov.ndrc import (  # noqa: E402
    CONFIG as NDRC_CONFIG,
    fetch_ndrc_pages,
    parse_ndrc_items,
)
from downloads.macro.gov.articles import download_articles  # noqa: E402

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
GOV_JIEDU_LIST_URL = "https://www.gov.cn/zhengce/jiedu/index.htm"
GOV_JIEDU_JSON_URL = "https://www.gov.cn/zhengce/jiedu/ZCJD_QZ.json"

CONFIG = SourceConfig(
    name="gov_news",
    list_url=GOV_JIEDU_LIST_URL,
    out_dirname="gov_news",
    csv_filename="gov_news_titles.csv",
)

logger = setup_logger("gov_news")


# ----------------------------------------------------------------------------
# Fetcher
# ----------------------------------------------------------------------------
def fetch_gov_json(session: Any, proxy: Any, config: SourceConfig) -> Optional[List[Dict[str, Any]]]:
    """Fetch the ``ZCJD_QZ.json`` title list via the shared anti-bot proxy.

    Returns the parsed list of items, or None on hard failure. Retries on
    transient network errors with exponential backoff; the proxy handles
    browser-fingerprint rotation, the ``random`` query param, host-blocking
    detection, and the inter-request sleep cadence.
    """
    headers = dict(COMMON_BASE_HEADERS)
    headers["Referer"] = config.list_url

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        if proxy.is_blocked(GOV_JIEDU_JSON_URL):
            logger.error("  host blocked, aborting JSON fetch")
            return None
        resp = proxy.get(
            session, GOV_JIEDU_JSON_URL,
            headers=headers, logger=logger, log_tag="  ",
        )
        if resp is None:
            logger.warning("  JSON fetch failed (attempt %d/%d)", attempt, max_retries)
            time.sleep(min(2 ** attempt, 30))
            continue
        try:
            data = resp.json()
        except ValueError as e:
            logger.error("  JSON parse error: %s (body: %s)", e, resp.text[:200])
            return None
        if isinstance(data, list):
            return data
        logger.error("  unexpected JSON shape: %s", type(data).__name__)
        return None

    logger.error("  exhausted retries for JSON fetch")
    return None


# ----------------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------------
def parse_gov_items(raw_items: List[Dict[str, Any]], start: date) -> List[Dict[str, str]]:
    """Filter *raw_items* to those on/after *start*, returning CSV rows.

    Each row is ``{"pub_date", "title", "url"}``. Items with an unparseable
    date or empty title are dropped. The orchestrator dedupes by URL and sorts.
    """
    rows: List[Dict[str, str]] = []
    for it in raw_items:
        title = (it.get("TITLE") or "").strip()
        url = (it.get("URL") or "").strip()
        d = parse_date_str(it.get("DOCRELPUBTIME"))
        if not title or d is None:
            continue
        rows.append({
            "pub_date": d.strftime("%Y-%m-%d"),
            "title": title,
            "url": url,
        })
    return rows


# ----------------------------------------------------------------------------
# CLI — single entry point orchestrating gov.news -> gov.ndrc -> gov.articles
# ----------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="gov.cn macro-news pipeline: gov.news + gov.ndrc title lists + gov.articles detail crawl",
    )
    parser.add_argument("--start-date", type=str, default=None,
                        help=f"Floor date YYYY-MM-DD. Default: {DEFAULT_START_DATE}")
    parser.add_argument("--sleep-sec", type=float, default=LONG_SLEEP_INTERVAL,
                        help=f"Anti-bot sleep between requests (s). Default: {LONG_SLEEP_INTERVAL}")
    parser.add_argument("--out-root", type=str, default=None,
                        help="Output root dir. Default: <project>/temps/<source>")
    parser.add_argument("--force", action="store_true",
                        help="Force re-fetch of today's raw snapshots / all article detail pages.")
    parser.add_argument("--skip-news", action="store_true",
                        help="Skip the gov.cn 政策解读 title-list step.")
    parser.add_argument("--skip-ndrc", action="store_true",
                        help="Skip the ndrc.gov.cn 新闻发布 title-list step.")
    parser.add_argument("--skip-articles", action="store_true",
                        help="Skip the gov.cn article detail-crawl step.")
    parser.add_argument("--max-articles", type=int, default=None,
                        help="Cap article detail fetches (for testing). Default: no cap.")
    args = parser.parse_args()

    summaries: Dict[str, Any] = {}
    gov_news_csv_path: Optional[str] = None

    # --- Step 1: gov.cn 政策解读 title list ---
    if not args.skip_news:
        logger.info("=== Step 1/3: gov.cn 政策解读 title list ===")
        s1 = download_source(
            CONFIG, fetch_gov_json, parse_gov_items,
            out_root=args.out_root,
            start_date=args.start_date,
            sleep_sec=args.sleep_sec,
            force=args.force,
        )
        summaries["gov_news"] = s1
        if not s1.get("failed"):
            gov_news_csv_path = s1.get("csv_path")

    # --- Step 2: ndrc.gov.cn 新闻发布 title list ---
    if not args.skip_ndrc:
        logger.info("=== Step 2/3: ndrc.gov.cn 新闻发布 title list ===")
        s2 = download_source(
            NDRC_CONFIG, fetch_ndrc_pages, parse_ndrc_items,
            out_root=args.out_root,
            start_date=args.start_date,
            sleep_sec=args.sleep_sec,
            force=args.force,
        )
        summaries["gov_ndrc"] = s2

    # --- Step 3: gov.cn article detail crawl (uses gov_news titles CSV) ---
    if not args.skip_articles:
        logger.info("=== Step 3/3: gov.cn article detail crawl ===")
        if gov_news_csv_path is None:
            # Fallback: derive the default path from out_root (titles CSV may
            # already exist from a prior run even if step 1 was skipped).
            from downloads.macro.gov.main_gov import resolve_out_dir as _resolve
            out_dir = _resolve(str(Path(__file__).resolve()), CONFIG.out_dirname, args.out_root)
            gov_news_csv_path = str(out_dir / CONFIG.csv_filename)
            logger.info("  step 1 skipped — using existing titles CSV: %s", gov_news_csv_path)
        s3 = download_articles(
            out_root=args.out_root,
            start_date=args.start_date,
            sleep_sec=args.sleep_sec,
            max_articles=args.max_articles,
            force=args.force,
            titles_csv=gov_news_csv_path,
        )
        summaries["gov_articles"] = s3

    print(summaries)


if __name__ == "__main__":
    main()
