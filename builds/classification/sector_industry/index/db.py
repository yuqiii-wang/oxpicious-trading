"""DB queries for the index leaf — fetch index names + coverage."""
from __future__ import annotations

from typing import Any, Dict


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
    return {
        r["code"]: {
            "name": r["name"] or "",
            "n_days": int(r["n_days"]),
            "first_date": r["first_date"],
            "last_date": r["last_date"],
        }
        for r in rows
    }
