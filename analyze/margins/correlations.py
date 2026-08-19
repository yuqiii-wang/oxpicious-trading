"""Internal correlations step for analyze.margins.

Pairwise rolling Pearson correlation of RONGZI (融资) margin
flows/balances between two SECURITIES within the SAME industry.

Populates analysis.margin_industry_correlation with one row per
  (date, industry_id, security_code, benchmark_code, attribution_type)

where:
  - A "security" is an ETF code or an index code, both within the same
    industry_id. ALL pairs within an industry are materialized:
    index<->index, index<->etf, etf<->etf.
  - attribution_type ∈ ('index', 'etf'):
      'index' — each security's INDEX margin series = weighted-average of
                its constituent stocks' rongzi, from the
                analysis.margin_index_series VIEW. For an ETF this is its
                TRACKING index's series; for an index this is its OWN
                series. Every security (ETF + index) has an 'index'
                series, so ALL pairs get an 'index' correlation row.
      'etf'   — each security's OWN ETF margin series (rz_balance /
                rz_buy from stats.etf_liquidity_margin). Only ETFs have
                this, so ONLY etf<->etf pairs get an 'etf' correlation
                row. index<->index and index<->etf pairs are skipped
                under 'etf'.
  - security_code < benchmark_code (lexicographic, COLLATE "C") to
    deduplicate (A,B) vs (B,A). Self-pairs (A = B) are skipped
    (self-correlation is always 1).

SOURCE
  analysis.margin_index_series (VIEW): the 'index' series.
  stats.etf_liquidity_margin: the 'etf' series.

WINDOWS
  5d / 20d / 60d / 120d / 255d trailing trading days. Pearson
  correlation requires at least 2 overlapping dates in the window
  (min_periods=2). Computed for BOTH margin_balance and margin_buy
  (2 series × 5 windows = 10 correlation columns).

CONVENTION
  Mirrors analysis.industry_correlations exactly:
    - Inner-join the two series by date (overlapping dates only).
    - rolling(W, min_periods=2).corr(...).
    - One row per shared date (rows where the shortest-window corr is
      still NULL are kept — they carry longer-window corr values).
    - Pre-filter: pairs with fewer than MIN_OVERLAP shared dates are
      skipped entirely (cannot produce any non-NULL corr).
    - NaN -> NULL via sanitize_for_db_insert.

This module is an INTERNAL step of analyze.margins — invoked from
__main__.py after margin_industry_stats is populated, reusing the same
DB connection. It is NOT a standalone runnable.
"""
from __future__ import annotations

import time
from itertools import combinations

import numpy as np
import pandas as pd

from _common.build_commons import (
    copy_insert_async,
    truncate_table_async,
)
from _common.db_commons import copy_or_upsert_split_async
from _common.df_utils import should_use_gpu
from analyze._common import (
    sanitize_for_db_insert,
    upsert_analysis_identity,
)

from analyze.margins.config import TABLE_INDUSTRY_CORRELATION


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

ANALYSIS_NAME = "margin_industry_correlation"
ANALYSIS_DESCRIPTION = (
    "Pairwise rolling Pearson correlation of RONGZI (融资) margin "
    "flows/balances between two SECURITIES within the SAME industry, "
    "over 5/20/60/120/255-day windows. RONQIN (融券 / sec borrow) "
    "EXCLUDED. One row per (date, industry_id, security_code, "
    "benchmark_code, attribution_type). A security is an ETF code or "
    "an index code; ALL pairs within an industry materialized "
    "(index<->index, index<->etf, etf<->etf). attribution_type: "
    "index=weighted-avg constituent-stock margin "
    "(analysis.margin_index_series VIEW), etf=security's own ETF "
    "margin. 10 columns = 2 series × 5 windows. Range -1.0..+1.0. "
    "Self-pairs excluded; order convention security_code < "
    "benchmark_code (COLLATE \"C\"). Convention mirrors "
    "analysis.industry_correlations. Built by analyze.margins "
    "(truncate-then-recompute)."
)

# Trailing trading-day windows for rolling Pearson correlation.
WINDOWS = [5, 20, 60, 120, 255]

