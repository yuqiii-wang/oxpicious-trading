"""JSON load / save for the authoritative index-classification cache.

The cache (the ``database/sec_statics/sec_classification/`` shard tree) holds
ONLY the catalog + STATIC index classifications (no ETF/stock data, and no
build-time coverage — the n_days / first_date / last_date source-data audit
lives in monitor.src_data_audit since its 2026-10-10 migration out of the
cache: data that changes every build never belonged in hand-editable
shards).  ETF and stock mappings are rebuilt every run and upserted to the
DB directly — they are never persisted to the cache.  Shard layout and
read/write mechanics live in ``database.sec_statics.classification_store``
(single source of truth).
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

from database.sec_statics import classification_store

import logging
logger = logging.getLogger(__name__)

# Build-time fields stripped from every index entry at the save boundary —
# the coverage audit is upserted to monitor.src_data_audit (see
# sector_industry.src_data_audit), never written back into the shards.
_AUDIT_ONLY_FIELDS = ("n_days", "first_date", "last_date")


def load_json() -> Optional[Dict[str, Any]]:
    """Load the classification cache if it exists (None when absent)."""
    return classification_store.load_state()


def save_json(state: Dict[str, Any]) -> None:
    """Save the classification cache (catalog + indices only — no ETF/stock data).

    The catalog covers BOTH industry and strategy sectors (INDEX_RULES =
    INDUSTRY_RULES + STRATEGY_RULES), so a single catalog is sufficient.
    Each index entry stores its PRIMARY (sector_id, industry_id) +
    is_industry_not_strategy flag + industry tags — there is no separate
    strategy_id/theme_id/strategy_tags field.  Entries are stripped of the
    build-time coverage fields (old caches may still carry them; the strip
    also retires them from shards written by pre-migration builds).
    """
    # Include all indices (real + dummy).  Dummy indices (is_dummy=True) are
    # synthetic industry parents for orphan ETFs; persisting them to the JSON
    # cache keeps the cache in sync with the DB.
    indices = {
        code: {k: v for k, v in entry.items() if k not in _AUDIT_ONLY_FIELDS}
        for code, entry in state["indices"].items()
    }
    json_state = {
        "version": 1,
        "built_at": datetime.datetime.now().isoformat(),
        "csv_source": state.get("csv_source", ""),
        "catalog": state["catalog"],
        "indices": indices,
    }
    classification_store.write_state(json_state)
