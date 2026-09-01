"""Crawl gov.cn 政策解读 article detail pages for titles matching keyword taxonomy.

Reads the title list produced by :mod:`downloads.macro.gov.news`
(``temps/gov_news/gov_news_titles.csv``), loads the static keyword taxonomy
from ``downloads/macro/gov/keywords.json``, builds a reverse map
(keyword -> [(type, category), ...]) at runtime, scans each title for keyword
mentions, and — for matched titles — visits the article detail link to fetch
its body text. Detail pages are cached as ``.md`` files (one per article,
keyed by the URL content slug) so subsequent runs only fetch new articles.

Only matched articles are crawled — detail links for titles with no keyword
hit are NOT visited. Anti-bot behaviour (browser-fingerprint rotation,
``random`` query param, host-blocking detection, sleep cadence) is provided
by the shared ``AntiBotProxy`` from ``downloads._common``; the
inter-request sleep defaults to ``LONG_SLEEP_INTERVAL`` (90s).

Outputs (under ``temps/gov_news/``):

  * ``articles/<slug>.md`` — per-article detail with YAML front-matter
    (pub_date, title, url, industries, broadmarkets) + body text.
  * ``articles_index.csv`` — ``pub_date,title,url,industries,broadmarkets,
    detail_file,status`` for every matched title (newest-first).

Usage::

    python -m downloads.macro.gov.articles                       # incremental (latest missing dates only)
    python -m downloads.macro.gov.articles --start-date 2025-01-01
    python -m downloads.macro.gov.articles --max-articles 5       # test run
    python -m downloads.macro.gov.articles --force                # full re-crawl
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from bs4 import BeautifulSoup

# Make the project root importable when this module is executed directly.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from downloads._common import (  # noqa: E402
    AntiBotConfig,
    AntiBotProxy,
    COMMON_BASE_HEADERS,
    DEFAULT_START_DATE,
    DEFAULT_TIMEOUT,
    LONG_SLEEP_INTERVAL,
    build_default_session,
    resolve_out_dir,
    setup_logger,
)
from downloads.macro.gov.main_gov import latest_csv_date, read_csv_rows  # noqa: E402

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
GOV_HOST = "https://www.gov.cn"
GOV_LIST_URL = "https://www.gov.cn/zhengce/jiedu/index.htm"
KEYWORDS_PATH = Path(__file__).resolve().parent.parent / "keywords.json"
OUTPUT_DIRNAME = "gov_news"
ARTICLES_SUBDIR = "articles"
INDEX_FILENAME = "articles_index.csv"
TITLES_FILENAME = "gov_news_titles.csv"
GOV_MIN_VALID_BYTES = 200

# URL slug extractor: content_7077850.htm -> content_7077850
_RE_SLUG = re.compile(r"content_(\d+)\.htm")
# Detail-page body / title / date selectors (tried in order).
_BODY_SELECTORS = ["#zoom", ".pages_content", ".TRS_Editor", "div.content", "td.Normal"]
_TITLE_SELECTORS = ["h1", "h2", ".tit", ".title"]
_DATE_SELECTORS = [".pages-date", ".date", ".time", ".pub_date"]

INDEX_COLUMNS = [
    "pub_date", "title", "url",
    "industries", "broadmarkets",
    "detail_file", "status",
]

logger = setup_logger("gov_articles")


# ----------------------------------------------------------------------------
# Keywords: load + reverse map
# ----------------------------------------------------------------------------
def load_keywords(path: Path = KEYWORDS_PATH) -> Dict[str, Dict[str, List[str]]]:
    """Load the static keyword taxonomy JSON."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # Strip the _comment key if present.
    return {k: v for k, v in data.items() if not k.startswith("_")}


def build_reverse_map(
    keywords: Dict[str, Dict[str, List[str]]],
) -> Dict[str, List[Tuple[str, str]]]:
    """Build keyword -> [(type, category), ...] from the taxonomy.

    A keyword listed under multiple categories maps to a list of all of them.
    """
    rev: Dict[str, List[Tuple[str, str]]] = {}
    for type_name, categories in keywords.items():
        for cat, kws in categories.items():
            for kw in kws:
                rev.setdefault(kw, []).append((type_name, cat))
    return rev


def extract_categories(
    title: str,
    rev_map: Dict[str, List[Tuple[str, str]]],
) -> Dict[str, Set[str]]:
    """Scan *title* for keyword mentions; return {type: set(categories)}.

    Uses simple substring matching (``kw in title``). All keywords are 2+
    characters, so false positives on single chars are avoided.
    """
    matches: Dict[str, Set[str]] = {}
    for kw, cats in rev_map.items():
        if kw in title:
            for type_name, cat in cats:
                matches.setdefault(type_name, set()).add(cat)
    return matches