# Minimum overlapping dates for a pair to be materialized at all. Pairs
# with fewer overlapping dates only yield NULL correlations for every
# window. Set to the SHORTEST window so a pair must share at least 5
# dates to be worth computing.
MIN_OVERLAP = min(WINDOWS)

# attribution_type values materialized.
ATTRIBUTION_TYPES = ["index", "etf"]


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

    GPU acceleration: when the cuDF router determines the GPU is
    worthwhile for this series length, the computation runs on a cuDF
    Series pair. For short series (the typical case — ~1600 trading
    days) the CPU path is faster, so GPU only kicks in for long series.
    """
    if len(a) != len(b):
        raise ValueError(f"length mismatch: {len(a)} vs {len(b)}")

    router_df = pd.DataFrame({"a": a, "b": b})
    if should_use_gpu(router_df, op_type="rolling_corr"):
        print(f"    [cuDF router] {len(router_df):,} rows — rolling_corr (GPU-worthy)", flush=True)

    s_a = pd.Series(a)
    s_b = pd.Series(b)
    return s_a.rolling(window=window, min_periods=2).corr(s_b).to_numpy()


# ---------------------------------------------------------------------------
#  Fetchers
# ---------------------------------------------------------------------------

async def _fetch_pair_universe(conn) -> pd.DataFrame:
    """Build the per-industry security universe for correlation pairing.

    Returns a DataFrame with columns:
      industry_id, security_code, is_etf, index_series_code

    where:
      - Rows with is_etf=False are INDICES that have a series in the
        analysis.margin_index_series VIEW (i.e. they have constituent
        stocks with margin data). index_series_code = the index's own
        code.
      - Rows with is_etf=True are ETFs whose tracking index has a series
        in the VIEW. index_series_code = the tracking index code. Only
        ETFs mapping to an INDUSTRY index (is_industry_not_strategy=TRUE)
        are included — ETFs tracking BROAD/strategy indices have no
        single-industry home and are excluded.

    An ETF appears here iff its tracking index has a VIEW series, so it
    can participate in 'index' attribution. Whether it also participates
    in 'etf' attribution is decided downstream (needs etf_liquidity_margin
    rows) — this universe is the 'index'-eligible set.
    """
    rows = await conn.fetch(
        """
        WITH view_indices AS (
            SELECT DISTINCT index_code, industry_id
            FROM analysis.margin_index_series
            WHERE industry_id IS NOT NULL AND industry_id <> ''
        )
        -- Indices present in the VIEW (have a weighted-stock margin series)
        SELECT industry_id,
               index_code AS security_code,
               FALSE      AS is_etf,
               index_code AS index_series_code
        FROM view_indices
        UNION ALL
        -- ALL ETFs in sec_classification (no VIEW filter). For the 'etf'
        -- attribution we only need the ETF's own margin data, not its
        -- tracking index's VIEW series. For the 'index' attribution, ETFs
        -- whose tracking index is NOT in the VIEW are simply skipped
        -- (a_series is None → pair skipped).
        SELECT e.industry_id,
               e.code    AS security_code,
               TRUE      AS is_etf,
               e.parent_index_code AS index_series_code
        FROM stats.sec_classification e
        WHERE e.type = 'etf'
          AND e.parent_index_is_primary = TRUE
          AND e.is_active = TRUE
          AND e.industry_id IS NOT NULL AND e.industry_id <> ''
        """
    )
    return pd.DataFrame(
        {
            "industry_id": [r["industry_id"] for r in rows],
            "security_code": [r["security_code"] for r in rows],
            "is_etf": [r["is_etf"] for r in rows],
            "index_series_code": [r["index_series_code"] for r in rows],
        }
    )


async def _fetch_index_series(conn) -> pd.DataFrame:
    """Load the full analysis.margin_index_series VIEW into a DataFrame.

    Returns columns: index_code, date, balance, buy.
    (Renamed from index_margin_balance / index_margin_buy for brevity.)
    """
    rows = await conn.fetch(
        """
        SELECT index_code, date,
               index_margin_balance AS balance,
               index_margin_buy     AS buy
        FROM analysis.margin_index_series
        ORDER BY index_code, date
        """
    )
    return pd.DataFrame(
        {
            "index_code": [r["index_code"] for r in rows],
            "date": [r["date"] for r in rows],
            "balance": [
                float(r["balance"]) if r["balance"] is not None else None
                for r in rows
            ],
            "buy": [
                float(r["buy"]) if r["buy"] is not None else None
                for r in rows
            ],
        }
    )


async def _fetch_etf_series(conn, etf_codes: list[str]) -> pd.DataFrame:
    """Load rz_balance / rz_buy for the given ETF codes from
    stats.etf_liquidity_margin.

    Returns columns: code, date, balance, buy.
    """
    if not etf_codes:
        return pd.DataFrame(columns=["code", "date", "balance", "buy"])
    rows = await conn.fetch(
        """
        SELECT code, date,
               rz_balance AS balance,
               rz_buy     AS buy
        FROM stats.etf_liquidity_margin
        WHERE code = ANY($1::text[])
        ORDER BY code, date
        """,
        etf_codes,
    )
    return pd.DataFrame(
        {
            "code": [r["code"] for r in rows],
            "date": [r["date"] for r in rows],
            "balance": [
                float(r["balance"]) if r["balance"] is not None else None
                for r in rows
            ],
            "buy": [
                float(r["buy"]) if r["buy"] is not None else None
                for r in rows
            ],
        }
    )


# ---------------------------------------------------------------------------
#  Pipeline
# ---------------------------------------------------------------------------

async def run_correlations(
    conn,
    *,
    force: bool = True,
    target_dates: set | None = None,
) -> None:
    """Run the pairwise security-level rolling-correlation pipeline.

    Reuses the caller's DB connection (does not open/close its own).

    Pipeline
      1. Build the per-industry security universe (indices + ETFs whose
         tracking index has a VIEW series).
      2. Load the 'index' series (VIEW) and the 'etf' series
         (etf_liquidity_margin for the universe's ETFs).
      3. For each industry and each attribution_type:
           - 'index': ALL pairs within the industry (every security has
             an index series). Inner-join the two series on date,
             compute rolling Pearson corr for 5/20/60/120/255d on both
             balance and buy. Emit one row per shared date.
           - 'etf': ONLY etf<->etf pairs within the industry (only ETFs
             have an etf margin series). Same inner-join + rolling corr.
      4. Write rows:
           - force: Truncate + COPY-insert (full recompute).
           - incremental: upsert only rows whose date is in target_dates.
      5. Upsert analysis.analysis_identity.

    Args:
      force: when True (default), truncate the table first and recompute
        all rows. When False, upsert only target_dates rows (incremental).
      target_dates: set of missing dates to write (incremental mode).
        Ignored when ``force`` is True.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  MARGIN INDUSTRY CORRELATIONS (internal step of analyze.margins)",
          flush=True)
    print("=" * 78, flush=True)
    print(f"    mode: {'FORCE (full recompute)' if force else 'incremental (missing dates only)'}",
          flush=True)

    # ---- Step 1: build the pair universe ----------------------------
    print("\n[c1/5] Building per-industry security universe...", flush=True)
    universe = await _fetch_pair_universe(conn)
    n_ind = len(universe[~universe["is_etf"]])
    n_etf = len(universe[universe["is_etf"]])
    print(f"      -> {len(universe)} securities "
          f"({n_ind} indices, {n_etf} ETFs) across "
          f"{universe['industry_id'].nunique()} industries", flush=True)

    if universe.empty:
        print("      -> no universe; skipping correlations step.", flush=True)
        return

    # ---- Step 2: load series ----------------------------------------
    print("\n[c2/5] Loading 'index' series (VIEW) + 'etf' series...",
          flush=True)
    index_df = await _fetch_index_series(conn)
    print(f"      -> index series: {len(index_df):,} rows, "
          f"{index_df['index_code'].nunique()} index codes", flush=True)

    etf_codes_in_universe = universe.loc[universe["is_etf"], "security_code"].tolist()
    etf_df = await _fetch_etf_series(conn, etf_codes_in_universe)
    print(f"      -> etf series: {len(etf_df):,} rows, "
          f"{etf_df['code'].nunique() if not etf_df.empty else 0} ETF codes",
          flush=True)

    # Build per-code series lookup dicts: code -> DataFrame[date, balance, buy]
    index_series_by_code: dict[str, pd.DataFrame] = {}
    for code, g in index_df.groupby("index_code"):
        index_series_by_code[code] = (
            g.sort_values("date")
            .drop_duplicates(subset="date", keep="last")
            .reset_index(drop=True)[["date", "balance", "buy"]]
        )
    etf_series_by_code: dict[str, pd.DataFrame] = {}
    if not etf_df.empty:
        for code, g in etf_df.groupby("code"):
            etf_series_by_code[code] = (
                g.sort_values("date")
                .drop_duplicates(subset="date", keep="last")
                .reset_index(drop=True)[["date", "balance", "buy"]]
            )

    # ---- Step 3: pairwise rolling correlations ----------------------
    print("\n[c3/5] Computing pairwise rolling correlations "
          f"(windows={WINDOWS}, attributions={ATTRIBUTION_TYPES})...",
          flush=True)

    out_frames: list[pd.DataFrame] = []
    n_pairs_index = 0
    n_pairs_etf = 0

    corr_balance_cols = [f"corr_balance_{w}d" for w in WINDOWS]
    corr_buy_cols = [f"corr_buy_{w}d" for w in WINDOWS]

    for industry_id, group in universe.groupby("industry_id"):
        # --- 'index' attribution: ALL pairs within the industry ---
        # Every security has an index_series_code present in the VIEW
        # (guaranteed by _fetch_pair_universe), so all pairs are eligible.
        sec_codes = sorted(group["security_code"].tolist())
        for a_code, b_code in combinations(sec_codes, 2):
            assert a_code < b_code, f"order invariant violated: {a_code} >= {b_code}"
            a_idx = group.loc[group["security_code"] == a_code, "index_series_code"].iloc[0]
            b_idx = group.loc[group["security_code"] == b_code, "index_series_code"].iloc[0]
            a_series = index_series_by_code.get(a_idx)
            b_series = index_series_by_code.get(b_idx)
            if a_series is None or b_series is None:
                continue
            pair_df = _compute_pair_corr(
                a_series, b_series,
                industry_id, a_code, b_code, "index",
                corr_balance_cols, corr_buy_cols,
            )
            if pair_df is not None:
                n_pairs_index += 1
                out_frames.append(pair_df)

        # --- 'etf' attribution: ONLY etf<->etf pairs ---
        etf_codes = sorted(
            group.loc[group["is_etf"], "security_code"].tolist()
        )
        # Only ETFs that actually have etf margin data participate.
        etf_codes = [c for c in etf_codes if c in etf_series_by_code]
        for a_code, b_code in combinations(etf_codes, 2):
            assert a_code < b_code
            a_series = etf_series_by_code[a_code]
            b_series = etf_series_by_code[b_code]
            pair_df = _compute_pair_corr(
                a_series, b_series,
                industry_id, a_code, b_code, "etf",
                corr_balance_cols, corr_buy_cols,
            )
            if pair_df is not None:
                n_pairs_etf += 1
                out_frames.append(pair_df)

    print(f"      -> {n_pairs_index} index pairs + {n_pairs_etf} etf pairs "
          f"had >= {MIN_OVERLAP} overlapping dates", flush=True)

    corr_col_names = corr_balance_cols + corr_buy_cols
    if out_frames:
        out_df = pd.concat(out_frames, ignore_index=True)
        if not force and target_dates:
            n_before = len(out_df)
            out_df = out_df[out_df["date"].isin(target_dates)].reset_index(
                drop=True
            )
            print(f"      -> incremental filter: {len(out_df):,} of "
                  f"{n_before:,} rows are in target_dates", flush=True)
        out_rows = sanitize_for_db_insert(
            out_df, numeric_cols=corr_col_names, round_to=4,
        )
    else:
        out_rows = []

    print(f"      -> {len(out_rows):,} correlation rows to write", flush=True)

    if not out_rows:
        print("      -> no rows to write; skipping correlations insert.",
              flush=True)
        if force:
            # Still truncate so the table doesn't hold stale data.
            await truncate_table_async(conn, TABLE_INDUSTRY_CORRELATION)
    elif force:
        # ---- Step 4: truncate + COPY-insert -------------------------
        print(f"\n[c4/5] Truncating {TABLE_INDUSTRY_CORRELATION} and "
              f"COPY-inserting {len(out_rows):,} rows...", flush=True)
        await truncate_table_async(conn, TABLE_INDUSTRY_CORRELATION)
        n = await copy_insert_async(conn, TABLE_INDUSTRY_CORRELATION, out_rows)
        print(f"      -> inserted {n:,} rows", flush=True)
    else:
        # ---- Step 4: upsert (incremental) ---------------------------
        print(f"\n[c4/5] Upserting {len(out_rows):,} rows into "
              f"{TABLE_INDUSTRY_CORRELATION}...", flush=True)
        n_copied, n_upserted = await copy_or_upsert_split_async(
            conn, TABLE_INDUSTRY_CORRELATION, out_rows,
            key_columns=[
                "date", "industry_id", "security_code",
                "benchmark_code", "attribution_type",
            ],
        )
        n = n_copied + n_upserted
        via = "COPY" if n_copied > 0 and n_upserted == 0 else \
              f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
              "upsert"
        print(f"      -> inserted {n:,} rows via {via}", flush=True)

    # ---- Step 5: register in analysis_identity ----------------------
    print("\n[c5/5] Registering in analysis.analysis_identity...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=ANALYSIS_NAME,
        detail_name=ANALYSIS_NAME,
        description=ANALYSIS_DESCRIPTION,
    )

    # Sanity summary: row count by attribution_type.
    summary = await conn.fetch(
        """
        SELECT attribution_type,
               COUNT(*) AS n_rows,
               COUNT(DISTINCT (industry_id, security_code, benchmark_code))
                   AS n_pairs,
               MIN(date) AS first_date,
               MAX(date) AS last_date
        FROM analysis.margin_industry_correlation
        GROUP BY attribution_type
        ORDER BY attribution_type
        """
    )
    print("\n      Summary by attribution_type:", flush=True)
    for r in summary:
        print(f"        {r['attribution_type']:6s}: {r['n_rows']:>9,} rows . "
              f"{r['n_pairs']:>5} pairs . "
              f"{r['first_date']} -> {r['last_date']}", flush=True)

    print(f"\n  correlations wall time: {time.time() - t0:.1f}s", flush=True)


