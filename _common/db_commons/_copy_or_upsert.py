"""
_copy_or_upsert.py — Copy-or-upsert split for fast-path bulk inserts.

Provides:
  - copy_or_upsert_split_async() — split rows by MAX(date), COPY new dates + upsert existing
  - copy_or_upsert_split_pool_async() — pool-based variant for parallel table writes
  - get_max_table_date_async() — query MAX(date) from a table
"""
from __future__ import annotations

import datetime
from typing import Optional

from ._helpers import (
    _parse_table_name,
    _group_rows_by_key,
    _build_chunks,
    DEFAULT_COMMIT_CHUNK_ROWS,
)
from ._async_ops import (
    copy_insert_async,
    bulk_upsert_async,
    _wait_for_replica_lag_async,
    DEFAULT_MAX_REPLICA_LAG_MB,
)


def _to_date(v) -> datetime.date:
    """Normalize date/datetime/pd.Timestamp to datetime.date for comparison.

    ``sanitize_for_db_insert`` emits ``datetime.datetime`` for datetime64
    columns while PostgreSQL MAX() on a DATE column returns ``date`` —
    comparing the two raises TypeError. Both sides are normalized to the
    DAY granularity: routing a same-day row to the upsert path is always
    safe (ON CONFLICT handles new + existing rows); only rows strictly
    AFTER the max day take the COPY fast path (nothing exists there).
    """
    if isinstance(v, datetime.datetime):  # covers pd.Timestamp subclass
        return v.date()
    return v  # datetime.date


async def copy_or_upsert_split_async(
    conn,
    table_name: str,
    rows: list[dict],
    key_columns: list[str],
    date_column: str = "date",
    *,
    commit_chunk_rows: int = DEFAULT_COMMIT_CHUNK_ROWS,
    partition_key: str | None = "auto",
    max_replica_lag_mb: float | None = DEFAULT_MAX_REPLICA_LAG_MB,
) -> tuple[int, int]:
    """Split rows by MAX(date) in the target table and write via COPY (new
    dates) or bulk upsert (existing dates with PK conflicts).

    Rows whose ``date_column`` value is strictly greater than the table's
    MAX(date) are written via PostgreSQL COPY (5-10× faster than
    INSERT...ON CONFLICT). Rows at or before MAX(date) fall back to
    ``bulk_upsert_async`` for correct conflict handling.

    When the target table is empty (MAX(date) is NULL), all rows go
    through COPY. When the target table has rows but all new rows are
    after MAX(date), the entire batch uses COPY.

    BOTH paths commit in bounded chunks of ~``commit_chunk_rows`` rows
    (each ``copy_insert_async`` / ``bulk_upsert_async`` commit chunk is
    its own transaction), so a replication slot advances — and the
    primary can recycle WAL — continuously during the load instead of
    retaining everything behind one giant transaction. When
    ``partition_key`` is given, chunk boundaries never split one key's
    rows across commits. Between chunks, ``max_replica_lag_mb`` pauses
    the writer while a streaming standby lags further behind than the
    cap (no-op without a replica).

    This consolidates the "check MAX date → split → COPY + upsert" logic
    that was previously duplicated across every build script.

    Args:
        conn: asyncpg connection.
        table_name: schema-qualified table (e.g. "stats.stock_identity").
        rows: list of row dicts. Must contain ``date_column`` and all
              ``key_columns``.
        key_columns: PK column names (e.g. ["date", "code"]).
        date_column: column name used for MAX(date) boundary detection.
        commit_chunk_rows: rows per COMMIT on both paths (default 100K).
        partition_key: column name whose values are never split across
              commit boundaries (whole-key commits). Default "auto" →
              ``date_column`` (the repo-wide partitioning convention);
              explicit name wins; None = flat row-count chunks.
        max_replica_lag_mb: pause between commit chunks while the streaming
              standby's replay lag exceeds this (default 4096 MB — 1/16 of
              the primary's 64GB max_slot_wal_keep_size; None disables
              the throttle).

    Returns:
        (n_copied, n_upserted) tuple. n_copied is the number of rows
        written via COPY; n_upserted is the number written via upsert.
    """
    if not rows:
        return (0, 0)

    # Bulk-write rule: "auto" chunking key is the MAX(date) column itself —
    # rows are date-partitioned, so whole dates per commit are the natural
    # alignment (and no other key grouping is guaranteed to exist).
    if partition_key == "auto":
        partition_key = date_column

    schema, table = _parse_table_name(table_name)
    from_clause = f'"{schema}"."{table}"' if schema else f'"{table}"'

    # Fast-path: query MAX(date) once using the PK index
    row = await conn.fetchrow(
        f'SELECT MAX("{date_column}") AS max_date FROM {from_clause}'
    )
    max_date = row["max_date"] if row and row["max_date"] is not None else None
    max_day = _to_date(max_date) if max_date is not None else None

    # Partition: rows with date > max_date are safely new (no PK conflict)
    copy_batch: list[dict] = []
    upsert_batch: list[dict] = []
    for r in rows:
        if max_day is not None and _to_date(r[date_column]) <= max_day:
            upsert_batch.append(r)
        else:
            copy_batch.append(r)

    n_copied = 0
    n_upserted = 0

    # Execute COPY for the new-date batch (the common case), one
    # transaction per commit chunk so the replication slot advances
    # between chunks.
    if copy_batch:
        if partition_key is not None:
            copy_chunks = _build_chunks(
                _group_rows_by_key(copy_batch, partition_key),
                commit_chunk_rows,
            )
        else:
            copy_chunks = [
                copy_batch[lo:lo + commit_chunk_rows]
                for lo in range(0, len(copy_batch), commit_chunk_rows)
            ]
        for chunk in copy_chunks:
            # Between COPY commit chunks (no transaction open, no locks):
            # wait out standby replay lag before generating more WAL.
            if max_replica_lag_mb is not None:
                await _wait_for_replica_lag_async(conn, max_replica_lag_mb)
            n_copied += await copy_insert_async(conn, table_name, chunk)

    # Execute upsert for the gap/history batch (usually empty); it
    # applies its own bounded commit chunks + lag throttle internally.
    if upsert_batch:
        n_upserted = await bulk_upsert_async(
            conn, table_name, upsert_batch, key_columns,
            commit_chunk_rows=commit_chunk_rows,
            partition_key=partition_key,
            max_replica_lag_mb=max_replica_lag_mb,
        )

    return (n_copied, n_upserted)


