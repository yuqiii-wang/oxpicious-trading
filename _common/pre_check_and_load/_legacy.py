"""Legacy DB-scan helpers relocated from downloads._common.core.

Thin sync wrappers around get_db_connection kept only for backward
compatibility — new code should use check_identity / check_identity_years
directly (holiday-aware, missing-set semantics). DB access belongs in the
global ``_common`` package, not in the downloaders' file layer.
"""
from __future__ import annotations

from datetime import date as _date
from typing import Set, Tuple, Optional


def _parse_table_name_local(table_name: str) -> Tuple[Optional[str], str]:
    """Parse a (schema, table) tuple — same logic as _db_commons._parse_table_name."""
    parts = table_name.split(".", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, parts[0]


def get_existing_dates_from_db(
    table_name: str,
    date_column: str = "date",
) -> Set[_date]:
    """Query the database for existing dates in a table (sync, DEPRECATED).

    .. deprecated::
        Use :func:`_common.pre_check_and_load.check_identity` instead, which returns the
        complementary set (missing dates) and properly skips holidays and
        weekends. This wrapper queries the raw present-date set without any
        holiday awareness.

    Args:
        table_name: table name with optional schema prefix
                    (e.g., "stats.etf_identity").
        date_column: name of the date column (default "date").

    Returns:
        Set of ``datetime.date`` objects present in the table.
    """
    from _common.db_commons import get_db_connection
    conn = get_db_connection()
    try:
        schema, table = _parse_table_name_local(table_name)
        # Query ALL present dates (no range filter); use IS NOT NULL guard.
        # We reuse the identifier-quoting helper to avoid SQL injection on
        # the table/column names, but the predicate is just IS NOT NULL.
        from psycopg import sql
        where = sql.SQL("{col} IS NOT NULL").format(col=sql.Identifier(date_column))
        query = sql.SQL("SELECT DISTINCT {col} FROM {tbl} WHERE {where}").format(
            col=sql.Identifier(date_column),
            tbl=sql.Identifier(schema, table) if schema else sql.Identifier(table),
            where=where,
        )
        with conn.cursor() as cur:
            cur.execute(query)
            return {row[0] for row in cur.fetchall() if row[0] is not None}
    finally:
        conn.close()


def get_existing_years_from_db(
    table_name: str,
    date_column: str = "date",
) -> Set[int]:
    """Query the database for years that have at least one row in a table (DEPRECATED).

    .. deprecated::
        Use :func:`_common.pre_check_and_load.check_identity_years` instead.

    Args:
        table_name: table name with optional schema prefix.
        date_column: name of the date column (default "date").

    Returns:
        Set of years (int) that have data in the table.
    """
    from _common.db_commons import get_db_connection
    from psycopg import sql
    conn = get_db_connection()
    schema, table = _parse_table_name_local(table_name)
    try:
        tbl = sql.Identifier(schema, table) if schema else sql.Identifier(table)
        query = sql.SQL(
            'SELECT DISTINCT EXTRACT(YEAR FROM {col})::int '
            "FROM {tbl} "
            "WHERE {col} IS NOT NULL"
        ).format(col=sql.Identifier(date_column), tbl=tbl)
        with conn.cursor() as cur:
            cur.execute(query)
            return {row[0] for row in cur.fetchall() if row[0] is not None}
    finally:
        conn.close()
