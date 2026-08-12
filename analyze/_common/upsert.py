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

from _common.build_commons import bulk_upsert_async, copy_insert_async
from _common.pre_check_and_load.missing_dates import (
    filter_rows_to_missing_dates_async,
)


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


# ============================================================================
#  batched_copy_by_date — parallel COPY for force-mode (truncated-table) loads
# ============================================================================

async def _copy_chunk_sequential(
    conn, table_name, chunk, chunk_idx, n_chunks, label, total_counter,
) -> int:
    """COPY one chunk using the shared connection (sequential)."""
    n = await copy_insert_async(conn, table_name, chunk)
    total_counter[0] += n
    prefix = f"      {label} " if label else "      "
    print(f"{prefix}chunk {chunk_idx}/{n_chunks}: COPY {n:,} rows "
          f"(cumulative {total_counter[0]:,})", flush=True)
    return n


async def _copy_chunk_parallel(
    pool, table_name, chunk, chunk_idx, n_chunks, label, total_counter, lock,
) -> int:
    """COPY one chunk using a connection borrowed from the pool.

    Each parallel task runs in its own transaction (inside
    ``copy_insert_async``). The pool guarantees connection isolation.
    """
    async with pool.acquire() as conn:
        n = await copy_insert_async(conn, table_name, chunk)
    async with lock:
        total_counter[0] += n
        so_far = total_counter[0]
    prefix = f"      {label} " if label else "      "
    print(f"{prefix}chunk {chunk_idx}/{n_chunks} done: COPY {n:,} rows "
          f"(cumulative {so_far:,})", flush=True)
    return n


