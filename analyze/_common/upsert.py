"""Date-bounded chunked upsert for multi-million-row inserts.

Originally in analyze.mov_ave_spread.__main__._batched_upsert_by_date.
Moved here so all analyze scripts can reuse it.

Groups rows by their ``date`` key, then accumulates dates into chunks
of ~``chunk_target_rows`` rows each. Each chunk is upserted via a
separate ``bulk_upsert_async`` call (own transaction), so memory is
released between chunks. This avoids materializing the full
multi-million-row tuple list inside a single transaction.

PARALLEL MODE
=============

When ``pool`` (an asyncpg connection pool) is supplied, chunks run in
parallel with concurrency bounded by ``max_concurrent``. This is safe
because chunks are grouped by ``date`` and the detail table's PK is
``(sec_type, code, date)`` — two chunks NEVER share a date, so they can
never conflict on a PK. Each parallel task acquires its own connection
from the pool, runs ``bulk_upsert_async`` in its own transaction, and
releases the connection on completion.

A single asyncpg.Connection processes one query at a time — it cannot
run two ``executemany`` calls concurrently even via ``asyncio.gather``.
The pool is therefore REQUIRED for real parallelism.

Memory: the caller's ``rows`` list is already materialized, and parallel
chunks merely slice it. The only extra memory is the per-chunk tuple
list inside ``bulk_upsert_async`` (≈100K tuples × max_concurrent). With
``max_concurrent=4`` and ``chunk_target_rows=100_000`` that's ~400K
tuples in flight — bounded and small relative to the 8M-row input.
"""
from __future__ import annotations

import asyncio

from utils.build_commons import bulk_upsert_async


# Default target rows per upsert chunk. ~8.2M detail rows / ~1700 dates
# ≈ 5000 rows/date; grouping ~20 dates per chunk gives ~100K rows/chunk
# — small enough to keep memory bounded, large enough to amortize
# transaction overhead.
DEFAULT_CHUNK_TARGET_ROWS = 100_000

# Default parallelism when a pool is supplied. Each connection is a
# Postgres backend process, so keep this ≤ ~8 to avoid starving other
# clients. 4 is a good default: it saturates CPU-bound WAL flushing
# without overloading the connection pool.
DEFAULT_MAX_CONCURRENT = 4


def _group_rows_by_date(rows: list[dict]) -> list[tuple]:
    """Group a list of row dicts by their ``date`` key.

    Returns a list of (date, rows_for_date) pairs sorted by date.
    """
    by_date: dict = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    return sorted(by_date.items())


def _build_chunks(
    date_groups: list[tuple], chunk_target_rows: int
) -> list[list[dict]]:
    """Accumulate date-groups into chunks of ~``chunk_target_rows`` rows.

    Date-group boundaries are always respected — a single date's rows
    are never split across chunks. This guarantees that no two chunks
    share a date, which is the PK-cardinality safety invariant for
    parallel upsert (PK = (sec_type, code, date)).
    """
    chunks: list[list[dict]] = []
    chunk: list[dict] = []
    chunk_rows = 0
    for _d, group in date_groups:
        if chunk and chunk_rows + len(group) > chunk_target_rows:
            chunks.append(chunk)
            chunk = []
            chunk_rows = 0
        chunk.extend(group)
        chunk_rows += len(group)
    if chunk:
        chunks.append(chunk)
    return chunks


async def _upsert_chunk_sequential(
    conn, table_name, chunk, key_columns, batch_size,
    chunk_idx, n_chunks, label, total_counter,
) -> int:
    """Upsert one chunk using the shared connection (sequential)."""
    n = await bulk_upsert_async(
        conn, table_name, chunk,
        key_columns=key_columns,
        batch_size=batch_size,
    )
    total_counter[0] += n
    prefix = f"      {label} " if label else "      "
    print(f"{prefix}chunk {chunk_idx}/{n_chunks}: upserted {n:,} rows "
          f"(cumulative {total_counter[0]:,})", flush=True)
    return n