async def copy_or_upsert_split_pool_async(
    pool,
    table_name: str,
    rows: list[dict],
    key_columns: list[str],
    date_column: str = "date",
    *,
    commit_chunk_rows: int = DEFAULT_COMMIT_CHUNK_ROWS,
    partition_key: str | None = "auto",
    max_replica_lag_mb: float | None = DEFAULT_MAX_REPLICA_LAG_MB,
) -> tuple[int, int]:
    """Pool-based variant of ``copy_or_upsert_split_async``.

    Borrows a connection from ``pool`` and delegates to
    ``copy_or_upsert_split_async``. Used to run independent table writes
    in parallel via ``asyncio.gather`` — each parallel task gets its own
    connection from the pool, avoiding the single-connection bottleneck.

    SAFETY: each call processes ONE table's rows atomically on ONE
    connection (MAX(date) → split → COPY/upsert). Only multiple
    INDEPENDENT tables should be run in parallel — the same table MUST
    NOT be written from multiple pool connections concurrently, because
    the MAX(date) check would race and cause COPY PK conflicts.

    Args:
        pool: asyncpg connection pool.
        table_name, rows, key_columns, date_column, commit_chunk_rows,
        partition_key, max_replica_lag_mb: see
            ``copy_or_upsert_split_async``.

    Returns:
        (n_copied, n_upserted).
    """
    async with pool.acquire() as conn:
        return await copy_or_upsert_split_async(
            conn, table_name, rows, key_columns, date_column,
            commit_chunk_rows=commit_chunk_rows,
            partition_key=partition_key,
            max_replica_lag_mb=max_replica_lag_mb,
        )


async def get_max_table_date_async(
    conn,
    table_name: str,
    date_column: str = "date",
    where_clause: str = "",
) -> Optional[str]:
    """Return MAX(date) from a table, or None if the table is empty.

    Uses the primary-key index on (date, ...) for an index-only scan.

    Args:
        conn: asyncpg connection.
        table_name: schema-qualified table name (e.g. "stats.stock_identity").
        date_column: name of the date column (default "date").
        where_clause: optional SQL fragment appended after WHERE
            (e.g. "code LIKE '%.SS%'"). No leading "WHERE" needed.
    """
    schema, table = _parse_table_name(table_name)
    from_clause = f'"{schema}"."{table}"' if schema else f'"{table}"'
    sql = f'SELECT MAX("{date_column}") AS max_date FROM {from_clause}'
    if where_clause:
        sql += f' WHERE {where_clause}'
    row = await conn.fetchrow(sql)
    return row["max_date"] if row and row["max_date"] is not None else None