"""Shared internal-step plumbing for analysis.mov_ave_spread.

Every internal step (rsi / holiday / ema / ohlc lines / ohlc gaps /
trading_amt / trading_amt_ratios) repeats the SAME pipeline skeleton
around its step-specific compute + sanitize:

  0. column-select the needed source columns, resolve sec_types,
     then determine the target dates:
       single-code (--code)  -> compute ALL dates (rows pre-deleted)
       force                 -> purge the scope (per-sec_type DELETE,
                                or whole-table TRUNCATE when unscoped)
       incremental (default) -> per-sec_type missing-date detection vs
                                the source identity tables, unioned
  1. compute the step columns over the FULL per-code history
  2. filter to the target dates (datetime64 isin — never a python-date
     set), build rows, chunked insert (dict-sanitize or CSV-COPY path)
  3. upsert analysis.analysis_identity

:class:`TableStep` is that skeleton as a template-method base class:
each step module declares one subclass carrying its identity
(``table`` / ``detail_name`` / ``needed_cols`` …) as class attributes
and its compute / sanitize / detect logic as hook methods; the
per-call plumbing (conn / pool / force / code_filter / backfill) rides
in a :class:`StepRun` value built by the parent pipeline instead of the
keyword soup the old ``run_table_step`` function threaded through every
call. Steps with wholly custom flows (wholesale registries, pair-keyed
bands, streaks) override ``execute`` itself and reuse the smaller
helpers (infer_sec_types, filter_target_dates, purge_step_scope,
diff_write_scope_async).
"""
from __future__ import annotations

import abc
import datetime as dt
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Set

import asyncpg
import pandas as pd

from _common.build_commons import (
    truncate_table_async,
    find_missing_analysis_dates,
)
from _common.db_commons import (
    chunked_purge_async,
    csv_copy_from_frame_async,
)
from _common.df_utils import (
    column_subset,
    host_unique,
    to_dt64,
)
from _common.labels import SEC_TYPE_CODE, SEC_TYPE_LABEL
from analysis._common import (
    build_and_insert_chunked,
    build_and_insert_chunked_df,
    upsert_analysis_identity,
)
from analysis.mov_ave_spread.config import SEC_TYPE_IDENTITY_TABLE

import logging
logger = logging.getLogger(__name__)
from analysis.mov_ave_spread._common.data_definitions import (
    StepRun,
)

# ---------------------------------------------------------------------------
#  Default increment bound — the nightly run covers only the LATEST
#  missing dates
# ---------------------------------------------------------------------------
# Incremental detection is date-grain over the FULL identity history, so a
# table that is newer than the history it covers (e.g. roof_floor_estimates
# vs ~20y of ETF identity) reports thousands of missing dates and the
# nightly default would grind through a multi-year backfill every run.
# The default therefore BOUNDS the increment to the newest
# INCREMENTAL_RECENT_DATES identity dates; deeper backlog dates are
# deferred and converge only via the explicit --backfill flag (or a
# wholesale --force). StepRun.backfill = True (from __main__'s --backfill)
# lifts the bound.
INCREMENTAL_RECENT_DATES: int = 10




async def _recent_date_floor(
    conn, sec_types: tuple[str, ...],
) -> Optional[dt.date]:
    """The (INCREMENTAL_RECENT_DATES + 1)-th newest trading date across the
    sec_types' identity tables — dates at or after it stay in the default
    increment's scope. None when the identity history is shorter than the
    window (nothing to bound — the whole table is recent)."""
    if not sec_types:
        return None
    unions = " UNION ".join(
        f"SELECT date FROM {SEC_TYPE_IDENTITY_TABLE[st]}" for st in sec_types
    )
    # The offset is an internal constant — interpolated as a literal
    # (asyncpg's extended-protocol prepare does not see the $1 in this
    # position and plans the statement with zero parameters).
    row = await conn.fetchrow(
        f"SELECT DISTINCT date FROM ({unions}) u "
        f"ORDER BY date DESC OFFSET {int(INCREMENTAL_RECENT_DATES)} LIMIT 1",
    )
    return row["date"] if row else None


