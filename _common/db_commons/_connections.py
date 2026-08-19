"""
_connections.py — Database connection utilities (sync + async).

Provides:
  - get_db_connection() — sync connect to PostgreSQL using psycopg
  - get_db_connection_async() — async connect to PostgreSQL using asyncpg
  - get_db_pool_async() — create an asyncpg connection pool
"""
from __future__ import annotations

import psycopg

from ._helpers import _get_conn_params

try:
    import asyncpg

    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False


def get_db_connection() -> psycopg.Connection:
    """Connect to PostgreSQL database (sync).

    Reads connection info from:
    1. Environment variables
    2. database/.env file

    Priority: env vars > database/.env
    """
    conn_params = _get_conn_params()

    try:
        # connect_timeout caps each TCP connect attempt so an unreachable DB
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
            autocommit=True,
        )
        return conn
    except Exception as e:
        print(f"    [ERROR] Failed to connect to database: {e}", flush=True)
        raise


async def get_db_connection_async():
    """Connect to PostgreSQL database (async).

    asyncpg operates in implicit autocommit mode by default: each
    execute()/executemany() call runs in its own transaction unless
    explicitly wrapped in `async with conn.transaction():`.

    timeout=30s: the TCP connect itself is fast, but the PostgreSQL
    startup handshake (authentication + session setup) can stall for
    many seconds when the server is saturating disk I/O during a heavy
    checkpoint.
    """
    if not HAS_ASYNCPG:
        raise ImportError(
            "asyncpg is required for async database operations. Install with: pip install asyncpg"
        )

    conn_params = _get_conn_params()

    try:
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
            print(
                f"    [ERROR] Failed to connect to database: "
                f"{type(e).__name__}: {msg}",
                flush=True,
            )
        else:
            print(
                f"    [ERROR] Failed to connect to database: "
                f"{type(e).__name__} (no message) repr={e!r}",
                flush=True,
            )
        raise


async def get_db_pool_async(
    min_size: int = 1,
    max_size: int = 5,
    max_queries: int = 50000,
):
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