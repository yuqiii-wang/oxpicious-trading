"""JSON load / save for the authoritative index-classification cache.

The JSON (sec_classification.json) contains ONLY the catalog + index
classifications (no ETF/stock data).  ETF and stock mappings are rebuilt
every run and upserted to the DB directly — they are never persisted to the
JSON.
"""
from __future__ import annotations

import datetime
import json
import os
from typing import Any, Dict, Optional

from builds.classification.sector_industry.paths import JSON_PATH


def load_json() -> Optional[Dict[str, Any]]:
    """Load sec_classification.json if it exists."""
    if not os.path.isfile(JSON_PATH):
        return None
    try:
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"    [WARN] Failed to load {JSON_PATH}: {e}", flush=True)
        return None


def save_json(state: Dict[str, Any]) -> None:
    """Save sec_classification.json (catalog + indices only — no ETF/stock data).

    The catalog covers BOTH industry and strategy sectors (INDEX_RULES =
    INDUSTRY_RULES + STRATEGY_RULES), so a single catalog is sufficient.
    Each index entry stores its PRIMARY (sector_id, industry_id) +
    is_industry_not_strategy flag + industry tags — there is no separate
    strategy_id/theme_id/strategy_tags field.
    """
    # Include all indices (real + dummy).  Dummy indices (is_dummy=True) are
    # synthetic industry parents for orphan ETFs; persisting them to the JSON
    # cache keeps the cache in sync with the DB.
    json_state = {
        "version": 1,
        "built_at": datetime.datetime.now().isoformat(),
        "csv_source": state.get("csv_source", ""),
        "catalog": state["catalog"],
        "indices": state["indices"],
    }
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(json_state, f, ensure_ascii=False, indent=2)
    print(f"    [JSON] Saved {JSON_PATH} "
          f"({len(json_state['indices'])} indices, unified catalog)",
          flush=True)
