"""Internal correlations step for analyze.industry_sentiments.

Pairwise rolling Pearson correlation of industries' mean_price series.

Populates analysis.industry_correlations with one row per
(date, industry_id, benchmark_industry_id, pool_size) where:
  - pool_size is the SAME for both industries (single column; cross-pool
    comparisons conflate cross-index size effects with sentiment
    co-movement and are not materialized).
  - industry_id < benchmark_industry_id (lexicographic) to deduplicate
    (A,B) vs (B,A). Self-pairs (A = B) are skipped (self-corr is always 1).

SOURCE
  analysis.industry_sentiments.mean_price (per-industry mean price series
  produced by the sentiments step in __main__).

WINDOWS
  5d / 20d / 60d / 255d trailing trading days. Pearson correlation
  requires at least 2 overlapping pairs in the window.

Incremental mode (``target_dates`` is a non-empty set):
  Only rows whose date is in ``target_dates`` are upserted. The full
  mean_price history per (industry, pool_size) is still loaded from
  analysis.industry_sentiments so that rolling correlations ending on a
  target date use the correct trailing window. No truncate is issued.

Force mode (``force=True``):
  Truncates analysis.industry_correlations first, then recomputes and
  inserts all rows (target_dates is ignored).

This module is an INTERNAL step of analyze.industry_sentiments — it is
invoked from __main__.py after the sentiments table has been repopulated,
reusing the same DB connection. It is NOT a standalone runnable.
"""
from __future__ import annotations

import datetime
import time
from itertools import combinations
from typing import Optional, Set

import numpy as np
import pandas as pd

from utils.build_commons import (
    bulk_upsert_async,
    truncate_table_async,
)


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

TABLE = "analysis.industry_correlations"
ANALYSIS_NAME = "industry_correlations"
ANALYSIS_DESCRIPTION = (
    "Pairwise rolling Pearson correlation between two industries' "
    "mean_price series (analysis.industry_sentiments.mean_price). "
    "One row per (date, industry_id, benchmark_industry_id, pool_size) "
    "with corr_5d / corr_20d / corr_60d / corr_255d. Both industries are "
    "compared in the SAME pool_size slice (single pool_size column). "
    "Self-pairs (A=B) excluded. Order convention: industry_id < "
    "benchmark_industry_id to deduplicate. Only same-pool slices "
    "materialized (all, small, mid, large). Built by "
    "analyze.industry_sentiments (truncate-then-recompute)."
)

# Same-pool slices materialized. Cross-pool comparisons (e.g. corr(A.small,
# B.large)) are intentionally NOT materialized — see module docstring.
POOL_SIZES = ["small", "mid", "large", "all"]

# Trailing trading-day windows for rolling Pearson correlation.
WINDOWS = [5, 20, 60, 255]


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def rolling_corr(a: np.ndarray, b: np.ndarray, window: int) -> np.ndarray:
    """Rolling Pearson correlation between two equal-length 1-D arrays
    over a trailing ``window``-day window.

    Returns NaN where the window contains fewer than 2 valid (non-NaN)
    overlapping pairs OR where the standard deviation of either series in
    the window is zero (correlation is undefined when one series is
    constant).

    Uses pandas' ``rolling.corr`` which is vectorized and handles NaN
    pairs correctly (NaN values are excluded pairwise — they don't
    reduce the effective window).
    """
    if len(a) != len(b):
        raise ValueError(f"length mismatch: {len(a)} vs {len(b)}")
    s_a = pd.Series(a)
    s_b = pd.Series(b)
    return s_a.rolling(window=window, min_periods=2).corr(s_b).to_numpy()


# ---------------------------------------------------------------------------
#  Pipeline
# ---------------------------------------------------------------------------

