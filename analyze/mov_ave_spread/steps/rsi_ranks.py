"""Internal RSI-rank step for analyze.mov_ave_spread.

Per-code RSI HISTORY RANKS for the 6 Wilder windows — one row per
(sec_type, code, date) in analysis.mov_ave_rsi_ranks, mirroring
analysis.mov_ave_rsi row-for-row.

=======================================================================
  FINANCIAL SEMANTICS
=======================================================================

For each (sec_type, code) and window W, rank_rsi_{W}days is the ordinal
position of that date's rsi_{W}days WITHIN THE CODE'S OWN FULL RSI
HISTORY (every date whose rsi_{W}days is non-null): rank 1 = the code's
highest RSI reading ever for the window, N = the lowest. Ties break by
earlier date (order of appearance — the build frame is date-sorted).
Ranks are DENSE 1..N, so MAX(rank) per (sec_type, code, window) equals
the count of ranked days and the UI's "top X%" cutoff is
``rank <= ceil(X% * MAX(rank))``.

A date's rank is NULL wherever its RSI is NULL (the first W-1 rows of a
code's history — min_periods=W — plus fully-flat gain/loss windows),
so the rank columns are nullable and the table mirrors mov_ave_rsi 1:1.

=======================================================================
  REBUILD SEMANTICS (ranks SHIFT — wholesale per sec_type)
=======================================================================

Every stored rank can shift whenever ANY new day's RSI lands anywhere in
the code's history: a new reading re-ranks the whole distribution and
moves every percentile denominator. Incremental per-date upserts would
therefore update nearly every row on every run — the table is instead
rebuilt WHOLESALE per sec_type on every run that recomputes RSI (the
streaks precedent): DELETE the run's sec_type/code scope, recompute from
the RSI step's already-computed full-history frame (no extra DB
round-trip, and no recomputation of the recursive Wilder EWM), CSV-COPY
insert, registry upsert.

Per-code semantics also keep the rebuild valid in --code single-code
mode: a single code's ranks need only that code's own history (a
per-date cross-sectional rank would need the whole universe's frame,
which a single-code run does not have). The run hook fires only when the
RSI step actually computed (new dates / force / single-code), so the
table is never rewritten on no-op runs.

=======================================================================
  GPU LAYOUT (cudf.pandas)
=======================================================================

The rank is ONE whole-frame grouped pass per window, formulated to stay
on the cudf fast path end-to-end: cuDF's ``groupby.rank`` rejects
NaN-bearing value columns (NotImplementedError → CPU fallback), so the
rank is built as sort + cumcount — a frame sort by
(sec_type, code, value DESC, date ASC, NaN last) makes each
(sec_type, code) group contiguous with its non-null values in rank
order, ``groupby(...).cumcount()`` (GPU-native) numbers them 1..k, and
a ``where(notna)`` masks the trailing NaN-value rows back to NaN. The
result maps back positionally via the (unique) index labels — an
assignment the proxy aligns by index, no host ndarrays anywhere.
Ties break by earlier date (the explicit date-ASC sort key — identical
to pandas rank(method="first") on the date-sorted frame).

Ranks stay float64 in the compute frame (NaN = unranked — the skill's
"no nullable Int64 inside compute frames" rule). The integer/NULL split
happens ONCE at the emit boundary (build_ranks_copy_frame) in raw host
numpy — the sanctioned DB-literal boundary — because a nullable-Int64
column defeats the CSV COPY renderer (its ``to_numpy()`` cannot carry
NA as an integer and degrades to float64, which PostgreSQL COPY then
rejects as "1634.0" into an INTEGER column).
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from _common.db_commons import chunked_purge_async, csv_copy_frame_chunked_async
from _common.df_utils import host_array, host_unique
from _common.labels import SEC_TYPE_LABEL
from analyze._common import upsert_analysis_identity
from analyze.mov_ave_spread._common.steps import materialize_sec_type_text
from analyze.mov_ave_spread.steps.rsi import RSI_WINDOWS

import logging
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

RSI_RANKS_TABLE = "analysis.mov_ave_rsi_ranks"
RSI_RANKS_ANALYSIS_NAME = "mov_ave_rsi_ranks"

RSI_RANKS_COLUMNS = [
    "sec_type", "code", "date",
    *[f"rank_rsi_{w}days" for w in RSI_WINDOWS],
]

RSI_RANKS_DESCRIPTION = (
    "Per-code RSI history ranks (ETF + Index + Stock). For each security "
    "and date, the ordinal rank of each Wilder RSI window (rsi_3days .. "
    "rsi_60days) WITHIN THE CODE'S OWN FULL RSI HISTORY: rank 1 = the "
    "code's highest RSI reading for the window, N = the lowest, earlier "
    "date wins ties; ranks are dense 1..N so MAX(rank) per (code, window) "
    "is the count of ranked days and a top-X% cutoff is rank <= ceil(X% * "
    "MAX(rank)). Rank columns are NULL wherever that window's RSI is NULL "
    "(before min_periods=W / flat windows). Mirrors analysis.mov_ave_rsi "
    "row-for-row and is rebuilt WHOLESALE per sec_type on every run that "
    "recomputes RSI (a new day's RSI can re-rank the code's whole "
    "history, so incremental upserts would update nearly every row)."
)

# Rows per CSV COPY chunk left at the helper's default (the 100K-row
# single-transaction commit-chunk target) so each chunk's WAL stays
# committable — the throttle + replica-lag cap live inside
# csv_copy_frame_chunked_async.


# ---------------------------------------------------------------------------
#  Compute (whole-frame grouped rank on the cudf proxy)
# ---------------------------------------------------------------------------

def compute_rsi_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """Add rank_rsi_{W}days columns for the 6 Wilder windows.

    Per (sec_type, code): ordinal rank of each date's rsi_{W}days within
    the code's own non-null RSI history — rank 1 = highest RSI, ties
    break by earlier date, NaN (unranked) where the window's RSI is NaN.

    Formulated as sort + cumcount (see the module docstring's GPU layout
    section): cuDF's groupby.rank rejects NaN-bearing columns, so each
    window's rank is one frame sort (groups contiguous, values
    descending, date ascending among ties, NaN last) + one GPU-native
    groupby.cumcount + a where(notna) mask, assigned back by index
    labels.

    Args:
      df: frame carrying (sec_type, code, date, rsi_{W}days) — the RSI
          step's FULL per-code history frame (compute_rsi output) with a
          unique default index.

    Returns:
      A NEW frame with only the rank-relevant columns (sec_type, code,
      date, the 6 rsi values, the 6 rank columns). Ranks stay float64
      (NaN = unranked) — the integer/NULL split happens at the emit
      boundary only.
    """
    if df.empty:
        return df
    rsi_cols = [f"rsi_{w}days" for w in RSI_WINDOWS]
    base = df[["sec_type", "code", "date", *rsi_cols]].copy()
    grp = ["sec_type", "code"]
    for w, col in zip(RSI_WINDOWS, rsi_cols):
        ordered = base.sort_values(
            [*grp, col, "date"],
            ascending=[True, True, False, True],
            na_position="last",
        )
        rk = ordered.groupby(grp, sort=False).cumcount() + 1
        # Cast to float64 BEFORE the where: cudf's where on an int64
        # column builds an int64-with-NULLs column, and every later
        # to_numpy on it logs a cast fallback — float64 carries NaN
        # natively (na_option="keep" semantics).
        base[f"rank_rsi_{w}days"] = (
            rk.astype("float64").where(ordered[col].notna())
        )
    return base


def build_ranks_copy_frame(ranks: pd.DataFrame) -> pd.DataFrame:
    """Emit-boundary frame: project the rank columns and render each as a
    host object array of python ints / None — COPY-safe ("1634" / "").

    Built via the REAL pandas ctor from host numpy arrays (the same
    host-pure pattern csv_copy_from_frame_async itself uses): a nullable
    Int64 / int64-with-NULLs proxy column would defeat the renderer (its
    ``to_numpy()`` cannot carry NA as an integer and degrades to float64,
    which PostgreSQL COPY rejects as "1634.0" into an INTEGER column), so
    the integer/NULL split happens in raw host numpy ONCE.
    """
    real_pd = getattr(pd, "_fsproxy_slow", pd)
    data: dict = {
        "sec_type": host_array(ranks["sec_type"].to_numpy()),
        "code": host_array(ranks["code"].to_numpy()),
        "date": host_array(ranks["date"].to_numpy()),
    }
    for w in RSI_WINDOWS:
        arr = host_array(
            ranks[f"rank_rsi_{w}days"].to_numpy(
                dtype="float64", na_value=np.nan)
        )
        obj = np.empty(arr.size, dtype=object)
        valid = np.isfinite(arr)
        obj[valid] = arr[valid].astype("int64")
        obj[~valid] = None
        data[f"rank_rsi_{w}days"] = obj
    return real_pd.DataFrame(data, columns=RSI_RANKS_COLUMNS)


# ---------------------------------------------------------------------------
#  Pipeline (internal step — hooked into run_rsi via run_table_step's
#  post_compute_fn, invoked on the RSI step's FULL computed frame)
# ---------------------------------------------------------------------------

async def rebuild_rsi_ranks(
    conn,
    df: pd.DataFrame,
    *,
    sec_type: str | None = None,
    code_filter: str | None = None,
    pool=None,
    max_concurrent: int = 20,
) -> None:
    """Wholesale-rebuild analysis.mov_ave_rsi_ranks for the run's scope.

    Args:
      conn: asyncpg connection (reused from the RSI step).
      df: the RSI step's computed FULL per-code history frame (must carry
          the rsi_{W}days columns). NOT filtered to target dates — ranks
          need the whole history.
      sec_type: the run's sec_type scope (the parent loop passes one
          sec_type at a time; None infers from the frame).
      code_filter: single-code mode (--code) — purge + rebuild only this
          code's rows (valid: per-code ranks need only the code's frame).
      pool / max_concurrent: accepted for API symmetry with the sibling
          steps (the COPY runs sequentially on ``conn`` — the throttle
          lives inside csv_copy_frame_chunked_async).
    """
    if df.empty:
        logger.info("    -> no source data; skipping RSI-ranks rebuild.")
        return

    t0 = time.time()
    sec_types = (sec_type,) if sec_type is not None else (
        tuple(SEC_TYPE_LABEL[c] for c in host_unique(df["sec_type"]))
    )

    logger.info("\n[rr] Rebuilding RSI history ranks (wholesale: every new "
          "RSI day can re-rank a code's whole history)...")

    # ---- Phase 1: rank the full-history frame (one grouped pass/window) --
    ranks = compute_rsi_ranks(df)
    # Write boundary: decode the int16 sec_type frame code back to TEXT.
    ranks = materialize_sec_type_text(ranks)
    logger.info(f"    -> ranked {len(ranks):,} rows across "
          f"{len(sec_types)} sec_type(s)")

    # ---- Phase 2: purge the scope (conflict-free COPY afterwards) --------
    if code_filter is not None:
        status = await conn.execute(
            f"DELETE FROM {RSI_RANKS_TABLE} "
            f"WHERE sec_type = $1 AND code = $2",
            sec_type, code_filter,
        )
        n_del = int(status.rsplit(" ", 1)[-1]) if status else 0
        logger.info(f"    -> single-code mode: deleted {n_del:,} rank rows "
              f"for ({sec_type}, {code_filter})")
    else:
        for st in sec_types:
            n_del = await chunked_purge_async(
                conn, RSI_RANKS_TABLE,
                where_sql="sec_type = $1", params=(st,),
            )
            logger.info(f"    -> deleted {n_del:,} {st} rank rows "
                  f"(wholesale rebuild)")

    # ---- Phase 3: CSV-COPY insert (partition-key chunks + throttle) ------
    total = await csv_copy_frame_chunked_async(
        conn, RSI_RANKS_TABLE, build_ranks_copy_frame(ranks),
        partition_key="code",
        label="rsi_ranks",
    )
    logger.info(f"    -> inserted {total:,} rank rows "
          f"({time.time() - t0:.1f}s)")

    # ---- Phase 4: registry ------------------------------------------------
    await upsert_analysis_identity(
        conn,
        name=RSI_RANKS_ANALYSIS_NAME,
        detail_name=RSI_RANKS_TABLE,
        description=RSI_RANKS_DESCRIPTION,
    )


def make_ranks_rebuild_hook(
    conn,
    *,
    sec_type: str | None,
    code_filter: str | None,
    pool=None,
    max_concurrent: int = 20,
):
    """The run_table_step ``post_compute_fn`` hook for run_rsi: rebuild the
    RSI ranks from the step's FULL computed frame (before the target-date
    filter — ranks need the whole history). Closes over the run's scope.
    """

    async def _rebuild(computed_df: pd.DataFrame) -> None:
        await rebuild_rsi_ranks(
            conn, computed_df,
            sec_type=sec_type, code_filter=code_filter,
            pool=pool, max_concurrent=max_concurrent,
        )

    return _rebuild