async def _upsert_chunk_parallel(
    pool, table_name, chunk, key_columns, batch_size,
    chunk_idx, n_chunks, label, total_counter, lock,
) -> int:
    """Upsert one chunk using a connection borrowed from the pool.

    Each parallel task runs in its own transaction (inside
    ``bulk_upsert_async``). The pool guarantees connection isolation.
    """
    async with pool.acquire() as conn:
        n = await bulk_upsert_async(
            conn, table_name, chunk,
            key_columns=key_columns,
            batch_size=batch_size,
        )
    async with lock:
        total_counter[0] += n
        so_far = total_counter[0]
    prefix = f"      {label} " if label else "      "
    print(f"{prefix}chunk {chunk_idx}/{n_chunks} done: upserted {n:,} rows "
          f"(cumulative {so_far:,})", flush=True)
    return n


async def batched_upsert_by_date(
    conn,
    table_name: str,
    rows: list[dict],
    key_columns: list[str],
    *,
    chunk_target_rows: int = DEFAULT_CHUNK_TARGET_ROWS,
    batch_size: int = 1000,
    label: str = "",
    pool=None,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> int:
    """Upsert rows in date-bounded chunks to bound memory.

    Args:
        conn: asyncpg connection. Used when ``pool`` is None (sequential
            mode). When ``pool`` is provided, ``conn`` is only used for
            the (rare) case where the pool is exhausted — but in
            practice parallel mode does not touch ``conn``.
        table_name: target table (schema-qualified, e.g.
            "analysis.mov_ave_spreads_detail").
        rows: list of row dicts.
        key_columns: PK columns for ON CONFLICT.
        chunk_target_rows: flush a chunk when it reaches this many rows.
        batch_size: asyncpg executemany batch size (passed to
            ``bulk_upsert_async``).
        label: optional label for progress messages (e.g. "detail" or
            "peaks_and_floors").
        pool: optional asyncpg connection pool. When supplied, chunks
            run in parallel (bounded by ``max_concurrent``). When None,
            chunks run sequentially on ``conn``.
        max_concurrent: maximum parallel chunk tasks (only used when
            ``pool`` is provided). Each task acquires a connection from
            the pool, so this MUST be ≤ the pool's ``max_size``.

    Returns:
        Total rows upserted.

    PK cardinality safety: chunks are grouped by date and the detail
    table's PK includes date, so two chunks can never conflict on a PK.
    Parallel upsert is therefore equivalent to sequential upsert for
    the final table state.
    """
    if not rows:
        return 0
    date_groups = _group_rows_by_date(rows)
    n_dates = len(date_groups)

    chunks = _build_chunks(date_groups, chunk_target_rows)
    n_chunks = len(chunks)

    # Shared mutable counter for cumulative progress logging.
    total_counter = [0]
    prefix = f"      {label} " if label else "      "

    if pool is None or max_concurrent <= 1 or n_chunks <= 1:
        # ---- Sequential mode (backward-compatible) ----
        for i, chunk in enumerate(chunks, start=1):
            await _upsert_chunk_sequential(
                conn, table_name, chunk, key_columns, batch_size,
                i, n_chunks, label, total_counter,
            )
        return total_counter[0]

    # ---- Parallel mode (pool required) ----
    # Cap concurrency by the number of chunks (no point scheduling more
    # tasks than chunks) and by the pool's max_size (can't acquire more
    # connections than the pool holds).
    pool_max = getattr(pool, "_maxsize", max_concurrent)
    concurrency = max(1, min(max_concurrent, n_chunks, pool_max))
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()

    print(f"{prefix}parallel mode: {n_chunks} chunks, "
          f"{concurrency} concurrent (pool max_size={pool_max})",
          flush=True)

    async def _task(chunk_idx, chunk):
        async with sem:
            return await _upsert_chunk_parallel(
                pool, table_name, chunk, key_columns, batch_size,
                chunk_idx, n_chunks, label, total_counter, lock,
            )

    tasks = [
        _task(i, chunk) for i, chunk in enumerate(chunks, start=1)
    ]
    results = await asyncio.gather(*tasks)
    return sum(results)
