"""llm_agents.deep_agents.planning.prompt_loading.scoped_data — the
PLANNER-SCOPED data compaction.

The planner names steps, it does not read article bodies, so its
message carries the analysis items as IDENTITIES: key / label /
description plus the item's insight TAG BLOCK (as_of, codes/industry,
page keywords) with the related-article excerpts slimmed to TITLES (the
STEP hops keep the full data — the paragraph excerpts and any specs
ground the work they must report). Same measured shape as the former
per-metric-recipes cut (2026-11-03: a 71% cut of the planner's data
block; the metric specs stopped riding the summary data entirely on
2026-11-08 — tags + article excerpts replaced them).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def planner_scoped_data(data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The planner's copy of the run's structured context: IDENTITIES
    only, never the executor payload. Analysis items keep their key /
    label / description and their insight tag block, with the
    related-article excerpts slimmed to a title list (the step hops'
    messages keep the FULL excerpts). Docs keep doc_id + title (their
    authored bodies are execution-time description context). Everything
    else passes through untouched. None in, None out."""
    if not isinstance(data, dict):
        return data
    scoped: Dict[str, Any] = dict(data)
    items = scoped.get("analysis_items")
    if isinstance(items, list):
        compact_items: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                compact_items.append(item)
                continue
            slim = {k: v for k, v in item.items() if k != "insight"}
            insight = item.get("insight")
            if isinstance(insight, dict):
                slim_insight = {k: v for k, v in insight.items()
                                if k != "articles"}
                articles = insight.get("articles")
                if isinstance(articles, list):
                    titles = [str(a.get("title"))
                              for a in articles
                              if isinstance(a, dict) and a.get("title")]
                    if titles:
                        slim_insight["articles"] = titles
                slim["insight"] = slim_insight
            compact_items.append(slim)
        scoped["analysis_items"] = compact_items
    docs = scoped.get("docs")
    if isinstance(docs, list):
        scoped["docs"] = [
            {k: v for k, v in d.items() if k in ("doc_id", "title")}
            for d in docs if isinstance(d, dict)]
    return scoped
