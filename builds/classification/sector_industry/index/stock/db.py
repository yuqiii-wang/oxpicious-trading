"""DB queries for the stock leaf — stock → index weight mapping + coverage."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

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

    result: Dict[str, List[Tuple[str, float]]] = {}
    for r in rows:
        result.setdefault(r["stock_code"], []).append(
            (r["index_code"], float(r["weight_pct"])))
    return result


async def fetch_stock_meta(conn) -> Dict[str, Dict[str, Any]]:
    """Fetch stock names + coverage from stock_identity (non-NULL close only).

    Returns: { code: { "name": ..., "n_days": ..., "first_date": ..., "last_date": ... } }
    """
    rows = await conn.fetch("""
        SELECT si.code,
               MAX(si.name) AS name,
               COUNT(*)     AS n_days,
               MIN(si.date)::text AS first_date,
               MAX(si.date)::text AS last_date
          FROM stats.stock_identity si
          JOIN stats.stock_basic_stats b ON si.date = b.date AND si.code = b.code
         WHERE b.close IS NOT NULL
         GROUP BY si.code
    """)
    return {
        r["code"]: {
            "name": r["name"] or "",
            "n_days": int(r["n_days"]),
            "first_date": r["first_date"],
            "last_date": r["last_date"],
        }
        for r in rows
    }
