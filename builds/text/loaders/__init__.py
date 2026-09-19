"""builds.text.loaders — Parse the downloaded news artifacts into news rows.

Pure parsers: no DB, no network. One module per source plus the shared
parsing helpers in ``_parsing``; this package owns the source registry and
the merged entry point. Each loader returns a list of row dicts with the
uniform shape::

    {"title": str, "content": str | None, "date": datetime.date,
     "source": str, "url": str | None}

Sources + formats (produced by downloads.macro.*):

  gov      — temps/gov_news/: every crawled articles/*.md
             (pseudo-YAML frontmatter + ``## Body`` markdown) plus
             titles-only rows from gov_news_titles.csv for titles never
             crawled (content=None).
  ndrc     — temps/ndrc_news/ndrc_news_titles.csv (titles only).
  pboc_*   — temps/pboc_{lpr,omo,oma}_news/: per-article .md with
             frontmatter + ``## Raw body`` fenced block.
  zhihu    — temps/zhihu_news/*.json: one JSON per (keyword, target_date);
             each item → one row, dated by its EditTime (unix seconds,
             Asia/Shanghai).
  ai_daily — temps/ai_daily/ai_daily_<date>.json (the
             downloads.macro.ai_daily artifact): the AI summary answer,
             every search reference hit and the industry-movers answers
             (embedded ``movers`` + the standalone *_movers.json
             envelopes).

The md frontmatter is written with ``repr()``-quoted values and is NOT valid
YAML in general (e.g. multi-line ``date_raw``), so it is parsed line-by-line
in ``_parsing`` instead of with a YAML library.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from builds.text.loaders import _parsing, ai_daily, gov, ndrc, pboc, zhihu
from builds.text.loaders.ai_daily import load_ai_daily  # noqa: F401
from builds.text.loaders.gov import load_gov_news  # noqa: F401
from builds.text.loaders.ndrc import load_ndrc_news  # noqa: F401
from builds.text.loaders.pboc import (  # noqa: F401
    load_pboc_lpr_news, load_pboc_oma_news, load_pboc_omo_news,
)
from builds.text.loaders.zhihu import (  # noqa: F401
    load_zhihu_comments, load_zhihu_news,
)

logger = logging.getLogger(__name__)

# All sources this build knows about, in load order.
ALL_SOURCES = [
    gov.SOURCE, ndrc.SOURCE, pboc.SOURCE_LPR,
    pboc.SOURCE_OMO, pboc.SOURCE_OMA, zhihu.SOURCE, ai_daily.SOURCE,
]

# Per-source loader registry (order matters only for log readability).
LOADERS = {
    gov.SOURCE: gov.load_gov_news,
    ndrc.SOURCE: ndrc.load_ndrc_news,
    pboc.SOURCE_LPR: pboc.load_pboc_lpr_news,
    pboc.SOURCE_OMO: pboc.load_pboc_omo_news,
    pboc.SOURCE_OMA: pboc.load_pboc_oma_news,
    zhihu.SOURCE: zhihu.load_zhihu_news,
    ai_daily.SOURCE: ai_daily.load_ai_daily,
}


def load_news(sources: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Load + merge news rows for *sources* (default: all), deduped on the
    text.news natural key (title, source, date) — last occurrence wins."""
    wanted = sources or ALL_SOURCES
    unknown = [s for s in wanted if s not in LOADERS]
    if unknown:
        raise ValueError(f"unknown sources: {unknown} (known: {ALL_SOURCES})")

    merged: Dict[tuple, Dict[str, Any]] = {}
    for source in wanted:
        rows = LOADERS[source]()
        logger.info("    [%s] parsed %d rows", source, len(rows))
        for row in rows:
            merged[(row["title"], row["source"], row["date"])] = row
    return list(merged.values())
