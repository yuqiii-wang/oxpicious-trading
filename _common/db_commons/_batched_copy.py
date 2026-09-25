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

``copy_frame_chunked_async`` is the DataFrame-flavored sibling: it
chunks a FRAME by the partition key (stable key-sorted, whole keys per
chunk; flat row-count when no key resolves) and sanitizes each slice
INSIDE the loop, so the sanitized dict list never exceeds one chunk —
the whole-table sanitize-then-COPY pattern (e.g. a --force rebuild of a
7M-row table) peaked at ~1 KB/row of host RSS (measured,
temp_scripts/study_inplace_col_reduce.py scenario F) before this
helper existed.

SAFE ONLY when the target table has been TRUNCATEd (or is otherwise
guaranteed conflict-free) — COPY has no ON CONFLICT handling. The
caller is responsible for truncating first (force mode does this).

Lives in ``_common.db_commons`` (not analyze._common) so BOTH builds.*
and analyze.* pipelines can share one implementation.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

from ._async_ops import (
    copy_insert_async,
    csv_copy_from_frame_async,
    _wait_for_replica_lag_async,
    DEFAULT_MAX_REPLICA_LAG_MB,
)
from ._helpers import (
    _group_rows_by_key,
    _build_chunks,
    DEFAULT_COMMIT_CHUNK_ROWS,
    PARTITION_KEY_AUTO,
)
from ..df_utils import host_array
from ..df_utils.sanitize import sanitize_for_db_insert

import logging
logger = logging.getLogger(__name__)

# ~100K rows per chunk — small enough to bound memory, large enough to
# amortize per-chunk transaction overhead (matches
# analyze._common.upsert.DEFAULT_CHUNK_TARGET_ROWS). Each chunk commits
# independently, so a replication slot advances between chunks instead of
# retaining WAL behind one giant transaction.
DEFAULT_CHUNK_TARGET_ROWS = 100_000


