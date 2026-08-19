"""
db_commons — Database connection and bulk insert utilities for build scripts.

This package re-exports all public symbols from the submodules so that
existing imports like ``from _common.db_commons import get_db_connection``
continue to work unchanged.

Submodules:
  - _helpers        — internal helpers (env loading, table name parsing)
  - _connections   — sync + async connection creation
  - _sync_ops       — synchronous bulk operations and table management
  - _async_ops      — asynchronous bulk operations and table management
  - _copy_or_upsert — copy-or-upsert split for fast-path bulk inserts

Uses psycopg for sync connections and asyncpg for async connections.
"""

__all__ = [
    # Connection utilities
    "HAS_ASYNCPG",
    "get_db_connection",
    "get_db_connection_async",
    "get_db_pool_async",
    # Synchronous API
    "check_stock_intraday_exists",
    "get_existing_keys",
    "bulk_upsert",
    "ensure_table_exists",
    "truncate_table",
    # Async API
    "get_existing_keys_async",
    "bulk_upsert_async",
    "copy_insert_async",
    "ensure_table_exists_async",
    "truncate_table_async",
    # Copy-or-upsert split
    "copy_or_upsert_split_async",
    "copy_or_upsert_split_pool_async",
    "get_max_table_date_async",
]

# -- Internal helpers (exported for sibling modules) --
from ._helpers import (
    _load_env_vars,
    _get_conn_params,
    _parse_table_name,
)

# -- Connection utilities --
from ._connections import (
    HAS_ASYNCPG,
    get_db_connection,
    get_db_connection_async,
    get_db_pool_async,
)

# -- Synchronous API (backward compatible) --
from ._sync_ops import (
    check_stock_intraday_exists,
    get_existing_keys,
    bulk_upsert,
    ensure_table_exists,
    truncate_table,
)

# -- Async API --
from ._async_ops import (
    get_existing_keys_async,
    bulk_upsert_async,
    copy_insert_async,
    ensure_table_exists_async,
    truncate_table_async,
)

# -- Copy-or-upsert split --
from ._copy_or_upsert import (
    copy_or_upsert_split_async,
    copy_or_upsert_split_pool_async,
    get_max_table_date_async,
)