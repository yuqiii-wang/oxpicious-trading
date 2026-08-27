"""DB queries for the stock leaf — stock → index weight mapping + coverage."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from _common.build_commons import rec_col, rec_cols

# Stock → index weight threshold: only map a stock to an index if the stock's
# weight in that index exceeds this percentage.
STOCK_WEIGHT_THRESHOLD = 2.0


async def fetch_stock_index_mapping(conn) -> Dict[str, List[Tuple[str, float]]]:
    """For each stock, find ALL indexes where the stock's weight > 2%.

    Uses the LATEST composition snapshot per index. BROAD-sector indices are
    NOT filtered here (the caller filters them using the in-memory ``indices``
    dict, which knows each index's sector_id). Returns:
        { stock_code: [(index_code, weight_pct), ...] }
    """
    rows = await conn.fetch("""
        WITH latest AS (
            SELECT code, MAX(snapshot_date) AS max_date
              FROM stats.sec_composition
             WHERE source_type = 'index' AND stock_code IS NOT NULL
             GROUP BY code
        )
        SELECT sc.stock_code, sc.code AS index_code, sc.weight_pct
          FROM stats.sec_composition sc
          JOIN latest ld ON sc.code = ld.code AND sc.snapshot_date = ld.max_date
         WHERE sc.source_type = 'index' AND sc.stock_code IS NOT NULL
           AND sc.weight_pct > $1::numeric
         ORDER BY sc.stock_code, sc.weight_pct DESC, sc.code
    """, STOCK_WEIGHT_THRESHOLD)

    # Whole-column zip pairing → grouping per stock (pure host, SQL-ordered
    # by stock_code so groups stay contiguous)
    result: Dict[str, List[Tuple[str, float]]] = {}
    for s, idx, w in zip(rec_col(rows, "stock_code"),
                         rec_col(rows, "index_code"),
                         rec_col(rows, "weight_pct")):
        result.setdefault(s, []).append((idx, float(w)))
    return result


async def fetch_stock_meta(conn) -> Dict[str, Dict[str, Any]]:
    """Fetch stock names + coverage from stock_identity (non-NULL close only).

    Returns: { code: { "name": ..., "exchange": ..., "n_days": ...,
                       "first_date": ..., "last_date": ... } }
    """
    rows = await conn.fetch("""
        SELECT si.code,
               MAX(si.name) AS name,
               MAX(si.exchange) AS exchange,
               COUNT(*)     AS n_days,
               MIN(si.date)::text AS first_date,
               MAX(si.date)::text AS last_date
          FROM stats.stock_identity si
          JOIN stats.stock_basic_stats b ON si.date = b.date AND si.code = b.code
         WHERE b.close IS NOT NULL
         GROUP BY si.code
    """)
    # Whole-column extraction (one positional-unpack pass)
    cols = rec_cols(rows)
    return {
        code: {
            "name": name or "",
            "exchange": exchange or "",
            "n_days": int(n_days),
            "first_date": first_date,
            "last_date": last_date,
        }
        for code, name, exchange, n_days, first_date, last_date in zip(
            cols["code"], cols["name"], cols["exchange"], cols["n_days"],
            cols["first_date"], cols["last_date"],
        )
    }