# ----------------------------------------------------------------------------
# Titles CSV reading
# ----------------------------------------------------------------------------
def read_titles_csv(csv_path: Path) -> List[Dict[str, str]]:
    """Read the gov_news_titles.csv into a list of row dicts."""
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Titles CSV not found: {csv_path}. Run `python -m downloads.macro.gov.news` first."
        )
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ----------------------------------------------------------------------------
# Slug + cache helpers
# ----------------------------------------------------------------------------
def extract_slug(url: str) -> Optional[str]:
    """Extract the content slug from a gov.cn article URL."""
    m = _RE_SLUG.search(url)
    return m.group(1) if m else None


def detail_md_path(articles_dir: Path, url: str) -> Optional[Path]:
    """Return the cached .md path for *url*, or None if no slug is parseable."""
    slug = extract_slug(url)
    if not slug:
        return None
    return articles_dir / f"{slug}.md"


def is_cached(md_path: Optional[Path]) -> bool:
    """Return True if the detail .md file exists and is non-trivially sized."""
    if md_path is None or not md_path.exists() or not md_path.is_file():
        return False
    try:
        return md_path.stat().st_size >= GOV_MIN_VALID_BYTES
    except OSError:
        return False


# ----------------------------------------------------------------------------
# HTML parsing
# ----------------------------------------------------------------------------
def _select_text(soup: BeautifulSoup, selectors: List[str]) -> str:
    """Return the text of the first matching selector, or empty string."""
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            txt = el.get_text("\n", strip=True)
            if txt:
                return txt
    return ""


def _clean_text(s: str) -> str:
    """Normalise whitespace in extracted body text."""
    s = s.replace("\u3000", " ").replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def parse_detail_html(html: str) -> Dict[str, str]:
    """Parse a gov.cn article detail page into {title, body, pub_date_raw}."""
    soup = BeautifulSoup(html, "html.parser")
    title = _select_text(soup, _TITLE_SELECTORS)
    body = _clean_text(_select_text(soup, _BODY_SELECTORS))
    date_raw = _select_text(soup, _DATE_SELECTORS)
    return {"title": title, "body": body, "date_raw": date_raw}


def build_markdown(
    *,
    pub_date: str,
    title: str,
    url: str,
    industries: List[str],
    broadmarkets: List[str],
    detail: Dict[str, str],
) -> str:
    """Build the .md content with YAML front-matter + body text."""
    lines: List[str] = []
    lines.append("---")
    lines.append(f"pub_date: {pub_date}")
    lines.append(f"title: {title!r}")
    lines.append(f"url: {url}")
    lines.append(f"industries: [{', '.join(industries)}]")
    lines.append(f"broadmarkets: [{', '.join(broadmarkets)}]")
    if detail.get("date_raw"):
        lines.append(f"date_raw: {detail['date_raw']!r}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- Pub date: **{pub_date}**")
    lines.append(f"- Industries: {', '.join(industries) or '-'}")
    lines.append(f"- Broadmarkets: {', '.join(broadmarkets) or '-'}")
    lines.append(f"- Source: {url}")
    lines.append("")
    lines.append("## Body")
    lines.append("")
    lines.append(detail.get("body") or "(body extraction failed)")
    lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# Detail fetcher
# ----------------------------------------------------------------------------
def fetch_detail(
    session: Any,
    proxy: AntiBotProxy,
    url: str,
    *,
    max_retries: int = 2,
) -> Optional[str]:
    """Fetch a gov.cn article detail page HTML via the anti-bot proxy.

    Returns the HTML string, or None on hard failure. Retries on transient
    network errors with exponential backoff; the proxy handles fingerprint
    rotation, the ``random`` query param, host-blocking detection, and the
    inter-request sleep cadence.
    """
    headers = dict(COMMON_BASE_HEADERS)
    headers["Referer"] = GOV_LIST_URL

    for attempt in range(1, max_retries + 1):
        if proxy.is_blocked(url):
            logger.error("  host blocked, aborting detail fetch: %s", url)
            return None
        resp = proxy.get(
            session, url,
            headers=headers, timeout=DEFAULT_TIMEOUT,
            logger=logger, log_tag="  ",
        )
        if resp is None:
            logger.warning("  detail fetch failed (attempt %d/%d): %s", attempt, max_retries, url)
            if attempt < max_retries:
                import time
                time.sleep(min(2 ** attempt, 30))
            continue
        if len(resp.content) < GOV_MIN_VALID_BYTES:
            logger.warning("  detail too small (%d bytes): %s", len(resp.content), url)
            return None
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text

    return None


