"""builds.text.loaders.pboc — the three pboc.gov.cn announcement leaves.

``lpr_news`` / ``omo`` (repo) / ``oma`` all share one artifact format: a
per-article ``.md`` with pseudo-YAML frontmatter + a fenced ``## Raw body``
block, one temps directory per leaf.
"""
from __future__ import annotations

from typing import Any, Dict, List

from builds.text import paths
from builds.text.loaders._parsing import load_md_articles

SOURCE_LPR = "pboc_lpr"
SOURCE_OMO = "pboc_omo"
SOURCE_OMA = "pboc_oma"


def load_pboc_lpr_news() -> List[Dict[str, Any]]:
    return load_md_articles(paths.PBOC_LPR_NEWS_DIR, "pboc_lpr_*.md",
                            SOURCE_LPR)


def load_pboc_omo_news() -> List[Dict[str, Any]]:
    return load_md_articles(paths.PBOC_OMO_NEWS_DIR, "pboc_omo_*.md",
                            SOURCE_OMO)


def load_pboc_oma_news() -> List[Dict[str, Any]]:
    return load_md_articles(paths.PBOC_OMA_NEWS_DIR, "pboc_oma_*.md",
                            SOURCE_OMA)
