"""Per-code summary refresh for analyze.mov_ave_spread.

Rebuilds analysis.mov_ave_spreads_codes (the offline aggregate of the
detail table's per-code coverage + gap extremes + latest snapshot +
identity name) for the sec_types a pipeline run touched, so the
/mov-ave-spread/codes + /themes endpoints stay a small indexed-table read.
"""
from __future__ import annotations

import time

from analyze.mov_ave_spread.config import (
    CODES_SUMMARY_TABLE,
    DETAIL_TABLE,
    PAIRS,
    SEC_TYPE_IDENTITY_TABLE,
)

import logging
logger = logging.getLogger(__name__)

# Gap column → summary-table alias (price_vs_ma5 → p5, ma5_vs_ma20 → 5_20,
# ...). Mirrors the column names in 10_mov_ave_spreads_codes.sql.
_GAP_SUMMARY_ALIASES = [
    ("price_vs_ma5", "p5"),
    ("price_vs_ma20", "p20"),
    ("price_vs_ma60", "p60"),
    ("price_vs_ma120", "p120"),
    ("price_vs_ma255", "p255"),
    ("ma5_vs_ma20", "5_20"),
    ("ma5_vs_ma60", "5_60"),
    ("ma5_vs_ma120", "5_120"),
    ("ma5_vs_ma255", "5_255"),
]


async def refresh_codes_summary(
    conn, sec_types, code: str | None = None,
) -> None:
    """Rebuild analysis.mov_ave_spreads_codes for the affected sec_types.

    DELETE + INSERT per sec_type (optionally scoped to ONE code for
    ``--code`` runs): aggregates the detail table's per-code date coverage,
    the 9 gap columns' all-time extremes, the latest gap snapshot, and the
    latest identity name. This is the offline counterpart of what the
    /mov-ave-spread/codes + /themes endpoints used to compute per request
    (7-14s on the stock universe); after the refresh those endpoints are a
    small indexed table read.
    """
    # All 9 gap column names from PAIRS, in canonical order.
    gaps = [gap_col for _, _, gap_col in PAIRS]
    extremes = ",\n                       ".join(
        f"MAX({g}) AS max_{alias}, MIN({g}) AS min_{alias}"
        for g, alias in _GAP_SUMMARY_ALIASES
    )
    max_cols = ", ".join(f"max_{alias}" for _, alias in _GAP_SUMMARY_ALIASES)
    min_cols = ", ".join(f"min_{alias}" for _, alias in _GAP_SUMMARY_ALIASES)
    latest_cols = ", ".join(f"latest_{g}" for g in gaps)
    a_max_cols = ", ".join(f"a.max_{alias}" for _, alias in _GAP_SUMMARY_ALIASES)
    a_min_cols = ", ".join(f"a.min_{alias}" for _, alias in _GAP_SUMMARY_ALIASES)
    l_cols = ", ".join(f"l.{g}" for g in gaps)

    for st in sec_types:
        t0 = time.time()
        identity = SEC_TYPE_IDENTITY_TABLE[st]
        if code:
            where, params = "sec_type = $1 AND code = $2", (st, code)
        else:
            where, params = "sec_type = $1", (st,)
        await conn.execute(
            f"DELETE FROM {CODES_SUMMARY_TABLE} WHERE {where}", *params,
        )
        status = await conn.execute(f"""
            WITH agg AS (
                SELECT code,
                       MIN(date) AS first_date, MAX(date) AS last_date,
                       COUNT(date) AS n_dates,
                       {extremes}
                  FROM {DETAIL_TABLE}
                 WHERE {where}
                 GROUP BY code
            ),
            latest AS (
                SELECT DISTINCT ON (code) code,
                       {", ".join(gaps)}
                  FROM {DETAIL_TABLE}
                 WHERE {where}
                 ORDER BY code, date DESC
            )
            INSERT INTO {CODES_SUMMARY_TABLE} (
                sec_type, code, name, first_date, last_date, n_dates,
                {max_cols},
                {min_cols},
                {latest_cols}
            )
            SELECT $1, a.code, COALESCE(n.name, ''),
                   a.first_date, a.last_date, a.n_dates,
                   {a_max_cols},
                   {a_min_cols},
                   {l_cols}
              FROM agg a
              JOIN latest l ON l.code = a.code
              LEFT JOIN LATERAL (
                  SELECT x.name FROM {identity} x
                   WHERE x.code = a.code
                   ORDER BY x.date DESC LIMIT 1 OFFSET 0
              ) n ON TRUE
        """, *params)
        n = int(status.rsplit(" ", 1)[-1]) if status else 0
        logger.info(f"  [{st}] codes summary refreshed: {n:,} codes "
              f"({time.time() - t0:.1f}s)")
