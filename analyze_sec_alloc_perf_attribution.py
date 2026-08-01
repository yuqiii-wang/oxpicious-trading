"""
analyze_sec_alloc_perf_attribution.py — Daily Performance Attribution
(ETF + Index subjects × Index benchmarks)

Populates analysis.sec_alloc_perf_attribution with daily return decomposition
for:
  1. ETF subjects (top-3 by avg volume per industry, classified via
     _study_select_etf.study_etf_themes) vs ALL indices as benchmarks.
  2. Index subjects (ALL indices) vs ALL indices as benchmarks (excl. self).

Subject / benchmark pairing:
  code            = ETF code WITH suffix (e.g. "510050.SS") for sec_type='etf'
                    OR bare index code (e.g. "000300") for sec_type='index'
  sec_type        = 'etf' or 'index' (determines source table for returns)
  benchmark_code  = any index code from stats.index_identity (e.g. "000300")

Per-row decomposition:
  subject_return     = ETF  (close_t - close_{t-1})  — adj_close when available
  benchmark_return   = index (close_t - close_{t-1})
  active_return      = subject_return - benchmark_return
  allocation_effect  = NULL  (Brinson-Fachler not computed in this run)
  code_sec_shared_weight         = Σ w_etf   on stocks held by BOTH ETF & benchmark
  benchmark_sec_shared_weight    = Σ w_index on stocks held by BOTH ETF & benchmark
    (Computed from latest snapshot in stats.sec_composition; same for all dates.)

  ETF-MARKET AMOUNT (liquidity view, NOT price attribution):
    benchmark_etf_amount = stats.index_exts.total_etf_amt for benchmark_code
                           on this date (precomputed by build_index_exts.py =
                           Σ etf_liquidity_margin.amount_wan×1e4 across ALL
                           ETFs tracking benchmark_code, identified via
                           stats.sec_classification.parent_index_code). NULL
                           when no ETF tracks the benchmark (e.g. 000001).
    code_etf_amount      = subject's own amount_wan×1e4 (sec_type='etf') OR
                           stats.index_exts.total_etf_amt for the subject
                           index (sec_type='index', same source as above).
    etf_amount_ratio_benchmark_to_code = GENERATED (not inserted) =
                           benchmark_etf_amount / code_etf_amount.
                           The inverse (1/ratio) is the subject's SHARE of the
                           benchmark ETF market — interpretable as a proportion.

  STATISTICAL ATTRIBUTION (rolling correlations):
    corr_5d/20d/60d/255d = rolling Pearson corr of subject vs benchmark close.

Table is TRUNCATE-then-INSERT on every run (full recompute).

Usage:
  python analyze_sec_alloc_perf_attribution.py
"""
import os
import sys
import time
import asyncio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _build_commons import (  # noqa: E402
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

# study_etf_themes classifies ETFs by name into theme/industry and computes
# per-ETF quality metrics (n_ohlcv_days, avg_volume_wan, has_recent_data).
from _study_select_etf import study_etf_themes  # noqa: E402


# ----------------------------------------------------------------------------
#  Configuration
# ----------------------------------------------------------------------------

ANALYSIS_NAME = "sec_alloc_perf_attribution"
TABLE = "analysis.sec_alloc_perf_attribution"

# ETF selection: top-N ETFs by avg trading volume per industry.
TOP_N_PER_INDUSTRY = 3
MIN_OHLCV_DAYS = 40

# Benchmark selection: keep ALL broad-market indices + top-N highest-traded
# non-broad indices (ranked by aggregate ETF turnover). Bounds the
# subject × benchmark cross product while retaining the most liquid
# sector/industry benchmarks.
TOP_N_NON_BROAD = 3

DESCRIPTION = (
    "Daily performance attribution for ETF + Index subjects vs all index "
    "benchmarks. ETF subjects: top-3 by avg volume per industry (via "
    "_study_select_etf.study_etf_themes; min 40 OHLCV days; recent data; "
    "MUST have composition data in stats.sec_composition — cross-border ETFs "
    "whose SZSE composition CSVs contain only a cash placeholder row are "
    "excluded). Index subjects: only indices with composition data (44 CSI "
    "indices with closeweight CSVs) vs all indices (excl. self-pairs). For "
    "each (code, index, date, sec_type) tuple: subject_return = ETF adj_close "
    "diff or index close diff, benchmark_return = index close diff (absolute "
    "price/points difference, NOT fractional ratio), active_return = "
    "subject_return - benchmark_return. allocation_effect (Brinson-Fachler) "
    "is NULL. code_sec_shared_weight and benchmark_sec_shared_weight computed "
    "from latest stats.sec_composition snapshot overlap (stocks held by BOTH "
    "subject and benchmark). "
    "ETF-MARKET AMOUNT (liquidity view): benchmark_etf_amount = "
    "stats.index_exts.total_etf_amt for benchmark_code on this date "
    "(precomputed by build_index_exts.py = Σ "
    "etf_liquidity_margin.amount_wan×1e4 across ALL ETFs tracking "
    "benchmark_code via stats.sec_classification.parent_index_code); NULL "
    "when no ETF tracks the benchmark (e.g. 000001 上证指数 has no direct "
    "ETF). code_etf_amount = subject's own amount_wan×1e4 (sec_type='etf') "
    "OR stats.index_exts.total_etf_amt for the subject index "
    "(sec_type='index'). Both in yuan; "
    "etf_amount_ratio_benchmark_to_code (GENERATED ALWAYS column) = "
    "benchmark_etf_amount / code_etf_amount; its INVERSE is the subject's "
    "share of the benchmark ETF market. NOTE: this is a LIQUIDITY ratio, not "
    "a price-attribution proportion. "
    "STATISTICAL ATTRIBUTION: corr_5d/20d/60d/255d = Pearson correlation "
    "between subject and benchmark close prices over trailing N trading days "
    "(computed via pandas vectorized df.rolling(N, min_periods=max(2N/3, 3))."
    "corr(series) against a wide-format benchmark-close pivot; min_periods="
    "2N/3 allows up to 1/3 of window data missing). "
    "Subject close = COALESCE(etf_adjustment.adj_close, etf_basic_stats.close) "
    "for ETFs, index_basic_stats.close for indices. "
    "NOTE: benchmark indices WITHOUT composition data are still included as "
    "benchmarks (benchmark_return is meaningful on its own); only their "
    "shared_weight columns are NULL."
)


# ----------------------------------------------------------------------------
#  Step 1 — Select ETF subjects: top-3 by volume per industry
# ----------------------------------------------------------------------------

async def fetch_codes_with_composition(conn) -> set:
    """Return the set of codes (with exchange suffix for ETFs, bare for indices)
    that have at least one non-cash holding in the LATEST snapshot of
    stats.sec_composition.

    This is used to filter subjects to only those with real composition data,
    so the resulting perf-attr rows actually have shared_weight values
    populated (the chart's "Contribution" bar = return × shared_weight needs
    shared_weight to be non-NULL).

    Background: many ETFs (cross-border ETFs like 159920 恒生ETF) and many
    indices (SSE/SZSE-published 000xxx/399xxx that aren't CSI indices) have
    NO published composition. Their SZSE composition CSVs contain only a
    cash placeholder row (cash_sub_flag='必须') that the build script filters
    out, leaving sec_composition empty for those codes. Without this filter,
    every perf-attr row for such a subject would have NULL shared_weight and
    the "Fluctuation Attribution" chart would render zero bars.
    """
    rows = await conn.fetch("""
        SELECT DISTINCT code
        FROM stats.sec_composition
        WHERE stock_code IS NOT NULL
          AND source_type IN ('etf', 'index')
    """)
    return {r["code"] for r in rows}


async def select_etf_subjects(conn, codes_with_comp: set) -> list:
    """Select top-N ETFs by avg volume per industry.

    Uses _study_select_etf.study_etf_themes() for classification.  The
    combined DataFrame is fetched directly (WITH exchange-suffixed codes) so
    that the returned study_df codes carry the .SZ/.SS/.SH suffix required by
    the attribution table's CHECK constraint.

    Filters out ETFs that have no composition data in stats.sec_composition
    (cross-border ETFs whose SZSE-published composition CSVs contain only a
    cash placeholder row). Without this filter, every (subject, benchmark)
    pair for such ETFs would have NULL shared_weight — making the entire
    "Fluctuation Attribution" chart empty for those subjects.

    Returns:
        List of ETF codes with exchange suffix (e.g. "510050.SS").
    """
    # Fetch combined ETF data with suffixed codes (do NOT strip suffix —
    # the attribution table CHECK requires \d{6}.(SZ|SS|SH) for ETFs).
    rows = await conn.fetch("""
        SELECT
            i.date, i.code, i.name,
            b.prev_close, b.open, b.high, b.low, b.close, b.pct_change,
            COALESCE(l.volume_wan, 0)     AS volume_wan,
            COALESCE(l.amount_wan, 0)     AS amount_wan,
            COALESCE(l.rz_balance, 0)     AS rz_balance,
            COALESCE(l.rq_balance_amt, 0) AS rq_balance_amt
        FROM stats.etf_identity i
        JOIN stats.etf_basic_stats b
            ON b.date = i.date AND b.code = i.code
        LEFT JOIN stats.etf_liquidity_margin l
            ON l.date = i.date AND l.code = i.code
        ORDER BY i.code, i.date
    """)
    if not rows:
        print("    [FATAL] No ETF data found in stats.etf_identity.", flush=True)
        return []

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values(["code", "date"]).reset_index(drop=True)
    print(f"    → {len(df):,} rows · {df['code'].nunique()} ETFs · "
          f"{df['date'].min().date()} → {df['date'].max().date()}", flush=True)

    # study_etf_themes classifies by name (classify_etf) and computes
    # per-code: theme_id, industry_id, avg_volume_wan, n_ohlcv_days,
    # has_recent_data, has_margin.  save=False avoids CSV side-effects.
    study_df, summary_df = study_etf_themes(df, save=False, require_recent_data=True)
    print(f"    → {len(study_df):,} ETFs after recent-data filter across "
          f"{study_df['industry_id'].nunique()} industries", flush=True)

    # Filter: sufficient OHLCV history.
    study_df = study_df[study_df["n_ohlcv_days"] >= MIN_OHLCV_DAYS].copy()
    print(f"    → {len(study_df):,} ETFs after ≥{MIN_OHLCV_DAYS} OHLCV-day filter",
          flush=True)

    # Filter: must have real composition data in stats.sec_composition.
    # Cross-border ETFs (e.g. 159920 恒生ETF) have only a cash placeholder row
    # in their SZSE composition CSV — build_szse_sse_etf_and_margin.py filters
    # those out, leaving sec_composition empty for those codes. Without this
    # filter, every (subject, benchmark) pair for those ETFs would have NULL
    # shared_weight, rendering the Fluctuation Attribution chart empty.
    before = len(study_df)
    study_df = study_df[study_df["code"].isin(codes_with_comp)].copy()
    print(f"    → {len(study_df):,} ETFs after composition-data filter "
          f"(dropped {before - len(study_df)} without composition)", flush=True)

    # Top-N by avg_volume_wan DESC per industry_id.
    selected = (
        study_df.sort_values("avg_volume_wan", ascending=False)
        .groupby("industry_id", group_keys=False)
        .head(TOP_N_PER_INDUSTRY)
    )
    codes = selected["code"].tolist()
    print(f"    → {len(codes)} ETFs selected (top-{TOP_N_PER_INDUSTRY} per industry)",
          flush=True)
    if codes:
        # Print per-industry breakdown for visibility.
        for iid, grp in selected.groupby("industry_id"):
            labels = grp["industry_label"].iloc[0] if "industry_label" in grp.columns else iid
            names = ", ".join(
                f"{c}({n})" for c, n in zip(grp["code"], grp["name"])
            )
            print(f"      · {iid:<20s} {labels[:16]:<16s} → {names}", flush=True)
    return codes


# ----------------------------------------------------------------------------
#  Step 2 — Fetch ETF daily returns + volume
# ----------------------------------------------------------------------------

async def fetch_etf_returns(conn, codes: list) -> pd.DataFrame:
    """Fetch daily returns for the selected ETF codes.

    subject_return = price_t - price_{t-1}  (absolute diff, per code ordered
    by date).  price = COALESCE(etf_adjustment.adj_close, etf_basic_stats.close).

    Returns DataFrame: [code, date, subject_return, code_etf_amount, subject_close]
      - First row per code (NULL return) is dropped.
      - code_etf_amount = etf_liquidity_margin.amount_wan * 10000 (万元→yuan;
        NULL when no liquidity_margin row). For sec_type='etf' subjects this
        IS the code_etf_amount written to the table; for sec_type='index'
        subjects the caller (build_and_insert) overwrites it with the aggregate
        ETF amount tracking the subject index.
      - subject_close = the close price used to compute subject_return
        (kept for downstream rolling-correlation computation).
    """
    if not codes:
        return pd.DataFrame(columns=["code", "date", "subject_return", "code_etf_amount", "subject_close"])

    rows = await conn.fetch("""
        SELECT
            b.code,
            b.date,
            COALESCE(a.adj_close, b.close) AS price,
            l.amount_wan * 10000 AS code_etf_amount
        FROM stats.etf_basic_stats b
        LEFT JOIN stats.etf_adjustment a
            ON a.date = b.date AND a.code = b.code
        LEFT JOIN stats.etf_liquidity_margin l
            ON l.date = b.date AND l.code = b.code
        WHERE b.code = ANY($1::text[])
        ORDER BY b.code, b.date
    """, codes)

    if not rows:
        return pd.DataFrame(columns=["code", "date", "subject_return", "code_etf_amount", "subject_close"])

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["code_etf_amount"] = pd.to_numeric(df["code_etf_amount"], errors="coerce")
    df = df.sort_values(["code", "date"]).reset_index(drop=True)

    # subject_return = today's price - previous trading day's price (per code).
    df["subject_return"] = df.groupby("code")["price"].diff()
    # Drop first row per code (NULL return — no previous day).
    df = df.dropna(subset=["subject_return"])
    # Rename price → subject_close for clarity (used for rolling correlation).
    df = df.rename(columns={"price": "subject_close"})
    return df[["code", "date", "subject_return", "code_etf_amount", "subject_close"]]


# ----------------------------------------------------------------------------
#  Step 2b — Fetch composition shared weights (ALL subject × benchmark pairs)
# ----------------------------------------------------------------------------

async def fetch_shared_weights(conn) -> dict:
    """Compute shared weight for every (subject, benchmark) pair.

    Uses the LATEST composition snapshot in stats.sec_composition for each
    code (both ETF and index source_types).  For stocks held by BOTH, sums
    the weights:
      code_sec_shared_weight      = Σ w_subject   on shared stocks
      benchmark_sec_shared_weight = Σ w_benchmark on shared stocks

    Computes ALL pairs (ETF×Index, Index×Index, ETF×ETF, Index×ETF) in one
    query.  Callers look up only the pairs they need.

    Pairs where both codes have composition data but ZERO overlapping stocks
    (e.g. CSI 300 vs CSI 500 — disjoint by design) are explicitly set to
    (0, 0) so they appear in the chart with zero-height bars instead of
    being indistinguishable from pairs that lack composition data entirely.

    Returns dict: { (subject_code, benchmark_code): (code_wt, bench_wt) }
    """
    rows = await conn.fetch("""
        WITH latest AS (
            SELECT code, source_type, MAX(snapshot_date) AS max_date
            FROM stats.sec_composition
            WHERE stock_code IS NOT NULL
            GROUP BY code, source_type
        ),
        holdings AS (
            SELECT sc.code, sc.stock_code,
                   LEFT(sc.stock_code, 6) AS normalized_code,
                   sc.weight_pct
            FROM stats.sec_composition sc
            JOIN latest ld ON sc.code = ld.code
                          AND sc.source_type = ld.source_type
                          AND sc.snapshot_date = ld.max_date
            WHERE sc.stock_code IS NOT NULL
        )
        SELECT
            h1.code AS subject_code,
            h2.code AS benchmark_code,
            SUM(h1.weight_pct) AS code_sec_shared_weight,
            SUM(h2.weight_pct)  AS benchmark_sec_shared_weight
        FROM holdings h1
        JOIN holdings h2 ON h1.normalized_code = h2.normalized_code
        WHERE h1.code != h2.code
        GROUP BY h1.code, h2.code
    """)

    result = {}
    for r in rows:
        csw = float(r["code_sec_shared_weight"])
        bsw = float(r["benchmark_sec_shared_weight"])
        # Skip NaN (PostgreSQL NUMERIC supports NaN; SUM propagates it)
        if csw != csw or bsw != bsw:
            continue
        result[(r["subject_code"], r["benchmark_code"])] = (csw, bsw)

    # For pairs where both codes have composition data but zero overlapping
    # stocks, set shared weight to (0, 0) instead of leaving them absent.
    # This distinguishes "zero overlap" from "no composition data" and
    # ensures disjoint indices (e.g. CSI 300 vs CSI 500/1000) appear in
    # the chart with explicit zero bars rather than being invisible.
    all_codes = await conn.fetch("""
        SELECT DISTINCT code
        FROM stats.sec_composition
        WHERE stock_code IS NOT NULL
    """)
    code_set = {r["code"] for r in all_codes}
    for c1 in code_set:
        for c2 in code_set:
            if c1 != c2 and (c1, c2) not in result:
                result[(c1, c2)] = (0.0, 0.0)

    return result

async def fetch_index_returns(conn) -> pd.DataFrame:
    """Fetch daily returns for STOCK-BASED indices used as benchmarks.

    Benchmark pool:
      1. ALL broad-market indices (stats.sec_classification.sector_id='BROAD')
         — kept in full because they are the primary market benchmarks.
      2. Per-industry top-N highest-traded NON-broad indices. For each
         industry_id, the top TOP_N_NON_BROAD indices (sector_id NOT IN
         ('BROAD', 'DEBT')) are kept, ranked by aggregate ETF turnover
         (SUM(stats.index_exts.total_etf_amt) over all dates). This ensures
         every industry is represented by its most liquid indices, bounding
         the subject × benchmark cross product while preserving sector
         diversity.
      3. DEBT-sector indices are always excluded.

    benchmark_return = close_t - close_{t-1}  (absolute points diff, per
    code ordered by date).

    Returns DataFrame: [benchmark_code, date, benchmark_return, benchmark_close]
      - First row per code (NULL return) is dropped.
      - benchmark_close = the index close (kept for downstream rolling-
        correlation computation against subject closes).
      - benchmark_etf_amount is NOT fetched here — it is fetched separately
        by fetch_etf_amount_by_index() and merged in build_and_insert()
        because the new semantics aggregate ETF turnover tracking the index
        (via stats.sec_classification.parent_index_code), not the index's
        own turnover.
    """
    rows = await conn.fetch("""
        WITH broad_codes AS (
            -- All broad-market indices (kept in full)
            SELECT b.code
            FROM stats.index_basic_stats b
            JOIN stats.sec_classification sc
                ON sc.code = b.code AND sc.type = 'index'
            WHERE sc.sector_id = 'BROAD'
        ),
        non_broad_ranked AS (
            -- Non-broad, non-debt indices ranked WITHIN each industry
            -- by aggregate ETF turnover.
            SELECT b.code,
                   sc.industry_id,
                   SUM(ie.total_etf_amt) AS total_amt,
                   ROW_NUMBER() OVER (
                       PARTITION BY sc.industry_id
                       ORDER BY SUM(ie.total_etf_amt) DESC NULLS LAST
                   ) AS rn
            FROM stats.index_basic_stats b
            JOIN stats.sec_classification sc
                ON sc.code = b.code AND sc.type = 'index'
            LEFT JOIN stats.index_exts ie
                ON ie.code = b.code
            WHERE sc.sector_id NOT IN ('BROAD', 'DEBT')
            GROUP BY b.code, sc.industry_id
        ),
        top_non_broad AS (
            -- Per-industry top-N indices by ETF turnover
            SELECT code FROM non_broad_ranked
            WHERE rn <= $1
        ),
        kept_codes AS (
            SELECT code FROM broad_codes
            UNION
            SELECT code FROM top_non_broad
        )
        SELECT
            b.code AS benchmark_code,
            b.date,
            b.close
        FROM stats.index_basic_stats b
        JOIN kept_codes kc ON kc.code = b.code
        ORDER BY b.code, b.date
    """, TOP_N_NON_BROAD)

    if not rows:
        return pd.DataFrame(
            columns=["benchmark_code", "date", "benchmark_return", "benchmark_close"]
        )

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.sort_values(["benchmark_code", "date"]).reset_index(drop=True)

    df["benchmark_return"] = df.groupby("benchmark_code")["close"].diff()
    df = df.dropna(subset=["benchmark_return"])
    # Rename close → benchmark_close for clarity (used for rolling correlation).
    df = df.rename(columns={"close": "benchmark_close"})
    return df[["benchmark_code", "date", "benchmark_return", "benchmark_close"]]


# ----------------------------------------------------------------------------
#  Step 2c — Fetch aggregate ETF amount per (date, tracking_index)
# ----------------------------------------------------------------------------
async def fetch_etf_amount_by_index(conn) -> pd.DataFrame:
    """Aggregate ETF turnover per (date, tracking_index) — used to populate
    benchmark_etf_amount AND code_etf_amount for index subjects.

    Reads the precomputed total_etf_amt from stats.index_exts (built by
    build_index_exts.py = Σ etf_liquidity_margin.amount_wan × 1e4 across ALL
    ETFs whose stats.sec_classification.parent_index_code = index_code on
    that date). index_exts only stores rows where (date, code) exists in
    stats.index_identity, but every benchmark / index subject used by this
    analysis comes from index_identity, so no values are lost.

    This is the "index amount" semantics: instead of using the index's own
    turnover (stats.index_basic_stats.amount, which includes ALL market
    participants — stocks, futures, etc.), we use the ETF-market turnover
    tracking the index — a tighter measure of ETF-market liquidity for that
    benchmark. For ETF subjects (sec_type='etf'), code_etf_amount is the
    subject's OWN amount (set in fetch_etf_returns) and is NOT overwritten
    by this aggregate — only index subjects and benchmarks use it.

    Caveats:
      - Indices with no tracking ETF (e.g. 000001 上证指数, 399001 深证成指)
        have NO rows in this DataFrame → their benchmark_etf_amount will be
        NULL after the merge, and etf_amount_ratio_benchmark_to_code will
        also be NULL.
      - The ETF universe grows over time as new ETFs are listed, so the
        aggregate for an index mechanically trends upward — this is a known
        bias of the metric (a liquidity view, not a price-attribution view).

    Returns DataFrame: [index_code, date, etf_amount]
      - etf_amount is in yuan (amount_wan × 1e4).
    """
    rows = await conn.fetch("""
        SELECT code AS index_code, date, total_etf_amt AS etf_amount
        FROM stats.index_exts
        WHERE total_etf_amt IS NOT NULL
    """)

    if not rows:
        return pd.DataFrame(columns=["index_code", "date", "etf_amount"])

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["etf_amount"] = pd.to_numeric(df["etf_amount"], errors="coerce")
    return df[["index_code", "date", "etf_amount"]]


# ----------------------------------------------------------------------------
#  Step 4 — Build + insert attribution rows (per ETF to bound memory)
# ----------------------------------------------------------------------------

async def build_and_insert(conn, subject_returns: pd.DataFrame,
                           index_returns: pd.DataFrame,
                           shared_weights: dict,
                           etf_amount_by_index: pd.DataFrame,
                           sec_type: str) -> int:
    """For each subject code, cross-join with all indices on date, insert.

    Processes one subject at a time to bound memory.

    For sec_type='index': excludes self-pairs (code == benchmark_code).

    shared_weights: dict from fetch_shared_weights() —
      { (subject_code, benchmark_code): (code_wt, bench_wt) }

    etf_amount_by_index: DataFrame from fetch_etf_amount_by_index() with
      columns [index_code, date, etf_amount]. Used to populate:
        - benchmark_etf_amount = etf_amount where index_code = benchmark_code
        - code_etf_amount for sec_type='index' = etf_amount where
          index_code = subject_code (overwrites the per-ETF amount that
          was attached in fetch_etf_returns — for index subjects that
          column is absent/irrelevant anyway).
      For sec_type='etf' subjects, code_etf_amount is the subject's own
      amount already present in subject_returns (NOT overwritten).

    For each (subject, benchmark, date) row, also computes the Pearson
    correlation between the subject's close prices and the benchmark's
    close prices over trailing N-day windows (N ∈ {5, 20, 60, 255}).
    The correlations are computed via pandas' vectorized
    `df.rolling(N).corr(series)` against a wide-format benchmark-close
    pivot, so all benchmarks for one subject are handled in one pass.

    Returns total rows inserted.
    """
    n_subjects = subject_returns["code"].nunique() if not subject_returns.empty else 0
    n_indices = (index_returns["benchmark_code"].nunique()
                 if not index_returns.empty else 0)
    print(f"    → {n_subjects} {sec_type}s × {n_indices} indices "
          f"(cross-product on shared dates)", flush=True)

    if n_subjects == 0 or n_indices == 0:
        print("    → no data to insert.", flush=True)
        return 0

    # Pre-pivot benchmark closes to wide format (date × benchmark_code) for
    # vectorized rolling-correlation computation.  Computed once per call and
    # reused for every subject.  Each column is one benchmark's close-price
    # history; the index is trading dates (date objects).
    benchmark_close_wide = (
        index_returns.pivot(index="date", columns="benchmark_code",
                            values="benchmark_close")
        .sort_index()
    )

    # Pre-pivot etf_amount_by_index to wide format (date × index_code) for
    # fast per-subject lookup via reindex.  Each column is one index's
    # aggregate-ETF-amount time series.  Computed once per call.
    if not etf_amount_by_index.empty:
        etf_amount_wide = (
            etf_amount_by_index.pivot(index="date", columns="index_code",
                                      values="etf_amount")
            .sort_index()
        )
    else:
        etf_amount_wide = pd.DataFrame()

    # Rolling-correlation windows (trading days).  255 ≈ 1 year of trading
    # days; 60 ≈ 1 quarter.  min_periods is set to max(2N/3, 3) so that
    # up to 1/3 of the window can be NaN (handles benchmarks with
    # occasional data gaps). Combined with close estimation in the
    # build scripts (is_close_estimated), this eliminates most NULLs.
    CORR_WINDOWS = (5, 20, 60, 255)

    total = 0
    subject_codes = sorted(subject_returns["code"].unique())

    for i, subject_code in enumerate(subject_codes):
        sub = subject_returns[subject_returns["code"] == subject_code].copy()
        # Inner merge on date: pairs this subject's returns with every index's
        # return on the same trading day.
        merged = sub.merge(index_returns, on="date", how="inner")
        if merged.empty:
            continue

        # For index subjects, exclude self-pairs (subject == benchmark).
        if sec_type == "index":
            merged = merged[merged["code"] != merged["benchmark_code"]]
            if merged.empty:
                continue

        # Vectorized column assembly.
        merged["active_return"] = (
            merged["subject_return"] - merged["benchmark_return"]
        )
        merged["sec_type"] = sec_type
        merged["allocation_effect"] = None

        # Look up shared weights for each (subject_code, benchmark_code) pair.
        # Shared weights are from latest composition snapshot — same for all dates.
        def _lookup_wt(benchmark_code):
            pair = shared_weights.get((subject_code, benchmark_code))
            return pair if pair is not None else (None, None)

        wt = merged["benchmark_code"].map(_lookup_wt)
        merged["code_sec_shared_weight"] = [w[0] for w in wt]
        merged["benchmark_sec_shared_weight"] = [w[1] for w in wt]

        # ---- Compute rolling close-price correlations -------------------
        # For each window N, corr_Nd = Pearson correlation between the
        # subject's close prices and each benchmark's close prices over the
        # trailing N trading days ending at each date.
        #
        # Vectorized via `df.rolling(N).corr(series)`:
        #   - sub_aligned: Series of subject closes, indexed by date
        #   - bench_aligned: DataFrame of all benchmark closes (date ×
        #     benchmark_code), reindexed to the same dates as sub_aligned
        # The result is a date × benchmark_code DataFrame of per-benchmark
        # rolling correlations, which we stack to long format and merge
        # back into the per-(date, benchmark_code) `merged` frame.
        subject_close_series = (
            sub.set_index("date")["subject_close"].sort_index()
        )
        common_dates = subject_close_series.index.intersection(
            benchmark_close_wide.index
        )
        sub_aligned = subject_close_series.reindex(common_dates)
        bench_aligned = benchmark_close_wide.reindex(common_dates)

        for N in CORR_WINDOWS:
            # Use min_periods to allow some NaN values in the rolling window.
            # Default pandas behavior (min_periods=N) requires ALL N values
            # to be non-NaN, causing widespread NULLs when benchmarks have
            # even a single NaN close price within the trailing N days.
            # Setting min_periods to ~2/3 of the window allows correlation
            # computation when up to 1/3 of the data is missing.
            min_p = max(N * 2 // 3, 3)
            corr_wide = bench_aligned.rolling(N, min_periods=min_p).corr(sub_aligned)
            # Stack date × benchmark_code wide frame → long format with
            # columns [date, benchmark_code, corr_Nd].
            corr_long = corr_wide.stack().reset_index()
            corr_long.columns = ["date", "benchmark_code", f"corr_{N}d"]
            # Normalize date to python date objects so the merge with
            # `merged` (whose date column is date objects) keys correctly.
            corr_long["date"] = pd.to_datetime(corr_long["date"]).dt.date
            merged = merged.merge(
                corr_long, on=["date", "benchmark_code"], how="left"
            )

        # ---- benchmark_etf_amount + code_etf_amount (for index subjects) -
        # For ALL subjects: benchmark_etf_amount = aggregate ETF turnover
        # tracking the benchmark index on this date.  Looked up from the
        # wide pivot by reindexing to the merged frame's dates and
        # selecting the column matching each benchmark_code.
        #
        # For sec_type='index': code_etf_amount = aggregate ETF turnover
        # tracking the SUBJECT index (column = subject_code in the wide
        # pivot).  Overwrites any per-ETF amount that was attached in
        # fetch_etf_returns (irrelevant for index subjects — they have no
        # own amount in etf_liquidity_margin).
        #
        # For sec_type='etf': code_etf_amount is already in `merged` (from
        # fetch_etf_returns via the per-subject sub-frame) and is NOT
        # overwritten — it is the subject's own ETF turnover.
        if not etf_amount_wide.empty:
            # Build a long-format DataFrame of (date, benchmark_code, etf_amount)
            # by stacking the wide pivot.  This is reused for both the
            # benchmark_etf_amount lookup (keyed on benchmark_code) and the
            # code_etf_amount lookup for index subjects (keyed on subject_code).
            etf_amount_long = (
                etf_amount_wide.stack().reset_index()
            )
            etf_amount_long.columns = ["date", "index_code", "etf_amount"]
            etf_amount_long["date"] = pd.to_datetime(
                etf_amount_long["date"]
            ).dt.date

            # benchmark_etf_amount: merge on (date, benchmark_code=index_code).
            merged = merged.merge(
                etf_amount_long.rename(columns={
                    "index_code": "benchmark_code",
                    "etf_amount": "benchmark_etf_amount",
                }),
                on=["date", "benchmark_code"], how="left",
            )

            # code_etf_amount for index subjects: when sec_type='index',
            # overwrite code_etf_amount with the aggregate ETF turnover
            # tracking the subject index (keyed on subject code).
            if sec_type == "index":
                subject_amt = (
                    etf_amount_long[etf_amount_long["index_code"] == subject_code]
                    .rename(columns={"etf_amount": "code_etf_amount"})
                    [["date", "code_etf_amount"]]
                )
                # Drop the (now-irrelevant) per-ETF code_etf_amount column
                # if it was carried in from fetch_etf_returns; replace with
                # the aggregate.
                if "code_etf_amount" in merged.columns:
                    merged = merged.drop(columns=["code_etf_amount"])
                merged = merged.merge(subject_amt, on="date", how="left")
        else:
            # No ETF→index mapping data — both columns stay NULL.
            merged["benchmark_etf_amount"] = None
            if "code_etf_amount" not in merged.columns:
                merged["code_etf_amount"] = None

        out_cols = [
            "code", "date", "sec_type", "benchmark_code",
            "subject_return", "benchmark_return", "active_return",
            "allocation_effect",
            "code_sec_shared_weight", "benchmark_sec_shared_weight",
            "benchmark_etf_amount", "code_etf_amount",
            "corr_5d", "corr_20d", "corr_60d", "corr_255d",
        ]
        out = merged[out_cols].copy()
        # Round numeric columns to 4 decimal places — aligns inserted
        # precision with the SQL NUMERIC scale and strips float artifacts
        # from the rolling-sum / correlation math.  DataFrame.round skips
        # object columns (code, date, sec_type, benchmark_code,
        # allocation_effect), leaving them untouched.
        out = out.round(4)
        # Replace ±inf with NaN — rolling correlation can produce inf when
        # one series has zero variance over the window (constant prices).
        # NUMERIC(10,6) rejects inf; sanitize before insertion.
        out = out.replace([np.inf, -np.inf], np.nan)
        # Replace NaN with None so asyncpg serializes them as SQL NULL.
        # astype(object) prevents pandas from converting None back to NaN.
        out = out.astype(object).where(pd.notna(out), None)
        rows = out.to_dict(orient="records")

        n = await bulk_upsert_async(
            conn, TABLE, rows,
            key_columns=["code", "date", "sec_type", "benchmark_code"],
            batch_size=1000,
        )
        total += n
        if (i + 1) % 10 == 0 or (i + 1) == n_subjects:
            print(f"    [{i + 1}/{n_subjects}] {subject_code}: {len(rows):,} rows "
                  f"(cumulative: {total:,})", flush=True)

    return total


# ----------------------------------------------------------------------------
#  Main
# ----------------------------------------------------------------------------

async def main():
    t0 = time.time()
    print_build_header(
        "ANALYZE SEC ALLOC PERF ATTRIBUTION (ETF + INDEX × INDEX)",
        table=TABLE,
        sec_types="etf, index",
        top_n_per_industry=f"{TOP_N_PER_INDUSTRY}",
        min_ohlcv_days=MIN_OHLCV_DAYS,
        allocation_effect="NULL (not computed)",
    )

    conn = await get_db_connection_async()
    try:
        # ---- Step 1: fetch ALL index returns (used as benchmarks) -----
        print("\n[1/6] Fetching all index returns (benchmarks)...",
              flush=True)
        index_returns = await fetch_index_returns(conn)
        n_indices = index_returns["benchmark_code"].nunique() if not index_returns.empty else 0
        print(f"    → {len(index_returns):,} index rows across {n_indices} indices",
              flush=True)

        if index_returns.empty:
            print("    → no index data; exiting.", flush=True)
            return

        # ---- Step 2: fetch composition shared weights + codes-with-comp --
        print("\n[2/6] Fetching composition shared weights (ALL pairs) + "
              "codes-with-composition filter set...", flush=True)
        shared_weights = await fetch_shared_weights(conn)
        print(f"    → {len(shared_weights):,} (subject, benchmark) pairs with shared weights",
              flush=True)
        codes_with_comp = await fetch_codes_with_composition(conn)
        print(f"    → {len(codes_with_comp):,} codes have composition data "
              f"(used to filter subjects)", flush=True)

        # ---- Step 2b: fetch aggregate ETF amount per (date, index) ----
        # Reads precomputed total_etf_amt from stats.index_exts (built by
        # build_index_exts.py). Used to populate benchmark_etf_amount (for all
        # subjects) and code_etf_amount (for index subjects).  ETF subjects
        # get their own amount from fetch_etf_returns instead.
        print("\n[2b/6] Fetching total_etf_amt from stats.index_exts per "
              "(date, tracking_index)...", flush=True)
        etf_amount_by_index = await fetch_etf_amount_by_index(conn)
        if not etf_amount_by_index.empty:
            n_idx_with_etf = etf_amount_by_index["index_code"].nunique()
            print(f"    → {len(etf_amount_by_index):,} rows across "
                  f"{n_idx_with_etf} indices that have tracking ETFs",
                  flush=True)
        else:
            print("    → no ETF→index mapping data; benchmark_etf_amount "
                  "and code_etf_amount (for index subjects) will be NULL.",
                  flush=True)

        # ---- Step 3: truncate -----------------------------------------
        print(f"\n[3/6] Truncating {TABLE}...", flush=True)
        await truncate_table_async(conn, TABLE)

        total = 0

        # ---- Step 3a: ETF subjects -----------------------------------
        # BYPASS: ETF attribution disabled for now — no ETF rows generated.
        # To re-enable, uncomment the block below.
        print("\n[3a/6] ETF subjects BYPASSED — skipping ETF attribution flow.",
              flush=True)
        # etf_codes = await select_etf_subjects(conn, codes_with_comp)
        # if etf_codes:
        #     print(f"\n[3b/6] Fetching ETF daily returns...", flush=True)
        #     etf_returns = await fetch_etf_returns(conn, etf_codes)
        #     print(f"    → {len(etf_returns):,} ETF rows with returns", flush=True)
        #     if not etf_returns.empty:
        #         n = await build_and_insert(conn, etf_returns, index_returns,
        #                                    shared_weights,
        #                                    etf_amount_by_index,
        #                                    sec_type="etf")
        #         total += n
        #         print(f"    → ETF total: {n:,} rows", flush=True)
        # else:
        #     print("    → no ETFs selected; skipping ETF flow.", flush=True)

        # ---- Step 3c: Index subjects ---------------------------------
        print("\n[3c/6] Building Index subjects (indices with composition vs all "
              "indices)...", flush=True)
        # Index subjects: rename columns from benchmark_* to subject_*.
        # NOTE: benchmark_etf_amount is NOT carried in this rename — it is
        # fetched separately via etf_amount_by_index and merged inside
        # build_and_insert (keyed on subject_code as the tracked index).
        index_subject_returns = index_returns.rename(columns={
            "benchmark_code": "code",
            "benchmark_return": "subject_return",
            "benchmark_close": "subject_close",
        })
        # Filter: only include index subjects that have composition data.
        # Many indices (SSE/SZSE-published 000xxx/399xxx, BeSec 899xxx) have
        # NO published composition (only 44 CSI indices have closeweight CSVs).
        # Without this filter, every (subject, benchmark) pair for those
        # indices would have NULL shared_weight, rendering the chart empty.
        before_idx = index_subject_returns["code"].nunique()
        index_subject_returns = index_subject_returns[
            index_subject_returns["code"].isin(codes_with_comp)
        ].copy()
        after_idx = index_subject_returns["code"].nunique()
        print(f"    → {after_idx} of {before_idx} indices have composition data "
              f"(dropped {before_idx - after_idx} without composition)", flush=True)
        if not index_subject_returns.empty:
            n = await build_and_insert(conn, index_subject_returns, index_returns,
                                       shared_weights,
                                       etf_amount_by_index,
                                       sec_type="index")
            total += n
            print(f"    → Index total: {n:,} rows", flush=True)

        print(f"\n    → grand total: {total:,} rows", flush=True)

        # ---- Step 4: upsert analysis_identity -------------------------
        print(f"\n[4/6] Upserting analysis.analysis_identity registry...",
              flush=True)
        await conn.execute(
            """
            INSERT INTO analysis.analysis_identity
                (name, detail_name, summary_name, last_run_datetime, description)
            VALUES ($1, $2, $3, NOW(), $4)
            ON CONFLICT (name) DO UPDATE SET
                detail_name       = EXCLUDED.detail_name,
                summary_name      = EXCLUDED.summary_name,
                last_run_datetime = NOW(),
                description       = EXCLUDED.description
            """,
            ANALYSIS_NAME,
            "sec_alloc_perf_attribution",
            None,
            DESCRIPTION,
        )
        print(f"    → upserted analysis_identity: name={ANALYSIS_NAME!r}, "
              f"detail_name='sec_alloc_perf_attribution', summary_name=NULL",
              flush=True)

        print_wall_time(t0)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
