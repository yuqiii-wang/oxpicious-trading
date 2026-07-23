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

Uses psycopg for sync connections and asyncpg for async connections.
"""
import os
import sys
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
    """Load environment variables from .env files if not already set."""
    env_paths = [
        Path(__file__).parent / "database" / ".env",
        Path(__file__).parent / ".env",
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
        "host": os.environ.get("SUPABASE_HOST", "localhost"),
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
    3. .env file in project root
    
    Priority: env vars > database/.env > .env
    """
    conn_params = _get_conn_params()
    
    try:
        conn = psycopg.connect(
            host=conn_params["host"],
            port=conn_params["port"],
            dbname=conn_params["database"],
            user=conn_params["user"],
            password=conn_params["password"],
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
    """Perform bulk upsert (INSERT ... ON CONFLICT DO UPDATE) with batching (sync).
    
    Uses batched multi-row INSERT for better performance (~1000 rows per batch).
    
    Args:
        conn: psycopg connection
        table_name: target table (may include schema prefix like "stats.debt_identity")
        rows: list of dictionaries, each representing a row
        key_columns: list of column names forming the primary key
        batch_size: number of rows per INSERT batch
        
    Returns:
        Number of rows inserted/updated
    """
    if not rows:
        return 0
    
    columns = list(rows[0].keys())
    if not columns:
        return 0
    
    schema, table = _parse_table_name(table_name)
    
    columns_sql = sql.SQL(", ").join([sql.Identifier(c) for c in columns])
    
    conflict_columns = sql.SQL(", ").join([sql.Identifier(c) for c in key_columns])
    update_clause = sql.SQL(", ").join([
        sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c))
        for c in columns if c not in key_columns
    ])
    
    all_values = [[row[c] for c in columns] for row in rows]
    
    total_inserted = 0
    
    with conn.cursor() as cur:
        try:
            for i in range(0, len(all_values), batch_size):
                batch = all_values[i:i+batch_size]
                
                single_row_placeholders = sql.SQL("({})").format(
                    sql.SQL(", ").join([sql.Placeholder() for _ in columns])
                )
                
                batch_placeholders = sql.SQL(", ").join(
                    [single_row_placeholders for _ in batch]
                )
                
                if schema:
                    query = sql.SQL(
                        "INSERT INTO {schema}.{table} ({columns}) VALUES {batch_values} "
                        "ON CONFLICT ({conflict_columns}) DO UPDATE SET {update_clause}"
                    ).format(
                        schema=sql.Identifier(schema),
                        table=sql.Identifier(table),
                        columns=columns_sql,
                        batch_values=batch_placeholders,
                        conflict_columns=conflict_columns,
                        update_clause=update_clause,
                    )
                else:
                    query = sql.SQL(
                        "INSERT INTO {table} ({columns}) VALUES {batch_values} "
                        "ON CONFLICT ({conflict_columns}) DO UPDATE SET {update_clause}"
                    ).format(
                        table=sql.Identifier(table),
                        columns=columns_sql,
                        batch_values=batch_placeholders,
                        conflict_columns=conflict_columns,
                        update_clause=update_clause,
                    )
                
                flat_values = [v for row in batch for v in row]
                
                cur.execute(query, flat_values)
                total_inserted += cur.rowcount
            
            return total_inserted
        except Exception as e:
            print(f"    [ERROR] Bulk upsert failed for {table_name}: {e}", flush=True)
            conn.rollback()
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
        conn = await asyncpg.connect(
            host=conn_params["host"],
            port=conn_params["port"],
            database=conn_params["database"],
            user=conn_params["user"],
            password=conn_params["password"],
        )
        return conn
    except Exception as e:
        print(f"    [ERROR] Failed to connect to database: {e}", flush=True)
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
    """Perform bulk upsert (INSERT ... ON CONFLICT DO UPDATE) with batching (async)."""
    if not rows:
        return 0
    
    columns = list(rows[0].keys())
    if not columns:
        return 0
    
    schema, table = _parse_table_name(table_name)
    
    columns_sql = ", ".join([f'"{c}"' for c in columns])
    conflict_columns = ", ".join([f'"{c}"' for c in key_columns])
    update_clause = ", ".join([
        f'"{c}" = EXCLUDED."{c}"'
        for c in columns if c not in key_columns
    ])
    
    all_values = [[row[c] for c in columns] for row in rows]
    
    total_inserted = 0
    
    try:
        for i in range(0, len(all_values), batch_size):
            batch = all_values[i:i+batch_size]
            
            # Create placeholders for all rows in batch
            row_placeholders = ", ".join(
                "(" + ", ".join(["${}".format(j + i * len(columns) + 1) for j in range(len(columns))]) + ")"
                for i, _ in enumerate(batch)
            )
            
            # Build query: if no update columns, use DO NOTHING instead of DO UPDATE SET
            if update_clause:
                conflict_action = f"ON CONFLICT ({conflict_columns}) DO UPDATE SET {update_clause}"
            else:
                conflict_action = f"ON CONFLICT ({conflict_columns}) DO NOTHING"
            
            if schema:
                query = f"""
                    INSERT INTO "{schema}"."{table}" ({columns_sql}) VALUES {row_placeholders}
                    {conflict_action}
                """
            else:
                query = f"""
                    INSERT INTO "{table}" ({columns_sql}) VALUES {row_placeholders}
                    {conflict_action}
                """
            
            flat_values = [v for row in batch for v in row]
            
            result = await conn.execute(query, *flat_values)
            # asyncpg/PostgreSQL status string formats:
            #   "INSERT 0 N"  → command="INSERT", OID="0", rowcount=N
            #   "UPDATE N"    → command="UPDATE", rowcount=N
            #   "DELETE N"    → command="DELETE", rowcount=N
            # For INSERT, the OID is always 0 and the row count is the LAST token.
            # Previous code used split()[1], which returned the OID ("0") for
            # INSERTs, making every insert look like 0 rows inserted.
            parts = result.split() if result else []
            rowcount = int(parts[-1]) if parts else 0
            total_inserted += rowcount
        
        return total_inserted
    except Exception as e:
        print(f"    [ERROR] Bulk upsert failed for {table_name}: {e}", flush=True)
        # asyncpg operates in implicit autocommit mode; each execute() runs in its
        # own transaction. There's no rollback() method - just re-raise the exception.
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