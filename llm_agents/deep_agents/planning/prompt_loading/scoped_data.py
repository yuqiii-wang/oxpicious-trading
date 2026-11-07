"""llm_agents.deep_agents.planning.prompt_loading.scoped_data — the
PLANNER-SCOPED data compaction.

The planner names steps, it does not ground metric values, so its
message carries the analysis items as label + metric names without the
per-metric ``db_keywords`` query recipes and without the docs bodies
(the STEP hops keep the full data — recipes ground the numbers they
must report). Measured 2026-11-03: a 71% cut of the planner's data
block.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def planner_scoped_data(data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The planner's copy of the run's structured context: IDENTITIES
    only, never the executor payload. Analysis items keep their key /
    label / description and each metric's name + label (the planner
    names steps after them), dropping the per-metric ``db_keywords``
    query recipes, ``description``, ``unit`` and ``scope`` (the step
    hops' messages keep the FULL data — the recipes ground the values
    they must report). Docs keep doc_id + title (their authored bodies
    are execution-time description context). Everything else passes
    through untouched. None in, None out."""
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
                                if k != "metrics"}
                metrics = insight.get("metrics")
                if isinstance(metrics, list):
                    slim_insight["metrics"] = [
                        {mk: mv for mk, mv in m.items()
                         if mk in ("metric", "label")}
                        for m in metrics if isinstance(m, dict)]
                slim["insight"] = slim_insight
            compact_items.append(slim)
        scoped["analysis_items"] = compact_items
    docs = scoped.get("docs")
    if isinstance(docs, list):
        scoped["docs"] = [
            {k: v for k, v in d.items() if k in ("doc_id", "title")}
            for d in docs if isinstance(d, dict)]
    return scoped
