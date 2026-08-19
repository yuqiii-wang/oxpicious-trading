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

from datetime import date

from psycopg import sql
from psycopg.rows import dict_row

from ._helpers import _parse_table_name


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


def bulk_upsert(conn, table_name: str, rows: list, key_columns: list, batch_size: int = 1000) -> int:
    """Perform bulk upsert (INSERT ... ON CONFLICT DO UPDATE/NOTHING) (sync).

    Uses psycopg3's executemany (pipeline-mode: multiple Bind+Execute sent
    without per-row round-trips) wrapped in a single transaction for atomicity
    and WAL efficiency. A 233k-row insert goes from 233 COMMITs/WAL flushes
    (autocommit-per-batch) to 1, which is also the best defense against
    checkpoint storms on bulk loads.

    Args:
        conn: psycopg3 connection
        table_name: target table (may include schema prefix like "stats.debt_identity")
        rows: list of dictionaries, each representing a row
        key_columns: list of column names forming the primary key
        batch_size: chunk size for pipelined executemany (capped at 1000).

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

    all_values = [tuple(row[c] for c in columns) for row in rows]

    try:
        # Wrap ALL batches in a single transaction. Without this, each
        # executemany call would be its own implicit transaction (autocommit),
        # producing a separate COMMIT + WAL flush per batch — the root cause
        # of the checkpoint storms observed during bulk build-script runs.
        with conn.transaction():
            with conn.cursor() as cur:
                for i in range(0, len(all_values), batch_size):
                    chunk = all_values[i:i + batch_size]
                    cur.executemany(query, chunk)
        return len(rows)
    except Exception as e:
        print(
            f"    [ERROR] Bulk upsert failed for {table_name}: "
            f"{type(e).__name__}: {e}",
            flush=True,
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
            print(f"    [INFO] Creating table {table_name}", flush=True)
            cur.execute(create_sql)
            print(f"    [INFO] Table {table_name} created", flush=True)


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
            print(f"    [INFO] Table {table_name} does not exist, skipping truncate", flush=True)
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
        print(f"    [INFO] Truncated table {table_name}", flush=True)