async def run_correlations(
    conn,
    *,
    target_dates: Optional[Set[datetime.date]] = None,
    force: bool = False,
) -> None:
    """Run the pairwise rolling-correlation pipeline against the freshly
    populated analysis.industry_sentiments table.

    Reuses the caller's DB connection (does not open/close its own) so the
    sentiments + correlations steps form a single atomic-ish batch.

    Pipeline
      1. Load all (date, industry_id, pool_size, mean_price) rows from
         analysis.industry_sentiments (skipping rows where mean_price is
         NULL). Full history is always loaded so rolling-correlation
         windows have correct trailing context.
      2. Group by (industry_id, pool_size) -> per-industry x pool mean
         series.
      3. For each pair of industries (A, B) with A < B (lexicographic)
         AND each pool_size P (same for both):
           - Inner-join A's series and B's series by date (sorted).
           - Compute rolling Pearson correlation over windows
             [5, 20, 60, 255] ending on each shared date.
           - Emit one row per shared date with the 4 correlation values
             (NULL when fewer than `window` overlapping pairs).
      3b. In incremental mode, filter emitted rows to target_dates only.
      4. Truncate (force mode) + bulk upsert.
      5. Upsert analysis.analysis_identity (name='industry_correlations').

    Args:
      target_dates: when non-empty, only rows whose date is in this set
        are upserted (incremental mode). Rolling correlations are still
        computed over the full history for correctness. Ignored when
        ``force`` is True.
      force: when True, truncate the table first and recompute all rows.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  INDUSTRY CORRELATIONS (internal step of industry_sentiments)",
          flush=True)
    print("=" * 78, flush=True)

    incremental = (not force
                   and target_dates is not None
                   and len(target_dates) > 0)
    if force:
        print("    mode: FORCE (full recompute)", flush=True)
    elif incremental:
        print(f"    mode: incremental ({len(target_dates)} target dates)",
              flush=True)

    # ---- Step 1: load mean_price series from industry_sentiments ----
    # Only non-NULL mean_price rows are useful — NULL rows mean no
    # member indices contributed to that (date, industry, pool) slice
    # and cannot be correlated.
    print("\n[c1/4] Loading (date, industry_id, pool_size, mean_price) "
          "from analysis.industry_sentiments (non-NULL mean only)...",
          flush=True)
    rows = await conn.fetch("""
        SELECT date, industry_id, pool_size, mean_price
        FROM analysis.industry_sentiments
        WHERE mean_price IS NOT NULL
        ORDER BY industry_id, pool_size, date
    """)
    print(f"      -> {len(rows):,} rows across "
          f"{len(set((r['industry_id'], r['pool_size']) for r in rows))} "
          f"(industry, pool_size) series", flush=True)

    if not rows:
        print("      -> no data; skipping correlations step.", flush=True)
        return

    df = pd.DataFrame(
        {
            "date": [r["date"] for r in rows],
            "industry_id": [r["industry_id"] for r in rows],
            "pool_size": [r["pool_size"] for r in rows],
            "mean_price": [float(r["mean_price"]) for r in rows],
        }
    )

    # ---- Step 2: build per (industry_id, pool_size) series -----------
    # Series dict: keyed by (industry_id, pool_size) -> sorted DataFrame
    # with [date, mean_price]. We'll need this to inner-join pairs.
    print("\n[c2/4] Grouping by (industry_id, pool_size) -> mean series...",
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
    print(f"      -> {len(series_by_key)} (industry, pool_size) series",
          flush=True)

    # List of industry_ids that have at least one pool_size series.
    industry_ids = sorted({k[0] for k in series_by_key.keys()})
    print(f"      -> {len(industry_ids)} distinct industries with data",
          flush=True)

    # ---- Step 3: pairwise rolling correlations ----------------------
    # For each pair (A, B) with A < B (lexicographic) and each pool_size
    # P, inner-join the two series on date and compute rolling
    # correlations. Emit one row per shared date.
    print("\n[c3/4] Computing pairwise rolling correlations "
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
            # have a mean_price value.
            merged = a_series.merge(
                b_series, on="date", suffixes=("_a", "_b")
            )
            if len(merged) < 2:
                # Need at least 2 overlapping pairs for any correlation.
                continue

            n_pairs_with_data += 1
            a_vals = merged["mean_price_a"].to_numpy(dtype=np.float64)
            b_vals = merged["mean_price_b"].to_numpy(dtype=np.float64)
            dates = merged["date"].to_numpy()

            # Compute rolling correlations for each window.
            corr_cols = {}
            for w in WINDOWS:
                c = rolling_corr(a_vals, b_vals, w)
                # min_periods=2 means rolling.corr returns NaN when
                # fewer than 2 valid pairs in window. Additionally,
                # rolling.corr returns NaN when either series has zero
                # variance in the window (correlation undefined).
                corr_cols[f"industry_mean_corr_{w}d"] = c

            for i, d in enumerate(dates):
                # In incremental mode, skip dates not in target_dates.
                # The rolling correlation is computed over the full
                # history (so the window is correct) but only target
                # dates are emitted for upsert.
                if incremental and d not in target_dates:
                    continue

                row = {
                    "industry_id": a_id,
                    "benchmark_industry_id": b_id,
                    "pool_size": pool,
                    "date": d,
                }
                for w in WINDOWS:
                    v = corr_cols[f"industry_mean_corr_{w}d"][i]
                    # NaN -> None (NULL in DB). Also guard against
                    # inf/-inf which can theoretically occur.
                    if v is None or not np.isfinite(v):
                        row[f"industry_mean_corr_{w}d"] = None
                    else:
                        # Round to NUMERIC(8,4) precision.
                        row[f"industry_mean_corr_{w}d"] = float(
                            round(float(v), 4)
                        )
                out_rows.append(row)

    print(f"      -> {n_pairs_total} industry pairs x up to 4 pools "
          f"= up to {n_pairs_total * 4} (pair, pool) combinations; "
          f"{n_pairs_with_data} had >=2 overlapping dates",
          flush=True)
    print(f"      -> {len(out_rows):,} correlation rows emitted"
          f"{' (target_dates filtered)' if incremental else ''}",
          flush=True)

    if not out_rows:
        print("      -> no rows to upsert; skipping correlations upsert.",
              flush=True)
        return

    # ---- Step 4: truncate (force only) + upsert ---------------------
    if force:
        print(f"\n[c4/4] Truncating {TABLE} and upserting "
              f"{len(out_rows):,} rows...", flush=True)
        await truncate_table_async(conn, TABLE)
    else:
        print(f"\n[c4/4] Upserting {len(out_rows):,} rows into {TABLE}...",
              flush=True)

    n = await bulk_upsert_async(
        conn, TABLE, out_rows,
        key_columns=[
            "date",
            "industry_id",
            "benchmark_industry_id",
            "pool_size",
        ],
        batch_size=1000,
    )
    print(f"      -> upserted {n:,} rows", flush=True)

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
    print(f"      -> upserted analysis_identity "
          f"(name='{ANALYSIS_NAME}')", flush=True)

    # Sanity summary: row count by pool_size.
    summary = await conn.fetch("""
        SELECT pool_size AS pool,
               COUNT(*) AS n_rows,
               COUNT(DISTINCT (industry_id, benchmark_industry_id))
                   AS n_pairs,
               MIN(date) AS first_date,
               MAX(date) AS last_date
        FROM analysis.industry_correlations
        GROUP BY pool_size
        ORDER BY pool_size
    """)
    print("\n      Summary by pool_size:", flush=True)
    for r in summary:
        print(f"        {r['pool']:6s}: {r['n_rows']:>8,} rows . "
              f"{r['n_pairs']:>4} pairs . "
              f"{r['first_date']} -> {r['last_date']}", flush=True)

    print(f"\n  correlations wall time: {time.time() - t0:.1f}s", flush=True)
