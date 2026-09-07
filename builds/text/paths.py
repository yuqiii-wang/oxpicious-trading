"""builds.text.paths — Source directories for the downloaded news text.

The downloaders (downloads.macro.*) write under ``temps/``; this module pins
the exact directories once so loaders.py and __main__.py share one source of
truth (same pattern as builds._commons.paths).
"""
from __future__ import annotations

import os

from _common.build_commons import PROJECT_ROOT

# gov.cn 政策解读 — title list + articles_index.csv + articles/*.md
GOV_NEWS_DIR = os.path.join(PROJECT_ROOT, "temps", "gov_news")
GOV_ARTICLES_DIR = os.path.join(GOV_NEWS_DIR, "articles")
GOV_TITLES_CSV = os.path.join(GOV_NEWS_DIR, "gov_news_titles.csv")
GOV_ARTICLES_INDEX_CSV = os.path.join(GOV_NEWS_DIR, "articles_index.csv")

# ndrc.gov.cn 新闻发布 — title list only (no detail crawl)
NDRC_NEWS_DIR = os.path.join(PROJECT_ROOT, "temps", "ndrc_news")
NDRC_TITLES_CSV = os.path.join(NDRC_NEWS_DIR, "ndrc_news_titles.csv")

# pboc.gov.cn — per-article .md (frontmatter + fenced raw body)
PBOC_LPR_NEWS_DIR = os.path.join(PROJECT_ROOT, "temps", "pboc_lpr_news")
PBOC_OMO_NEWS_DIR = os.path.join(PROJECT_ROOT, "temps", "pboc_repo_news")
PBOC_OMA_NEWS_DIR = os.path.join(PROJECT_ROOT, "temps", "pboc_oma_news")

# zhihu — one JSON per (keyword, target_date), items inside
ZHIHU_NEWS_DIR = os.path.join(PROJECT_ROOT, "temps", "zhihu_news")

# Canonical SEC classification inputs used by keywords.py
SEC_CLASSIFICATION_JSON = os.path.join(
    PROJECT_ROOT, "_common", "sec_statics", "sec_classification.json")
GOV_KEYWORDS_JSON = os.path.join(
    PROJECT_ROOT, "downloads", "macro", "gov", "keywords.json")
