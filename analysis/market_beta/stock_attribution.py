"""Internal stock-attribution step for analysis.market_beta.

Audits, per (industry_id, date), the DISTRIBUTION of member stocks across
daily pct-change buckets — "how many stocks in industry X rose >= 7.5%
today" — producing analysis.industry_stock_change_dist with one row per
(industry_id, date, bucket), all 12 buckets of the
'stock_change_bucket' static.label_dim domain stored per pair
(zero-count buckets included) and total_stocks denormalized per pair.

SOURCE
  stats.stock_basic_stats.pct_change  (daily 涨跌幅 %, per (code, date))
  stats.sec_classification type='stock' collapsed to ONE industry per
  code via DISTINCT ON (code) ORDER BY code, parent_index_is_primary
  DESC, parent_index_weight DESC NULLS LAST (a stock may carry one row
  per qualifying industry-index parent; the primary/max-weight row
  wins). Stocks without any qualifying parent keep their fallback row
  (name-classified industry or OTHER/未分类) — stored too, so the
  audit's coverage is complete; the UI only queries nav-selectable
  industries, so OTHER is never rendered.

COMPUTE (cudf whole-frame; NO host boundary needed)
  bucket = number of bucket EDGES <= pct_change (a sum of 11 boolean
  comparisons cast to int16 — bucket k = [edge[k-1], edge[k]), bucket 0
  open below, bucket 11 closed above; see _common.labels
  STOCK_CHANGE_BUCKET_EDGES). Counting is ONE grouped pass
  (industry_id, date, bucket).size(); the uniform 12-bucket grid per
  (industry, date) pair is rebuilt by merging the deduped pairs frame
  with a 12-row bucket frame on a constant int8 key (cuDF-native hash
  join); totals per pair come from a second grouped sum merged back.
  industry_label strings NEVER ride the big source frame (the cuDF
  26.08 string fault class) — they merge onto the SMALL aggregated
  frame at the write boundary, from the tiny industry_labels sql view.

INCREMENTAL
  Self-managing (the stock daily build adds dates independently of the
  cross_stats calendar the sibling steps follow): target dates =
  DISTINCT source dates missing from the target table
  (stock_attribution/missing_dates sql view). The caller may override
  with explicit target_dates. --force: TRUNCATE + full recompute via
  the COPY fast path (conflict-free).

This module is an INTERNAL step of analysis.market_beta — it is invoked
from __main__.py after the hypes_and_drains step, reusing the same DB
connection. It is NOT a standalone runnable.
"""
from __future__ import annotations

import datetime
import time
from typing import Optional, Set

import numpy as np
import pandas as pd

from _common.build_commons import (
    truncate_table_async,
    copy_or_upsert_split_async,
    rec_cols,
)
from _common.db_commons import analyze_tables_async
from _common.sql_views import frag, render_sql_view
from _common.df_utils import epoch_col_to_dt64
from _common.labels import STOCK_CHANGE_BUCKET_EDGES
from analysis._common import (
    sanitize_for_db_insert,
    upsert_analysis_identity,
)

import logging
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

TABLE = "analysis.industry_stock_change_dist"
ANALYSIS_NAME = "industry_stock_change_dist"
ANALYSIS_DESCRIPTION = (
    "Per-(industry_id, date, bucket) number of member stocks in each "
    "daily pct-change bucket — the Stock Attribution audit on the "
    "Market Beta page ('how many stocks in industry X rose >= 7.5% "
    "today'). bucket = static.label_dim 'stock_change_bucket' code (12 "
    "codes, drop tail -> rise tail; bucket k = [edge[k-1], edge[k]) "
    "over edges -17.5/-12.5/-7.5/-5/-2.5/0/2.5/5/7.5/12.5/17.5 "
    "percent). All 12 buckets stored per (industry, date) pair with "
    ">=1 mapped stock; total_stocks denormalized per pair. Mapping: "
    "stats.sec_classification type='stock' DISTINCT ON (code) "
    "primary-first. Source: stats.stock_basic_stats.pct_change. Built "
    "by analysis.market_beta.stock_attribution (self-managing "
    "incremental; --force truncate + rebuild)."
)
N_BUCKETS = len(STOCK_CHANGE_BUCKET_EDGES) + 1  # 11 edges -> 12 buckets


# ---------------------------------------------------------------------------
#  Step
# ---------------------------------------------------------------------------

