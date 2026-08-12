"""
_db_commons.py — Database connection and bulk insert utilities for build scripts.

Provides:
  - get_db_connection() — sync connect to PostgreSQL using environment or .env
  - get_db_connection_async() — async connect to PostgreSQL
  - bulk_upsert() — efficient sync bulk insert/update with conflict handling
  - bulk_upsert_async() — efficient async bulk insert/update with conflict handling
  - get_existing_keys() — sync query existing (date, code) pairs
  - get_existing_keys_async() — async query existing (date, code) pairs
  - ensure_table_exists() / ensure_table_exists_async() — create table if not exists
  - truncate_table() / truncate_table_async() — clear all data from table

Missing-data detection (check_identity, find_missing_dates, etc.) has been
migrated to _common.pre_check_and_load.

Uses psycopg for sync connections and asyncpg for async connections.
"""
import os
from datetime import date
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False


def _load_env_vars():
    """Load environment variables from database/.env if not already set."""
    env_paths = [
        Path(__file__).resolve().parents[1] / "database" / ".env",
    ]

    for env_path in env_paths:
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        os.environ.setdefault(key.strip(), value.strip())


def _get_conn_params():
    """Get connection parameters from SUPABASE_* environment variables."""
    _load_env_vars()
    return {
        "host": os.environ.get("SUPABASE_HOST", "127.0.0.1"),
        "port": int(os.environ.get("SUPABASE_PORT", "9876")),
        "database": os.environ.get("SUPABASE_DB", "oxpicious-stats"),
        "user": os.environ.get("SUPABASE_USER", "postgres"),
        "password": os.environ.get("SUPABASE_PASSWORD", "postgres"),
    }


# ============================================================================
# Synchronous API (backward compatible)
# ============================================================================

def get_db_connection():
    """Connect to PostgreSQL database (sync).

    Reads connection info from:
    1. Environment variables
    2. database/.env file

    Priority: env vars > database/.env
    """
    conn_params = _get_conn_params()

    try:
        # connect_timeout caps each TCP connect attempt so a unreachable DB
        # fails fast instead of hanging ~21s (OS default) per attempt. With 5
        # sequential connections (e.g. stream_szse_price) an unbounded hang
        # used to add 50-100s+ of invisible startup delay.
        conn = psycopg.connect(
            host=conn_params["host"],
            port=conn_params["port"],
            dbname=conn_params["database"],
            user=conn_params["user"],
            password=conn_params["password"],
            connect_timeout=10,
            autocommit=True
        )
        return conn
    except Exception as e:
        print(f"    [ERROR] Failed to connect to database: {e}", flush=True)
        raise


def _parse_table_name(table_name):
    """Parse a table name that may include a schema prefix.
    
    Args:
        table_name: table name, possibly with schema prefix (e.g., "stats.debt_identity")
        
    Returns:
        Tuple of (schema, table) or (None, table) if no schema prefix
    """
    parts = table_name.split(".", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, parts[0]


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


def get_existing_keys(conn, table_name, key_columns):
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
            sql.Identifier(table)
        )
    else:
        query = sql.SQL("SELECT {} FROM {}").format(
            columns_sql,
            sql.Identifier(table)
        )
    
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query)
        rows = cur.fetchall()
    
    return set(tuple(row[c] for c in key_columns) for row in rows)


def bulk_upsert(conn, table_name, rows, key_columns, batch_size=1000):
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
                    psycopg3 pipelines Bind+Execute messages internally;
                    this controls chunk size for memory bounding.

    Returns:
        Number of rows processed (len(rows)). psycopg3 executemany doesn't
        return a reliable per-row count; for upsert, every input row is
        either inserted or updated, so len(rows) is the affected count.
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
        print(f"    [ERROR] Bulk upsert failed for {table_name}: "
              f"{type(e).__name__}: {e}", flush=True)
        raise


