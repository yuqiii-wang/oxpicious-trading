"""Download the 新闻发布 (press releases) news title list from ndrc.gov.cn.

The list page https://www.ndrc.gov.cn/xwdt/xwfb/ uses classic HTML pagination
(``index.html`` = page 1, ``index_1.html`` = page 2, …, ~40 pages). Each list
item is an ``<a>`` whose parent carries the publish date (``YYYY/MM/DD``). We
paginate newest-to-oldest until the oldest date on a page falls before
``--start-date`` (default 2020-01-01) or the page returns no items, then write
the titles to CSV.

Only the list is scraped — detail links are NOT followed (no per-article
crawling). Anti-bot, CSV writing, and caching are provided by the shared
internal :mod:`downloads.macro.gov.main_gov` module.

Usage::

    python -m downloads.macro.gov.ndrc                       # backfill to 2020-01-01
    python -m downloads.macro.gov.ndrc --start-date 2024-01-01
    python -m downloads.macro.gov.ndrc --force                # re-fetch today's pages
"""
from __future__ import annotations

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

from downloads._common import COMMON_BASE_HEADERS, DEFAULT_TIMEOUT  # noqa: E402
from downloads.macro.gov.main_gov import (  # noqa: E402
    SourceConfig,
    parse_date_str,
    run_cli,
)
from downloads.macro.gov.main_gov import setup_logger  # noqa: E402

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
NDRC_LIST_BASE = "https://www.ndrc.gov.cn/xwdt/xwfb/"
NDRC_LIST_PAGE1 = NDRC_LIST_BASE  # page 1 is the bare directory URL
NDRC_HOST = "https://www.ndrc.gov.cn"

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
# Fetcher
# ----------------------------------------------------------------------------
def fetch_ndrc_pages(
    session: Any,
    proxy: Any,
    config: SourceConfig,
) -> Optional[List[Dict[str, Any]]]:
    """Paginate the NDRC list newest-to-oldest and accumulate raw items.

    Stops when a page returns no items, the host is blocked, or the oldest
    date on a page is on/before the backfill floor. The floor is read from
    the orchestrator via the cached-file convention is not applicable here, so
    we stop at the earliest date available (the site goes back to ~2017, well
    past the default 2020-01-01 floor); the orchestrator's parse_fn + filter
    enforces the precise floor afterwards.

    Returns the accumulated raw items (newest-first across pages), or None on
    hard failure of the first page.
    """
    headers = dict(COMMON_BASE_HEADERS)
    headers["Referer"] = config.list_url

    all_items: List[Dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        if proxy.is_blocked(NDRC_HOST):
            logger.warning("  host blocked — stopping pagination at page %d", page)
            break

        url = _page_url(page)
        logger.info("[%s] page %d (%s)", config.name, page, url)
        resp = proxy.get(
            session, url,
            headers=headers, timeout=DEFAULT_TIMEOUT,
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

        # Stop once we've gone past the default 2020-01-01 floor — the site
        # only has ~40 pages so this naturally bounds the sweep. Using the
        # DEFAULT_START_DATE here keeps the fetcher self-contained; the
        # orchestrator re-applies the user's --start-date filter afterwards.
        from downloads._common import DEFAULT_START_DATE as _FLOOR
        floor = parse_date_str(_FLOOR)
        if oldest is not None and floor is not None and oldest <= floor:
            logger.info("  reached %s floor on page %d -> stopping", _FLOOR, page)
            break

    logger.info("[%s] fetched %d items across pages", config.name, len(all_items))
    return all_items


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
# CLI
# ----------------------------------------------------------------------------
def main() -> None:
    run_cli(CONFIG, fetch_ndrc_pages, parse_ndrc_items,
            "Download ndrc.gov.cn 新闻发布 news title list to CSV")


if __name__ == "__main__":
    main()
