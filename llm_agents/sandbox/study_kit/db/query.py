"""study_kit.db.query — run ONE SELECT into a DataFrame.

READ-ONLY by the role (the container's preset PG* env points at the
``readonly_analytics`` role — writes raise server-side). Numeric result
columns arrive as Decimal from psycopg and are coerced to float here,
so downstream pandas math is native. Prefer narrow aggregate SQL over
wide fetches — the middleware caps reads at 1 MB.
"""
from __future__ import annotations

from typing import Any, Optional, Iterable

import pandas as pd

from study_kit.db._shared import connect, coerce_frame


def query(sql: str, params: Optional[Iterable[Any]] = None) -> pd.DataFrame:
    """Run ONE SELECT and return the rows as a DataFrame.

    READ-ONLY by the role (writes raise server-side). Numeric result
    columns arrive as Decimal from psycopg and are coerced to float
    here, so downstream pandas math is native. Prefer narrow aggregate
    SQL over wide fetches — the middleware caps reads at 1 MB.
    """
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        if cur.description is None:
            raise ValueError("query returned no result set — SELECT only")
        columns = [d.name for d in cur.description]
        return coerce_frame(pd.DataFrame(cur.fetchall(), columns=columns))
