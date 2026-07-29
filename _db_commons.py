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
  - check_identity() / check_identity_async() — find missing trading days in an
    identity table, skipping holidays and weekends (calendar from
    utils._holidays_and_weekdays)

Uses psycopg for sync connections and asyncpg for async connections.
"""
import os
import sys
from datetime import date, time
from pathlib import Path
from typing import Optional, Set

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False

# Import the trading-day calendar from the dedicated utils module so that
# check_identity() can skip holidays/weekends without depending on
# _download_commons (which would create a circular import).
sys.path.insert(0, str(Path(__file__).parent))
from utils._holidays_and_weekdays import (  # noqa: E402
    business_days,
    date_range_forward,
    is_trading_day,
)


def _load_env_vars():
    """Load environment variables from database/.env if not already set."""
    env_paths = [
        Path(__file__).parent / "database" / ".env",
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


async def bulk_upsert_async(conn, table_name, rows, key_columns, batch_size=1000):
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
        batch_size: chunk size for executemany calls (capped at 1000).
                    asyncpg pipelines within each executemany call; this
                    controls how many rows are pipelined together and bounds
                    memory.

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

    # Cap batch_size at 1000 to bound memory and avoid too-large pipeline
    # batches. Even with executemany, chunking keeps the internal buffer
    # manageable for very large inputs (e.g. 233k rows).
    batch_size = min(batch_size, 1000)

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
# Identity-based missing-data detection
# ============================================================================
#
# check_identity() / check_identity_async() answer the question:
#   "Which trading days in [start_date, end_date] are NOT yet present in
#    this identity table?"
#
# They replace the ad-hoc get_existing_dates_from_db / get_existing_years_from_db
# helpers that used to live in _download_commons.py, and add proper holiday /
# weekend skipping via utils._holidays_and_weekdays.
#
# Supported identity table shapes:
#   * date-only                  (e.g. stats.debt_identity, PK=date)
#   * (date, code)                (e.g. stats.etf_identity, stats.index_identity,
#                                  stats.stock_identity, stats.options_identity)
#   * (date, code, time)          (e.g. stats.stock_intraday_5min,
#                                  stats.index_intraday_5min)
#
# Optional filters let callers narrow the query:
#   * code=...           → only rows for this code (e.g. "510050.SS")
#   * code_suffix=...    → only rows for this exchange (e.g. "SS", "SZ", "BJ")
#   * time_value=...     → only rows for this intraday bar time (e.g. time(15, 0))
#
# When skip_holidays=True (default), the expected date set is generated using
# is_trading_day() so weekends and CN_HOLIDAYS are excluded. Pass
# skip_holidays=False for tables that may hold non-trading-day data.


def _build_identity_where_clause(
    schema: Optional[str],
    table: str,
    *,
    start_date: date,
    end_date: date,
    date_column: str,
    code: Optional[str],
    code_column: str,
    code_suffix: Optional[str],
    code_suffix_column: str,
    time_value: Optional[time],
    time_column: str,
) -> "sql.Composed":
    """Build the parameterized WHERE clause for check_identity (psycopg).

    Returns a sql.Composed object with placeholders matching the param list
    returned by _build_identity_params().
    """
    clauses = [
        sql.SQL("{col} BETWEEN %s AND %s").format(col=sql.Identifier(date_column))
    ]
    if code is not None:
        clauses.append(sql.SQL("{col} = %s").format(col=sql.Identifier(code_column)))
    if code_suffix is not None:
        clauses.append(
            sql.SQL("{col} = %s").format(col=sql.Identifier(code_suffix_column))
        )
    if time_value is not None:
        clauses.append(sql.SQL("{col} = %s").format(col=sql.Identifier(time_column)))

    where_clause = sql.SQL(" AND ").join(clauses)

    select_distinct = sql.SQL("SELECT DISTINCT {date_col} FROM {tbl} WHERE {where}").format(
        date_col=sql.Identifier(date_column),
        tbl=sql.Identifier(schema, table) if schema else sql.Identifier(table),
        where=where_clause,
    )
    return select_distinct


def _build_identity_params(
    *,
    start_date: date,
    end_date: date,
    code: Optional[str],
    code_suffix: Optional[str],
    time_value: Optional[time],
) -> list:
    """Build the positional parameter list matching _build_identity_where_clause."""
    params: list = [start_date, end_date]
    if code is not None:
        params.append(code)
    if code_suffix is not None:
        params.append(code_suffix)
    if time_value is not None:
        params.append(time_value)
    return params


def _expected_dates(
    start_date: date,
    end_date: date,
    skip_holidays: bool,
) -> Set[date]:
    """Generate the set of expected dates in [start_date, end_date]."""
    if skip_holidays:
        return set(business_days(start_date, end_date, reverse=False))
    return set(date_range_forward(start_date, end_date))


def check_identity(
    table_name: str,
    start_date: date,
    end_date: date,
    *,
    code: Optional[str] = None,
    code_suffix: Optional[str] = None,
    time_value: Optional[time] = None,
    date_column: str = "date",
    code_column: str = "code",
    code_suffix_column: str = "code_suffix",
    time_column: str = "time",
    skip_holidays: bool = True,
    conn=None,
) -> Set[date]:
    """Return the set of trading days in [start_date, end_date] that are NOT
    yet present in the identity table ``table_name`` (sync).

    Args:
        table_name: identity table, optionally schema-qualified
                    (e.g. "stats.etf_identity", "stats.stock_intraday_5min").
        start_date, end_date: inclusive date window to check.
        code: optional filter on the ``code_column`` value (e.g. "510050.SS").
            When omitted, every code is considered — the function returns
            dates with NO row of ANY code.
        code_suffix: optional filter on the exchange suffix column
            (e.g. "SS", "SZ", "BJ").
        time_value: optional filter on the intraday bar time column
            (datetime.time, e.g. time(15, 0)).
        date_column, code_column, code_suffix_column, time_column:
            column name overrides for tables with non-standard naming.
        skip_holidays: when True (default), the expected date set excludes
            weekends and CN_HOLIDAYS via utils._holidays_and_weekdays.
            Set False for tables that may legitimately hold non-trading-day
            rows.
        conn: optional existing psycopg connection. When None, a new
            connection is opened and closed internally.

    Returns:
        Set of datetime.date that are expected but missing from the table.
    """
    expected = _expected_dates(start_date, end_date, skip_holidays)
    if not expected:
        return set()

    schema, table = _parse_table_name(table_name)
    query = _build_identity_where_clause(
        schema, table,
        start_date=start_date, end_date=end_date,
        date_column=date_column,
        code=code, code_column=code_column,
        code_suffix=code_suffix, code_suffix_column=code_suffix_column,
        time_value=time_value, time_column=time_column,
    )
    params = _build_identity_params(
        start_date=start_date, end_date=end_date,
        code=code, code_suffix=code_suffix, time_value=time_value,
    )

    owns_conn = conn is None
    if owns_conn:
        conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            present = {row[0] for row in cur.fetchall() if row[0] is not None}
    finally:
        if owns_conn:
            try:
                conn.close()
            except Exception:
                pass

    return expected - present


async def check_identity_async(
    conn,
    table_name: str,
    start_date: date,
    end_date: date,
    *,
    code: Optional[str] = None,
    code_suffix: Optional[str] = None,
    time_value: Optional[time] = None,
    date_column: str = "date",
    code_column: str = "code",
    code_suffix_column: str = "code_suffix",
    time_column: str = "time",
    skip_holidays: bool = True,
) -> Set[date]:
    """Async counterpart of :func:`check_identity`.

    Uses an existing asyncpg connection (no internal connect/close, because
    asyncpg connections are typically pooled and short-lived). The caller is
    responsible for opening/closing the connection.

    Args: see :func:`check_identity` (without ``conn=None`` — conn is
    required here as the first positional argument).
    """
    expected = _expected_dates(start_date, end_date, skip_holidays)
    if not expected:
        return set()

    schema, table = _parse_table_name(table_name)

    # Build the query with $N placeholders for asyncpg.
    clauses = [f'"{date_column}" BETWEEN $1 AND $2']
    params: list = [start_date, end_date]
    placeholder_idx = 3
    if code is not None:
        clauses.append(f'"{code_column}" = ${placeholder_idx}')
        params.append(code)
        placeholder_idx += 1
    if code_suffix is not None:
        clauses.append(f'"{code_suffix_column}" = ${placeholder_idx}')
        params.append(code_suffix)
        placeholder_idx += 1
    if time_value is not None:
        clauses.append(f'"{time_column}" = ${placeholder_idx}')
        params.append(time_value)
        placeholder_idx += 1

    if schema:
        from_clause = f'"{schema}"."{table}"'
    else:
        from_clause = f'"{table}"'

    query = (
        f'SELECT DISTINCT "{date_column}" FROM {from_clause} '
        f'WHERE ' + ' AND '.join(clauses)
    )

    rows = await conn.fetch(query, *params)
    present = {r[date_column] for r in rows if r[date_column] is not None}
    return expected - present


# ============================================================================
# Convenience wrappers for the year-keyed use case
# ============================================================================
def check_identity_years(
    table_name: str,
    start_date: date,
    end_date: date,
    *,
    date_column: str = "date",
    skip_holidays: bool = True,
    conn=None,
) -> Set[int]:
    """Return the set of years in [start_date, end_date] that have NO row
    in ``table_name`` (sync).

    This is the year-granular counterpart of :func:`check_identity`, useful
    for sources organized as one file per year (e.g. chinabond yearly
    archives). A year is "missing" if it has zero rows in the table within
    the [start_date, end_date] window.

    Args: see :func:`check_identity` (code/time/code_suffix filters do not
    apply at year granularity).
    """
    years = list(range(start_date.year, end_date.year + 1))
    if not years:
        return set()

    schema, table = _parse_table_name(table_name)
    if schema:
        from_clause = sql.Identifier(schema, table)
    else:
        from_clause = sql.Identifier(table)

    query = sql.SQL(
        'SELECT DISTINCT EXTRACT(YEAR FROM {col})::int '
        "FROM {tbl} "
        'WHERE {col} BETWEEN %s AND %s'
    ).format(col=sql.Identifier(date_column), tbl=from_clause)

    owns_conn = conn is None
    if owns_conn:
        conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, [start_date, end_date])
            present_years = {row[0] for row in cur.fetchall() if row[0] is not None}
    finally:
        if owns_conn:
            try:
                conn.close()
            except Exception:
                pass

    return set(years) - present_years