class StockAttributionStep:
    """The stock-attribution step: bucket + count member stocks' daily
    pct_change per industry, write analysis.industry_stock_change_dist.

    Holds the run's typed params (conn + optional caller-provided target
    dates + force flag); state and behavior live here so the step is a
    capability object, not a branch in the pipeline. Instantiated per
    run via :func:`run_stock_attribution`.
    """

    def __init__(
        self,
        conn,
        *,
        target_dates: Optional[Set[datetime.date]] = None,
        force: bool = False,
    ) -> None:
        self.conn = conn
        self.target_dates = target_dates
        self.force = force

    # -- target-date resolution (self-managing incremental) ---------------

    async def _resolve_target_dates(self) -> Set[datetime.date]:
        """Dates to (re)compute: caller-provided, or source dates missing
        from the target table (the stock daily build's calendar is
        independent of the cross_stats grid the sibling steps follow)."""
        if self.target_dates is not None:
            return set(self.target_dates)
        view = render_sql_view("analysis/market_beta/stock_attribution/missing_dates")
        rows = await self.conn.fetch(view.sql)
        return {r["date"] for r in rows}

    # -- the distribution computation (whole-frame cudf) ------------------

    @staticmethod
    def _bucket_count_frame(df: pd.DataFrame) -> pd.DataFrame:
        """(date, industry_id, pct_change) source frame -> the uniform
        (industry_id, date, bucket) count frame with total_stocks.

        Whole-frame vectorized cudf throughout — no per-group loops, no
        host-numpy boundary: bucket assignment is the sum of 11 boolean
        comparisons (bool cast to int16 BEFORE the arithmetic), counting
        is one grouped size() pass, the uniform 12-bucket grid and the
        per-pair totals are tiny-frame hash merges.
        """
        # Bucket code = number of EDGES <= pct_change (left-closed
        # [edge[k-1], edge[k]); pct_change is NOT NULL from the source
        # view, so no NaN handling is needed here.
        bucket = (df["pct_change"] >= STOCK_CHANGE_BUCKET_EDGES[0]).astype("int16")
        for edge in STOCK_CHANGE_BUCKET_EDGES[1:]:
            bucket = bucket + (df["pct_change"] >= edge).astype("int16")
        df = df.assign(bucket=bucket).drop(columns=["pct_change"])

        # ONE grouped count pass: (industry_id, date, bucket) -> size.
        counts = (
            df.groupby(["industry_id", "date", "bucket"], sort=True)
            .size()
            .rename("stock_count")
            .reset_index()
        )

        # Uniform 12-bucket grid per (industry, date) pair: dedup the
        # pairs, then merge with the 12-row bucket frame on a constant
        # int8 key (cuDF-native hash join; avoids cross-join support
        # questions). Zero-count buckets fill in below.
        pairs = counts[["industry_id", "date"]].drop_duplicates()
        pairs = pairs.assign(__join_key=np.int8(0))
        bucket_rows = pd.DataFrame({
            "__join_key": np.zeros(N_BUCKETS, dtype="int8"),
            "bucket": np.arange(N_BUCKETS, dtype="int16"),
        })
        grid = pairs.merge(bucket_rows, on="__join_key").drop(columns=["__join_key"])

        counts = grid.merge(
            counts, on=["industry_id", "date", "bucket"], how="left",
        )
        counts["stock_count"] = (
            counts["stock_count"].fillna(0).astype("int64")
        )

        # Per-pair totals (denormalized onto every bucket row): one
        # grouped sum over the completed grid, merged back.
        totals = (
            counts.groupby(["industry_id", "date"], sort=False)["stock_count"]
            .sum()
            .rename("total_stocks")
            .reset_index()
        )
        return counts.merge(totals, on=["industry_id", "date"], how="left")

    async def _attach_industry_labels(self, df: pd.DataFrame) -> pd.DataFrame:
        """Merge industry_id -> industry_label onto the SMALL aggregated
        frame at the write boundary (labels never ride the big source
        frame — the cuDF 26.08 string fault class)."""
        view = render_sql_view("analysis/market_beta/stock_attribution/industry_labels")
        rows = await self.conn.fetch(view.sql)
        if rows:
            labels = pd.DataFrame(rec_cols(rows))
            df = df.merge(labels, on="industry_id", how="left")
            df["industry_label"] = df["industry_label"].fillna("")
        else:
            df = df.assign(industry_label="")
        return df

    # -- orchestration -----------------------------------------------------

    async def run(self) -> None:
        """Run the stock-attribution step on the caller's connection.

        Pipeline
          1. Resolve target dates (caller-provided or self-detected).
             No dates + not force -> register identity, exit (up to date).
          2. Force mode: TRUNCATE the table.
          3. Fetch source rows (date, industry_id, pct_change) via the
             source_daily sql view (date-filtered when incremental).
          4. pandas: bucket -> grouped count -> uniform grid -> totals
             -> industry labels (write-boundary merge).
          5. Upsert (COPY fast path after truncate; upsert fallback for
             backfilled historical dates), then ANALYZE.
          6. Register in analysis.analysis_identity.
        """
        t0 = time.time()
        logger.info("\n" + "=" * 78)
        logger.info("  INDUSTRY STOCK CHANGE DIST (internal step of market_beta)")
        logger.info("=" * 78)

        # ---- Step 1: target dates --------------------------------------
        target_dates = await self._resolve_target_dates()
        incremental = not self.force and len(target_dates) > 0
        if self.force:
            logger.info("    mode: FORCE (full recompute)")
        elif incremental:
            logger.info(f"    mode: incremental ({len(target_dates)} target dates)")
        else:
            logger.info("    -> no missing dates; nothing to do.")
            await upsert_analysis_identity(
                self.conn,
                name=ANALYSIS_NAME,
                detail_name=ANALYSIS_NAME,
                description=ANALYSIS_DESCRIPTION,
            )
            return

        # ---- Step 2: truncate (full recompute only) --------------------
        if not incremental:
            logger.info(f"\n[sa2/6] Truncating {TABLE} (full recompute)...")
            await truncate_table_async(self.conn, TABLE)

        # ---- Step 3: fetch source rows ---------------------------------
        logger.info("\n[sa3/6] Fetching stock_daily pct_change x industry map...")
        t_sql = time.time()
        if incremental:
            sorted_dates = sorted(target_dates)
            view = render_sql_view(
                "analysis/market_beta/stock_attribution/source_daily",
                date_filter=frag("AND s.date = ANY($1::date[])"),
            )
            rows = await self.conn.fetch(view.sql, *view.params, sorted_dates)
        else:
            view = render_sql_view(
                "analysis/market_beta/stock_attribution/source_daily",
                date_filter=frag(""),
            )
            rows = await self.conn.fetch(view.sql)
        logger.info(f"    -> {len(rows):,} source rows ({time.time() - t_sql:.1f}s)")
        if not rows:
            logger.info("    -> no source rows; skipping write.")
            await upsert_analysis_identity(
                self.conn,
                name=ANALYSIS_NAME,
                detail_name=ANALYSIS_NAME,
                description=ANALYSIS_DESCRIPTION,
            )
            return

        # ---- Step 4: bucket + count (whole-frame cudf) ------------------
        logger.info("\n[sa4/6] Bucketing pct_change + counting per "
              "(industry, date, bucket)...")
        t_df = time.time()
        df = pd.DataFrame(rec_cols(rows))
        df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
        df["pct_change"] = df["pct_change"].astype("float64")
        out = self._bucket_count_frame(df)
        out = await self._attach_industry_labels(out)
        out = out.sort_values(["industry_id", "date", "bucket"]).reset_index(drop=True)
        logger.info(f"    -> {len(out):,} bucket rows "
              f"({out['industry_id'].nunique()} industries) "
              f"({time.time() - t_df:.1f}s)")

        # ---- Step 5: write ---------------------------------------------
        logger.info(f"\n[sa5/6] Writing {TABLE}...")
        data = sanitize_for_db_insert(
            out,
            numeric_cols=["bucket", "stock_count", "total_stocks"],
            date_cols=["date"],
        )
        n_copied, n_upserted = await copy_or_upsert_split_async(
            self.conn, TABLE, data,
            key_columns=["industry_id", "date", "bucket"],
        )
        n = n_copied + n_upserted
        via = "COPY" if n_copied > 0 and n_upserted == 0 else \
              f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
              "upsert"
        logger.info(f"    -> wrote {n:,} rows via {via}")
        if n >= 100_000:
            await analyze_tables_async(self.conn, [TABLE])

        # ---- Step 6: register in analysis_identity ---------------------
        await upsert_analysis_identity(
            self.conn,
            name=ANALYSIS_NAME,
            detail_name=ANALYSIS_NAME,
            description=ANALYSIS_DESCRIPTION,
        )

        logger.info(f"\n  stock_attribution wall time: {time.time() - t0:.1f}s")


async def run_stock_attribution(
    conn,
    *,
    target_dates: Optional[Set[datetime.date]] = None,
    force: bool = False,
) -> None:
    """Run the stock-attribution step (see :class:`StockAttributionStep`).

    Reuses the caller's DB connection (does not open/close its own) so
    the market_beta steps form a single atomic-ish batch. When
    ``target_dates`` is None the step self-detects missing dates.
    """
    await StockAttributionStep(
        conn, target_dates=target_dates, force=force,
    ).run()
