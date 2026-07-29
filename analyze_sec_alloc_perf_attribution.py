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
  benchmark_amount   = stats.index_basic_stats.amount × 1e8     (yuan; src=亿元)
  code_amount        = stats.etf_liquidity_margin.amount_wan×1e4 (yuan; src=万元)
  amount_ratio_benchmark_to_code = GENERATED column (not inserted)

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
    "subject and benchmark). benchmark_amount from stats.index_basic_stats."
    "amount × 1e8 (亿元→yuan); code_amount from etf_liquidity_margin."
    "amount_wan × 1e4 (etf) or index_basic_stats.amount × 1e8 (index). "
    "Both in yuan so amount_ratio_benchmark_to_code (GENERATED ALWAYS column) "
    "is a meaningful amount ratio. NOTE: benchmark indices WITHOUT composition "
    "data are still included as benchmarks (benchmark_return + amount are "
    "meaningful on their own); only their shared_weight columns are NULL."
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

    Returns DataFrame: [code, date, subject_return, code_amount]
      - First row per code (NULL return) is dropped.
      - code_amount = etf_liquidity_margin.amount_wan * 10000 (万元→yuan;
        NULL when no liquidity_margin row).
    """
    if not codes:
        return pd.DataFrame(columns=["code", "date", "subject_return", "code_amount"])

    rows = await conn.fetch("""
        SELECT
            b.code,
            b.date,
            COALESCE(a.adj_close, b.close) AS price,
            l.amount_wan * 10000 AS code_amount
        FROM stats.etf_basic_stats b
        LEFT JOIN stats.etf_adjustment a
            ON a.date = b.date AND a.code = b.code
        LEFT JOIN stats.etf_liquidity_margin l
            ON l.date = b.date AND l.code = b.code
        WHERE b.code = ANY($1::text[])
        ORDER BY b.code, b.date
    """, codes)

    if not rows:
        return pd.DataFrame(columns=["code", "date", "subject_return", "code_amount"])

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["code_amount"] = pd.to_numeric(df["code_amount"], errors="coerce")
    df = df.sort_values(["code", "date"]).reset_index(drop=True)

    # subject_return = today's price - previous trading day's price (per code).
    df["subject_return"] = df.groupby("code")["price"].diff()
    # Drop first row per code (NULL return — no previous day).
    df = df.dropna(subset=["subject_return"])
    return df[["code", "date", "subject_return", "code_amount"]]


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
    return result

async def fetch_index_returns(conn) -> pd.DataFrame:
    """Fetch daily returns for ALL indices in stats.index_basic_stats.

    benchmark_return = close_t - close_{t-1}  (absolute points diff, per
    code ordered by date).

    Returns DataFrame: [benchmark_code, date, benchmark_return, benchmark_amount]
      - First row per code (NULL return) is dropped.
      - benchmark_amount = index_basic_stats.amount * 1e8 (亿元→yuan).
    """
    rows = await conn.fetch("""
        SELECT
            b.code AS benchmark_code,
            b.date,
            b.close,
            b.amount * 100000000 AS benchmark_amount
        FROM stats.index_basic_stats b
        ORDER BY b.code, b.date
    """)

    if not rows:
        return pd.DataFrame(
            columns=["benchmark_code", "date", "benchmark_return", "benchmark_amount"]
        )

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["benchmark_amount"] = pd.to_numeric(df["benchmark_amount"], errors="coerce")
    df = df.sort_values(["benchmark_code", "date"]).reset_index(drop=True)

    df["benchmark_return"] = df.groupby("benchmark_code")["close"].diff()
    df = df.dropna(subset=["benchmark_return"])
    return df[["benchmark_code", "date", "benchmark_return", "benchmark_amount"]]


# ----------------------------------------------------------------------------
#  Step 4 — Build + insert attribution rows (per ETF to bound memory)
# ----------------------------------------------------------------------------

async def build_and_insert(conn, subject_returns: pd.DataFrame,
                           index_returns: pd.DataFrame,
                           shared_weights: dict,
                           sec_type: str) -> int:
    """For each subject code, cross-join with all indices on date, insert.

    Processes one subject at a time to bound memory.

    For sec_type='index': excludes self-pairs (code == benchmark_code).

    shared_weights: dict from fetch_shared_weights() —
      { (subject_code, benchmark_code): (code_wt, bench_wt) }

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

    total = 0
    subject_codes = sorted(subject_returns["code"].unique())

    for i, subject_code in enumerate(subject_codes):
        sub = subject_returns[subject_returns["code"] == subject_code]
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

        out_cols = [
            "code", "date", "sec_type", "benchmark_code",
            "subject_return", "benchmark_return", "active_return",
            "allocation_effect",
            "code_sec_shared_weight", "benchmark_sec_shared_weight",
            "benchmark_amount", "code_amount",
        ]
        out = merged[out_cols].copy()
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
        print("\n[1/5] Fetching all index returns (benchmarks)...",
              flush=True)
        index_returns = await fetch_index_returns(conn)
        n_indices = index_returns["benchmark_code"].nunique() if not index_returns.empty else 0
        print(f"    → {len(index_returns):,} index rows across {n_indices} indices",
              flush=True)

        if index_returns.empty:
            print("    → no index data; exiting.", flush=True)
            return

        # ---- Step 2: fetch composition shared weights + codes-with-comp --
        print("\n[2/5] Fetching composition shared weights (ALL pairs) + "
              "codes-with-composition filter set...", flush=True)
        shared_weights = await fetch_shared_weights(conn)
        print(f"    → {len(shared_weights):,} (subject, benchmark) pairs with shared weights",
              flush=True)
        codes_with_comp = await fetch_codes_with_composition(conn)
        print(f"    → {len(codes_with_comp):,} codes have composition data "
              f"(used to filter subjects)", flush=True)

        # ---- Step 3: truncate -----------------------------------------
        print(f"\n[3/5] Truncating {TABLE}...", flush=True)
        await truncate_table_async(conn, TABLE)

        total = 0

        # ---- Step 3a: ETF subjects -----------------------------------
        print("\n[3a/5] Selecting ETF subjects (top-3 by volume per industry, "
              "with composition data)...", flush=True)
        etf_codes = await select_etf_subjects(conn, codes_with_comp)
        if etf_codes:
            print(f"\n[3b/5] Fetching ETF daily returns...", flush=True)
            etf_returns = await fetch_etf_returns(conn, etf_codes)
            print(f"    → {len(etf_returns):,} ETF rows with returns", flush=True)
            if not etf_returns.empty:
                n = await build_and_insert(conn, etf_returns, index_returns,
                                           shared_weights, sec_type="etf")
                total += n
                print(f"    → ETF total: {n:,} rows", flush=True)
        else:
            print("    → no ETFs selected; skipping ETF flow.", flush=True)

        # ---- Step 3c: Index subjects ---------------------------------
        print("\n[3c/5] Building Index subjects (indices with composition vs all "
              "indices)...", flush=True)
        # Index subjects: rename columns from benchmark_* to subject_*.
        index_subject_returns = index_returns.rename(columns={
            "benchmark_code": "code",
            "benchmark_return": "subject_return",
            "benchmark_amount": "code_amount",
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
                                       shared_weights, sec_type="index")
            total += n
            print(f"    → Index total: {n:,} rows", flush=True)

        print(f"\n    → grand total: {total:,} rows", flush=True)

        # ---- Step 4: upsert analysis_identity -------------------------
        print(f"\n[4/5] Upserting analysis.analysis_identity registry...",
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
