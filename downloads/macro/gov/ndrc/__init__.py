"""Download the 新闻发布 (press releases) news title list from ndrc.gov.cn.

The list page https://www.ndrc.gov.cn/xwdt/xwfb/ uses classic HTML pagination
(``index.html`` = page 1, ``index_1.html`` = page 2, …, ~40 pages). Each list
item is an ``<a>`` whose parent carries the publish date (``YYYY/MM/DD``). By
default the run is incremental: pagination (newest-to-oldest) stops as soon as
the last date already available in the titles CSV is reached. ``--force`` does
a full backfill to the start-date floor (default 2020-01-01). Either way the
run ends when a page returns no items. Titles are then written to CSV.

Only the list is scraped — detail links are NOT followed (no per-article
crawling). Anti-bot, CSV writing, and caching are provided by the shared
internal :mod:`downloads.macro.gov.main_gov` module.

The entry point is the :class:`GovNdrcDownload` DownloadJob — the former
``main()``/``run_cli`` CLI surface became its ``add_arguments``/``run``
(byte-identical) and the pagination fetch moved onto the
:class:`GovNdrcFetcher` class (body unchanged); the pure URL/HTML/row
helpers stay module-level.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

# Make the project root importable when this module is executed directly.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from bs4 import BeautifulSoup  # noqa: E402

from downloads._common import (  # noqa: E402
    COMMON_BASE_HEADERS,
    DEFAULT_START_DATE,
    DownloadJob,
    service_urls,
)
from downloads.macro.gov.main_gov import (  # noqa: E402
    SourceConfig,
    download_source,
    parse_date_str,
    setup_logger,
)

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
SERVICE = "downloads.macro.gov.ndrc"

# Endpoint URLs — the service's external_request_config JSON row (urls
# object) is the single source; a missing role raises at the point of use.
URLS = service_urls(SERVICE)
NDRC_LIST_BASE = URLS["ndrc_list_base"]
NDRC_LIST_PAGE1 = NDRC_LIST_BASE  # page 1 is the bare directory URL
NDRC_HOST = URLS["ndrc_host"]

MAX_PAGES = 200  # safety cap; the site currently has ~40 pages

CONFIG = SourceConfig(
    name="ndrc_news",
    list_url=NDRC_LIST_BASE,
    out_dirname="ndrc_news",
    csv_filename="ndrc_news_titles.csv",
)

logger = setup_logger("ndrc_news")

# hrefs look like "./202607/t20260731_1406862.html"; require the "t20" detail slug.
_RE_DETAIL_HREF = re.compile(r"t20\d{6}_\d+\.html")
# Publish date in the parent element's text, e.g. "2026/07/31".
_RE_DATE = re.compile(r"(20\d{2}[/\-]\d{1,2}[/\-]\d{1,2})")


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def _page_url(page: int) -> str:
    """Return the list URL for *page* (1-indexed).

    Page 1 is the bare directory; page N>=2 uses ``index_{N-1}.html``.
    """
    if page <= 1:
        return NDRC_LIST_PAGE1
    return f"{NDRC_LIST_BASE}index_{page - 1}.html"


def _absolutize(href: str) -> str:
    """Resolve a relative list href against the list base URL."""
    if href.startswith("http"):
        return href
    return urljoin(NDRC_LIST_BASE, href)


def parse_list_html(html: str) -> List[Dict[str, Any]]:
    """Parse one NDRC list page into raw items ``{"title","url","pub_date"}``.

    Each item is an ``<a>`` whose href matches the detail-slug pattern; the
    publish date is read from the anchor's parent element text. Items without
    a parseable date or with an empty title are dropped.
    """
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict[str, Any]] = []
    seen: set = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not _RE_DETAIL_HREF.search(href):
            continue
        title = a.get_text(strip=True)
        if not title:
            continue
        url = _absolutize(href)
        if url in seen:
            continue
        parent_text = a.parent.get_text(" ", strip=True) if a.parent else ""
        m = _RE_DATE.search(parent_text)
        pub_date_raw = m.group(1) if m else ""
        d = parse_date_str(pub_date_raw)
        if d is None:
            continue
        seen.add(url)
        items.append({
            "title": title,
            "url": url,
            "pub_date": d.strftime("%Y-%m-%d"),
        })
    return items


# ----------------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------------
def parse_ndrc_items(raw_items: List[Dict[str, Any]], start: date) -> List[Dict[str, str]]:
    """Project raw items onto CSV columns. The orchestrator filters/sorts."""
    return [
        {
            "pub_date": it.get("pub_date", ""),
            "title": it.get("title", ""),
            "url": it.get("url", ""),
        }
        for it in raw_items
    ]


# ----------------------------------------------------------------------------
# Fetcher
# ----------------------------------------------------------------------------
class GovNdrcFetcher:
    """The ndrc.gov.cn 新闻发布 pagination fetcher — the former
    ``fetch_ndrc_pages`` function as a method with unchanged body. Stateless:
    session and proxy are built per run by the orchestrator."""

    def fetch_ndrc_pages(
        self,
        session: Any,
        proxy: Any,
        config: SourceConfig,
        floor: Optional[date] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Paginate the NDRC list newest-to-oldest and accumulate raw items.

        Stops when a page returns no items, the host is blocked, or the oldest
        date on a page is on/before the stop date: *floor* (incremental mode —
        the last pub_date already available in the titles CSV) when given, else
        the 2020-01-01 backfill floor. The stop page may straddle the boundary;
        the orchestrator re-filters and dedupes against the existing CSV.
        """
        headers = dict(COMMON_BASE_HEADERS)
        headers["Referer"] = config.list_url

        stop_floor = floor if floor is not None else parse_date_str(DEFAULT_START_DATE)

        all_items: List[Dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            if proxy.is_blocked(NDRC_HOST):
                logger.warning("  host blocked — stopping pagination at page %d", page)
                break

            url = _page_url(page)
            logger.info("[%s] page %d (%s)", config.name, page, url)
            resp = proxy.get(
                session, url,
                headers=headers,
                logger=logger, log_tag="  ",
            )
            if resp is None:
                # 4xx / network failure — proxy already logged. Page 1 failure is
                # a hard error; later pages just mean we've passed the end.
                if page == 1:
                    logger.error("  first page fetch failed; aborting")
                    return None
                logger.info("  page %d fetch failed -> end of pagination", page)
                break

            resp.encoding = resp.apparent_encoding or "utf-8"
            items = parse_list_html(resp.text)
            if not items:
                logger.info("  page %d returned no items -> end of pagination", page)
                break

            all_items.extend(items)
            dates = [parse_date_str(it["pub_date"]) for it in items]
            dates = [d for d in dates if d is not None]
            oldest = min(dates) if dates else None
            newest = max(dates) if dates else None
            logger.info(
                "  page %d: %d items [%s .. %s]",
                page, len(items),
                oldest.strftime("%Y-%m-%d") if oldest else "-",
                newest.strftime("%Y-%m-%d") if newest else "-",
            )

            # Stop once we've reached the already-downloaded range (incremental)
            # or the backfill floor — pages are newest-first, so this bounds the
            # sweep. The stop page's tail is deduped/filtered by the orchestrator.
            if stop_floor is not None and oldest is not None and oldest <= stop_floor:
                logger.info("  reached last-available date %s on page %d -> stopping", stop_floor, page)
                break

        logger.info("[%s] fetched %d items across pages", config.name, len(all_items))
        return all_items


# ----------------------------------------------------------------------------
# CLI — DownloadJob entry (the former run_cli surface, byte-identical)
# ----------------------------------------------------------------------------
class GovNdrcDownload(DownloadJob):
    """DownloadJob for the ndrc.gov.cn 新闻发布 title list — the former
    ``main()``/``run_cli`` body is ``run()`` verbatim and the shared
    ``build_cli`` argument surface is ``add_arguments``; the pagination
    fetch is the GovNdrcFetcher held in ``__init__``."""

    description = "Download ndrc.gov.cn 新闻发布 news title list to CSV"
    component = "ndrc_news"

    def __init__(self) -> None:
        super().__init__()
        self.fetcher = GovNdrcFetcher()

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        # Byte-identical to main_gov.build_cli(service=SERVICE).
        parser.add_argument("--start-date", type=str, default=None,
                            help=f"Floor date YYYY-MM-DD. Default: {DEFAULT_START_DATE}")
        parser.add_argument("--sleep-sec", type=float, default=None,
                            help="Anti-bot sleep between requests (s). Default: "
                                 f"external_request_config JSON for {SERVICE}")
        parser.add_argument("--out-root", type=str, default=None,
                            help="Output root dir. Default: <project>/temps/<source>")
        parser.add_argument("--force", action="store_true",
                            help="Force re-fetch of today's raw snapshot even if cached.")

    def run(self) -> None:
        """One download cycle — the former ``run_cli`` body (argparse parsing
        is owned by the base template methods)."""
        summary = download_source(
            CONFIG, self.fetcher.fetch_ndrc_pages, parse_ndrc_items,
            out_root=self.args.out_root,
            start_date=self.args.start_date,
            service=SERVICE,
            sleep_sec=self.args.sleep_sec,
            force=self.args.force,
        )
        logger.info(summary)


if __name__ == "__main__":
    GovNdrcDownload().execute()