async def batched_copy_by_key_async(
    conn,
    table_name: str,
    rows: list[dict],
    *,
    key: str,
    chunk_target_rows: int = DEFAULT_CHUNK_TARGET_ROWS,
    label: str = "",
    max_replica_lag_mb: float | None = DEFAULT_MAX_REPLICA_LAG_MB,
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
        max_replica_lag_mb: pause between chunks while a streaming
            standby's replay lag exceeds this (default 4096 MB — 1/16 of
            the primary's 64GB max_slot_wal_keep_size; None disables the
            throttle). Each chunk is its own COMMIT, so the
            replica (and the slot's retained-WAL bound) advances between
            chunks while the primary keeps receiving data.

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
        if max_replica_lag_mb is not None:
            await _wait_for_replica_lag_async(conn, max_replica_lag_mb)
        total += await copy_insert_async(conn, table_name, chunk)
        logger.info(f"{prefix}chunk {i}/{n_chunks}: COPY {len(chunk):,} rows "
              f"(cumulative {total:,})")
    return total


def _partition_key_frame_order(
    df: pd.DataFrame, key: str, chunk_target_rows: int,
) -> tuple[list, object | None]:
    """Key-aligned (lo, hi) chunk bounds + the stable sort order.

    Stable-argsorts the frame by the partition key and accumulates whole
    key-runs into ~``chunk_target_rows`` chunks — a key's rows are NEVER
    split across chunks (the bulk-write rule, frame flavor). Bounds index
    into the returned ``order`` array; a chunk materializes
    ``df.take(order[lo:hi])`` so only one chunk's rows are copied at a
    time. Returns ``(flat_bounds, None)`` when the key dtype is not
    sortable — the caller falls back to flat row-count chunks rather
    than failing the write on its chunking strategy. NaN keys sort as
    singleton runs (no key semantics to preserve).
    """
    try:
        key_vals = host_array(df[key].to_numpy())
        order = np.argsort(key_vals, kind="stable")
        sorted_vals = key_vals[order]
        change = np.flatnonzero(sorted_vals[1:] != sorted_vals[:-1]) + 1
    except (TypeError, ValueError) as e:
        logger.warning(
            f"      partition-key ordering by '{key}' failed "
            f"({type(e).__name__}: {e}); falling back to flat row-count "
            f"chunks (commit boundaries may split keys)")
        return [
            (lo, min(lo + chunk_target_rows, len(df)))
            for lo in range(0, len(df), chunk_target_rows)
        ], None
    group_starts = np.concatenate(([0], change))
    group_ends = np.concatenate((change, [len(order)]))
    bounds: list = []
    lo = 0
    run = 0
    for s, e in zip(group_starts, group_ends):
        glen = int(e - s)
        if run and run + glen > chunk_target_rows:
            bounds.append((lo, lo + run))
            lo += run
            run = 0
        run += glen
    if run:
        bounds.append((lo, lo + run))
    return [(int(a), int(b)) for a, b in bounds], order


async def copy_frame_chunked_async(
    conn,
    table_name: str,
    df: pd.DataFrame,
    *,
    columns: Sequence[str],
    numeric_cols: Sequence[str],
    round_to: int | None = None,
    date_cols: Sequence[str] | None = None,
    chunk_target_rows: int = DEFAULT_CHUNK_TARGET_ROWS,
    label: str = "",
    max_replica_lag_mb: float | None = DEFAULT_MAX_REPLICA_LAG_MB,
    partition_key: str | None = PARTITION_KEY_AUTO,
) -> int:
    """COPY-insert a DataFrame in partition-key-aligned chunks, sanitizing
    each slice INSIDE the loop.

    The DataFrame-flavored force-mode writer: the caller has already
    DELETEd / TRUNCATEd the target scope (conflict-free COPY contract),
    and this helper bounds the sanitized dict list to ONE
    ``chunk_target_rows`` chunk instead of materializing the whole
    table's dicts at once (~1 KB of host RSS per row — a 7M-row
    full-frame sanitize peaked at ~6 GB before this helper).

    Args:
        conn: asyncpg connection (chunks run sequentially on it).
        table_name: schema-qualified target table.
        df: the frame to write. May carry extra columns — ``columns``
            selects the COPY projection (mirroring the former
            ``sanitize(whole frame) + copy_insert(columns=...)`` pairs);
            slices are taken with ``.iloc`` so the frame is not copied.
        columns: explicit COPY column order.
        numeric_cols: columns sanitize_for_db_insert rounds / NaN-sweeps.
        round_to: decimal places for the numeric columns (None = skip).
        date_cols: columns sanitize_for_db_insert emits as python
            ``datetime.date`` objects (asyncpg DATE columns).
        chunk_target_rows: rows per COPY chunk.
        label: optional progress-message prefix.
        max_replica_lag_mb: pause between chunks while a streaming
            standby's replay lag exceeds this (default 4096 MB — 1/16 of
            the primary's 64GB max_slot_wal_keep_size; None disables
            the throttle).
        partition_key: column whose rows are never split across COPY
            chunks (bulk-write rule, frame flavor). Default "auto":
            "date" when the frame carries one, else flat row-count
            chunks with a warning — pass an explicit key for non-date
            frames. None = flat row-count chunks.

    Returns:
        Total rows COPY-inserted.
    """
    n_total = len(df)
    if n_total == 0:
        return 0

    auto = partition_key == PARTITION_KEY_AUTO
    if auto:
        partition_key = "date" if "date" in df.columns else None

    order = None
    if partition_key is not None:
        bounds, order = _partition_key_frame_order(
            df, partition_key, chunk_target_rows,
        )
    else:
        if auto:
            logger.warning(
                f"      {table_name}: no 'date' column and no explicit "
                f"partition_key — chunking flat by row count (the "
                f"bulk-write rule prefers partition-key-aligned chunks)")
        bounds = [
            (lo, min(lo + chunk_target_rows, n_total))
            for lo in range(0, n_total, chunk_target_rows)
        ]
    n_chunks = len(bounds)
    cols = list(columns)
    prefix = f"      {label} " if label else "      "
    total = 0
    for i, (lo, hi) in enumerate(bounds, start=1):
        if max_replica_lag_mb is not None:
            await _wait_for_replica_lag_async(conn, max_replica_lag_mb)
        chunk_df = df.take(order[lo:hi]) if order is not None else df.iloc[lo:hi]
        rows = sanitize_for_db_insert(
            chunk_df, numeric_cols=list(numeric_cols), round_to=round_to,
            date_cols=list(date_cols) if date_cols is not None else None,
        )
        n = await copy_insert_async(conn, table_name, rows, columns=cols)
        total += n
        logger.info(f"{prefix}chunk {i}/{n_chunks}: COPY {n:,} rows "
              f"(cumulative {total:,})")
    return total


async def csv_copy_frame_chunked_async(
    conn,
    table_name: str,
    df: pd.DataFrame,
    *,
    partition_key: str = "code",
    chunk_target_rows: int = DEFAULT_CHUNK_TARGET_ROWS,
    label: str = "",
    max_replica_lag_mb: float | None = DEFAULT_MAX_REPLICA_LAG_MB,
) -> int:
    """CSV-COPY a DataFrame in partition-key-aligned chunks — the
    DataFrame-COPY flavor of the bulk-write rule (the frame flavor of
    :func:`copy_frame_chunked_async` for callers whose frames are ALREADY
    sanitized, so no per-chunk re-sanitize is wanted; the raw
    ``csv_copy_from_frame_async`` commits the WHOLE frame in one
    transaction, which retains all its WAL behind one commit on
    force-mode rebuilds that reach millions of rows).

    Chunks never split a key (``_partition_key_frame_order`` — stable
    key-sorted bounds); each chunk commits its own transaction with the
    replica-lag cap paused before it.

    SAFE ONLY when the target is conflict-free (truncated /
    PK-filtered-missing scope) — same contract as
    ``csv_copy_from_frame_async``.
    """
    n_total = len(df)
    if n_total == 0:
        return 0
    bounds, order = _partition_key_frame_order(df, partition_key,
                                               chunk_target_rows)
    n_chunks = len(bounds)
    prefix = f"      {label} " if label else "      "
    total = 0
    for i, (lo, hi) in enumerate(bounds, start=1):
        if max_replica_lag_mb is not None:
            await _wait_for_replica_lag_async(conn, max_replica_lag_mb)
        chunk_df = df.take(order[lo:hi]) if order is not None else df.iloc[lo:hi]
        total += await csv_copy_from_frame_async(conn, table_name, chunk_df)
        logger.info(f"{prefix}chunk {i}/{n_chunks}: CSV COPY {hi - lo:,} rows "
              f"(cumulative {total:,})")
    return total