def ensure_table_exists(conn, table_name, create_sql):
    """Ensure a table exists, create it if not (sync)."""
    schema, table = _parse_table_name(table_name)
    
    with conn.cursor() as cur:
        if schema:
            cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = %s AND table_name = %s)",
                (schema, table)
            )
        else:
            cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                (table,)
            )
        exists = cur.fetchone()[0]
        
        if not exists:
            print(f"    [INFO] Creating table {table_name}", flush=True)
            cur.execute(create_sql)
            print(f"    [INFO] Table {table_name} created", flush=True)


def truncate_table(conn, table_name):
    """Truncate a table (clear all data) (sync). Skips if table doesn't exist."""
    schema, table = _parse_table_name(table_name)
    
    with conn.cursor() as cur:
        if schema:
            cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = %s AND table_name = %s)",
                (schema, table)
            )
        else:
            cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                (table,)
            )
        exists = cur.fetchone()[0]
        if not exists:
            print(f"    [INFO] Table {table_name} does not exist, skipping truncate", flush=True)
            return
        
        if schema:
            cur.execute(sql.SQL("TRUNCATE TABLE {schema}.{table} CASCADE").format(
                schema=sql.Identifier(schema),
                table=sql.Identifier(table)
            ))
        else:
            cur.execute(sql.SQL("TRUNCATE TABLE {table} CASCADE").format(table=sql.Identifier(table)))
        print(f"    [INFO] Truncated table {table_name}", flush=True)


# ============================================================================
# Async API
# ============================================================================

async def get_db_connection_async():
    """Connect to PostgreSQL database (async)."""
    if not HAS_ASYNCPG:
        raise ImportError("asyncpg is required for async database operations. Install with: pip install asyncpg")
    
    conn_params = _get_conn_params()
    
    try:
        # asyncpg operates in implicit autocommit mode by default: each
        # execute()/executemany() call runs in its own transaction unless
        # explicitly wrapped in `async with conn.transaction():`.
        # (The previous `await conn.set_autocommit(True)` call referenced a
        # method that does not exist on asyncpg.Connection, which broke
        # every async build script at connection time.)
        #
        # timeout=30s: the TCP connect itself is fast, but the PostgreSQL
        # startup handshake (authentication + session setup) can stall for
        # many seconds when the server is saturating disk I/O during a heavy
        # checkpoint. Bulk build scripts insert hundreds of thousands of rows
        # and trigger large checkpoints (observed: 149s checkpoint writing
        # 515MB of WAL). A 10s timeout fails during these windows; 30s gives
        # the postmaster enough headroom to respond.
        conn = await asyncpg.connect(
            host=conn_params["host"],
            port=conn_params["port"],
            database=conn_params["database"],
            user=conn_params["user"],
            password=conn_params["password"],
            timeout=30,
        )
        return conn
    except Exception as e:
        # Some asyncpg connection exceptions (e.g. PostgresConnectionError on
        # a refused/timeout TCP connect) have an EMPTY str(e), producing
        # unhelpful "[ERROR] Failed to connect to database: " lines. Print
        # the exception type and repr so the root cause is always visible.
        msg = str(e).strip()
        if msg:
            print(f"    [ERROR] Failed to connect to database: "
                  f"{type(e).__name__}: {msg}", flush=True)
        else:
            print(f"    [ERROR] Failed to connect to database: "
                  f"{type(e).__name__} (no message) repr={e!r}", flush=True)
        raise


