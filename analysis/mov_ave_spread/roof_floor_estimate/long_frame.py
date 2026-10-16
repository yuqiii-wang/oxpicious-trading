"""Wide -> LONG melt for the roof/floor level estimates table.

The compute pipeline produces one (sec_type, code, date) frame with a
roof_/floor_ column pair per (method, window); the target table
analysis.mov_ave_spreads_roof_floor_estimates is LONG — one row per
(sec_type, code, date, period, method). This boundary melt is fully
vectorized: one slice + rename per (window, method) part, one concat,
and a whole-column overflow guard (no per-row loops, no row dicts —
the CSV COPY writer consumes the frame directly).

The method column is stamped as its int16 label code (the label-code
contract: codes ride frames; labels never do).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from analysis.mov_ave_spread._common import null_if_overflow_counted
from analysis.mov_ave_spread.config.roof_floor_estimate import (
    ESTIMATE_COLUMNS,
    ESTIMATE_METHOD_ORDER,
    ESTIMATE_WINDOWS,
)
from analysis.mov_ave_spread.roof_floor_estimate.methods import (
    ESTIMATE_METHOD_CODE,
)

logger = logging.getLogger(__name__)


def build_estimate_long_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Melt the wide estimate frame to the long DB schema.

    Args:
        df: wide frame from compute_estimate_columns (typically
            filtered to one date-chunk by the chunked insert path).

    Returns:
        Long frame with columns [sec_type, code, date, period, method,
        today_close, roof_estimate, floor_estimate], ready for
        ``csv_copy_from_frame_async``.
    """
    if df.empty:
        return pd.DataFrame(
            columns=["sec_type", "code", "date", "period", "method"]
            + list(ESTIMATE_COLUMNS)
        )

    base: list[str] = ["sec_type", "code", "date", "today_close"]
    parts: list[pd.DataFrame] = []
    for w in ESTIMATE_WINDOWS:
        for m in ESTIMATE_METHOD_ORDER:
            roof_col = f"roof_{m}_{w}d"
            floor_col = f"floor_{m}_{w}d"
            parts.append(
                df[base + [roof_col, floor_col]]
                .rename(columns={
                    roof_col: "roof_estimate",
                    floor_col: "floor_estimate",
                })
                .assign(
                    period=w,
                    method=np.int16(ESTIMATE_METHOD_CODE[m]),
                )
            )
    long_df = pd.concat(parts, ignore_index=True)
    long_df = long_df[
        ["sec_type", "code", "date", "period", "method"]
        + list(ESTIMATE_COLUMNS)
    ]

    # Overflow guard on the numeric columns (whole-column ops; same
    # NUMERIC(18,6) safety net as the anchor table's melt).
    nulled: dict[str, int] = {}
    for c in ("today_close", "roof_estimate", "floor_estimate"):
        clean, n = null_if_overflow_counted(long_df[c], max_abs=1e12, scale=6)
        long_df[c] = clean
        if n > 0:
            nulled[c] = n
    if nulled:
        total: int = sum(nulled.values())
        per: str = ", ".join(f"{c}={n}" for c, n in nulled.items())
        logger.info(f"    -> overflow-guard nulled {total:,} value(s) across "
              f"{len(nulled)} column(s): {per}")

    return long_df