async def diff_write_scope_async(
    conn,
    *,
    table: str,
    frame: pd.DataFrame,
    columns: list[str],
    key_cols: tuple[str, ...],
    sec_types: tuple[str, ...],
    label: str,
    insert_only: bool = False,
) -> tuple[int, int]:
    """Set-based diff write: make ``table``'s scope rows exactly match
    ``frame`` — upsert new/changed rows, delete vanished PKs — instead of
    the nightly purge-all + COPY-all.

    The computed frame is CSV-COPY'd into a TEMP table cast to the
    target's column types (precision-safe: NUMERIC/float rounding is
    identical on both sides of the comparison), then the whole diff runs
    set-based in the database:

      - rows in the frame that are new or IS DISTINCT FROM the stored
        row → INSERT ... ON CONFLICT DO UPDATE,
      - scope rows with no frame counterpart → DELETE.

    Temp tables are not WAL-logged, so the only WAL is the changed rows
    themselves — the replica stays caught up and the replay-lag throttle
    stays quiet. History rows whose values did not move are never
    rewritten, so the nightly write volume collapses to the tail rows
    the new trading day actually touched.

    insert_only mode (the frozen-lookback semantics): existing rows are
    NEVER rewritten — only PK rows not yet in the table are inserted
    (ON CONFLICT DO NOTHING) and nothing is deleted. Historical rows
    keep their as-of-first-computation adaptive values; restatements
    converge only via --force.

    Returns (inserted/upserted, deleted) row counts.
    """
    t0 = time.time()
    sec_ints = [SEC_TYPE_CODE[st] for st in sec_types]
    tmp = "_mov_ave_diff_tmp"
    col_sql = ", ".join(f'"{c}"' for c in columns)
    tmp_cols = ", ".join(f't."{c}"' for c in columns)
    pk_join = " AND ".join(f's."{k}" = t."{k}"' for k in key_cols)
    pk_not_exists = " AND ".join(
        f't."{k}" = s."{k}"' for k in key_cols
    )
    val_cmp = ", ".join(f's."{c}"' for c in columns)
    tmp_cmp = ", ".join(f't."{c}"' for c in columns)
    set_clause = ", ".join(
        f'"{c}" = EXCLUDED."{c}"' for c in columns if c not in key_cols
    )
    await conn.execute(f"DROP TABLE IF EXISTS {tmp}")
    await conn.execute(
        f"CREATE TEMP TABLE {tmp} AS TABLE {table} WITH NO DATA"
    )
    try:
        await csv_copy_from_frame_async(conn, tmp, frame, columns=columns)
        if insert_only:
            n_ins = await conn.fetchval(f"""
                WITH ins AS (
                    INSERT INTO {table} ({col_sql})
                    SELECT {tmp_cols}
                    FROM {tmp} t
                    ON CONFLICT ({", ".join(key_cols)}) DO NOTHING
                    RETURNING 1
                )
                SELECT COUNT(*) FROM ins
            """)
            logger.info(
                f"    -> insert-only write ({label}): {int(n_ins):,} new "
                f"rows inserted, existing rows frozen "
                f"[{len(frame):,} computed rows, {time.time() - t0:.1f}s]")
            return int(n_ins), 0
        n_up = await conn.fetchval(f"""
            WITH ins AS (
                INSERT INTO {table} ({col_sql})
                SELECT {tmp_cols}
                FROM {tmp} t
                LEFT JOIN {table} s
                  ON {pk_join} AND s.sec_type = ANY($1)
                WHERE s."{key_cols[0]}" IS NULL
                   OR ({val_cmp}) IS DISTINCT FROM ({tmp_cmp})
                ON CONFLICT ({", ".join(key_cols)}) DO UPDATE SET {set_clause}
                RETURNING 1
            )
            SELECT COUNT(*) FROM ins
        """, sec_ints)
        status = await conn.execute(f"""
            DELETE FROM {table} s
            WHERE s.sec_type = ANY($1)
              AND NOT EXISTS (
                SELECT 1 FROM {tmp} t WHERE {pk_not_exists})
        """, sec_ints)
        n_del = int(status.rsplit(" ", 1)[-1]) if status else 0
        logger.info(
            f"    -> diff write ({label}): {int(n_up):,} upserted "
            f"(new+changed), {n_del:,} vanished deleted "
            f"[{len(frame):,} computed rows, "
            f"{time.time() - t0:.1f}s]")
        return int(n_up), n_del
    finally:
        await conn.execute(f"DROP TABLE IF EXISTS {tmp}")


def infer_sec_types(
    frame: pd.DataFrame, sec_type: str | None,
) -> tuple[str, ...]:
    """Resolve the sec_types a step processes: the caller-passed scope
    (the parent's per-sec_type loop) or every sec_type present in the
    frame as its int16 frame code decoded to labels (backward
    compatibility for standalone callers)."""
    if sec_type is not None:
        return (sec_type,)
    return tuple(SEC_TYPE_LABEL[c] for c in host_unique(frame["sec_type"]))


