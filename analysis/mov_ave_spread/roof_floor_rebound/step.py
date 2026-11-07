"""Pipeline runner — the roof/floor rebound-audit internal step.

WHOLESALE rebuild per sec_type (the high_low_pct_streaks precedent):
event OUTCOMES depend on data AFTER the event date (retest dates, later
breaks resolve months later), so PK-coverage incremental does not apply
— every run that computes a sec_type DELETEs that scope and recomputes
all events from the code's full history. Single-code mode (--code)
purges + recomputes just that code.

The emit boundary renders the INTEGER days/count columns as int strings
(the CSV COPY writer's INTEGER-input contract — float64 "3.0" is
rejected; the ohlc melt's days_since_last precedent) and leaves the
BOOLEAN columns as python bool/None (to_csv renders True/False, which
PostgreSQL BOOLEAN COPY accepts; None renders empty -> NULL).
"""
from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

from _common.db_commons import chunked_purge_async, csv_copy_from_frame_async
from _common.df_utils import host_array, host_unique
from analysis.mov_ave_spread._common import (
    StepRun,
    TableStep,
    sec_type_code,
)
from analysis.mov_ave_spread._common.steps import infer_sec_types
from analysis.mov_ave_spread.config.roof_floor_rebound import (
    REBOUND_ANALYSIS_NAME,
    REBOUND_DESCRIPTION,
    REBOUND_TABLE,
    REBOUND_WINDOWS,
)
from analysis.mov_ave_spread.roof_floor_rebound.events import (
    _EVENT_COLUMNS,
    compute_rebound_events,
)
from analysis._common import upsert_analysis_identity

logger = logging.getLogger(__name__)

_NEEDED_COLS = ["sec_type", "code", "date", "price", "high", "low"]
_CHUNK_ROWS = 100_000
_INT_COLUMNS = ("days_to_retest", "days_to_break", "retest_episodes")


def _render_emit_columns(events: pd.DataFrame) -> pd.DataFrame:
    """Render the INTEGER columns as int strings (empty -> NULL on COPY)."""
    for c in _INT_COLUMNS:
        vals = host_array(events[c].to_numpy())
        ok = ~np.isnan(vals)
        out = np.full(len(vals), None, dtype=object)
        out[ok] = vals[ok].astype("int64").astype(str)
        events[c] = out
    return events


async def _copy_events_chunked(conn, events: pd.DataFrame) -> int:
    """CSV-COPY the events frame in row-count chunks (sequential on
    ``conn`` — the audit is a sparse event table). Conflict-free by
    construction: the scope was DELETEd upfront (wholesale rebuild)."""
    n_total = len(events)
    n_chunks = (n_total + _CHUNK_ROWS - 1) // _CHUNK_ROWS
    total = 0
    for i in range(n_chunks):
        chunk = events.iloc[i * _CHUNK_ROWS:(i + 1) * _CHUNK_ROWS]
        n = await csv_copy_from_frame_async(
            conn, REBOUND_TABLE, chunk, columns=list(_EVENT_COLUMNS),
        )
        total += n
    return total


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__; the
#  wholesale flow overrides TableStep.execute itself)
# ---------------------------------------------------------------------------