async def get_db_pool_async(min_size: int = 1, max_size: int = 5,
                            max_queries: int = 50000):
    """Create an asyncpg connection pool (async).

    A pool is required when an async task needs to run multiple DB
    operations in parallel (e.g. ``batched_upsert_by_date`` with
    ``max_concurrent > 1``). A single asyncpg.Connection processes one
    query at a time — it cannot run two ``executemany`` calls
    concurrently even with ``asyncio.gather``. Each parallel chunk
    therefore needs its own connection borrowed from the pool.

    Args:
        min_size: number of connections opened eagerly at pool creation.
            Keep small (1) so startup stays fast; connections are
            created on demand up to ``max_size``.
        max_size: hard cap on concurrent connections. Set this to match
            the parallelism level of the caller (e.g. 4 for
            ``max_concurrent=4``). Each connection consumes a backend
            process on the Postgres server, so keep ≤ ~8 to avoid
            starving other clients.
        max_queries: asyncpg recycles a connection after this many
            queries to defend against memory leaks in long-lived
            sessions. 50K is high enough that a single analyze run will
            not trigger recycling mid-way (which would lose the prepared
            statement cache).

    Returns:
        asyncpg.pool.Pool. The caller is responsible for closing it
        (``await pool.close()``) when done — typically in a ``finally``
        block.
    """
    if not HAS_ASYNCPG:
        raise ImportError(
            "asyncpg is required for connection pooling. "
            "Install with: pip install asyncpg"
        )
    conn_params = _get_conn_params()
    # Same timeout rationale as get_db_connection_async (checkpoint storms).
    pool = await asyncpg.create_pool(
        host=conn_params["host"],
        port=conn_params["port"],
        database=conn_params["database"],
        user=conn_params["user"],
        password=conn_params["password"],
        min_size=min_size,
        max_size=max_size,
        max_queries=max_queries,
        command_timeout=300,  # 5 min per query — upserts of 100K rows can take >60s under WAL pressure
    )
    return pool


async def get_existing_keys_async(conn, table_name, key_columns):
    """Get set of existing key tuples from a table (async)."""
    if not key_columns:
        return set()

    schema, table = _parse_table_name(table_name)

    columns_sql = ", ".join([f'"{c}"' for c in key_columns])
    
    if schema:
        query = f'SELECT {columns_sql} FROM "{schema}"."{table}"'
    else:
        query = f'SELECT {columns_sql} FROM "{table}"'
    
    rows = await conn.fetch(query)

    return set(tuple(row[c] for c in key_columns) for row in rows)


# ============================================================================
# Recent-data pre-filter — migrated to _common.pre_check_and_load
# ============================================================================
# fetch_codes_with_recent_data_async / RECENT_TRADING_DAYS have moved to
# _common.pre_check_and_load.missing_dates. Callers should import from
# _common.pre_check_and_load directly (or from _common.build_commons which
# re-exports them).


