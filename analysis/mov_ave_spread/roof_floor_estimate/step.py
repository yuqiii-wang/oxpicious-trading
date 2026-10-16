"""Pipeline runner — the roof/floor level estimates internal step.

run_roof_floor_estimates rides the shared run_table_step skeleton
(target-date detection / force purge / incremental filter / chunked
CSV-COPY insert / analysis_identity upsert) exactly like the ohlc
anchor step. Incremental mode: only dates present in the source
identity tables but NOT yet in
analysis.mov_ave_spreads_roof_floor_estimates are (re)computed (the
table is new — no legacy-row repair hook is needed). Force mode purges
the scope and recomputes everything. Invoked from
mov_ave_spread.__main__ after the anchor (ohlc) step, reusing the same
DB connection + source DataFrame.
"""
from __future__ import annotations

import pandas as pd

from analysis.mov_ave_spread._common import run_table_step
from analysis.mov_ave_spread.config.roof_floor_estimate import (
    ESTIMATE_ANALYSIS_NAME,
    ESTIMATE_DESCRIPTION,
    ESTIMATE_TABLE,
)
from analysis.mov_ave_spread.roof_floor_estimate.compute import (
    compute_estimate_columns,
)
from analysis.mov_ave_spread.roof_floor_estimate.long_frame import (
    build_estimate_long_frame,
)

import logging
logger = logging.getLogger(__name__)


async def run_roof_floor_estimates(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the roof/floor level-estimate step on the parent's source data.

    Args:
      conn: asyncpg connection (reused from the parent pipeline).
      df: source DataFrame with [sec_type, code, date, price, high,
          low] — the FULL per-code history (rolling methods need up to
          1275 trading days of lookback).
      force: purge the scope and recompute all dates.
      pool / max_concurrent: the chunked insert's pool + concurrency.
      sec_type: scope to one sec_type (the parent's per-sec_type loop).
      code_filter: single-code mode (--code) — compute all dates for
          this code (rows were pre-deleted by the caller).
    """
    await run_table_step(
        conn, df,
        title="ROOF_FLOOR_ESTIMATES (internal step of mov_ave_spread)",
        tag="e",
        table=ESTIMATE_TABLE,
        analysis_name=ESTIMATE_ANALYSIS_NAME,
        detail_name="mov_ave_spreads_roof_floor_estimates",
        description=ESTIMATE_DESCRIPTION,
        needed_cols=[
            "sec_type", "code", "date", "price", "high", "low",
        ],
        compute_fn=compute_estimate_columns,
        build_df_fn=build_estimate_long_frame,
        force=force,
        pool=pool,
        max_concurrent=max_concurrent,
        sec_type=sec_type,
        code_filter=code_filter,
        label="mov_ave_spreads_roof_floor_estimates",
    )