async def batched_copy_by_date(
    conn,
    table_name: str,
    rows: list[dict],
    *,
    chunk_target_rows: int = DEFAULT_CHUNK_TARGET_ROWS,
    label: str = "",
    pool=None,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> int:
    """Bulk-insert rows via PostgreSQL COPY, chunked by date.

    Sibling of :func:`batched_upsert_by_date` for the **force-mode /
    truncated-table** path. Uses :func:`copy_insert_async` (asyncpg
    ``copy_records_to_table``) per chunk instead of
    ``INSERT ... ON CONFLICT``. On multi-million-row loads COPY is
    typically 5-10× faster than ``executemany`` upsert because it
    bypasses per-row conflict arbitration, extended-query parsing, and
    writes WAL in bulk.

    SAFE ONLY when the target table has been TRUNCATEd (or is otherwise
    guaranteed conflict-free) before this call. COPY has no
    ``ON CONFLICT`` handling — a PK violation on ANY row aborts the whole
    chunk's transaction. The caller is responsible for truncating first
    (force mode does this). For incremental upsert into a non-empty
    table, use :func:`batched_upsert_by_date` instead.

    Parallel safety: identical to ``batched_upsert_by_date``. Chunks are
    grouped by ``date`` and the detail table's PK includes date, so two
    chunks never share a date → no PK conflict even when chunks run
    concurrently on separate connections into the same (truncated) table.

    Args:
        conn: asyncpg connection. Used when ``pool`` is None (sequential).
            When ``pool`` is provided, ``conn`` is not used.
        table_name: target table (schema-qualified, e.g.
            "analysis.mov_ave_spreads_detail").
        rows: list of row dicts (same shape as ``batched_upsert_by_date``).
        chunk_target_rows: flush a chunk when it reaches this many rows.
        label: optional label for progress messages (e.g. "detail" or
            "mov_ave_rsi").
        pool: optional asyncpg connection pool. When supplied, chunks run
            in parallel (bounded by ``max_concurrent``).
        max_concurrent: maximum parallel chunk tasks (only used when
            ``pool`` is provided). MUST be ≤ the pool's ``max_size``.

    Returns:
        Total rows COPY-inserted.
    """
    if not rows:
        return 0
    date_groups = _group_rows_by_date(rows)
    chunks = _build_chunks(date_groups, chunk_target_rows)
    n_chunks = len(chunks)

    total_counter = [0]
    prefix = f"      {label} " if label else "      "

    if pool is None or max_concurrent <= 1 or n_chunks <= 1:
        # ---- Sequential mode ----
        for i, chunk in enumerate(chunks, start=1):
            await _copy_chunk_sequential(
                conn, table_name, chunk, i, n_chunks, label, total_counter,
            )
        return total_counter[0]

    # ---- Parallel mode (pool required) ----
    pool_max = getattr(pool, "_maxsize", max_concurrent)
    concurrency = max(1, min(max_concurrent, n_chunks, pool_max))
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()

    print(f"{prefix}parallel COPY: {n_chunks} chunks, "
          f"{concurrency} concurrent (pool max_size={pool_max})",
          flush=True)

    async def _task(chunk_idx, chunk):
        async with sem:
            return await _copy_chunk_parallel(
                pool, table_name, chunk, chunk_idx, n_chunks,
                label, total_counter, lock,
            )

    tasks = [_task(i, chunk) for i, chunk in enumerate(chunks, start=1)]
    results = await asyncio.gather(*tasks)
    return sum(results)


# ============================================================================
#  build_and_insert_chunked — memory-bounded build + insert for huge universes
# ============================================================================
#
#  batched_upsert_by_date / batched_copy_by_date expect the FULL list of row
#  dicts to be materialized upfront. That list is the dominant memory cost:
#  sanitize_for_db_insert converts each numeric column to object dtype
#  (~4x float64 footprint) and to_dict(orient="records") creates one Python
#  dict per row (a 45-key dict is ~1.6 KB; 6.7M stock rows ≈ 10+ GB of
#  dicts alone). On large universes (stock: 6.7M rows) this OOMs even with
#  22 GB RAM.
#
#  build_and_insert_chunked splits the source DataFrame into date-bounded
#  sub-frames (~chunk_target_rows each), calls the caller-supplied build_fn
#  (which assembles + sanitizes into row dicts) PER sub-frame, and inserts
#  each chunk immediately. Peak memory is bounded to one chunk's dicts
#  (~100K rows ≈ 160 MB) instead of the full universe.
#
#  Date boundaries are respected (a single date is never split across
#  chunks) so the (sec_type, code, date) PK invariant is preserved — two
#  chunks never share a date.
#
#  The build_fn receives the full peaks_and_floors context (via closure)
#  so the nearest-preceding-extreme asof mapping picks up ALL extremes,
#  not just the chunk's dates.
# ============================================================================


def group_df_by_date_chunks(
    df, chunk_target_rows: int = DEFAULT_CHUNK_TARGET_ROWS
) -> list:
    """Split a DataFrame into date-bounded sub-frames of ~chunk_target_rows.

    Date boundaries are always respected — a single date's rows are never
    split across sub-frames. Returns a list of sub-DataFrames (views /
    boolean-indexed slices of ``df``), sorted by date ascending.

    Used by build_and_insert_chunked to bound peak memory: each sub-frame
    is built + sanitized + inserted independently, so the full dict list
    is never materialized at once.
    """
    if df.empty:
        return []
    # Per-date row counts, sorted by date ascending.
    date_sizes = df.groupby("date", sort=True).size()
    date_groups: list = []
    current: list = []
    current_rows = 0
    for d, size in date_sizes.items():
        if current and current_rows + size > chunk_target_rows:
            date_groups.append(current)
            current = [d]
            current_rows = size
        else:
            current.append(d)
            current_rows += size
    if current:
        date_groups.append(current)
    # Materialize sub-frames via boolean indexing (one pass per chunk).
    return [df[df["date"].isin(dates)] for dates in date_groups]


async def _filter_per_sec_type_chunk(conn, table_name, rows, sec_types):
    """Per-sec_type skip-filter for a single chunk's rows.

    Splits ``rows`` by sec_type and drops dates already present in the
    target table, scoped per-sec_type so a date populated for one
    sec_type doesn't mask the same date being missing for another.
    In force mode the table is truncated so this is a no-op (returns
    all rows); kept as a safety net for incremental edge cases.
    """
    if not rows:
        return []
    by_st: dict = {}
    for r in rows:
        by_st.setdefault(r.get("sec_type"), []).append(r)
    out: list = []
    for st, group in by_st.items():
        if st not in sec_types:
            out.extend(group)
            continue
        filtered = await filter_rows_to_missing_dates_async(
            conn, table_name, group, sec_type=st,
        )
        out.extend(filtered)
    return out


async def build_and_insert_chunked(
    conn,
    pool,
    df,
    build_fn,
    *,
    table_name: str,
    key_columns: list[str],
    force: bool,
    sec_types,
    chunk_target_rows: int = DEFAULT_CHUNK_TARGET_ROWS,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    label: str = "",
):
    """Build row dicts per date-chunk and insert each chunk immediately.

    Bounds peak memory by never materializing the full dict list — each
    date-chunk is built (via ``build_fn``), skip-filtered, and inserted
    before the next chunk is built.

    Insert strategy: COPY is used in BOTH force and incremental modes.
    In incremental mode the per-sec_type skip-filter (called below)
    removes every date that already has rows in the target table, so the
    remaining rows are guaranteed conflict-free — COPY bypasses the
    per-row ON CONFLICT arbiter check and is typically 5-10× faster than
    ``INSERT ... ON CONFLICT`` on multi-million-row loads.

    Parallelism (producer-consumer):
      - The build loop (build_fn) is CPU/GPU-bound (pandas merge, sanitize)
        and runs sequentially — it cannot be parallelized.
      - COPY is I/O-bound (network + WAL flush) and CAN run in parallel
        across pool connections.
      - This function overlaps the two: while chunk N's COPY runs on a
        pool connection, the build loop builds chunk N+1 on the CPU. A
        semaphore bounds how many chunks can be in-flight, bounding peak
        memory to ``max_concurrent`` chunks' dicts (~160 MB each).
      - Chunks are date-bounded and the target table PK includes date, so
        two chunks never share a date → no PK conflict even when COPYs
        run concurrently into the same table.

    Args:
        conn: asyncpg connection (used for skip-filter; used for COPY
            when ``pool`` is None or ``max_concurrent <= 1``).
        pool: connection pool for parallel COPY. When None or when
            ``max_concurrent <= 1``, COPY runs sequentially on ``conn``.
        df: source DataFrame (already filtered to target dates by the
            caller). Must have a ``date`` column and a ``sec_type`` column.
        build_fn: callable(sub_df) -> list[dict]. Assembles + sanitizes
            one chunk's rows. For mov_ave_spreads_detail this is
            ``lambda sub: build_detail_rows(sub, pf_rows=all_pf_rows)``;
            for mov_ave_rsi it is ``sanitize_rsi_rows``.
        table_name: target table (schema-qualified).
        key_columns: PK columns (used by the skip-filter; COPY itself
            doesn't need them).
        force: when True, table is pre-truncated (skip-filter is a no-op).
        sec_types: iterable of sec_type values for the per-sec_type
            skip-filter.
        chunk_target_rows: target rows per date-chunk (date boundaries
            respected).
        max_concurrent: maximum parallel COPY tasks. Each acquires one
            pool connection. MUST be ≤ the pool's ``max_size``.
        label: progress-message prefix.

    Returns:
        Total rows inserted.
    """
    if df.empty:
        return 0
    sub_frames = group_df_by_date_chunks(df, chunk_target_rows)
    n_chunks = len(sub_frames)
    prefix = f"      {label} " if label else "      "

    sec_types_set = set(sec_types)

    # Decide parallel vs sequential. Parallel requires a pool, >1 chunk,
    # and >1 max_concurrent. Otherwise sequential on conn is simpler and
    # avoids pool-acquire overhead for tiny loads.
    use_parallel = (
        pool is not None
        and max_concurrent > 1
        and n_chunks > 1
    )

    if use_parallel:
        pool_max = getattr(pool, "_maxsize", max_concurrent)
        concurrency = max(1, min(max_concurrent, n_chunks, pool_max))
        print(f"{prefix}build+insert: {n_chunks} date-chunks "
              f"(~{chunk_target_rows:,} rows/chunk), parallel COPY "
              f"({concurrency} concurrent, pool max_size={pool_max})",
              flush=True)
        return await _build_and_insert_parallel(
            conn, pool, sub_frames, build_fn, table_name,
            sec_types_set, concurrency, n_chunks, prefix,
        )
    else:
        print(f"{prefix}build+insert: {n_chunks} date-chunks "
              f"(~{chunk_target_rows:,} rows/chunk), sequential (COPY)",
              flush=True)
        return await _build_and_insert_sequential(
            conn, sub_frames, build_fn, table_name,
            sec_types_set, n_chunks, prefix,
        )


async def _build_and_insert_sequential(
    conn, sub_frames, build_fn, table_name,
    sec_types_set, n_chunks, prefix,
):
    """Sequential build + COPY on a single connection."""
    total = 0
    for i, sub in enumerate(sub_frames, start=1):
        rows = build_fn(sub)
        if not rows:
            continue
        rows = await _filter_per_sec_type_chunk(
            conn, table_name, rows, sec_types_set,
        )
        if not rows:
            continue
        n = await copy_insert_async(conn, table_name, rows)
        total += n
        print(f"{prefix}chunk {i}/{n_chunks}: COPY {n:,} rows "
              f"(cumulative {total:,})", flush=True)
    return total


async def _build_and_insert_parallel(
    conn, pool, sub_frames, build_fn, table_name,
    sec_types_set, concurrency, n_chunks, prefix,
):
    """Parallel build + COPY: build sequentially, COPY in parallel.

    The build loop (CPU/GPU-bound) runs sequentially — each chunk's dicts
    are built via ``build_fn`` and skip-filtered on ``conn`` before being
    submitted to a parallel COPY worker. The COPY (I/O-bound) runs on a
    pool connection, overlapping with the next chunk's build.

    A semaphore bounds how many chunks can be in-flight (built but not
    yet COPY'd), bounding peak memory to ``concurrency`` chunks' dicts.
    """
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    total_counter = [0]
    copy_tasks: list = []

    async def _copy_worker(chunk_idx, rows):
        async with sem:
            async with pool.acquire() as pool_conn:
                n = await copy_insert_async(pool_conn, table_name, rows)
            async with lock:
                total_counter[0] += n
                so_far = total_counter[0]
            print(f"{prefix}chunk {chunk_idx}/{n_chunks}: COPY {n:,} rows "
                  f"(cumulative {so_far:,})", flush=True)
            return n

    for i, sub in enumerate(sub_frames, start=1):
        # Build sequentially (CPU/GPU-bound — cannot parallelize).
        rows = build_fn(sub)
        if not rows:
            continue
        # Skip-filter on main conn (fast single SQL per sec_type).
        rows = await _filter_per_sec_type_chunk(
            conn, table_name, rows, sec_types_set,
        )
        if not rows:
            continue
        # Submit to a parallel COPY worker. The semaphore blocks if too
        # many chunks are already in-flight, bounding peak memory.
        task = asyncio.create_task(_copy_worker(i, rows))
        copy_tasks.append(task)

    results = await asyncio.gather(*copy_tasks)
    return sum(results)
