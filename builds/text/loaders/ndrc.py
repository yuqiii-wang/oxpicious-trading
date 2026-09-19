"""builds.text.loaders.ndrc — ndrc.gov.cn 新闻发布 title list.

Titles only (no detail crawl): rows come from the downloader's
``ndrc_news_titles.csv``.
"""
from __future__ import annotations

from typing import Any, Dict, List

from builds.text import paths
from builds.text.loaders._parsing import parse_date_str, read_csv_rows

SOURCE = "ndrc"


def load_ndrc_news() -> List[Dict[str, Any]]:
    """ndrc.gov.cn 新闻发布 title list — titles only (no detail crawl)."""
    rows: List[Dict[str, Any]] = []
    for r in read_csv_rows(paths.NDRC_TITLES_CSV):
        title = (r.get("title") or "").strip()
        pub_date = parse_date_str(r.get("pub_date"))
        if not title or pub_date is None:
            continue
        rows.append({
            "title": title,
            "content": None,
            "date": pub_date,
            "source": SOURCE,
            "url": (r.get("url") or "").strip() or None,
            "author": None,
        })
    return rows
