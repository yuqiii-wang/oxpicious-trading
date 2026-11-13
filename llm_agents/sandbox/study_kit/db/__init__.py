"""study_kit.db — the read-only query layer for sandbox study scripts.

One public function per module (plain psycopg through the container's
preset libpq env — the ``readonly_analytics`` role: SELECT-only, writes
fail server-side):

- ``query``             — any SELECT → DataFrame
- ``fetch_daily_bars``  — daily OHLCV + precomputed MA/EMA columns
- ``fetch_spread_stats`` — the MATERIALIZED MA-spread trailing stats
                          (table-first; no computation)
- ``fetch_amt_metrics`` — the MATERIALIZED trading-amount metrics
                          (amt MAs / slopes / market share — the 量能
                          lane; table-first)

Shared plumbing (views map, sec_type codes, coercion) lives in the
private ``_shared`` module.
"""
from study_kit.db._shared import DAILY_VIEWS, SEC_TYPE_CODES  # noqa: F401
from study_kit.db.fetch_amt_metrics import AMT_COLUMNS, fetch_amt_metrics
from study_kit.db.fetch_daily_bars import fetch_daily_bars
from study_kit.db.fetch_spread_stats import (
    ALL_PAIRS,
    VERDICT_LABELS,
    fetch_spread_stats,
)
from study_kit.db.query import query

__all__ = [
    "query", "fetch_daily_bars", "fetch_spread_stats", "fetch_amt_metrics",
    "DAILY_VIEWS", "SEC_TYPE_CODES", "ALL_PAIRS", "VERDICT_LABELS",
    "AMT_COLUMNS",
]
