"""DB I/O for margin_changes: truncate-then-COPY-insert.

Sanitizes numeric columns (NaN → NULL, round to 4 decimals), restores
the table-schema column order, and COPY-inserts all episodes in a single
batch. The table is always TRUNCATEd first — new dates shift trend
boundaries, so a full recompute is the only correct option.
"""
from __future__ import annotations

import pandas as pd

from _common.build_commons import copy_insert_async, truncate_table_async
from analyze._common import sanitize_for_db_insert

from analyze.margins.changes.constants import (
    INSERT_COLUMNS,
    NUMERIC_COLS,
    TABLE_CHANGES,
)


async def truncate_and_insert(conn, episodes: pd.DataFrame) -> int:
    """Truncate margin_changes and COPY-insert the given episodes.

    Returns the number of rows inserted.
    """
    await truncate_table_async(conn, TABLE_CHANGES)

    if episodes.empty:
        return 0

    # Ensure column order matches the table schema.
    episodes = episodes[INSERT_COLUMNS].copy()
    # Ensure days_of_trend is int (groupby may produce float).
    episodes["days_of_trend"] = episodes["days_of_trend"].astype(int)

    rows = sanitize_for_db_insert(
        episodes,
        numeric_cols=NUMERIC_COLS,
        round_to=4,
    )
    n = await copy_insert_async(
        conn, TABLE_CHANGES, rows,
        columns=INSERT_COLUMNS,
    )
    return n