class RoofFloorReboundStep(TableStep):
    """Run the rebound-audit step on the parent's source data.

    WHOLESALE rebuild per sec_type (the high_low_pct_streaks precedent):
    event OUTCOMES depend on data AFTER the event date (retest dates,
    later breaks resolve months later), so PK-coverage incremental does
    not apply — every run that computes a sec_type DELETEs that scope
    and recomputes all events from the code's full history.

    Args (now run fields / the execute sec_type argument):
      run.conn: asyncpg connection (reused from the parent pipeline).
      df: source DataFrame with [sec_type, code, date, price, high,
          low] — the FULL per-code history (outcomes need all
          post-event data).
      run.force: accepted for API symmetry (the wholesale DELETE below
              already covers the force semantics).
      run.pool / run.max_concurrent: accepted for API compatibility
              (unused — sequential COPY).
      sec_type: scope to one sec_type (the parent's per-sec_type loop).
      run.code_filter: single-code mode (--code): purge + recompute
          only this code's audit rows.
    """

    title = "ROOF_FLOOR_REBOUNDS (internal step of mov_ave_spread)"
    tag = "rb"
    table = REBOUND_TABLE
    analysis_name = REBOUND_ANALYSIS_NAME
    detail_name = "mov_ave_spreads_roof_floor_rebounds"
    description = REBOUND_DESCRIPTION
    needed_cols = tuple(_NEEDED_COLS)
    in_skip_gate = True

    async def has_pending_work(
        self, conn, sec_type: str, src_identity_table: str,
    ) -> bool:
        """The parent phase-0 empty-scope check: the audit rebuilds
        wholesale per sec_type (the streaks precedent), so only a
        COMPLETELY EMPTY scope (fresh schema / wiped table) counts as
        pending work. Read-only; ``src_identity_table`` is unused."""
        return not await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM " + self.table
            + " WHERE sec_type = $1)",
            sec_type_code(sec_type),
        )

    async def execute(
        self, run: StepRun, df: pd.DataFrame,
        *, sec_type: str | None = None,
    ) -> None:
        t0 = time.time()
        logger.info("\n" + "=" * 78)
        logger.info("  ROOF_FLOOR_REBOUNDS (internal step of mov_ave_spread)")
        logger.info("=" * 78)
        if run.code_filter is not None:
            logger.info(f"    mode: SINGLE-CODE (audit rebuild for "
                  f"{run.code_filter})")
        else:
            logger.info("    mode: WHOLESALE per sec_type (outcomes depend on "
                  "post-event data; high_low_pct_streaks precedent)")

        step_df = df[_NEEDED_COLS].copy()
        if step_df.empty:
            logger.info("    -> no source data; skipping rebound-audit step.")
            return

        sec_types = (
            (sec_type,) if sec_type is not None
            else infer_sec_types(step_df, None)
        )

        n_total = 0
        for st in sec_types:
            st_code = sec_type_code(st)
            if run.code_filter is not None:
                st_df = step_df[step_df["code"] == run.code_filter]
                scope_sql = "sec_type = $1 AND code = $2"
                scope_params: tuple = (st_code, run.code_filter)
            else:
                st_df = step_df[step_df["sec_type"] == st_code]
                scope_sql = "sec_type = $1"
                scope_params = (st_code,)

            # ---- Wholesale scope wipe -------------------------------------
            n_del = await chunked_purge_async(
                run.conn, REBOUND_TABLE,
                where_sql=scope_sql, params=scope_params,
            )
            logger.info(f"    -> {st}: deleted {n_del:,} audit rows"
                  + (f" (code {run.code_filter})"
                     if run.code_filter is not None else ""))

            if st_df.empty:
                continue

            # ---- Compute + insert -----------------------------------------
            events = _render_emit_columns(compute_rebound_events(st_df))
            n_codes = len(host_unique(st_df["code"]))
            logger.info(f"    -> {st}: {len(events):,} extreme events across "
                  f"{n_codes:,} codes "
                  f"({len(REBOUND_WINDOWS)} windows x 2 sides)")
            if events.empty:
                continue
            n = await _copy_events_chunked(run.conn, events)
            n_total += n
            logger.info(f"    -> {st}: inserted {n:,} audit rows")

        # ---- Register in analysis_identity ------------------------------
        await upsert_analysis_identity(
            run.conn,
            name=REBOUND_ANALYSIS_NAME,
            detail_name="mov_ave_spreads_roof_floor_rebounds",
            description=REBOUND_DESCRIPTION,
        )

        logger.info(f"\n  mov_ave_spreads_roof_floor_rebounds wall time: "
              f"{time.time() - t0:.1f}s")
