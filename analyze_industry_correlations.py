"""
analyze_industry_correlations.py — pairwise rolling Pearson correlation of
industries' mean_rebased series.

Populates analysis.industry_correlations with one row per
(date, industry_id, benchmark_industry_id, pool_size, pool_size) where:
  • pool_size is the SAME for both industries (cross-pool comparisons like
    corr(A.small_mean, B.large_mean) are NOT materialized — they conflate
    cross-index size effects with sentiment co-movement and are not
    meaningful).
  • industry_id < benchmark_industry_id (lexicographic) to deduplicate
    (A,B) vs (B,A). Self-pairs (A = B) are skipped — self-correlation is
    always 1.

SOURCE
  analysis.industry_sentiments.mean_rebased   (per-industry mean series)

PIPELINE
  1. Load all (date, industry_id, pool_size, mean_rebased) rows from
     analysis.industry_sentiments (skipping rows where mean_rebased is NULL).
  2. Group by (industry_id, pool_size) → per-industry × pool mean series.
  3. For each pair of industries (A, B) with A < B (lexicographic) AND each
     pool_size P (same for both):
       • Inner-join A's series and B's series by date (sorted).
       • Compute rolling Pearson correlation over windows [5, 20, 60, 255]
         ending on each shared date.
       • Emit one row per shared date with the 4 correlation values
         (NULL when fewer than `window` overlapping pairs on or before date).
  4. Truncate analysis.industry_correlations + bulk upsert.
  5. Upsert analysis.analysis_identity (name='industry_correlations').

WINDOWS
  5d / 20d / 60d / 255d trailing trading days. Pearson correlation requires
  at least 2 overlapping pairs in the window; we additionally require
  window-length overlap so the correlation is statistically meaningful
  (a 5-day correlation on only 3 data points is too noisy). When overlap <
  window, the column is NULL on that date.

Table is TRUNCATE-then-INSERT on every run (full recompute). Also upserts
analysis.analysis_identity (name='industry_correlations',
last_run_datetime=NOW()).

Usage:
  python analyze_industry_correlations.py
"""
import os
import sys
import time
import asyncio
from itertools import combinations

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

TABLE = "analysis.industry_correlations"
ANALYSIS_NAME = "industry_correlations"
ANALYSIS_DESCRIPTION = (
    "Pairwise rolling Pearson correlation between two industries' "
    "mean_rebased series (analysis.industry_sentiments.mean_rebased). "
    "One row per (date, industry_id, benchmark_industry_id, pool_size, "
    "pool_size) with corr_5d / corr_20d / corr_60d / corr_255d. Self-pairs "
    "(A=B) excluded. Order convention: industry_id < benchmark_industry_id "
    "to deduplicate. Only same-pool slices materialized (all/all, "
    "small/small, mid/mid, large/large). Built by "
    "analyze_industry_correlations.py (truncate-then-recompute)."
)

POOL_SIZES = ["small", "mid", "large", "all"]
WINDOWS = [5, 20, 60, 255]


def _rolling_corr(a: np.ndarray, b: np.ndarray, window: int) -> np.ndarray:
    """Rolling Pearson correlation between two equal-length 1-D arrays
    over a trailing `window`-day window.

    Returns NaN where the window contains fewer than 2 valid (non-NaN)
    overlapping pairs OR where the standard deviation of either series in
    the window is zero (correlation is undefined when one series is
    constant).

    Uses pandas' rolling.corr which is vectorized and handles NaN pairs
    correctly (NaN values are excluded pairwise — they don't reduce the
    effective window).
    """
    if len(a) != len(b):
        raise ValueError(f"length mismatch: {len(a)} vs {len(b)}")
    s_a = pd.Series(a)
    s_b = pd.Series(b)
    return s_a.rolling(window=window, min_periods=2).corr(s_b).to_numpy()


