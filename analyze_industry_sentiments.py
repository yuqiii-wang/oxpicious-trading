"""
analyze_industry_sentiments.py — Industry Sentiments (rebased-to-100 levels).

Populates analysis.industry_sentiments with cross-sectional aggregation of
rebased-to-100 index values across member indices within each industry,
bucketed by pool_size.

PK: (date, industry_id, pool_size)
  pool_size ∈ ('small','mid','large','all')
    small = stock_num < 51   (tight thematic indices, e.g. 中证银行 50)
    mid   = stock_num < 301  (mid-cap baskets, e.g. 中证200 200)
    large = otherwise         (broad baskets, e.g. CSI 300/500/800/1000)
    all   = every member index regardless of pool size

REBASE CONVENTION (fixed at history start, scale-invariant)
  Each member index's close series is rebased to 100 at its FIRST available
  close (per-index first date — indices listed later start at 100 on their
  own first date). This makes member indices comparable regardless of
  absolute price level — e.g. CSI 500 (~5500pts) and SSE 50 (~2600pts) both
  start at 100, so a +10% move on either looks equally large. Mean and var
  are computed across these rebased-to-100 values.

  The frontend multi-line plot uses a CLIENT-SIDE slider that re-rebases the
  LINES to the slider's window-start — so the mean/var overlay and the lines
  are aligned only when the slider is at full range. When the slider narrows,
  the lines re-rebase but the mean/var overlay stays anchored at history
  start. This tradeoff was chosen by the user.

SOURCE
  stats.index_basic_stats.close    (raw daily index closes)
  JOIN stats.sec_classification    (type='index') for industry membership
  stats.sec_composition            (stock_num → pool_size classification)

COMPOSITION-ONLY FILTER
  Only indices that have at least one snapshot in stats.sec_composition
  (source_type='index') are included. Indices without any composition data
  are DROPPED entirely — they contribute nothing to any pool_size slice.
  This keeps the aggregation honest: pool_size classification (small/mid/
  large) is only meaningful for indices whose member count is known, and
  the 'all' slice should reflect compositioned indices only.

BROAD-MARKET INDICES
  Broad-market benchmarks (BROAD_CSI, BROAD_SSE, BROAD_SZSE, BROAD_STAR)
  are classified in stats.sec_classification as 'industries' under the FIN
  sector — they are aggregated IDENTICALLY to industry indices (no special
  handling). The 'all' pool_size slice for BROAD_* industries gives the
  broad-market aggregate sentiment.

PIPELINE
  1. Load all (date, code, close) from index_basic_stats JOIN sec_classification
     WHERE the index has composition data (EXISTS sec_composition).
  2. Per (date, code): look up stock_num from the latest sec_composition
     snapshot <= date (NULL before the index's first snapshot → 'all' only).
  3. Per code: rebase close to 100 at first available close (history start).
  4. Per (date, code): classify pool_size from stock_num (NULL → 'all' only).
  5. Per (date, industry_id, pool_size): aggregate rebased values across
     member indices in that slice → mean, var, index_count.
  6. Truncate + upsert analysis.industry_sentiments.

Table is TRUNCATE-then-INSERT on every run (full recompute). Also upserts
analysis.analysis_identity (name='industry_sentiments', last_run_datetime=NOW()).

Usage:
  python analyze_industry_sentiments.py
"""
import os
import sys
import time
import asyncio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    bulk_upsert_async,
    truncate_table_async,
    print_build_header,
    print_wall_time,
)

setup_utf8_stdout()

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

TABLE = "analysis.industry_sentiments"
ANALYSIS_NAME = "industry_sentiments"
ANALYSIS_DESCRIPTION = (
    "Industry sentiment cross-section (rebased-to-100 levels): one row per "
    "(date, industry_id, pool_size). Aggregates rebased-to-100 index values "
    "across member indices (stats.sec_classification type='index' AND "
    "industry_id matches AND index has composition data in "
    "stats.sec_composition source_type='index') in the named pool_size "
    "slice. Indices WITHOUT composition data are excluded entirely. "
    "Rebased-to-100 at each index's first available close (history start). "
    "pool_size: small (stock_num < 51), mid (51-180), large (> 180), all "
    "(every compositioned member). Stats: mean_rebased + var_rebased. "
    "Broad-market industries BROAD_CSI/BROAD_SSE/BROAD_SZSE/BROAD_STAR "
    "aggregated identically. Built by analyze_industry_sentiments.py "
    "(truncate-then-recompute)."
)

POOL_SIZES = ["small", "mid", "large", "all"]