# ----------------------------------------------------------------------------
# Index CSV
# ----------------------------------------------------------------------------
def write_index_csv(rows: List[Dict[str, str]], out_path: Path) -> None:
    """Write the articles index CSV."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=INDEX_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


# ----------------------------------------------------------------------------
# Main orchestrator
# ----------------------------------------------------------------------------
def download_articles(
    *,
    out_root: Optional[str] = None,
    start_date: Optional[str] = None,
    sleep_sec: float = LONG_SLEEP_INTERVAL,
    max_articles: Optional[int] = None,
    force: bool = False,
    titles_csv: Optional[str] = None,
) -> Dict[str, Any]:
    """Scan gov_news titles for keyword matches and crawl matched detail pages.

    By default the run is **incremental**: when ``articles_index.csv`` already
    exists, only matched titles on/after its latest ``pub_date`` are scanned
    (same-day additions are re-checked; cached ``.md`` files skip re-fetches).
    ``--force`` re-scans all titles and re-fetches every matched detail page.

    Returns a summary dict with counts and output paths.
    """
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), OUTPUT_DIRNAME, out_root)
    articles_dir = out_dir / ARTICLES_SUBDIR
    index_path = out_dir / INDEX_FILENAME
    csv_path = Path(titles_csv) if titles_csv else out_dir / TITLES_FILENAME

    start = datetime.strptime(start_date or DEFAULT_START_DATE, "%Y-%m-%d").date()

    # Incremental state: latest pub_date already present in the index CSV.
    index_floor: Optional[date] = None
    if not force:
        index_floor = latest_csv_date(read_csv_rows(index_path))
        if index_floor is not None:
            logger.info("Incremental: index has data up to %s — only newer titles scanned", index_floor)

    # 1. Load keywords + build reverse map
    keywords = load_keywords()
    rev_map = build_reverse_map(keywords)
    logger.info(
        "Loaded %d keywords (%d industry cats, %d broadmarket cats) from %s",
        len(rev_map),
        len(keywords.get("industry", {})),
        len(keywords.get("broadmarket", {})),
        KEYWORDS_PATH.name,
    )

    # 2. Read titles CSV
    titles = read_titles_csv(csv_path)
    logger.info("Read %d titles from %s", len(titles), csv_path.name)

    # 3. Scan titles for keyword matches, filter to start_date + matched
    matched: List[Tuple[Dict[str, str], Dict[str, Set[str]]]] = []
    for row in titles:
        pd = datetime.strptime(row["pub_date"], "%Y-%m-%d").date()
        if pd < start:
            continue
        if index_floor is not None and pd < index_floor:
            continue
        cats = extract_categories(row.get("title", ""), rev_map)
        if not cats:
            continue
        matched.append((row, cats))

    logger.info(
        "Matched %d / %d titles (start=%s, index_floor=%s)%s",
        len(matched), len(titles), start,
        index_floor if index_floor is not None else "-",
        f" — capping to {max_articles}" if max_articles else "",
    )

    if max_articles is not None and max_articles >= 0:
        matched = matched[:max_articles]

    # 4. Fetch detail pages for matched titles
    session = build_default_session()
    proxy = AntiBotProxy(AntiBotConfig(
        base_sleep_sec=sleep_sec,
        enable_host_tracking=True,
    ))

    index_rows: List[Dict[str, str]] = []
    fetched = skipped_cached = failed = 0

    # Track runs of consecutively cached files between actual fetches so we
    # can emit a single concise summary line (date range + count) instead of
    # one INFO line per cached file. The run is flushed lazily when the next
    # fetch starts, or once at the end of the loop for any trailing tail.
    cached_run_start_date: Optional[str] = None
    cached_run_end_date: Optional[str] = None
    cached_run_count = 0

    def _flush_cached_run(idx: int, total: int, *, tail: bool = False) -> None:
        if cached_run_count == 0:
            return
        tag = "tail (no further fetch)" if tail else "before next fetch"
        logger.info(
            "[%d/%d] skipped %d cached files (%s -> %s) %s",
            idx, total, cached_run_count,
            cached_run_start_date, cached_run_end_date, tag,
        )

    try:
        for i, (row, cats) in enumerate(matched, 1):
            if proxy.is_blocked(GOV_HOST):
                logger.warning("  host blocked — stopping at article %d/%d", i, len(matched))
                break

            pub_date = row["pub_date"]
            title = row.get("title", "")
            url = row.get("url", "")
            industries = sorted(cats.get("industry", set()))
            broadmarkets = sorted(cats.get("broadmarket", set()))
            md_path = detail_md_path(articles_dir, url)

            status = "no_slug"
            detail_file = ""

            if md_path is None:
                # No parseable slug — can't cache; record as skipped.
                logger.warning("[%d/%d] no slug: %s", i, len(matched), title[:50])
            elif not force and is_cached(md_path):
                skipped_cached += 1
                status = "cached"
                detail_file = md_path.name
                # Accumulate into the current cached run; the summary is
                # emitted lazily when the next fetch starts (or at loop end).
                if cached_run_count == 0:
                    cached_run_start_date = pub_date
                cached_run_end_date = pub_date
                cached_run_count += 1
            else:
                # Actual fetch — first flush any cached run we just finished.
                _flush_cached_run(i, len(matched))
                cached_run_count = 0
                cached_run_start_date = None
                cached_run_end_date = None
                logger.info("[%d/%d] %s fetching: %s",
                            i, len(matched), pub_date, title[:50])
                html = fetch_detail(session, proxy, url)
                if html is None:
                    failed += 1
                    status = "fetch_failed"
                    logger.warning("  fetch failed: %s", url)
                else:
                    detail = parse_detail_html(html)
                    md_content = build_markdown(
                        pub_date=pub_date, title=title, url=url,
                        industries=industries, broadmarkets=broadmarkets,
                        detail=detail,
                    )
                    md_path.parent.mkdir(parents=True, exist_ok=True)
                    md_path.write_text(md_content, encoding="utf-8")
                    fetched += 1
                    status = "fetched"
                    detail_file = md_path.name
                    logger.info("  saved %s (%d chars body)",
                                md_path.name, len(detail.get("body", "")))

            index_rows.append({
                "pub_date": pub_date,
                "title": title,
                "url": url,
                "industries": ";".join(industries),
                "broadmarkets": ";".join(broadmarkets),
                "detail_file": detail_file,
                "status": status,
            })
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    # Flush any trailing cached run that had no subsequent fetch (e.g. all
    # remaining articles were already cached, or the run was interrupted).
    _flush_cached_run(len(matched), len(matched), tail=True)
    cached_run_count = 0

    # 5. Write index CSV (always, even on partial/interrupt)
    write_index_csv(index_rows, index_path)

    summary = {
        "matched": len(matched),
        "mode": "incremental" if index_floor is not None else "full",
        "index_floor": str(index_floor) if index_floor is not None else None,
        "fetched": fetched,
        "skipped_cached": skipped_cached,
        "failed": failed,
        "out_dir": str(out_dir),
        "articles_dir": str(articles_dir),
        "index_path": str(index_path),
        "start_date": str(start),
    }
    logger.info(
        "Done. matched=%d fetched=%d cached=%d failed=%d -> %s",
        len(matched), fetched, skipped_cached, failed, index_path.name,
    )
    return summary


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crawl gov.cn 政策解读 article details for keyword-matched titles",
    )
    parser.add_argument("--start-date", type=str, default=None,
                        help=f"Floor date YYYY-MM-DD. Default: {DEFAULT_START_DATE}")
    parser.add_argument("--sleep-sec", type=float, default=LONG_SLEEP_INTERVAL,
                        help=f"Anti-bot sleep between requests (s). Default: {LONG_SLEEP_INTERVAL}")
    parser.add_argument("--max-articles", type=int, default=None,
                        help="Cap number of detail fetches (for testing). Default: no cap.")
    parser.add_argument("--out-root", type=str, default=None,
                        help="Output root dir. Default: <project>/temps/gov_news")
    parser.add_argument("--titles-csv", type=str, default=None,
                        help="Path to titles CSV. Default: <out_root>/gov_news_titles.csv")
    parser.add_argument("--force", action="store_true",
                        help="Force re-fetch of all matched detail pages even if cached.")
    args = parser.parse_args()

    summary = download_articles(
        out_root=args.out_root,
        start_date=args.start_date,
        sleep_sec=args.sleep_sec,
        max_articles=args.max_articles,
        force=args.force,
        titles_csv=args.titles_csv,
    )
    print(summary)


if __name__ == "__main__":
    main()
