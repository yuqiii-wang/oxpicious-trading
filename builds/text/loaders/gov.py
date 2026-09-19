"""builds.text.loaders.gov — gov.cn 政策解读 articles + title list.

Every crawled ``articles/*.md`` (pseudo-YAML frontmatter + ``## Body``
markdown) PLUS titles-only rows from ``gov_news_titles.csv`` for titles
never crawled (content=None). See :func:`load_gov_news` for why the two
files merge.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from builds.text import paths
from builds.text.loaders._parsing import (
    load_md_articles, parse_date_str, read_csv_rows,
)

logger = logging.getLogger(__name__)

SOURCE = "gov"


def load_gov_news() -> List[Dict[str, Any]]:
    """gov.cn 政策解读: every crawled article .md (frontmatter + ## Body)
    PLUS every title from the titles CSV that was never crawled
    (content=None). articles_index.csv is the downloader's crawl STATE file
    (only reflects the latest incremental run), so the .md files — one per
    crawled article, self-describing via frontmatter — are the source of
    truth for content; the titles CSV guarantees full title coverage."""
    # Articles first: frontmatter carries title/pub_date/url + the body.
    articles = load_md_articles(paths.GOV_ARTICLES_DIR, "*.md", SOURCE,
                                body_header="## Body", fenced=False)
    crawled_urls = {a["url"] for a in articles if a["url"]}

    rows: List[Dict[str, Any]] = list(articles)
    n_dup = 0
    for r in read_csv_rows(paths.GOV_TITLES_CSV):
        title = (r.get("title") or "").strip()
        pub_date = parse_date_str(r.get("pub_date"))
        if not title or pub_date is None:
            continue
        url = (r.get("url") or "").strip() or None
        if url in crawled_urls:
            n_dup += 1  # already loaded from its .md (with content)
            continue
        rows.append({
            "title": title,
            "content": None,
            "date": pub_date,
            "source": SOURCE,
            "url": url,
            "author": None,  # titles CSV carries no 来源 — the .md rows do
        })
    if n_dup:
        logger.info("    [gov] %d crawled titles merged with their .md bodies",
                    n_dup)
    return rows
