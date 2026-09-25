"""
_sync_ops.py — Synchronous database operations.

Provides:
  - check_stock_intraday_exists() — check if intraday data exists (sync)
  - get_existing_keys() — query existing key tuples (sync)
  - bulk_upsert() — efficient sync bulk insert/update with conflict handling
  - ensure_table_exists() — create table if not exists (sync)
  - truncate_table() — clear all data from table (sync)
"""
from __future__ import annotations

import logging
import time
from datetime import date

from psycopg import sql
from psycopg.rows import dict_row

from ._async_ops import DEFAULT_MAX_REPLICA_LAG_MB
from ._helpers import (
    _parse_table_name,
    _build_commit_chunks,
    _resolve_partition_key,
    DEFAULT_COMMIT_CHUNK_ROWS,
    PARTITION_KEY_AUTO,
)

logger = logging.getLogger(__name__)


def _wait_for_replica_lag(conn, max_lag_mb: float, poll_s: float = 2.0) -> None:
    """Pause until the streaming standby's replay lag is within cap (sync).

    Sync twin of ``_async_ops._wait_for_replica_lag_async`` — called
    between commit chunks (never inside a transaction) so pausing holds
    no locks and the primary stops generating WAL while the replica
    drains. Returns immediately when no standby is streaming; a query
    error (permission, broken connection) disables the wait rather than
    failing a write pipeline over monitoring.
    """
    max_lag_bytes = int(max_lag_mb * 1024 * 1024)
    warned = False
    while True:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT max(pg_wal_lsn_diff(pg_current_wal_lsn(), "
                    "replay_lsn)) FROM pg_stat_replication "
                    "WHERE state IN ('streaming', 'catchup')"
                )
                row = cur.fetchone()
        except Exception as e:
            logger.warning(
                f"      replica-lag check unavailable "
                f"({type(e).__name__}: {e}); continuing without throttle")
            return
        lag = row[0] if row else None
        if lag is None or lag <= max_lag_bytes:
            return
        if not warned:
            logger.info(
                f"      replica replay lag "
                f"{lag / 1024 / 1024:,.0f} MB exceeds "
                f"{max_lag_mb:,.0f} MB cap — pausing between commit "
                f"chunks until it drains")
            warned = True
        time.sleep(poll_s)


def check_stock_intraday_exists(conn, code: str, check_date: date) -> bool:
    """Check if stock intraday data already exists for a given date (sync).

    Args:
        conn: psycopg connection
        code: stock code with exchange suffix (e.g., "002080.SZ")
        check_date: date to check

    Returns:
        True if data exists, False otherwise
    """
    query = """
        SELECT EXISTS (SELECT 1 FROM stats.stock_intraday_5min 
                       WHERE code = %s AND date = %s)
    """
    with conn.cursor() as cur:
        cur.execute(query, (code, check_date))
        return cur.fetchone()[0]


def get_existing_keys(conn, table_name: str, key_columns: list) -> set:
    """Get set of existing key tuples from a table (sync).

    Args:
        conn: psycopg connection
        table_name: table to query (may include schema prefix like "stats.debt_identity")
        key_columns: list of column names forming the primary key

    Returns:
        Set of tuples representing existing keys
    """
    if not key_columns:
        return set()

    schema, table = _parse_table_name(table_name)

    columns_sql = sql.SQL(", ").join([sql.Identifier(c) for c in key_columns])

    if schema:
        query = sql.SQL("SELECT {} FROM {}.{}").format(
            columns_sql,
            sql.Identifier(schema),
            sql.Identifier(table),
        )
    else:
        query = sql.SQL("SELECT {} FROM {}").format(
            columns_sql,
            sql.Identifier(table),
        )

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query)
        rows = cur.fetchall()

    return set(tuple(row[c] for c in key_columns) for row in rows)