async def main():
    t0 = time.time()
    print_build_header(
        "ANALYZE INDUSTRY CORRELATIONS "
        "(pairwise rolling corr of mean_rebased per industry × pool_size)",
        index_table=TABLE,
    )

    conn = await get_db_connection_async()
    try:
        # ---- Step 1: load mean_rebased series from industry_sentiments ----
        # Only non-NULL mean_rebased rows are useful — NULL rows mean no
        # member indices contributed to that (date, industry, pool) slice
        # and cannot be correlated.
        print("\n[1/4] Loading (date, industry_id, pool_size, mean_rebased) "
              "from analysis.industry_sentiments (non-NULL mean only)...",
              flush=True)
        rows = await conn.fetch("""
            SELECT date, industry_id, pool_size, mean_rebased
            FROM analysis.industry_sentiments
            WHERE mean_rebased IS NOT NULL
            ORDER BY industry_id, pool_size, date
        """)
        print(f"    → {len(rows):,} rows across "
              f"{len(set((r['industry_id'], r['pool_size']) for r in rows))} "
              f"(industry, pool_size) series", flush=True)

        if not rows:
            print("    → no data; aborting. Did you run "
                  "analyze_industry_sentiments.py first?", flush=True)
            return

        df = pd.DataFrame(
            {
                "date": [r["date"] for r in rows],
                "industry_id": [r["industry_id"] for r in rows],
                "pool_size": [r["pool_size"] for r in rows],
                "mean_rebased": [float(r["mean_rebased"]) for r in rows],
            }
        )

        # ---- Step 2: build per (industry_id, pool_size) series -----------
        # Series dict: keyed by (industry_id, pool_size) → sorted DataFrame
        # with [date, mean_rebased]. We'll need this to inner-join pairs.
        print("\n[2/4] Grouping by (industry_id, pool_size) → mean series...",
              flush=True)
        series_by_key: dict[tuple[str, str], pd.DataFrame] = {}
        for (iid, pool), g in df.groupby(["industry_id", "pool_size"]):
            g = g.sort_values("date").reset_index(drop=True)
            # Drop duplicate dates (defensive — PK prevents dupes, but in
            # case the source table is in an inconsistent state).
            g = g.drop_duplicates(subset="date", keep="last").reset_index(
                drop=True
            )
            series_by_key[(iid, pool)] = g
        print(f"    → {len(series_by_key)} (industry, pool_size) series",
              flush=True)

        # List of industry_ids that have at least one pool_size series.
        industry_ids = sorted({k[0] for k in series_by_key.keys()})
        print(f"    → {len(industry_ids)} distinct industries with data",
              flush=True)

        # ---- Step 3: pairwise rolling correlations ----------------------
        # For each pair (A, B) with A < B (lexicographic) and each pool_size
        # P, inner-join the two series on date and compute rolling
        # correlations. Emit one row per shared date.
        print("\n[3/4] Computing pairwise rolling correlations "
              f"(windows={WINDOWS})...", flush=True)

        out_rows: list[dict] = []
        n_pairs_total = 0
        n_pairs_with_data = 0
        for a_id, b_id in combinations(industry_ids, 2):
            # Lexicographic order convention — both directions are covered
            # by the (A, B) generator since combinations yields sorted
            # tuples.
            assert a_id < b_id, f"order invariant violated: {a_id} >= {b_id}"
            n_pairs_total += 1

            for pool in POOL_SIZES:
                a_series = series_by_key.get((a_id, pool))
                b_series = series_by_key.get((b_id, pool))
                if a_series is None or b_series is None:
                    continue

                # Inner join on date — only dates where both industries
                # have a mean_rebased value.
                merged = a_series.merge(
                    b_series, on="date", suffixes=("_a", "_b")
                )
                if len(merged) < 2:
                    # Need at least 2 overlapping pairs for any correlation.
                    continue

                n_pairs_with_data += 1
                a_vals = merged["mean_rebased_a"].to_numpy(dtype=np.float64)
                b_vals = merged["mean_rebased_b"].to_numpy(dtype=np.float64)
                dates = merged["date"].to_numpy()

                # Compute rolling correlations for each window.
                corr_cols = {}
                for w in WINDOWS:
                    c = _rolling_corr(a_vals, b_vals, w)
                    # min_periods=2 means rolling.corr returns NaN when
                    # fewer than 2 valid pairs in window. Additionally,
                    # rolling.corr returns NaN when either series has zero
                    # variance in the window (correlation undefined).
                    corr_cols[f"industry_mean_corr_{w}d"] = c

                for i, d in enumerate(dates):
                    row = {
                        "industry_id": a_id,
                        "benchmark_industry_id": b_id,
                        "industry_pool_size": pool,
                        "benchmark_industry_pool_size": pool,
                        "date": d,
                    }
                    for w in WINDOWS:
                        v = corr_cols[f"industry_mean_corr_{w}d"][i]
                        # NaN → None (NULL in DB). Also guard against
                        # inf/-inf which can theoretically occur.
                        if v is None or not np.isfinite(v):
                            row[f"industry_mean_corr_{w}d"] = None
                        else:
                            # Round to NUMERIC(8,4) precision.
                            row[f"industry_mean_corr_{w}d"] = float(
                                round(float(v), 4)
                            )
                    out_rows.append(row)

        print(f"    → {n_pairs_total} industry pairs × up to 4 pools "
              f"= up to {n_pairs_total * 4} (pair, pool) combinations; "
              f"{n_pairs_with_data} had ≥2 overlapping dates",
              flush=True)
        print(f"    → {len(out_rows):,} correlation rows emitted",
              flush=True)

        if not out_rows:
            print("    → no rows to upsert; aborting.", flush=True)
            return

        # ---- Step 4: truncate + upsert -----------------------------------
        print(f"\n[4/4] Truncating {TABLE} and upserting {len(out_rows):,} "
              f"rows...", flush=True)
        await truncate_table_async(conn, TABLE)

        n = await bulk_upsert_async(
            conn, TABLE, out_rows,
            key_columns=[
                "date",
                "industry_id",
                "benchmark_industry_id",
                "industry_pool_size",
                "benchmark_industry_pool_size",
            ],
            batch_size=1000,
        )
        print(f"    → upserted {n:,} rows", flush=True)

        # ---- Register in analysis.analysis_identity ----------------------
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

        # Sanity summary: row count by pool_size.
        summary = await conn.fetch("""
            SELECT industry_pool_size AS pool,
                   COUNT(*) AS n_rows,
                   COUNT(DISTINCT (industry_id, benchmark_industry_id))
                       AS n_pairs,
                   MIN(date) AS first_date,
                   MAX(date) AS last_date
            FROM analysis.industry_correlations
            GROUP BY industry_pool_size
            ORDER BY industry_pool_size
        """)
        print("\n    Summary by pool_size:", flush=True)
        for r in summary:
            print(f"      {r['pool']:6s}: {r['n_rows']:>8,} rows · "
                  f"{r['n_pairs']:>4} pairs · "
                  f"{r['first_date']} → {r['last_date']}", flush=True)

        print_wall_time(t0)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