def _compute_pair_corr(
    a_series: pd.DataFrame,
    b_series: pd.DataFrame,
    industry_id: str,
    security_code: str,
    benchmark_code: str,
    attribution_type: str,
    corr_balance_cols: list[str],
    corr_buy_cols: list[str],
) -> pd.DataFrame | None:
    """Inner-join two per-security series on date and compute rolling
    Pearson correlations for all windows on both balance and buy.

    Returns a DataFrame with columns [industry_id, security_code,
    benchmark_code, attribution_type, date, <10 corr columns>], or None
    when the pair shares fewer than MIN_OVERLAP dates (cannot produce
    any non-NULL correlation).
    """
    merged = a_series.merge(b_series, on="date", suffixes=("_a", "_b"))
    if len(merged) < MIN_OVERLAP:
        return None

    merged = merged.sort_values("date").reset_index(drop=True)
    bal_a = merged["balance_a"].to_numpy(dtype=np.float64)
    bal_b = merged["balance_b"].to_numpy(dtype=np.float64)
    buy_a = merged["buy_a"].to_numpy(dtype=np.float64)
    buy_b = merged["buy_b"].to_numpy(dtype=np.float64)

    pair_df = pd.DataFrame(
        {
            "industry_id": industry_id,
            "security_code": security_code,
            "benchmark_code": benchmark_code,
            "attribution_type": attribution_type,
            "date": merged["date"].to_numpy(),
        }
    )
    for w, col in zip(WINDOWS, corr_balance_cols):
        pair_df[col] = rolling_corr(bal_a, bal_b, w)
    for w, col in zip(WINDOWS, corr_buy_cols):
        pair_df[col] = rolling_corr(buy_a, buy_b, w)
    return pair_df
