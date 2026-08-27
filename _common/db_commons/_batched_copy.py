"""Key-bounded chunked COPY for multi-million-row inserts.

Sibling of ``analyze._common.upsert.batched_copy_by_date`` (which chunks
by ``date`` for the (sec_type, code, date)-PK tables). This variant
chunks by an ARBITRARY key — the table's PARTITION key (e.g.
``industry_id`` / ``code`` on the HASH-partitioned stats/analysis
tables) — so chunk boundaries align with the semantic partition unit:

  - a single key's rows are NEVER split across chunks (whole-industry /
    whole-code chunks), and
  - rows are constructed key-major (sorted by the partition key), so
  each chunk streams a contiguous run of keys through COPY.

Why chunk by the partition key rather than one giant COPY: asyncpg's
``copy_records_to_table`` materializes the full record iterator inside
one transaction; on 15M-row loads that holds the entire sanitized dict
list (~GBs) at once. Chunking bounds peak memory to
``chunk_target_rows`` dicts while keeping the 5-10x COPY protocol
speedup. Chunking does NOT change server-side routing — COPY routes
each row to its hash partition independently of chunk boundaries.

SAFE ONLY when the target table has been TRUNCATEd (or is otherwise
guaranteed conflict-free) — COPY has no ON CONFLICT handling. The
caller is responsible for truncating first (force mode does this).

Lives in ``_common.db_commons`` (not analyze._common) so BOTH builds.*
and analyze.* pipelines can share one implementation.
"""
from __future__ import annotations

from typing import Any

from ._async_ops import copy_insert_async

# ~100K rows per chunk — small enough to bound memory, large enough to
# amortize per-chunk transaction overhead (matches
# analyze._common.upsert.DEFAULT_CHUNK_TARGET_ROWS).
DEFAULT_CHUNK_TARGET_ROWS = 100_000


def _group_rows_by_key(rows: list[dict], key: str) -> list[tuple]:
    """Group a list of row dicts by ``row[key]``.

    Returns a list of (key_value, rows_for_key) pairs sorted by key
    value. Sorting makes the emitted chunks key-major regardless of the
    input order.
    """
    by_key: dict[Any, list[dict]] = {}
    for r in rows:
        by_key.setdefault(r[key], []).append(r)
    return sorted(by_key.items(), key=lambda kv: kv[0])


def _build_chunks(
    key_groups: list[tuple], chunk_target_rows: int
) -> list[list[dict]]:
    """Accumulate key-groups into chunks of ~``chunk_target_rows`` rows.

    Key-group boundaries are always respected — a single key's rows are
    never split across chunks (a chunk holds WHOLE industries / codes).
    """
    chunks: list[list[dict]] = []
    chunk: list[dict] = []
    chunk_rows = 0
    for _k, group in key_groups:
        if chunk and chunk_rows + len(group) > chunk_target_rows:
            chunks.append(chunk)
            chunk = []
            chunk_rows = 0
        chunk.extend(group)
        chunk_rows += len(group)
    if chunk:
        chunks.append(chunk)
    return chunks


async def batched_copy_by_key_async(
    conn,
    table_name: str,
    rows: list[dict],
    *,
    key: str,
    chunk_target_rows: int = DEFAULT_CHUNK_TARGET_ROWS,
    label: str = "",
) -> int:
    """Bulk-insert rows via chunked PostgreSQL COPY, grouped by ``key``.

    Args:
        conn: asyncpg connection (chunks run sequentially on it).
        table_name: target table (schema-qualified, e.g.
            "analysis.industry_correlations").
        rows: list of row dicts (same shape as ``copy_insert_async``).
        key: the DB partition key column name (e.g. "industry_id",
            "code"). Rows are grouped + sorted by this key; chunks never
            split a key across boundaries.
        chunk_target_rows: flush a chunk when it reaches this many rows.
        label: optional progress-message prefix.

    Returns:
        Total rows COPY-inserted.
    """
    if not rows:
        return 0
    chunks = _build_chunks(_group_rows_by_key(rows, key), chunk_target_rows)
    n_chunks = len(chunks)
    prefix = f"      {label} " if label else "      "
    total = 0
    for i, chunk in enumerate(chunks, start=1):
        total += await copy_insert_async(conn, table_name, chunk)
        print(f"{prefix}chunk {i}/{n_chunks}: COPY {len(chunk):,} rows "
              f"(cumulative {total:,})", flush=True)
    return total