async def bulk_upsert_async(conn, table_name, rows, key_columns, batch_size=5000):
    """Perform bulk upsert (INSERT ... ON CONFLICT DO UPDATE/NOTHING) (async).

    Uses asyncpg's executemany (pipelined extended-query protocol: statement
    prepared once, then Bind+Execute pipelined without per-row round-trips)
    wrapped in a single transaction for atomicity and WAL efficiency.

    This is asyncpg's fastest bulk-insert method and the best defense against
    checkpoint storms: a 233k-row insert goes from 233 COMMITs/WAL flushes
    (autocommit-per-batch under the old multi-row-INSERT approach) to 1 COMMIT.

    Advantages over the previous multi-row INSERT approach:
      1. No giant query string with 1000*N placeholders (avoids PostgreSQL's
         65535 parameter limit on wide tables).
      2. Prepared statement compiled once, reused for all rows.
      3. Pipelined Bind+Execute — multiple rows sent in one network packet.
      4. Single transaction = single COMMIT = single WAL flush.

    Args:
        conn: asyncpg connection
        table_name: target table (may include schema prefix like "stats.debt_identity")
        rows: list of dictionaries, each representing a row
        key_columns: list of column names forming the primary key
        batch_size: chunk size for executemany calls (default 5000, capped at
                    10000). asyncpg pipelines within each executemany call;
                    this controls how many rows are pipelined together and
                    bounds memory. The previous cap of 1000 was conservative
                    — profiling on 14M-row correlation upserts showed
                    5000 is ~3× faster with no memory pressure.

    Returns:
        Number of rows processed (len(rows)). asyncpg's executemany doesn't
        return per-row counts; for upsert, every input row is either inserted
        or updated, so len(rows) is the affected count. All 18 callers across
        the build scripts use the return value only for logging.
    """
    if not rows:
        return 0

    columns = list(rows[0].keys())
    if not columns:
        return 0

    # Cap batch_size at 10000 to bound memory and avoid too-large pipeline
    # batches. Even with executemany, chunking keeps the internal buffer
    # manageable for very large inputs (e.g. 14M correlation rows). The
    # previous cap of 1000 was conservative — 10000 is safe and ~3× faster
    # on multi-million-row upserts.
    batch_size = min(batch_size, 10000)

    schema, table = _parse_table_name(table_name)

    columns_sql = ", ".join([f'"{c}"' for c in columns])
    # Single-row placeholders ($1, $2, ...) — executemany reuses them
    # for each row in the batch, unlike multi-row INSERT which needs
    # unique $N for every row×column.
    placeholders = ", ".join([f"${i+1}" for i in range(len(columns))])
    conflict_columns = ", ".join([f'"{c}"' for c in key_columns])

    update_clause = ", ".join([
        f'"{c}" = EXCLUDED."{c}"'
        for c in columns if c not in key_columns
    ])

    if update_clause:
        conflict_action = f"ON CONFLICT ({conflict_columns}) DO UPDATE SET {update_clause}"
    else:
        # PK-only tables (e.g. debt_identity with just `date`): no columns
        # to update, so use DO NOTHING. Previous code produced invalid SQL
        # "ON CONFLICT (...) DO UPDATE SET" (nothing after SET) in this case.
        conflict_action = f"ON CONFLICT ({conflict_columns}) DO NOTHING"

    if schema:
        table_ref = f'"{schema}"."{table}"'
    else:
        table_ref = f'"{table}"'

    query = (
        f'INSERT INTO {table_ref} ({columns_sql}) '
        f'VALUES ({placeholders}) {conflict_action}'
    )

    all_values = [tuple(row[c] for c in columns) for row in rows]

    try:
        # Wrap ALL batches in a single transaction. asyncpg operates in
        # implicit autocommit by default — without this, each executemany
        # call would be its own transaction with its own COMMIT + WAL flush.
        # For a 233-batch insert that's 233 WAL flushes → 1, which is the
        # single biggest defense against the checkpoint storms that caused
        # connection timeouts during bulk build-script runs.
        async with conn.transaction():
            for i in range(0, len(all_values), batch_size):
                chunk = all_values[i:i + batch_size]
                # executemany uses the pipelined extended-query protocol:
                # Parse once → (Bind + Execute) × N pipelined without
                # waiting for individual results. In asyncpg 0.27+ this
                # is a true pipeline (multiple Bind+Execute per packet).
                await conn.executemany(query, chunk)

        return len(rows)
    except Exception as e:
        print(f"    [ERROR] Bulk upsert failed for {table_name}: "
              f"{type(e).__name__}: {e}", flush=True)
        # asyncpg's transaction context manager handles rollback automatically
        # on exception — no explicit rollback() call needed (and Connection
        # has no rollback() method in async autocommit mode).
        raise