def classify_pool(stock_num):
    """Classify stock_num into a pool_size bucket. NULL → None (index only
    contributes to the 'all' slice, not to small/mid/large).

    Thresholds:
      small  = stock_num < 51    (tight thematic indices, e.g. 中证银行 50)
      mid    = 51–180            (mid-cap baskets, e.g. CSI 100/200)
      large  = > 180             (broad baskets, e.g. CSI 300/500/800/1000)
    """
    if stock_num is None or pd.isna(stock_num):
        return None
    n = int(stock_num)
    if n < 51:
        return "small"
    if n <= 180:
        return "mid"
    return "large"


async def main():
    t0 = time.time()
    print_build_header(
        "ANALYZE INDUSTRY SENTIMENTS "
        "(rebased-to-100 mean/var per date × industry × pool_size)",
        index_table=TABLE,
    )

    conn = await get_db_connection_async()
    try:
        # ---- Step 1: load all (date, code, industry_id, close) ------------
        # JOIN sec_classification for industry membership. Only indices with
        # a non-empty industry_id are loaded (industry_id is the grouping
        # dimension). stock_num is looked up DIRECTLY from sec_composition
        # (the authoritative source for index composition) via a LATERAL
        # latest-snapshot lookup per (date, code) — NOT from index_exts,
        # which only covers ETF-tracked indices (147 of 222 compositioned
        # indices). This ensures ALL indices with composition data get
        # pool_size-classified, regardless of ETF coverage.
        #
        # COMPOSITION-ONLY FILTER: the EXISTS subquery restricts to indices
        # that have at least one sec_composition snapshot. Indices without
        # ANY composition data are dropped entirely (they would always have
        # NULL stock_num and only pollute the 'all' slice with unclassifiable
        # members).
        print("\n[1/5] Loading (date, code, industry_id, close, stock_num) "
              "from index_basic_stats JOIN sec_classification (compositioned "
              "indices only), stock_num via LATERAL sec_composition "
              "latest-snapshot...", flush=True)
        rows = await conn.fetch("""
            WITH stock_counts AS (
                SELECT code, snapshot_date,
                       COUNT(DISTINCT stock_code) AS stock_num
                FROM stats.sec_composition
                WHERE source_type = 'index'
                GROUP BY code, snapshot_date
            )
            SELECT
                ib.date,
                ib.code,
                sc.industry_id,
                COALESCE(sc.industry_label, sc.industry_id) AS industry_label,
                ib.close,
                latest.stock_num
            FROM stats.index_basic_stats ib
            JOIN stats.sec_classification sc
                ON sc.code = ib.code AND sc.type = 'index'
            LEFT JOIN LATERAL (
                SELECT stock_num
                FROM stock_counts sc2
                WHERE sc2.code = ib.code
                  AND sc2.snapshot_date <= ib.date
                ORDER BY snapshot_date DESC
                LIMIT 1
            ) latest ON true
            WHERE sc.industry_id IS NOT NULL
              AND sc.industry_id <> ''
              AND ib.close IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM stats.sec_composition sc3
                  WHERE sc3.code = ib.code
                    AND sc3.source_type = 'index'
              )
            ORDER BY ib.code, ib.date
        """)
        print(f"    → {len(rows):,} rows across "
              f"{len(set((r['industry_id'], r['code']) for r in rows))} "
              f"(industry, code) pairs", flush=True)

        if not rows:
            print("    → no data; aborting.", flush=True)
            return

        df = pd.DataFrame(
            {
                "date": [r["date"] for r in rows],
                "code": [r["code"] for r in rows],
                "industry_id": [r["industry_id"] for r in rows],
                "industry_label": [r["industry_label"] for r in rows],
                "close": [float(r["close"]) for r in rows],
                "stock_num": [
                    None if r["stock_num"] is None else int(r["stock_num"])
                    for r in rows
                ],
            }
        )
        # Drop rows with NaN/zero close (can't rebase from zero).
        df = df[df["close"].notna() & (df["close"] > 0)].copy()

        # ---- Step 2: rebase each code to 100 at its first available close --
        # Per code: first close becomes 100, all subsequent = close / first × 100.
        # Indices listed later start at 100 on their own first date (per-index
        # history-start anchor). This is the scale-invariant rebase convention
        # documented in 05_industry_sentiments.sql.
        print("\n[2/5] Rebased-to-100 at per-index first available close "
              "(history start)...", flush=True)
        df = df.sort_values(["code", "date"]).reset_index(drop=True)
        first_close = (
            df.groupby("code", as_index=False)
              .agg(first_close=("close", "first"))
        )
        df = df.merge(first_close, on="code", how="left")
        df["rebased"] = (df["close"] / df["first_close"]) * 100.0
        # Sanity: rebased at first row of each code must be ~100.
        print(f"    → rebased {len(df):,} rows across {df['code'].nunique()} "
              f"indices", flush=True)

        # ---- Step 3: classify pool_size from stock_num --------------------
        print("\n[3/5] Classifying pool_size from stock_num "
              "(small<51, mid 51-180, large >180; NULL→'all' only)...",
              flush=True)
        df["pool_size"] = df["stock_num"].apply(classify_pool)

        # ---- Step 4: aggregate per (date, industry_id, pool_size) --------
        # For each (date, industry_id): emit 4 rows — one per pool_size slice.
        # 'all' = every member index on that date. 'small'/'mid'/'large' =
        # only indices whose stock_num classifies into that bucket (NULL
        # stock_num → contributes to 'all' only). mean and var are computed
        # across the rebased-to-100 values in each slice.
        print("\n[4/5] Aggregating mean/var per (date, industry_id, "
              "pool_size)...", flush=True)
        agg_rows = []
        # Cache industry_label by (industry_id) — label is the same across
        # all rows for a given industry_id, so take first non-empty.
        label_by_industry = (
            df[df["industry_label"].notna() & (df["industry_label"] != "")]
              .groupby("industry_id")["industry_label"].first().to_dict()
        )

        # 'all' slice: every member index.
        all_agg = (
            df.groupby(["date", "industry_id"], as_index=False)
              .agg(
                  mean_rebased=("rebased", "mean"),
                  var_rebased=("rebased", "var"),
                  index_count=("code", "nunique"),
              )
        )
        all_agg["pool_size"] = "all"
        agg_rows.append(all_agg)

        # Per-bucket slices: filter by pool_size, then aggregate.
        for bucket in ["small", "mid", "large"]:
            sub = df[df["pool_size"] == bucket]
            if sub.empty:
                continue
            bucket_agg = (
                sub.groupby(["date", "industry_id"], as_index=False)
                   .agg(
                       mean_rebased=("rebased", "mean"),
                       var_rebased=("rebased", "var"),
                       index_count=("code", "nunique"),
                   )
            )
            bucket_agg["pool_size"] = bucket
            agg_rows.append(bucket_agg)

        result = pd.concat(agg_rows, ignore_index=True)
        # Fill industry_label from cache.
        result["industry_label"] = result["industry_id"].map(
            lambda iid: label_by_industry.get(iid, iid)
        )
        # var() returns NaN when only 1 member (can't compute variance) —
        # leave as NULL in DB.
        result["var_rebased"] = result["var_rebased"].where(
            result["var_rebased"].notna(), other=None
        )
        print(f"    → {len(result):,} aggregated rows across "
              f"{result['industry_id'].nunique()} industries × "
              f"{result['pool_size'].nunique()} pool_size slices", flush=True)

        # ---- Step 5: truncate + upsert ------------------------------------
        print(f"\n[5/5] Truncating {TABLE} and upserting {len(result):,} "
              f"rows...", flush=True)
        await truncate_table_async(conn, TABLE)

        data = [
            {
                "date": r["date"],
                "industry_id": r["industry_id"],
                "pool_size": r["pool_size"],
                "industry_label": r["industry_label"],
                "index_count": int(r["index_count"]) if pd.notna(r["index_count"]) else None,
                "mean_rebased": float(r["mean_rebased"]) if pd.notna(r["mean_rebased"]) else None,
                "var_rebased": float(r["var_rebased"]) if pd.notna(r["var_rebased"]) else None,
            }
            for _, r in result.iterrows()
        ]
        n = await bulk_upsert_async(
            conn, TABLE, data,
            key_columns=["date", "industry_id", "pool_size"],
            batch_size=1000,
        )
        print(f"    → upserted {n:,} rows", flush=True)

        # ---- Register in analysis.analysis_identity -----------------------
        await conn.execute("""
            INSERT INTO analysis.analysis_identity
                (name, detail_name, summary_name, last_run_datetime, description)
            VALUES ($1, $2, NULL, NOW(), $3)
            ON CONFLICT (name) DO UPDATE SET
                detail_name       = EXCLUDED.detail_name,
                summary_name      = EXCLUDED.summary_name,
                last_run_datetime = NOW(),
                description       = EXCLUDED.description
        """, ANALYSIS_NAME, ANALYSIS_NAME, ANALYSIS_DESCRIPTION)
        print(f"    → upserted analysis_identity (name='{ANALYSIS_NAME}')",
              flush=True)

        print_wall_time(t0)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