def sec_type_code(sec_type: str) -> int:
    """The stored value of the mov_ave-family sec_type columns: its
    static.label_dim ('sec_type' domain) SMALLINT code — the DB-side
    contract since the 2026-10-06 smallint conversion (SQL params and
    frames both carry the code; the label string stays the loop/log
    vocabulary)."""
    return SEC_TYPE_CODE[sec_type]


def filter_target_dates(
    frame: pd.DataFrame,
    target_dates: Optional[Set],
) -> pd.DataFrame:
    """Keep only rows whose date is in ``target_dates`` (incremental mode).

    datetime64 ndarray comparison — isin with a python-date SET never
    matches a datetime64 column (the fetch incremental-filter
    convention). ``None`` (compute-all mode) returns the frame unchanged.
    """
    if target_dates is None or len(target_dates) == 0:
        return frame
    n_before = len(frame)
    td64 = to_dt64(sorted(target_dates))
    out = frame[frame["date"].isin(td64)].reset_index(drop=True)
    logger.info(f"    -> incremental filter: {len(out):,} of {n_before:,} "
          f"rows are in target_dates")
    return out


async def purge_step_scope(
    conn,
    table: str,
    sec_types: tuple[str, ...],
    force: bool,
    log_label: str,
) -> None:
    """Force-mode scope wipe for a step's table: per-sec_type DELETE when
    the parent loop scopes the run (other sec_types' rows are NOT rebuilt
    by this run), whole-table TRUNCATE when every sec_type is processed."""
    if sec_types:
        for st in sec_types:
            logger.info(f"    Force mode: deleting {st} rows from "
                  f"{table}...")
            n_del = await chunked_purge_async(
                conn, table,
                where_sql="sec_type = $1", params=(sec_type_code(st),),
            )
            logger.info(f"    -> deleted {n_del:,} rows from {log_label}; "
                  f"will recompute all {st} rows")
    else:
        logger.info(f"    Force mode: truncating {table}...")
        await truncate_table_async(conn, table)
        logger.info("    -> truncated; will recompute all rows")


async def resolve_target_dates(
    conn,
    table: str,
    sec_types: tuple[str, ...],
    *,
    force: bool,
    code_filter: str | None,
    backfill: bool,
    detect_fn: Optional[Callable[[object, str], Awaitable[Set]]] = None,
) -> Optional[Set]:
    """Determine the step's target dates; ``None`` means compute ALL
    (single-code / force mode), a (possibly empty) set is the incremental
    union. ``detect_fn(conn, st)`` adds per-sec_type extra target dates
    (repair dates, bounce-confirmation recompute) and may purge stale
    rows first. ``backfill`` (the --backfill flag) lifts the default
    increment bound."""
    if code_filter is not None:
        # Single-code mode (--code): the caller already DELETEd this
        # code's rows from the table, so compute ALL dates for this code
        # and bypass the per-sec_type skip-filter (sec_types=() at the
        # insert below keeps every row — dates covered by OTHER codes
        # would otherwise mask this code's gaps).
        logger.info("    mode: SINGLE-CODE (full recompute for this code)")
        return None
    if force:
        logger.info("    mode: FORCE (full recompute)")
        await purge_step_scope(conn, table, sec_types, force, table)
        return None

    logger.info("    mode: incremental (missing dates only)")
    target_dates_union: set = set()
    repair_dates: set = set()
    for st in sec_types:
        td_st = await find_missing_analysis_dates(
            conn, table,
            [SEC_TYPE_IDENTITY_TABLE[st]], sec_type=sec_type_code(st),
        )
        if detect_fn is not None:
            extra = await detect_fn(conn, st)
            repair_dates |= extra
            td_st = td_st | extra
        target_dates_union |= td_st
        logger.info(f"    -> {st}: {len(td_st)} target dates")
    logger.info(f"    -> union across sec_types: "
          f"{len(target_dates_union)} dates to (re)compute")

    # Default increment bound: only the LATEST missing dates are built —
    # detect_fn repair dates stay exempt (deliberate recomputes), while
    # deep-history backlog dates are deferred to --backfill.
    if not backfill and target_dates_union:
        floor = await _recent_date_floor(conn, sec_types)
        if floor is not None:
            bounded = {d for d in target_dates_union if d >= floor}
            backlog = len(target_dates_union) - len(bounded)
            if backlog:
                logger.info(
                    f"    -> default increment bound (identity dates >= "
                    f"{floor}): {backlog:,} backlog dates deferred — run "
                    f"with --backfill to converge them")
            target_dates_union = bounded | repair_dates
    return target_dates_union




