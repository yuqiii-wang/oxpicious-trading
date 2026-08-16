"""_scraper.py — PBoC statistics page parsing (year index → sub-pages → xls links).

Navigation flow:
  1. Central index (``/diaochatongjisi/116219/116319/index.html``) lists
     yearly archive pages — current year uses slug ``2026ntjsj``, past years
     use numeric IDs (``5570903`` for 2025, ``3959050`` for 2020, etc.).
  2. Each year index links to sub-pages:
       - 社会融资规模 (shrzgm) — AFRE Flow + Stock
       - 货币统计概览 (hbtjgl) — Official Reserve Assets, Depository Corps
         Survey, Overseas RMB Assets, etc.
     Current-year sub-page URLs: ``.../2026ntjsj/shrzgm/index.html``
     Past-year sub-page URLs: ``.../5570903/5570885/index.html`` (numeric IDs)
  3. Each sub-page has a table of rows; each row has a label and an
     ``<a href>`` xls/xlsx download link pointing to
     ``/diaochatongjisi/attachDir/YYYY/MM/<timestamp>.xlsx``.

This module discovers all URLs dynamically by parsing HTML — no hardcoded
sub-page IDs.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ._catalog import StatsItem, find_matching_item
from ._session import PBOC_BASE, CffiAntiBotSession


# Regex to extract year from link text like "2026年统计数据"
RE_YEAR_LINK = re.compile(r"(\d{4})\s*年\s*统计数据")

# Sub-page identification by link text
RE_SHRZGM_LINK = re.compile(r"社会融资规模|shrzgm", re.I)
RE_HBTJGL_LINK = re.compile(r"货币统计概览|Money and Banking|hbtjgl", re.I)

# xls/xlsx download link pattern
RE_XLS_HREF = re.compile(r"\.xls[x]?$", re.I)

# attachDir URL pattern (for extracting year/month from filename)
RE_ATTACH_DIR = re.compile(r"/attachDir/(\d{4})/(\d{2})/")


# ============================================================================
# Data classes
# ============================================================================
@dataclass
class YearArchive:
    """A year's archive page URL discovered from the central index."""
    year: int
    index_url: str


@dataclass
class SubPageLinks:
    """Discovered sub-page URLs for a single year."""
    year: int
    shrzgm_url: Optional[str] = None
    hbtjgl_url: Optional[str] = None


@dataclass
class DownloadLink:
    """A single xls download link found on a sub-page."""
    item: StatsItem
    year: int
    href: str
    row_text: str
    link_text: str
    # Year/month extracted from attachDir URL (publication date)
    pub_year: Optional[int] = None
    pub_month: Optional[int] = None
    ext: str = "xlsx"  # "xlsx" or "xls"

    def filename(self) -> str:
        """Generate output filename: pboc_stats_{slug}_{year}.{ext}"""
        return f"pboc_stats_{self.item.slug}_{self.year}.{self.ext}"

    def manifest_row(self) -> Dict[str, str]:
        """Return a dict suitable for CSV manifest output."""
        return {
            "year": str(self.year),
            "item_slug": self.item.slug,
            "cn_label": self.item.cn_label,
            "en_label": self.item.en_label,
            "page": self.item.page,
            "source_url": self.href,
            "pub_year": str(self.pub_year) if self.pub_year else "",
            "pub_month": str(self.pub_month) if self.pub_month else "",
            "filename": self.filename(),
            "row_text": self.row_text[:200],
        }


# ============================================================================
# Central index parsing — discover year archives
# ============================================================================
def parse_central_index(html: str, min_year: int = 2020, max_year: Optional[int] = None) -> List[YearArchive]:
    """Parse the central statistics index to discover year archive URLs.

    The page contains links like:
        [2026年统计数据] -> .../2026ntjsj/index.html
        [2025年统计数据] -> .../5570903/index.html

    Returns YearArchive objects sorted by year descending.
    """
    soup = BeautifulSoup(html, "html.parser")
    archives: Dict[int, str] = {}
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        m = RE_YEAR_LINK.search(text)
        if not m:
            continue
        year = int(m.group(1))
        if year < min_year:
            continue
        if max_year is not None and year > max_year:
            continue
        full_url = urljoin(PBOC_BASE + "/", href)
        # Keep the first occurrence (links can appear twice on the page)
        if year not in archives:
            archives[year] = full_url

    result = [YearArchive(year=y, index_url=u) for y, u in sorted(archives.items(), reverse=True)]
    return result


