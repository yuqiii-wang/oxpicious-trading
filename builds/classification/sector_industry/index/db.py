"""DB queries for the index leaf — fetch index names + coverage."""
from __future__ import annotations

from typing import Any, Dict

from _common.build_commons import rec_cols


async def fetch_index_meta(conn) -> Dict[str, Dict[str, Any]]:
    """Fetch index names + coverage from index_identity.

    Returns: { code: { "name": ..., "n_days": ..., "first_date": ..., "last_date": ... } }
    """
    rows = await conn.fetch("""
        SELECT code,
               MAX(name) AS name,
               COUNT(*)   AS n_days,
               MIN(date)::text AS first_date,
               MAX(date)::text AS last_date
          FROM stats.index_identity
         GROUP BY code
    """)
    # Whole-column extraction (one positional-unpack pass)
    cols = rec_cols(rows)
    return {
        code: {
            "name": name or "",
            "n_days": int(n_days),
            "first_date": first_date,
            "last_date": last_date,
        }
        for code, name, n_days, first_date, last_date in zip(
            cols["code"], cols["name"], cols["n_days"],
            cols["first_date"], cols["last_date"],
        )
    }
