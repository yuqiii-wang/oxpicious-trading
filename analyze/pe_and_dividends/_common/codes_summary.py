"""Per-code summary refresh — ``analysis.pe_codes``.

Rebuilds the (sec_type, code)-grain summary the /api/analysis/pe/codes
endpoint reads (date coverage over pe ∪ dividends, the latest metric
snapshots, and the latest identity name). The former request-time
aggregate cost 4.7s on the index universe / 35s on stocks (UNION + sort +
GROUP BY over 1.3M-7.1M rows per fetch) and gated the PE / Dividends
pages' first open; running the same aggregate offline after each detail
write pass turns the endpoint into a small indexed table read — the
``analysis.mov_ave_spreads_codes`` pattern.
"""
from __future__ import annotations

import logging
import time

from analyze.pe_and_dividends._common.config import SEC_TYPE_IDENTITY_TABLE

logger = logging.getLogger(__name__)

PE_CODES_TABLE = "analysis.pe_codes"


async def refresh_pe_codes_summary(
    conn, sec_types, code: str | None = None,
) -> None:
    """Rebuild analysis.pe_codes for the affected sec_types.

    DELETE + INSERT per sec_type (optionally scoped to ONE code for
    ``--code`` runs), mirroring mov_ave_spread's refresh_codes_summary.
    Column semantics mirror the former request-time query exactly (see
    database/sql/analysis/11_pe_and_dividends.sql).
    """
    for st in sec_types:
        t0 = time.time()
        identity = SEC_TYPE_IDENTITY_TABLE[st]
        if code:
            where, params = "sec_type = $1 AND code = $2", (st, code)
        else:
            where, params = "sec_type = $1", (st,)
        await conn.execute(
            f"DELETE FROM {PE_CODES_TABLE} WHERE {where}", *params,
        )
        n = await conn.execute(f"""
            WITH u AS (
                SELECT code, date FROM analysis.pe WHERE {where}
                UNION
                SELECT code, date FROM analysis.dividends WHERE {where}
            ), agg AS (
                SELECT code,
                       MIN(date) AS first_date, MAX(date) AS last_date,
                       COUNT(*) AS n_dates
                  FROM u GROUP BY code
            ), latest_pe AS (
                SELECT DISTINCT ON (code) code, pe
                  FROM analysis.pe WHERE {where}
                 ORDER BY code, date DESC
            ), latest_dy AS (
                SELECT DISTINCT ON (code) code, dividend_yield
                  FROM analysis.dividends WHERE {where}
                 ORDER BY code, date DESC
            )
            INSERT INTO {PE_CODES_TABLE} (
                sec_type, code, name, first_date, last_date, n_dates,
                latest_pe, latest_dividend_yield
            )
            SELECT $1, a.code, COALESCE(n.name, ''),
                   a.first_date, a.last_date, a.n_dates,
                   lp.pe, ld.dividend_yield
              FROM agg a
              LEFT JOIN LATERAL (
                  SELECT x.name FROM {identity} x
                   WHERE x.code = a.code
                   ORDER BY x.date DESC LIMIT 1 OFFSET 0
              ) n ON TRUE
              LEFT JOIN latest_pe lp ON lp.code = a.code
              LEFT JOIN latest_dy ld ON ld.code = a.code
        """, *params)
        rows = int(n.rsplit(" ", 1)[-1]) if n else 0
        logger.info(f"  [{st}] pe_codes summary refreshed: {rows:,} codes "
              f"({time.time() - t0:.1f}s)")
