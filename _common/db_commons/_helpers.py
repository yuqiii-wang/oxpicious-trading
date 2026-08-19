"""
_helpers.py — Internal helpers for db_commons.

Provides:
  - _load_env_vars() — load DB env vars from database/.env
  - _get_conn_params() — build connection params from env
  - _parse_table_name() — split "schema.table" into (schema, table)
"""
import os
from pathlib import Path


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