# ============================================================================
# Year index parsing — discover shrzgm and hbtjgl sub-pages
# ============================================================================
def parse_year_index(html: str, year: int, year_index_url: str) -> SubPageLinks:
    """Parse a year's index page to find shrzgm and hbtjgl sub-page URLs.

    Current-year pages use slug paths (``2026ntjsj/shrzgm/index.html``).
    Past-year pages use numeric IDs (``5570903/5570885/index.html``).
    We discover them by matching link text, not by guessing the URL.
    """
    soup = BeautifulSoup(html, "html.parser")
    links = SubPageLinks(year=year)

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        full_url = urljoin(year_index_url, href)

        if links.shrzgm_url is None and RE_SHRZGM_LINK.search(text):
            links.shrzgm_url = full_url
        if links.hbtjgl_url is None and RE_HBTJGL_LINK.search(text):
            links.hbtjgl_url = full_url

        if links.shrzgm_url and links.hbtjgl_url:
            break

    return links


# ============================================================================
# Sub-page parsing — extract xls download links for target items
# ============================================================================
def parse_sub_page(
    html: str,
    page: str,
    year: int,
    logger: Optional[logging.Logger] = None,
) -> List[DownloadLink]:
    """Parse a sub-page (shrzgm or hbtjgl) and extract download links for target items.

    The page has a table where each row contains:
      - A label cell (Chinese + English text)
      - Download links (htm / xls / pdf)

    We match each row against our target items and extract the xls href.
    """
    soup = BeautifulSoup(html, "html.parser")
    results: List[DownloadLink] = []
    matched_items: set = set()  # track which items we've already matched

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not RE_XLS_HREF.search(href):
            continue

        # Walk up to the containing <tr> for the row label
        tr = a.find_parent("tr")
        row_text = tr.get_text(" ", strip=True) if tr else ""
        # Clean up whitespace
        row_text = re.sub(r"\s+", " ", row_text).strip()

        if not row_text:
            continue

        # Match against target items for this page
        item = find_matching_item(row_text, page)
        if item is None or item.slug in matched_items:
            continue

        # Build full URL
        full_href = urljoin(PBOC_BASE + "/", href)

        # Extract publication year/month from attachDir URL
        pub_year, pub_month = None, None
        m = RE_ATTACH_DIR.search(full_href)
        if m:
            pub_year = int(m.group(1))
            pub_month = int(m.group(2))

        # Determine extension
        ext = "xlsx" if full_href.lower().endswith(".xlsx") else "xls"

        link_text = a.get_text(strip=True)
        dl = DownloadLink(
            item=item,
            year=year,
            href=full_href,
            row_text=row_text,
            link_text=link_text,
            pub_year=pub_year,
            pub_month=pub_month,
            ext=ext,
        )
        results.append(dl)
        matched_items.add(item.slug)

        if logger:
            logger.info(
                "  [match] %s <- %s (pub=%s-%s, ext=%s)",
                item.slug, row_text[:60], pub_year, pub_month, ext,
            )

    return results


# ============================================================================
# Full scraping pipeline
# ============================================================================
def discover_year_archives(
    session: CffiAntiBotSession,
    min_year: int,
    max_year: Optional[int],
    logger: logging.Logger,
) -> List[YearArchive]:
    """Fetch the central index and parse year archive URLs."""
    central_url = f"{PBOC_BASE}/diaochatongjisi/116219/116319/index.html"
    logger.info("[discover] fetching central index: %s", central_url)
    html = session.get_text(central_url)
    if html is None:
        logger.error("[discover] failed to fetch central index")
        return []
    archives = parse_central_index(html, min_year=min_year, max_year=max_year)
    logger.info("[discover] found %d year archives: %s",
                len(archives), [a.year for a in archives])
    return archives


def discover_sub_pages(
    session: CffiAntiBotSession,
    archive: YearArchive,
    logger: logging.Logger,
) -> SubPageLinks:
    """Fetch a year's index page and discover shrzgm/hbtjgl sub-page URLs."""
    logger.info("[discover] year %d index: %s", archive.year, archive.index_url)
    html = session.get_text(archive.index_url, referer=f"{PBOC_BASE}/diaochatongjisi/116219/116319/index.html")
    if html is None:
        logger.error("[discover] failed to fetch year %d index", archive.year)
        return SubPageLinks(year=archive.year)
    links = parse_year_index(html, archive.year, archive.index_url)
    logger.info("[discover] year %d: shrzgm=%s, hbtjgl=%s",
                archive.year,
                links.shrzgm_url or "(none)",
                links.hbtjgl_url or "(none)")
    return links


def scrape_sub_page(
    session: CffiAntiBotSession,
    page: str,
    year: int,
    sub_page_url: str,
    referer: str,
    logger: logging.Logger,
) -> List[DownloadLink]:
    """Fetch a sub-page and extract download links for target items."""
    logger.info("[scrape] %s %s: %s", page, year, sub_page_url)
    html = session.get_text(sub_page_url, referer=referer)
    if html is None:
        logger.error("[scrape] failed to fetch %s %s", page, year)
        return []
    return parse_sub_page(html, page, year, logger=logger)
