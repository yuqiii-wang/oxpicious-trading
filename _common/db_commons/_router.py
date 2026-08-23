"""
_router.py - Read/write routing between primary and replica with connection pooling.

Primary (SUPABASE_HOST:SUPABASE_PORT) handles all writes; the replica
(SUPABASE_REPLICA_HOST:SUPABASE_REPLICA_PORT) handles read-only SELECTs.
Each database instance has its own lazily-initialized connection pool
so connections are reused across queries instead of created/destroyed.

Provides:
  - is_read_query()   - classify a SQL statement as read or write
  - route_query()     - return "replica" or "primary" for a SQL statement
  - execute()         - sync: run SQL on the routed pool
  - execute_async()   - async: run SQL on the routed pool
  - get_routed_connection()      - sync context manager for multi-query use
  - get_routed_connection_async() - async context manager for multi-query use
  - close_pools()     - graceful shutdown of all pools
  - get_pool_stats()  - return current pool state

Thread-safety: the module-level pool singletons use lazy initialization
protected by a threading.Lock so concurrent callers from multiple threads
do not race on pool creation.
"""
from __future__ import annotations

import os
import re
import threading
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator

import psycopg
import psycopg_pool

from ._connections import (
    get_db_pool_async,
    get_read_db_pool_async,
)
from ._helpers import _get_conn_params, _get_replica_conn_params

# ---------------------------------------------------------------------------
#  Module-level pool singletons (lazily initialized)
# ---------------------------------------------------------------------------

_sync_primary_pool: psycopg_pool.ConnectionPool | None = None
_sync_replica_pool: psycopg_pool.ConnectionPool | None = None
_async_primary_pool: Any = None  # asyncpg.pool.Pool
_async_replica_pool: Any = None  # asyncpg.pool.Pool
_pool_lock = threading.Lock()


def _build_conninfo(params: dict) -> str:
    """Build a libpq connection string from a params dict."""
    return (
        f"host={params['host']} "
        f"port={params['port']} "
        f"dbname={params['database']} "
        f"user={params['user']} "
        f"password={params['password']} "
        f"connect_timeout=10"
    )


def _configure_replica(conn: psycopg.Connection) -> None:
    """Set a replica pool connection to read-only (psycopg_pool callback).

    psycopg_pool creates connections with autocommit=False by default,
    so we force autocommit before running the SET command.  The SET
    itself must not leave the connection in a transaction state or the
    pool will discard it as broken.
    """
    conn.autocommit = True
    conn.execute("SET default_transaction_read_only = on")


def _configure_primary(conn: psycopg.Connection) -> None:
    """Set up a primary pool connection (psycopg_pool callback).

    Ensures autocommit mode matches the existing get_db_connection()
    behaviour so callers that rely on implicit autocommit (single
    statements auto-committing) continue to work.
    """
    conn.autocommit = True


def _get_sync_pool(primary: bool = False) -> psycopg_pool.ConnectionPool:
    """Return the sync connection pool for the given database instance.

    Creates the pool lazily on first call.  Thread-safe via ``_pool_lock``.

    Args:
        primary: if True, return the primary (write) pool; otherwise
                 the replica (read) pool.

    Returns:
        psycopg_pool.ConnectionPool
    """
    global _sync_primary_pool, _sync_replica_pool
    pool = _sync_primary_pool if primary else _sync_replica_pool
    if pool is not None:
        return pool

    with _pool_lock:
        # Double-checked locking
        pool = _sync_primary_pool if primary else _sync_replica_pool
        if pool is not None:
            return pool

        params = _get_conn_params() if primary else _get_replica_conn_params()
        conninfo = _build_conninfo(params)
        min_size = int(os.environ.get("DB_POOL_MIN_SIZE", "2"))
        max_size = int(os.environ.get("DB_POOL_MAX_SIZE", "8"))

        kwargs: dict[str, Any] = {
            "min_size": min_size,
            "max_size": max_size,
            "configure": _configure_primary if primary else _configure_replica,
        }

        pool = psycopg_pool.ConnectionPool(conninfo, **kwargs)
        if primary:
            _sync_primary_pool = pool
        else:
            _sync_replica_pool = pool

    return pool


