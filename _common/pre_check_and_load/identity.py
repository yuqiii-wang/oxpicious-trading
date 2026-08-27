"""Identity-based missing-data detection.

check_identity() / check_identity_async() answer the question:
  "Which trading days in [start_date, end_date] are NOT yet present in
   this identity table?"

They replace the ad-hoc get_existing_dates_from_db / get_existing_years_from_db
helpers that used to live in _download_commons.py, and add proper holiday /
weekend skipping via _common._holidays_and_weekdays.

Supported identity table shapes:
  * date-only                  (e.g. stats.debt_identity, PK=date)
  * (date, code)                (e.g. stats.etf_identity, stats.index_identity,
                                  stats.stock_identity, stats.options_identity)
  * (date, code, time)          (e.g. stats.stock_intraday_5min,
                                  stats.index_intraday_5min)

Optional filters let callers narrow the query:
  * code=...           -> only rows for this code (e.g. "510050.SS")
  * exchange=...       -> only rows for this exchange (e.g. "SS", "SZ", "BJ")
  * time_value=...     -> only rows for this intraday bar time (e.g. time(15, 0))

When skip_holidays=True (default), the expected date set is generated using
is_trading_day() so weekends and CN_HOLIDAYS are excluded. Pass
skip_holidays=False for tables that may hold non-trading-day data.

Migrated from _common/db_commons.py.
"""
from __future__ import annotations

from datetime import date, time
from operator import itemgetter
from typing import Optional, Set

from psycopg import sql

from _common._holidays_and_weekdays import (
    business_days,
    date_range_forward,
)
from _common.db_commons import (
    _parse_table_name,
    get_db_connection,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build_identity_where_clause(
    schema: Optional[str],
    table: str,
    *,
    start_date: date,
    end_date: date,
    date_column: str,
    code: Optional[str],
    code_column: str,
    exchange: Optional[str],
    exchange_column: str,
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
    if exchange is not None:
        clauses.append(
            sql.SQL("{col} = %s").format(col=sql.Identifier(exchange_column))
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
    exchange: Optional[str],
    time_value: Optional[time],
) -> list:
    """Build the positional parameter list matching _build_identity_where_clause."""
    params: list = [start_date, end_date]
    if code is not None:
        params.append(code)
    if exchange is not None:
        params.append(exchange)
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


# ---------------------------------------------------------------------------
# Sync API
# ---------------------------------------------------------------------------
def check_identity(
    table_name: str,
    start_date: date,
    end_date: date,
    *,
    code: Optional[str] = None,
    exchange: Optional[str] = None,
    time_value: Optional[time] = None,
    date_column: str = "date",
    code_column: str = "code",
    exchange_column: str = "exchange",
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
            When omitted, every code is considered -- the function returns
            dates with NO row of ANY code.
        exchange: optional filter on the exchange column
            (e.g. "SS", "SZ", "BJ").
        time_value: optional filter on the intraday bar time column
            (datetime.time, e.g. time(15, 0)).
        date_column, code_column, exchange_column, time_column:
            column name overrides for tables with non-standard naming.
        skip_holidays: when True (default), the expected date set excludes
            weekends and CN_HOLIDAYS via _common._holidays_and_weekdays.
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
        exchange=exchange, exchange_column=exchange_column,
        time_value=time_value, time_column=time_column,
    )
    params = _build_identity_params(
        start_date=start_date, end_date=end_date,
        code=code, exchange=exchange, time_value=time_value,
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


# ---------------------------------------------------------------------------
# Async API
# ---------------------------------------------------------------------------
async def check_identity_async(
    conn,
    table_name: str,
    start_date: date,
    end_date: date,
    *,
    code: Optional[str] = None,
    exchange: Optional[str] = None,
    time_value: Optional[time] = None,
    date_column: str = "date",
    code_column: str = "code",
    exchange_column: str = "exchange",
    time_column: str = "time",
    skip_holidays: bool = True,
) -> Set[date]:
    """Async counterpart of :func:`check_identity`.

    Uses an existing asyncpg connection (no internal connect/close, because
    asyncpg connections are typically pooled and short-lived). The caller is
    responsible for opening/closing the connection.

    Args: see :func:`check_identity` (without ``conn=None`` -- conn is
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
    if exchange is not None:
        clauses.append(f'"{exchange_column}" = ${placeholder_idx}')
        params.append(exchange)
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
    present = {
        d for d in map(itemgetter(date_column), rows) if d is not None
    }
    return expected - present


# ---------------------------------------------------------------------------
# Year-keyed convenience wrapper
# ---------------------------------------------------------------------------
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

    Args: see :func:`check_identity` (code/time/exchange filters do not
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