class TableStep(abc.ABC):
    """One internal table-filling step of the mov_ave_spread pipeline.

    Template-method base: :meth:`execute` owns the shared phases 0-3
    (target dates → compute → incremental filter → chunked insert →
    identity upsert — the former ``run_table_step`` function); the
    subclass declares its identity as class attributes and implements
    the step-specific hooks.

    Class attributes (subclass overrides):
      title          step banner (the "=" bar header in the log).
      tag            log phase prefix, e.g. "r" for the RSI step's
                     [r0]/[r1]/… lines.
      table          target table (full ``analysis.*`` name).
      analysis_name  analysis_identity registry name.
      detail_name    registry detail_name + log noun.
      description    analysis_identity registry description.
      needed_cols    source columns the step's compute needs (the parent
                     frame carries many more; execute column-subsets).
      write_flavor   "rows" (default) → :meth:`sanitize_rows` dicts →
                     build_and_insert_chunked; "frame" → :meth:`build_frame`
                     wide→long frame → CSV COPY via
                     build_and_insert_chunked_df.
      in_skip_gate   whether the parent's incremental phase-0 consults
                     :meth:`has_pending_work` for this step when deciding
                     to skip an up-to-date sec_type (True only for the
                     steps the nightly skip decision has always polled).

    Hooks (default no-op unless noted):
      compute              abstract — add the step columns to the frame.
      sanitize_rows        "rows" flavor row builder (dicts for upsert).
      build_frame          "frame" flavor wide→long frame builder.
      log_note(df)         sync note on the selected step frame before
                           detection (e.g. RSI's null-price note).
      detect_extra_dates   async (conn, sec_type) -> set of EXTRA target
                           dates (repair dates, bounce confirmation); may
                           purge stale rows first.
      post_compute(df)     async hook on the FULL computed frame, fired
                           right after compute and BEFORE the incremental
                           filter — for derived tables that need the
                           whole history (RSI's wholesale ranks rebuild).
                           Receives (run, df, sec_type) so derived
                           rebuilds can scope themselves. Not fired on
                           the early returns (no source data / nothing
                           to do), so derived rebuilds track actual
                           computes.
      post_write(run, st)  async hook AFTER the step's own rows are
                           written (RSI's trend-episode-days rebuild —
                           reads the ranks table post-write).
    """

    title: str = ""
    tag: str = ""
    table: str = ""
    analysis_name: str = ""
    detail_name: str = ""
    description: str = ""
    needed_cols: tuple[str, ...] = ()
    write_flavor: str = "rows"
    in_skip_gate: bool = False

    # ------------------------------------------------------------------
    # Step-specific hooks
    # ------------------------------------------------------------------
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add the step's columns to the (full-history) frame."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement compute()")

    def sanitize_rows(self, df: pd.DataFrame) -> list[dict]:
        """Per-row dict builder for the "rows" write flavor."""
        raise NotImplementedError(
            f"{type(self).__name__} sets write_flavor='rows' and must "
            f"implement sanitize_rows()")

    def build_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        """Wide→long frame builder for the "frame" write flavor."""
        raise NotImplementedError(
            f"{type(self).__name__} sets write_flavor='frame' and must "
            f"implement build_frame()")

    def log_note(self, df: pd.DataFrame) -> None:
        """Sync pre-detection note on the selected step frame."""

    async def detect_extra_dates(
        self, conn, sec_type: str,
    ) -> set:
        """Extra target dates for ``sec_type`` (repair recomputes)."""
        return set()

    async def post_compute(
        self, run: StepRun, df: pd.DataFrame, sec_type: str | None,
    ) -> None:
        """Async hook on the full computed frame (pre-filter)."""

    async def post_write(self, run: StepRun, sec_type: str | None) -> None:
        """Async hook after the step's rows are written."""

    # ------------------------------------------------------------------
    # Parent phase-0 consult: does this step have work for a sec_type?
    # ------------------------------------------------------------------
    async def has_pending_work(
        self, conn, sec_type: str, src_identity_table: str,
    ) -> bool:
        """Default detection: dates present in the source identity table
        but missing from the step's target table. Steps with custom
        granularity (repair dates, pair-keyed bands, wholesale empty
        checks) override this."""
        td = await find_missing_analysis_dates(
            conn, self.table, [src_identity_table],
            sec_type=sec_type_code(sec_type),
        )
        return bool(td)

    # ------------------------------------------------------------------
    # Template method — phases 0-3 (the former run_table_step)
    # ------------------------------------------------------------------
    async def execute(
        self,
        run: StepRun,
        df: pd.DataFrame,
        *,
        sec_type: str | None = None,
    ) -> None:
        """Run the step end-to-end on the parent's source DataFrame.

        Args:
          run: the parent-built execution context (conn/pool/force/
              code_filter/backfill/max_concurrent).
          df: source DataFrame with at least ``needed_cols``. Must be the
              FULL per-code history (not filtered to target dates) so
              rolling computations are correct for the first target date
              of each code.
          sec_type: the parent loop's scope; None decodes every sec_type
              present in the frame (standalone callers).
        """
        t0 = time.time()
        logger.info("\n" + "=" * 78)
        logger.info(f"  {self.title}")
        logger.info("=" * 78)

        # Select only the columns the step needs — the parent DataFrame
        # carries many extra columns that are irrelevant here.
        # column_subset is host-pure (proxied Index membership falls back
        # under cudf.pandas).
        available = column_subset(df, list(self.needed_cols))
        step_df = df[available].copy()

        if step_df.empty:
            logger.info(
                f"    -> no source data; skipping {self.detail_name} step.")
            return

        self.log_note(step_df)

        sec_types = infer_sec_types(step_df, sec_type)

        # ---- Phase 0: determine target dates --------------------------------
        target_dates_union = await resolve_target_dates(
            run.conn, self.table, sec_types,
            force=run.force, code_filter=run.code_filter,
            backfill=run.backfill,
            detect_fn=self.detect_extra_dates,
        )
        if target_dates_union is not None and not target_dates_union:
            logger.info("    -> DB is up to date; nothing to do.")
            return

        # ---- Phase 1: compute the step columns over the full history --------
        logger.info(f"\n[{self.tag}1] Computing {self.detail_name} columns per "
              f"(sec_type, code, date) over full history...")
        step_df = self.compute(step_df)

        # ---- Post-compute hook (full-history derived tables) -----------------
        await self.post_compute(run, step_df, sec_type)

        # ---- Incremental filter ---------------------------------------------
        if target_dates_union is not None:
            step_df = filter_target_dates(step_df, target_dates_union)

        if step_df.empty:
            logger.info(f"    -> no rows to upsert; skipping "
                  f"{self.detail_name} upsert.")
            return

        # ---- Phase 2: build + insert (chunked by date) ----------------------
        # sec_type rides as its stored SMALLINT code end-to-end (the
        # label-code contract) — row builders / CSV writers / DB all take
        # the code; labels stay the loop/log vocabulary.
        logger.info(f"\n[{self.tag}2] Building + inserting {len(step_df):,} "
              f"{self.detail_name} rows in date-bounded chunks "
              f"({'COPY' if run.force else 'upsert'} per chunk)...")
        st_codes = tuple(sec_type_code(s) for s in sec_types)
        if self.write_flavor == "frame":
            n = await build_and_insert_chunked_df(
                run.conn, run.pool, step_df,
                self.build_frame,
                table_name=self.table,
                force=run.force,
                sec_types=() if run.code_filter is not None else st_codes,
                max_concurrent=run.max_concurrent,
                label=self.detail_name,
            )
        else:
            n = await build_and_insert_chunked(
                run.conn, run.pool, step_df,
                self.sanitize_rows,
                table_name=self.table,
                key_columns=["sec_type", "code", "date"],
                force=run.force,
                sec_types=() if run.code_filter is not None else st_codes,
                max_concurrent=run.max_concurrent,
                label=self.detail_name,
            )
        del step_df
        logger.info(f"    -> inserted {n:,} rows")

        # ---- Phase 3: register in analysis_identity -------------------------
        logger.info(f"\n[{self.tag}3] Upserting analysis.analysis_identity registry...")
        await upsert_analysis_identity(
            run.conn,
            name=self.analysis_name,
            detail_name=self.detail_name,
            description=self.description,
        )

        await self.post_write(run, sec_type)

        logger.info(
            f"\n  {self.detail_name} wall time: {time.time() - t0:.1f}s")
