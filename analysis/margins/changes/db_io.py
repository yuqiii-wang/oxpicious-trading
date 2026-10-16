"""DB I/O for margin_changes: truncate-then-COPY-insert.

Sanitizes numeric columns (NaN → NULL, round to 8 decimals — the
rz_buy_vs_trading_amt_ratio column is NUMERIC(12,10) and index shares run
sub-1‱, where the former round(4) quantized to integer ‱ and zeroed 392
episodes), restores
the table-schema column order, and COPY-inserts the episodes in
row-count chunks — the sanitized dict list never exceeds one chunk.
The table is always TRUNCATEd first — new dates shift trend
boundaries, so a full recompute is the only correct option.
"""
from __future__ import annotations

import pandas as pd

from _common.build_commons import truncate_table_async
from _common.db_commons import copy_frame_chunked_async

from analysis.margins.changes.constants import (
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

    return await copy_frame_chunked_async(
        conn, TABLE_CHANGES, episodes,
        columns=INSERT_COLUMNS,
        numeric_cols=NUMERIC_COLS, round_to=8,
        label="margin_changes",
    )