def bulk_upsert(
    conn,
    table_name: str,
    rows: list,
    key_columns: list,
    batch_size: int = 1000,
    *,
    commit_chunk_rows: int = DEFAULT_COMMIT_CHUNK_ROWS,
    partition_key: str | None = PARTITION_KEY_AUTO,
    max_replica_lag_mb: float | None = DEFAULT_MAX_REPLICA_LAG_MB,
) -> int:
    """Perform bulk upsert (INSERT ... ON CONFLICT DO UPDATE/NOTHING) (sync).

    Uses psycopg3's executemany (pipeline-mode: multiple Bind+Execute sent
    without per-row round-trips), committed in bounded chunks of
    ~``commit_chunk_rows`` rows — the checkpoint-storm defense of the old
    single-transaction design (thousands of rows amortize each COMMIT)
    WITHOUT its replication hazard: a slot cannot advance past an
    uncommitted transaction, so a multi-GB single-transaction upsert froze
    the replica ~29 GB behind while the primary kept writing WAL. Bounded
    commits let the slot — and WAL recycling — advance continuously.
    ON CONFLICT idempotency makes per-chunk commits crash-safe (a rerun
    re-upserts whatever did not commit).

    THE BULK-WRITE RULE (chunk by partition key → one COMMIT per chunk →
    replica-lag throttle between chunks) is the DEFAULT: ``partition_key``
    "auto" aligns commit boundaries so one key's rows are never split
    across commits ("date" when the rows carry one, else the first usable
    key column; explicit name wins; None opts out to flat chunks), and
    ``max_replica_lag_mb`` pauses between chunks while the standby lags
    beyond the cap.

    Args:
        conn: psycopg3 connection
        table_name: target table (may include schema prefix like "stats.debt_identity")
        rows: list of dictionaries, each representing a row
        key_columns: list of column names forming the primary key
        batch_size: chunk size for pipelined executemany (capped at 1000).
        commit_chunk_rows: rows per COMMIT (default 100K).
        partition_key: column name whose values are never split across
            commits. Default "auto" (see above); None = flat chunks.
        max_replica_lag_mb: pause between commit chunks while the streaming
            standby's replay lag exceeds this (default 4096 MB — 1/16 of
            the primary's 64GB max_slot_wal_keep_size; None disables).

    Returns:
        Number of rows processed (len(rows)).
    """
    if not rows:
        return 0

    columns = list(rows[0].keys())
    if not columns:
        return 0

    # Cap batch_size at 1000 to bound memory and avoid sending too-large
    # pipeline batches.
    batch_size = min(batch_size, 1000)
    commit_chunk_rows = max(commit_chunk_rows, batch_size)

    schema, table = _parse_table_name(table_name)

    columns_sql = sql.SQL(", ").join([sql.Identifier(c) for c in columns])
    placeholders = sql.SQL(", ").join([sql.Placeholder() for _ in columns])
    conflict_columns = sql.SQL(", ").join([sql.Identifier(c) for c in key_columns])
    update_clause = sql.SQL(", ").join([
        sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c))
        for c in columns if c not in key_columns
    ])

    if update_clause:
        conflict_action = sql.SQL("ON CONFLICT ({}) DO UPDATE SET {}").format(
            conflict_columns, update_clause
        )
    else:
        conflict_action = sql.SQL("ON CONFLICT ({}) DO NOTHING").format(conflict_columns)

    if schema:
        table_ref = sql.Identifier(schema, table)
    else:
        table_ref = sql.Identifier(table)

    query = sql.SQL("INSERT INTO {table} ({cols}) VALUES ({ph}) {conflict}").format(
        table=table_ref, cols=columns_sql, ph=placeholders, conflict=conflict_action,
    )

    # Commit-bounded chunks aligned to the partition key (the bulk-write
    # rule): "auto" resolves to "date" / first usable key column, None
    # keeps legacy flat row-count slices.
    effective_key = _resolve_partition_key(rows[0], key_columns, partition_key)
    commit_chunks = _build_commit_chunks(rows, effective_key, commit_chunk_rows)

    try:
        for chunk in commit_chunks:
            # Between commit chunks (no transaction open, no locks held):
            # wait out standby replay lag so the primary does not pile WAL
            # up faster than the replica can replay it.
            if max_replica_lag_mb is not None:
                _wait_for_replica_lag(conn, max_replica_lag_mb)
            # Dedup by PK within the chunk (last occurrence wins) — rows
            # sharing a PK inside one executemany batch would abort the
            # chunk ("cannot affect row a second time"); keeps every
            # chunk internally conflict-free. Cross-chunk duplicates
            # remain last-chunk-wins.
            by_pk: dict[tuple, dict] = {}
            for row in chunk:
                by_pk[tuple(row[c] for c in key_columns)] = row
            if len(by_pk) != len(chunk):
                logger.warning(
                    f"    [WARN] {table_name}: dropped "
                    f"{len(chunk) - len(by_pk):,} duplicate-PK row(s) "
                    f"within a commit chunk (last occurrence wins)")
            chunk_values = [
                tuple(row[c] for c in columns) for row in by_pk.values()
            ]
            # One COMMIT per chunk: without an explicit transaction each
            # executemany call would be its own implicit transaction
            # (per-batch COMMITs — the old checkpoint storms), while one
            # transaction for ALL chunks would freeze the replication
            # slot behind a single mega-transaction.
            with conn.transaction():
                with conn.cursor() as cur:
                    for i in range(0, len(chunk_values), batch_size):
                        cur.executemany(query, chunk_values[i:i + batch_size])
        return len(rows)
    except Exception as e:
        logger.error(
            f"    [ERROR] Bulk upsert failed for {table_name}: "
            f"{type(e).__name__}: {e}",
        )
        raise


def ensure_table_exists(conn, table_name: str, create_sql: str) -> None:
    """Ensure a table exists, create it if not (sync)."""
    schema, table = _parse_table_name(table_name)

    with conn.cursor() as cur:
        if schema:
            cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = %s AND table_name = %s)",
                (schema, table),
            )
        else:
            cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                (table,),
            )
        exists = cur.fetchone()[0]

        if not exists:
            logger.info(f"    [INFO] Creating table {table_name}")
            cur.execute(create_sql)
            logger.info(f"    [INFO] Table {table_name} created")


def truncate_table(conn, table_name: str) -> None:
    """Truncate a table (clear all data) (sync). Skips if table doesn't exist."""
    schema, table = _parse_table_name(table_name)

    with conn.cursor() as cur:
        if schema:
            cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = %s AND table_name = %s)",
                (schema, table),
            )
        else:
            cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                (table,),
            )
        exists = cur.fetchone()[0]
        if not exists:
            logger.info(f"    [INFO] Table {table_name} does not exist, skipping truncate")
            return

        if schema:
            cur.execute(
                sql.SQL("TRUNCATE TABLE {schema}.{table} CASCADE").format(
                    schema=sql.Identifier(schema),
                    table=sql.Identifier(table),
                )
            )
        else:
            cur.execute(sql.SQL("TRUNCATE TABLE {table} CASCADE").format(table=sql.Identifier(table)))
        logger.info(f"    [INFO] Truncated table {table_name}")