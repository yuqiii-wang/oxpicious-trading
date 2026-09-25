"""
_helpers.py — Internal helpers for db_commons.

Provides:
  - _load_env_vars() — load DB env vars from database/.env
  - _get_conn_params() — build connection params from env
  - _parse_table_name() — split "schema.table" into (schema, table)
  - _group_rows_by_key() / _build_chunks() — key-aligned commit-chunk
    grouping for the bulk-write helpers
  - DEFAULT_COMMIT_CHUNK_ROWS — rows per COMMIT for bulk writes
"""
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Rows per COMMIT for the bulk-write helpers (bulk_upsert_async /
# bulk_upsert / copy_or_upsert_split). Bounded commits let a replication
# slot's confirmed_flush_lsn advance while a bulk load is still running —
# each COMMIT is decoded and applied on the replica independently, so the
# primary can recycle WAL instead of retaining it behind one giant
# transaction (a multi-GB single-transaction upsert froze the slot at
# ~29 GB of retained WAL). ~100K rows still amortizes COMMIT/fsync
# overhead across thousands of rows, keeping the checkpoint-storm
# defense of the old single-transaction design.
DEFAULT_COMMIT_CHUNK_ROWS = 100_000

# Sentinel partition_key for the bulk-write helpers: resolve the chunking
# key automatically ("date" when the rows carry one — the repo-wide
# partitioning convention — else the first usable key column).
PARTITION_KEY_AUTO = "auto"


def _resolve_partition_key(
    sample: dict | None,
    key_columns: list,
    partition_key: str | None,
) -> str | None:
    """Resolve the effective partition-key column for commit chunking.

    The bulk-write rule: chunk BY PARTITION KEY, one COMMIT per chunk,
    replica-lag throttle between chunks. ``PARTITION_KEY_AUTO`` (the
    helpers' default) picks ``"date"`` when the rows carry a date column,
    else the first key column present in the rows. An explicit column
    name wins; explicit ``None`` keeps legacy flat row-count chunking
    (opt-out).
    """
    if partition_key is None:
        return None
    if partition_key != PARTITION_KEY_AUTO:
        return partition_key
    if sample and "date" in sample:
        return "date"
    for c in key_columns or []:
        if sample and c in sample:
            return c
    return None


def _build_commit_chunks(
    rows: list[dict],
    partition_key: str | None,
    chunk_target_rows: int,
) -> list[list[dict]]:
    """Split rows into commit chunks aligned to the partition key.

    Whole-key chunks: a key's rows are never split across a COMMIT
    boundary (replica readers consume whole keys, and the slot advances
    between keys). Falls back to flat row-count slices only when key
    grouping itself fails (unsortable mixed key types) — a write never
    fails on its chunking strategy; it just loses the alignment and says
    so.
    """
    if partition_key is None:
        return [
            rows[lo:lo + chunk_target_rows]
            for lo in range(0, len(rows), chunk_target_rows)
        ]
    try:
        return _build_chunks(
            _group_rows_by_key(rows, partition_key), chunk_target_rows,
        )
    except TypeError as e:
        logger.warning(
            f"    [WARN] partition-key grouping by '{partition_key}' failed "
            f"({type(e).__name__}: {e}); falling back to flat row-count "
            f"chunks (commit boundaries may split keys)")
        return [
            rows[lo:lo + chunk_target_rows]
            for lo in range(0, len(rows), chunk_target_rows)
        ]


def _group_rows_by_key(rows: list[dict], key: str) -> list[tuple]:
    """Group a list of row dicts by ``row[key]``.

    Returns a list of (key_value, rows_for_key) pairs sorted by key
    value. Sorting makes the emitted chunks key-major regardless of the
    input order.
    """
    by_key: dict[Any, list[dict]] = {}
    for r in rows:
        by_key.setdefault(r[key], []).append(r)
    return sorted(by_key.items(), key=lambda kv: kv[0])


def _build_chunks(
    key_groups: list[tuple], chunk_target_rows: int
) -> list[list[dict]]:
    """Accumulate key-groups into chunks of ~``chunk_target_rows`` rows.

    Key-group boundaries are always respected — a single key's rows are
    never split across chunks (a chunk holds WHOLE industries / codes /
    dates), so commit boundaries align with the semantic unit readers
    on the replica consume.
    """
    chunks: list[list[dict]] = []
    chunk: list[dict] = []
    chunk_rows = 0
    for _k, group in key_groups:
        if chunk and chunk_rows + len(group) > chunk_target_rows:
            chunks.append(chunk)
            chunk = []
            chunk_rows = 0
        chunk.extend(group)
        chunk_rows += len(group)
    if chunk:
        chunks.append(chunk)
    return chunks


def _chunk_keys_by_weight(
    weighted_keys: list[tuple[Any, int]],
    weight_target: int,
) -> list[list[Any]]:
    """Accumulate (key, weight) pairs — sorted by key — into chunks whose
    weights sum to ~``weight_target``. A key is NEVER split across
    chunks (whole-key chunks, the bulk-write rule's delete flavor): the
    delete flavor of ``_build_chunks`` for when the rows are not
    materialized client-side and only per-key ROW COUNTS are known
    (the caller counts the doomed rows per partition key, then deletes
    key-chunk by key-chunk so each COMMIT's WAL is bounded)."""
    chunks: list[list[Any]] = []
    chunk: list[Any] = []
    weight = 0
    for k, w in sorted(weighted_keys, key=lambda kv: kv[0]):
        if chunk and weight + w > weight_target:
            chunks.append(chunk)
            chunk = []
            weight = 0
        chunk.append(k)
        weight += w
    if chunk:
        chunks.append(chunk)
    return chunks


def _load_env_vars() -> None:
    """Load environment variables from database/.env if not already set."""
    env_paths = [
        Path(__file__).resolve().parents[2] / "database" / ".env",
    ]

    for env_path in env_paths:
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        os.environ.setdefault(key.strip(), value.strip())


def _get_conn_params() -> dict:
    """Get connection parameters from SUPABASE_* environment variables."""
    _load_env_vars()
    return {
        "host": os.environ.get("SUPABASE_HOST", "127.0.0.1"),
        "port": int(os.environ.get("SUPABASE_PORT", "9876")),
        "database": os.environ.get("SUPABASE_DB", "oxpicious-stats"),
        "user": os.environ.get("SUPABASE_USER", "postgres"),
        "password": os.environ.get("SUPABASE_PASSWORD", "postgres"),
    }


def _parse_table_name(table_name: str) -> tuple:
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


def _get_replica_conn_params() -> dict:
    """Get connection parameters for the read-only replica.

    Falls back to the primary params when the replica host is unset, so
    environments without a replica (CI, remote deployments) still work.
    """
    params = _get_conn_params()
    if os.environ.get("SUPABASE_REPLICA_HOST"):
        params["host"] = os.environ["SUPABASE_REPLICA_HOST"]
        params["port"] = int(os.environ.get("SUPABASE_REPLICA_PORT", params["port"]))
    return params