async def _get_async_pool(primary: bool = False) -> Any:
    """Return the async connection pool for the given database instance.

    Creates the pool lazily on first call.  Thread-safe via ``_pool_lock``.

    Args:
        primary: if True, return the primary (write) pool; otherwise
                 the replica (read) pool.

    Returns:
        asyncpg.pool.Pool
    """
    global _async_primary_pool, _async_replica_pool
    pool = _async_primary_pool if primary else _async_replica_pool
    if pool is not None:
        return pool

    with _pool_lock:
        pool = _async_primary_pool if primary else _async_replica_pool
        if pool is not None:
            return pool

        min_size = int(os.environ.get("DB_POOL_MIN_SIZE", "2"))
        max_size = int(os.environ.get("DB_POOL_MAX_SIZE", "8"))

        if primary:
            pool = await get_db_pool_async(min_size=min_size, max_size=max_size)
            _async_primary_pool = pool
        else:
            pool = await get_read_db_pool_async(min_size=min_size, max_size=max_size)
            _async_replica_pool = pool

    return pool


# ---------------------------------------------------------------------------
#  SQL classification
# ---------------------------------------------------------------------------

# Statements that mutate state - always routed to primary.
_WRITE_STMTS = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|MERGE|UPSERT|TRUNCATE|CREATE|DROP|ALTER|"
    r"GRANT|REVOKE|COPY|VACUUM|ANALYZE|REINDEX|CLUSTER|COMMENT|LOCK|SET|"
    r"RESET|CALL|DO|LISTEN|NOTIFY|REFRESH)\b",
    re.IGNORECASE,
)

# Explicitly read-only statements - routed to replica.
_READ_STMTS = re.compile(r"^\s*(SELECT|WITH|TABLE|SHOW|EXPLAIN|VALUES)\b", re.IGNORECASE)


def is_read_query(query: str) -> bool:
    """Return True if the SQL statement is read-only (safe for replica).

    A CTE (WITH) containing INSERT/UPDATE/DELETE is a write, so WITH
    statements are conservatively scanned for write keywords.
    """
    stripped = query.strip()
    # Strip leading comments (-- and /* */) that may precede the statement.
    stripped = re.sub(r"^(--[^\n]*\n|/\*.*?\*/|\s)+", "", stripped, flags=re.DOTALL)
    if not stripped:
        return False
    if _WRITE_STMTS.match(stripped):
        return False
    if _READ_STMTS.match(stripped):
        # WITH x AS (INSERT ...) SELECT ... is a write - check the body.
        body = re.sub(r"^\s*WITH\b", "", stripped, flags=re.IGNORECASE)
        return not re.search(
            r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|FOR\s+UPDATE|FOR\s+SHARE)\b",
            body,
            re.IGNORECASE,
        )
    # Unknown statement type - be safe, treat as write (primary).
    return False


def route_query(query: str) -> str:
    """Return "replica" for read-only queries, "primary" otherwise."""
    return "replica" if is_read_query(query) else "primary"


# ---------------------------------------------------------------------------
#  Public API: execute (uses pooled connections)
# ---------------------------------------------------------------------------

def execute(query: str, params: tuple | None = None, fetch: bool = True):
    """Run a SQL statement on the routed connection pool (sync).

    Read-only queries go to the replica pool; everything else goes to
    the primary pool.  Connections are borrowed from the pool and
    returned automatically — the caller does NOT need to manage
    connection lifecycle.

    Args:
        query: SQL statement.
        params: query parameters (tuple or None).
        fetch: if True, return fetched rows; if False, return None.

    Returns:
        List of rows (tuples) when fetch=True and the query produces
        rows; None otherwise.
    """
    pool = _get_sync_pool(primary=not is_read_query(query))
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            if fetch and cur.description is not None:
                return cur.fetchall()
            return None


async def execute_async(query: str, *args, fetch: bool = True):
    """Run a SQL statement on the routed connection pool (async).

    Read-only queries go to the replica pool; everything else goes to
    the primary pool.  Connections are borrowed from the pool and
    returned automatically.

    Args:
        query: SQL statement.
        *args: query parameters (passed positionally).
        fetch: if True, return fetched rows; if False, return None.

    Returns:
        List of rows (asyncpg Records) when fetch=True; None otherwise.
    """
    pool = await _get_async_pool(primary=not is_read_query(query))
    if fetch:
        return await pool.fetch(query, *args)
    await pool.execute(query, *args)
    return None


# ---------------------------------------------------------------------------
#  Multi-query context managers
# ---------------------------------------------------------------------------

@contextmanager
def get_routed_connection(query: str | None = None, *, primary: bool | None = None) -> Iterator[psycopg.Connection]:
    """Context manager: borrow a single connection from the routed sync pool.

    Use this when you need to run multiple queries on the same connection
    (e.g. a transaction with several steps).  The connection is
    automatically returned to the pool when the context exits.

    Args:
        query: if provided, the pool is chosen by classifying this SQL
               statement as read or write.  Mutually exclusive with
               ``primary``.
        primary: if True, force the primary pool; if False, force the
                 replica pool.  If both ``query`` and ``primary`` are
                 None, the primary pool is used.

    Yields:
        A psycopg.Connection borrowed from the appropriate pool.

    Usage::

        with get_routed_connection("SELECT 1") as conn:
            conn.execute("SELECT ...")
            conn.execute("SELECT ...")
    """
    if query is not None:
        use_primary = not is_read_query(query)
    elif primary is not None:
        use_primary = primary
    else:
        use_primary = True

    pool = _get_sync_pool(primary=use_primary)
    with pool.connection() as conn:
        yield conn