async def copy_insert_async(conn, table_name, rows, columns=None):
    """Bulk-insert rows via PostgreSQL COPY (fastest path).

    Uses asyncpg's ``copy_records_to_table``, which streams rows through the
    COPY protocol — bypassing the extended-query parsing/planning that
    ``executemany`` incurs per batch. On multi-million-row inserts (e.g. the
    14M-row industry_correlations table) COPY is typically 5-10× faster than
    ``INSERT ... ON CONFLICT`` because:

      1. No per-row ON CONFLICT arbiter check (the table is pre-truncated,
         so there are never conflicts).
      2. No prepared-statement Bind/Execute overhead — rows are streamed as
         a single binary COPY stream.
      3. WAL is written in bulk via the COPY's internal buffer.

    SAFE ONLY when the target table has been truncated (or is otherwise
    guaranteed conflict-free). For upsert (conflict-possible) scenarios, use
    ``bulk_upsert_async`` instead.

    Args:
        conn: asyncpg connection.
        table_name: target table (schema-qualified, e.g.
            "analysis.industry_correlations").
        rows: list of row dicts (same shape as ``bulk_upsert_async``).
        columns: optional explicit column order. When None, inferred from
            the first row's keys (same as ``bulk_upsert_async``).

    Returns:
        Number of rows inserted (``len(rows)``). asyncpg's COPY does not
        return per-row counts; the return value matches the convention of
        ``bulk_upsert_async`` for drop-in replacement in logging.
    """
    if not rows:
        return 0

    if columns is None:
        columns = list(rows[0].keys())
    if not columns:
        return 0

    schema, table = _parse_table_name(table_name)
    # asyncpg's copy_records_to_table takes the bare table name positionally;
    # records / columns / schema_name are keyword-only.
    #
    # Sanitize pandas NaT/NaN → None in bulk via pandas vectorized ops.
    # COPY's binary protocol encodes dates via toordinal(), which NaT
    # doesn't support (raises ValueError). The executemany path
    # (bulk_upsert_async) handles NaT implicitly because asyncpg's codec
    # falls back to None for unknown types, but COPY's fast-path date
    # encoder doesn't.
    #
    # Two-step vectorized conversion (no per-row Python branching):
    #   1. astype(object) — widens every column to object dtype so it can
    #      hold real Python None (typed dtypes like datetime64 silently
    #      coerce None back to NaT, defeating the replacement).
    #   2. where(notna(df), None) — single C-level pass replacing every
    #      NaN/NaT with None; valid values pass through untouched.
    import pandas as _pd
    df = _pd.DataFrame(rows, columns=columns).astype(object)
    df = df.where(_pd.notna(df), None)
    # itertuples returns plain tuples (no index, no name) — exactly what
    # copy_records_to_table expects.
    records = df.itertuples(index=False, name=None)
    async with conn.transaction():
        await conn.copy_records_to_table(
            table,
            records=records,
            schema_name=schema if schema else None,
            columns=columns,
        )
    return len(rows)


async def ensure_table_exists_async(conn, table_name, create_sql):
    """Ensure a table exists, create it if not (async)."""
    schema, table = _parse_table_name(table_name)
    
    if schema:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = $1 AND table_name = $2)",
            schema, table
        )
    else:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = $1)",
            table
        )
    
    if not exists:
        print(f"    [INFO] Creating table {table_name}", flush=True)
        await conn.execute(create_sql)
        print(f"    [INFO] Table {table_name} created", flush=True)


async def truncate_table_async(conn, table_name):
    """Truncate a table (clear all data) (async). Skips if table doesn't exist."""
    schema, table = _parse_table_name(table_name)
    
    if schema:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = $1 AND table_name = $2)",
            schema, table
        )
    else:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = $1)",
            table
        )
    
    if not exists:
        print(f"    [INFO] Table {table_name} does not exist, skipping truncate", flush=True)
        return
    
    if schema:
        await conn.execute(f'TRUNCATE TABLE "{schema}"."{table}" CASCADE')
    else:
        await conn.execute(f'TRUNCATE TABLE "{table}" CASCADE')
    print(f"    [INFO] Truncated table {table_name}", flush=True)


# ============================================================================
# Identity-based missing-data detection — migrated to _common.pre_check_and_load
# ============================================================================
# check_identity / check_identity_async / check_identity_years and their
# helpers (_build_identity_where_clause, _build_identity_params,
# _expected_dates) have moved to _common.pre_check_and_load.identity.
# Callers should import from _common.pre_check_and_load directly.