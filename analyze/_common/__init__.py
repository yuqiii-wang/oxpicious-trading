"""Shared helpers for all analyze.* subpackages.

Consolidates 4 patterns repeated 3+ times across the analyze scripts:

  - sanitize_for_db_insert: NaN/inf/None sanitization + to_dict for
    asyncpg bulk upsert. Replaces per-row ``iterrows`` dict construction
    and scattered ``replace([inf,-inf], nan)`` + ``astype(object).where``
    blocks in mov_ave_spread, sec_alloc_perf_attribution, and
    industry_sentiments.

  - upsert_analysis_identity: the INSERT...ON CONFLICT DO UPDATE for
    analysis.analysis_identity. Identical SQL was duplicated in 6 places.

  - grouped_rolling_agg: the ``groupby(keys)[col].rolling(W,
    min_periods=P).agg(...).reset_index(level=..., drop=True)`` pattern.
    Used in mov_ave_spread (std), sec_alloc_perf_attribution (MA5 mean),
    and industry_sentiments/etf_contribution (MA5 + MA20 mean).

  - batched_upsert_by_date: date-bounded chunked upsert to bound memory
    for multi-million-row inserts. Originally in mov_ave_spread/__main__.

  - batched_copy_by_date: date-bounded chunked PostgreSQL COPY for the
    force-mode (truncated-table) path. 5-10x faster than upsert on
    multi-million-row loads; safe only when the table is pre-truncated.

Each helper is a pure-pandas or pure-SQL operation with no object-dtype
intermediates, so the steps can be individually swapped for cuDF when
GPU acceleration is added later.
"""
from analyze._common.sanitize import sanitize_for_db_insert
from analyze._common.identity import upsert_analysis_identity
from analyze._common.rolling import grouped_rolling_agg
from analyze._common.upsert import (
    batched_copy_by_date,
    batched_upsert_by_date,
    build_and_insert_chunked,
    build_and_insert_chunked_df,
)

__all__ = [
    "sanitize_for_db_insert",
    "upsert_analysis_identity",
    "grouped_rolling_agg",
    "batched_upsert_by_date",
    "batched_copy_by_date",
    "build_and_insert_chunked",
    "build_and_insert_chunked_df",
]