@asynccontextmanager
async def get_routed_connection_async(
    query: str | None = None, *, primary: bool | None = None
) -> AsyncIterator[Any]:
    """Async context manager: borrow a single connection from the routed async pool.

    Use this when you need to run multiple queries on the same async
    connection (e.g. an async transaction with several steps).  The
    connection is automatically returned to the pool when the context
    exits.

    Args:
        query: if provided, the pool is chosen by classifying this SQL
               statement as read or write.  Mutually exclusive with
               ``primary``.
        primary: if True, force the primary pool; if False, force the
                 replica pool.  If both ``query`` and ``primary`` are
                 None, the primary pool is used.

    Yields:
        An asyncpg Connection borrowed from the appropriate pool.

    Usage::

        async with get_routed_connection_async("SELECT 1") as conn:
            await conn.fetch("SELECT ...")
            await conn.execute("INSERT ...")
    """
    if query is not None:
        use_primary = not is_read_query(query)
    elif primary is not None:
        use_primary = primary
    else:
        use_primary = True

    pool = await _get_async_pool(primary=use_primary)
    async with pool.acquire() as conn:
        yield conn


# ---------------------------------------------------------------------------
#  Pool lifecycle management
# ---------------------------------------------------------------------------

def close_pools(timeout: float = 5.0) -> None:
    """Gracefully close all connection pools.

    Waits up to ``timeout`` seconds for active connections to be
    returned before forcing closure.  Safe to call during process
    shutdown — subsequent queries will recreate pools lazily.

    Args:
        timeout: seconds to wait for active connections before
                 forcibly closing the pool.
    """
    global _sync_primary_pool, _sync_replica_pool

    with _pool_lock:
        for pool, name in [
            (_sync_primary_pool, "primary"),
            (_sync_replica_pool, "replica"),
        ]:
            if pool is not None:
                try:
                    pool.close(timeout=timeout)
                    print(f"    [DB] Closed sync {name} pool", flush=True)
                except Exception as e:
                    print(f"    [DB] Error closing sync {name} pool: {e}", flush=True)
        _sync_primary_pool = None
        _sync_replica_pool = None


async def close_pools_async(timeout: float = 5.0) -> None:
    """Gracefully close all async connection pools.

    Args:
        timeout: seconds to wait for active connections before
                 forcibly closing the pool.
    """
    global _async_primary_pool, _async_replica_pool

    with _pool_lock:
        for pool, name in [
            (_async_primary_pool, "primary"),
            (_async_replica_pool, "replica"),
        ]:
            if pool is not None:
                try:
                    await pool.close()
                    print(f"    [DB] Closed async {name} pool", flush=True)
                except Exception as e:
                    print(f"    [DB] Error closing async {name} pool: {e}", flush=True)
        _async_primary_pool = None
        _async_replica_pool = None


def get_pool_stats() -> dict[str, Any]:
    """Return current pool state for monitoring / diagnostics.

    Returns a dict with keys for each pool (sync_primary, sync_replica,
    async_primary, async_replica) and their live/idle/total connection
    counts, or ``None`` if the pool has not been created yet.
    """
    stats: dict[str, Any] = {}

    for name, pool in [
        ("sync_primary", _sync_primary_pool),
        ("sync_replica", _sync_replica_pool),
    ]:
        if pool is not None:
            s = pool.get_stats()
            stats[name] = {
                "pool_size": s["pool_size"],
                "pool_available": s["pool_available"],
                "pool_min": s["pool_min"],
                "pool_max": s["pool_max"],
                "requests_waiting": s["requests_waiting"],
                "closed": pool.closed,
            }
        else:
            stats[name] = None

    for name, pool in [
        ("async_primary", _async_primary_pool),
        ("async_replica", _async_replica_pool),
    ]:
        if pool is not None:
            stats[name] = {
                "size": pool.get_size(),
                "idle_size": pool.get_idle_size(),
                "min_size": pool.get_min_size(),
                "max_size": pool.get_max_size(),
            }
        else:
            stats[name] = None

    return stats


def is_pool_initialized(primary: bool = False) -> bool:
    """Return True if the sync pool for the given instance has been created."""
    return (_sync_primary_pool if primary else _sync_replica_pool) is not None