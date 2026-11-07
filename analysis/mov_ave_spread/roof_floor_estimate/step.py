"""Pipeline runner — the roof/floor level estimates internal step.

RoofFloorEstimateStep rides the shared TableStep skeleton
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

from analysis.mov_ave_spread._common import TableStep
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


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__ via the
#  TableStep template in _common.steps)
# ---------------------------------------------------------------------------

class RoofFloorEstimateStep(TableStep):
    """Run the roof/floor level-estimate step on the parent's source data.

    Reuses the caller's DB connection; the source DataFrame must carry
    [sec_type, code, date, price, high, low] — the FULL per-code
    history (rolling methods need up to 1275 trading days of lookback).

    Write flavor "frame": :meth:`build_frame` delegates to
    ``build_estimate_long_frame`` (wide per-(method, window) layout →
    LONG (sec_type, code, date, period, method) rows, chunked CSV
    COPY). The old run_table_step call's ``label`` kwarg equalled
    ``detail_name`` ("mov_ave_spreads_roof_floor_estimates") — dropped:
    TableStep.execute labels the insert with ``detail_name``.

    Args (now run fields / the execute sec_type argument):
      run.conn: asyncpg connection (reused from the parent pipeline).
      df: source DataFrame with [sec_type, code, date, price, high,
          low] — the FULL per-code history.
      run.force: purge the scope and recompute all dates.
      run.pool / run.max_concurrent: the chunked insert's pool +
          concurrency.
      sec_type: scope to one sec_type (the parent's per-sec_type loop).
      run.code_filter: single-code mode (--code) — compute all dates
          for this code (rows were pre-deleted by the caller).
    """

    title = "ROOF_FLOOR_ESTIMATES (internal step of mov_ave_spread)"
    tag = "e"
    table = ESTIMATE_TABLE
    analysis_name = ESTIMATE_ANALYSIS_NAME
    detail_name = "mov_ave_spreads_roof_floor_estimates"
    description = ESTIMATE_DESCRIPTION
    needed_cols = ("sec_type", "code", "date", "price", "high", "low")
    write_flavor = "frame"
    in_skip_gate = True

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        return compute_estimate_columns(df)

    def build_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        return build_estimate_long_frame(df)
