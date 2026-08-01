"""
analyze_capital_flow.py — Industry-based ETF Capital Flow Analysis
(with broad-market effect removed)

Populates analysis.capital_flow with per-(date, industry_id, benchmark_code)
metrics that quantify each industry's "trending popularity" after stripping
the dilution caused by broad-market ETFs that share overlapping stock holdings
with the industry.

MODEL (overlap-weighted, confirmed by user):
  Given:
    I  = industry ETF trading amount  (stats.etf_trading_amt.total_etf_amt)
    B  = benchmark ETF trading amount (stats.index_exts.total_etf_amt)
    w_i = fraction of INDUSTRY weight on overlap stocks (latest
          stats.sec_composition snapshot; industry composition proxied by
          its representative index = highest total_etf_amt in that industry).
    w_b = fraction of BENCHMARK weight on overlap stocks.
    O_i = I * w_i      (industry-side overlap trading)
    O_b = B * w_b      (benchmark-side overlap trading)
    g_i = industry daily return (amount-weighted avg of all ETFs in the
          industry; fractional = close_t / close_{t-1} - 1)
    g_b = benchmark daily return (index_basic_stats.close pct_change)

  Pure metrics (broad-market effect removed):
    pure_flow         = I * (1 - w_i * O_b / (O_b + O_i))
    pure_growth       = g_i - w_i * g_b
    pure_popularity   = pure_flow * pure_growth
    observed_popularity = I * g_i
    popularity_retention = pure_popularity / observed_popularity

  Example (B=1000mil, I=100mil, w_b=10%, w_i=60%, g_b=2%, g_i=5%):
    O_b=100, O_i=60, broad_share=0.625
    pure_flow = 100 * (1 - 0.6*0.625) = 62.5 mil
    pure_growth = 5% - 0.6*2% = 3.8%
    pure_popularity = 62.5 * 3.8% = 2.375 (47.5% of observed 5.0)

Table is TRUNCATE-then-INSERT on every run (full recompute).

Usage:
  python analyze_capital_flow.py
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


# ----------------------------------------------------------------------------
#  Configuration
# ----------------------------------------------------------------------------

ANALYSIS_NAME = "capital_flow"
TABLE = "analysis.capital_flow"

DESCRIPTION = (
    "Industry-based ETF capital flow with broad-market effect removed. One "
    "row per (date, industry_id, benchmark_code) where benchmark_code is a "
    "broad-market index (from stats.sec_index_tags is_broad_market=TRUE). "
    "MODEL (overlap-weighted proportional attribution): I=industry ETF "
    "trading amt (etf_trading_amt), B=benchmark ETF trading amt (index_exts), "
    "w_i/w_b=overlap weights from latest sec_composition snapshot (industry "
    "composition proxied by representative index = highest total_etf_amt in "
    "that industry), g_i=industry return (amount-weighted avg of ETF returns), "
    "g_b=benchmark index return. Pure metrics: pure_flow=I*(1 - w_i*O_b/"
    "(O_b+O_i)) strips broad-market-driven overlap trading; pure_growth="
    "g_i - w_i*g_b strips broad-market beta; pure_popularity=pure_flow*"
    "pure_growth; observed_popularity=I*g_i; popularity_retention=pure/"
    "observed (<1 means broad market was inflating the industry's apparent "
    "popularity). Example: B=1000mil, I=100mil, w_b=10%, w_i=60%, g_b=2%, "
    "g_i=5% -> pure_flow=62.5mil, pure_growth=3.8%, pure_popularity=2.375 "
    "(47.5% of observed 5.0)."
)


# ----------------------------------------------------------------------------
#  Step 1 — Fetch broad-market benchmark codes
# ----------------------------------------------------------------------------
async def fetch_broad_market_benchmarks(conn) -> list:
    """Return all broad-market index codes (from stats.sec_index_tags)."""
    rows = await conn.fetch("""
        SELECT DISTINCT code
        FROM stats.sec_index_tags
        WHERE is_broad_market = TRUE
        ORDER BY code
    """)
    return [r["code"] for r in rows]


# ----------------------------------------------------------------------------
#  Step 2 — Fetch all industries (from etf_trading_amt.code DISTINCT)
# ----------------------------------------------------------------------------
async def fetch_industries(conn) -> list:
    """Return all industry_ids that have rows in stats.etf_trading_amt."""
    rows = await conn.fetch("""
        SELECT DISTINCT code AS industry_id
        FROM stats.etf_trading_amt
        WHERE total_etf_amt IS NOT NULL
        ORDER BY code
    """)
    return [r["industry_id"] for r in rows]


# ----------------------------------------------------------------------------
#  Step 3 — Fetch representative index per industry
#   The index in that industry with the highest total ETF tracking amount.
#   Used as the industry's composition proxy for overlap-weight computation.
# ----------------------------------------------------------------------------
async def fetch_representative_indices(conn, industries: list) -> dict:
    """For each industry_id, find the index with the highest total_etf_amt.

    Returns: { industry_id: {"index_code": str, "index_name": str} }
    """
    if not industries:
        return {}
    rows = await conn.fetch("""
        WITH industry_indices AS (
            SELECT sc.industry_id, sc.code AS index_code, sc.name AS index_name
            FROM stats.sec_classification sc
            WHERE sc.type = 'index'
              AND sc.industry_id = ANY($1::text[])
        ),
        index_etf_totals AS (
            SELECT ie.code AS index_code, SUM(ie.total_etf_amt) AS total_etf_amt
            FROM stats.index_exts ie
            WHERE ie.total_etf_amt IS NOT NULL
            GROUP BY ie.code
        )
        SELECT DISTINCT ON (ii.industry_id)
            ii.industry_id,
            ii.index_code,
            ii.index_name
        FROM industry_indices ii
        LEFT JOIN index_etf_totals iet ON iet.index_code = ii.index_code
        ORDER BY ii.industry_id,
                 iet.total_etf_amt DESC NULLS LAST,
                 ii.index_code
    """, industries)
    return {
        r["industry_id"]: {
            "index_code": r["index_code"],
            "index_name": r["index_name"],
        }
        for r in rows
    }


# ----------------------------------------------------------------------------
#  Step 4 — Fetch overlap weights for (industry_rep_index, benchmark) pairs
#   Mirrors fetch_shared_weights() in analyze_sec_alloc_perf_attribution.py.
#   Returns: { (subject_code, benchmark_code): (subject_wt_pct, bench_wt_pct) }
# ----------------------------------------------------------------------------
async def fetch_overlap_weights(conn, rep_indices: dict, benchmarks: list) -> dict:
    """Compute shared weights for every (industry_rep_index, benchmark) pair.

    Uses the LATEST composition snapshot in stats.sec_composition for each
    code. For stocks held by BOTH, sums the weights:
      subject_wt  (industry side) = w_i  (Σ industry-rep-index weight on overlap)
      benchmark_wt               = w_b  (Σ benchmark weight on overlap)

    Pairs where both codes have composition data but ZERO overlapping stocks
    are explicitly set to (0, 0) so they appear with explicit zero overlap
    rather than being indistinguishable from pairs that lack composition.

    Returns: { (subject_code, benchmark_code): (w_i_pct, w_b_pct) }
    """
    industry_codes = list({v["index_code"] for v in rep_indices.values()})
    if not industry_codes or not benchmarks:
        return {}

    rows = await conn.fetch("""
        WITH latest AS (
            SELECT code, source_type, MAX(snapshot_date) AS max_date
            FROM stats.sec_composition
            WHERE stock_code IS NOT NULL
            GROUP BY code, source_type
        ),
        holdings AS (
            SELECT sc.code,
                   LEFT(sc.stock_code, 6) AS normalized_code,
                   sc.weight_pct
            FROM stats.sec_composition sc
            JOIN latest ld
              ON sc.code = ld.code
             AND sc.source_type = ld.source_type
             AND sc.snapshot_date = ld.max_date
            WHERE sc.stock_code IS NOT NULL
              AND (sc.code = ANY($1::text[]) OR sc.code = ANY($2::text[]))
        )
        SELECT
            h1.code AS subject_code,
            h2.code AS benchmark_code,
            SUM(h1.weight_pct) AS subject_shared_weight,
            SUM(h2.weight_pct)  AS benchmark_shared_weight
        FROM holdings h1
        JOIN holdings h2 ON h1.normalized_code = h2.normalized_code
        WHERE h1.code != h2.code
          AND h1.code = ANY($1::text[])
          AND h2.code = ANY($2::text[])
        GROUP BY h1.code, h2.code
    """, industry_codes, benchmarks)

    result = {}
    for r in rows:
        sw = float(r["subject_shared_weight"]) if r["subject_shared_weight"] else 0.0
        bw = float(r["benchmark_shared_weight"]) if r["benchmark_shared_weight"] else 0.0
        # Skip NaN (PostgreSQL NUMERIC supports NaN; SUM propagates it)
        if sw != sw or bw != bw:
            continue
        result[(r["subject_code"], r["benchmark_code"])] = (sw, bw)

    # For pairs where both codes have composition data but zero overlapping
    # stocks, set shared weight to (0, 0) — distinguishes "zero overlap"
    # from "no composition data".
    all_codes_rows = await conn.fetch("""
        SELECT DISTINCT code
        FROM stats.sec_composition
        WHERE stock_code IS NOT NULL
    """)
    code_set = {r["code"] for r in all_codes_rows}
    for ic in industry_codes:
        for bc in benchmarks:
            if ic != bc and (ic, bc) not in result and ic in code_set and bc in code_set:
                result[(ic, bc)] = (0.0, 0.0)

    return result


# ----------------------------------------------------------------------------
#  Step 5 — Fetch industry ETF flow (I) from stats.etf_trading_amt
# ----------------------------------------------------------------------------
async def fetch_industry_flow(conn) -> pd.DataFrame:
    """Fetch (date, industry_id, total_etf_amt, etf_num) from etf_trading_amt.

    Returns DataFrame: [date, industry_id, industry_etf_amount, industry_etf_num]
    """
    rows = await conn.fetch("""
        SELECT date,
               code AS industry_id,
               total_etf_amt AS industry_etf_amount,
               etf_num AS industry_etf_num
        FROM stats.etf_trading_amt
        WHERE total_etf_amt IS NOT NULL
    """)
    if not rows:
        return pd.DataFrame(columns=["date", "industry_id",
                                       "industry_etf_amount", "industry_etf_num"])
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["industry_etf_amount"] = pd.to_numeric(df["industry_etf_amount"], errors="coerce")
    df["industry_etf_num"] = df["industry_etf_num"].astype("Int64")
    return df


# ----------------------------------------------------------------------------
#  Step 6 — Fetch industry return (g_i): amount-weighted avg of ETF returns
#   For each (date, industry_id): Σ(etf_return × amount_wan) / Σ(amount_wan)
#   across all ETFs whose parent index's industry_id matches — mirroring
#   build_index_exts.py's stats.etf_trading_amt grouping.
# ----------------------------------------------------------------------------
async def fetch_industry_returns(conn) -> pd.DataFrame:
    """Compute amount-weighted average daily return per (date, industry_id).

    Each ETF's daily return = close_t / close_{t-1} - 1 (fractional),
    using COALESCE(etf_adjustment.adj_close, etf_basic_stats.close) as price.

    The industry_id is resolved via the ETF's parent_index_code →
    stats.sec_classification(type='index').industry_id, mirroring
    build_index_exts.py's industry aggregation. This avoids the sparse
    parent_index_is_primary flag (only ~180 ETFs set it) and matches the
    industry grouping used by stats.etf_trading_amt exactly.

    Returns DataFrame: [date, industry_id, industry_return (fractional)]
    """
    rows = await conn.fetch("""
        SELECT b.date,
               b.code,
               COALESCE(a.adj_close, b.close) AS close,
               l.amount_wan,
               sc_idx.industry_id AS industry_id
        FROM stats.etf_basic_stats b
        LEFT JOIN stats.etf_adjustment a
            ON a.date = b.date AND a.code = b.code
        LEFT JOIN stats.etf_liquidity_margin l
            ON l.date = b.date AND l.code = b.code
        JOIN stats.sec_classification sc_etf
            ON sc_etf.code = b.code AND sc_etf.type = 'etf'
        JOIN stats.sec_classification sc_idx
            ON sc_idx.code = sc_etf.parent_index_code
           AND sc_idx.type = 'index'
        WHERE sc_etf.parent_index_code <> ''
          AND sc_idx.industry_id IS NOT NULL
          AND COALESCE(a.adj_close, b.close) IS NOT NULL
          AND l.amount_wan IS NOT NULL
        ORDER BY sc_idx.industry_id, b.code, b.date
    """)

    if not rows:
        return pd.DataFrame(columns=["date", "industry_id", "industry_return"])

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["amount_wan"] = pd.to_numeric(df["amount_wan"], errors="coerce")

    # Per-ETF fractional return: close_t / close_{t-1} - 1
    df = df.sort_values(["industry_id", "code", "date"]).reset_index(drop=True)
    df["etf_return"] = df.groupby("code")["close"].pct_change()
    # Drop first row per ETF (NULL return — no prior close)
    df = df.dropna(subset=["etf_return", "amount_wan"])

    if df.empty:
        return pd.DataFrame(columns=["date", "industry_id", "industry_return"])

    # Amount-weighted average per (date, industry_id):
    #   Σ(etf_return × amount_wan) / Σ(amount_wan)
    df["weighted_return"] = df["etf_return"] * df["amount_wan"]
    agg = df.groupby(["date", "industry_id"]).agg(
        weighted_sum=("weighted_return", "sum"),
        total_weight=("amount_wan", "sum"),
    ).reset_index()
    # Guard against zero total weight
    agg["industry_return"] = np.where(
        agg["total_weight"] != 0,
        agg["weighted_sum"] / agg["total_weight"],
        np.nan,
    )
    return agg[["date", "industry_id", "industry_return"]]


# ----------------------------------------------------------------------------
#  Step 7 — Fetch benchmark flow (B) + return (g_b)
#   B = stats.index_exts.total_etf_amt for benchmark_code
#   g_b = index_basic_stats.close pct_change (fractional)
# ----------------------------------------------------------------------------
async def fetch_benchmark_data(conn, benchmarks: list) -> pd.DataFrame:
    """Fetch per-(date, benchmark_code) ETF trading amount + fractional return.

    Returns DataFrame: [date, benchmark_code, benchmark_etf_amount,
                        benchmark_etf_num, benchmark_return]
    """
    if not benchmarks:
        return pd.DataFrame(columns=["date", "benchmark_code",
                                       "benchmark_etf_amount",
                                       "benchmark_etf_num", "benchmark_return"])

    # B: total_etf_amt from index_exts
    flow_rows = await conn.fetch("""
        SELECT date,
               code AS benchmark_code,
               total_etf_amt AS benchmark_etf_amount,
               etf_num AS benchmark_etf_num
        FROM stats.index_exts
        WHERE total_etf_amt IS NOT NULL
          AND code = ANY($1::text[])
    """, benchmarks)
    flow_df = pd.DataFrame([dict(r) for r in flow_rows]) if flow_rows else pd.DataFrame()
    if not flow_df.empty:
        flow_df["date"] = pd.to_datetime(flow_df["date"]).dt.date
        flow_df["benchmark_etf_amount"] = pd.to_numeric(
            flow_df["benchmark_etf_amount"], errors="coerce")
        flow_df["benchmark_etf_num"] = flow_df["benchmark_etf_num"].astype("Int64")

    # g_b: close returns from index_basic_stats (fractional)
    ret_rows = await conn.fetch("""
        SELECT b.date, b.code AS benchmark_code, b.close
        FROM stats.index_basic_stats b
        WHERE b.code = ANY($1::text[])
        ORDER BY b.code, b.date
    """, benchmarks)
    if not ret_rows:
        return flow_df if not flow_df.empty else pd.DataFrame(
            columns=["date", "benchmark_code", "benchmark_etf_amount",
                      "benchmark_etf_num", "benchmark_return"])

    ret_df = pd.DataFrame([dict(r) for r in ret_rows])
    ret_df["date"] = pd.to_datetime(ret_df["date"]).dt.date
    ret_df["close"] = pd.to_numeric(ret_df["close"], errors="coerce")
    ret_df = ret_df.sort_values(["benchmark_code", "date"]).reset_index(drop=True)
    ret_df["benchmark_return"] = ret_df.groupby("benchmark_code")["close"].pct_change()
    ret_df = ret_df.dropna(subset=["benchmark_return"])
    ret_df = ret_df[["date", "benchmark_code", "benchmark_return"]]

    # Outer merge — keeps rows where only flow OR return exists
    if flow_df.empty:
        return ret_df
    return flow_df.merge(ret_df, on=["date", "benchmark_code"], how="outer")


# ----------------------------------------------------------------------------
#  Step 8 — Build + insert (cross-join industries × benchmarks on date)
# ----------------------------------------------------------------------------
async def build_and_insert(
    conn,
    industry_flow: pd.DataFrame,
    industry_returns: pd.DataFrame,
    benchmark_data: pd.DataFrame,
    rep_indices: dict,
    overlap_weights: dict,
    benchmarks: list,
) -> int:
    """Cross-join industries with benchmarks on date, compute pure metrics,
    and bulk-insert into analysis.capital_flow.

    Returns total rows inserted.
    """
    if industry_flow.empty or benchmark_data.empty:
        print("    → no industry flow or benchmark data; nothing to insert.",
              flush=True)
        return 0

    # ---- Lookup maps for display labels -------------------------------
    bench_label_rows = await conn.fetch("""
        SELECT DISTINCT ON (code) code, name
        FROM stats.index_identity
        WHERE code = ANY($1::text[])
        ORDER BY code, date DESC
    """, benchmarks)
    bench_labels = {r["code"]: r["name"] for r in bench_label_rows}

    industry_label_rows = await conn.fetch("""
        SELECT DISTINCT industry_id, industry_label
        FROM stats.sec_classification
        WHERE type = 'index' AND industry_id IS NOT NULL
    """)
    industry_labels = {r["industry_id"]: r["industry_label"]
                       for r in industry_label_rows}

    # ---- Merge industry flow + returns -------------------------------
    industry_df = industry_flow.merge(
        industry_returns, on=["date", "industry_id"], how="left")

    # ---- Cross-join with benchmarks on date ---------------------------
    # For each (date, industry), pair with every benchmark that has data
    # on the same date. This produces one row per (date, industry, benchmark).
    merged = industry_df.merge(benchmark_data, on="date", how="inner")
    if merged.empty:
        print("    → no overlapping dates between industry and benchmark data.",
              flush=True)
        return 0

    print(f"    → {len(merged):,} rows after cross-join "
          f"({industry_df['industry_id'].nunique()} industries × "
          f"{benchmark_data['benchmark_code'].nunique()} benchmarks × "
          f"{merged['date'].nunique()} dates)", flush=True)

    # ---- Attach overlap weights (constant per industry-benchmark pair) -
    # rep_indices: industry_id -> {index_code, index_name}
    # overlap_weights: (rep_index_code, benchmark_code) -> (w_i_pct, w_b_pct)
    def _lookup_wt(row):
        rep = rep_indices.get(row["industry_id"])
        if not rep:
            return (None, None)
        return overlap_weights.get((rep["index_code"], row["benchmark_code"]),
                                   (None, None))

    wt = merged.apply(_lookup_wt, axis=1)
    merged["industry_overlap_weight"] = [w[0] for w in wt]   # w_i in percent
    merged["benchmark_overlap_weight"] = [w[1] for w in wt]  # w_b in percent

    # Drop rows where overlap weights are NULL (no composition data for the
    # representative index or benchmark). These pairs can't have their
    # broad-market effect computed.
    before = len(merged)
    merged = merged.dropna(subset=["industry_overlap_weight",
                                    "benchmark_overlap_weight"])
    after = len(merged)
    if before > after:
        print(f"    → dropped {before - after:,} rows without overlap data",
              flush=True)

    if merged.empty:
        print("    → no rows after overlap filter.", flush=True)
        return 0

    # ---- Compute overlap amounts (O_i, O_b) in yuan -------------------
    # Weights are stored as percent (0-100); divide by 100 for fractional.
    w_i_frac = merged["industry_overlap_weight"] / 100.0
    w_b_frac = merged["benchmark_overlap_weight"] / 100.0

    # O_i = I * w_i_frac; O_b = B * w_b_frac
    # industry_etf_amount / benchmark_etf_amount may be NULL when the
    # benchmark has no tracking ETF on this date — fill with 0 so the
    # arithmetic doesn't propagate NaN.
    I_amt = merged["industry_etf_amount"].fillna(0)
    B_amt = merged["benchmark_etf_amount"].fillna(0)
    merged["industry_overlap_amount"] = I_amt * w_i_frac
    merged["benchmark_overlap_amount"] = B_amt * w_b_frac

    # ---- Pure metrics (the analysis output) ---------------------------
    O_i = merged["industry_overlap_amount"]
    O_b = merged["benchmark_overlap_amount"]
    O_total = O_i + O_b

    # broad_share = O_b / (O_b + O_i), guarded against div-by-zero
    broad_share = np.where(O_total > 0, O_b / O_total, 0.0)

    # pure_flow = I * (1 - w_i * broad_share)
    # When O_total = 0 (no overlap), broad_share = 0 → pure_flow = I
    merged["pure_flow"] = I_amt * (1 - w_i_frac * broad_share)

    # pure_growth = g_i - w_i * g_b (fractional)
    g_i = merged["industry_return"]
    g_b = merged["benchmark_return"]
    merged["pure_growth"] = g_i - (w_i_frac * g_b)

    # pure_popularity = pure_flow * pure_growth
    merged["pure_popularity"] = merged["pure_flow"] * merged["pure_growth"]

    # observed_popularity = I * g_i
    merged["observed_popularity"] = I_amt * g_i

    # popularity_retention = pure / observed (NULL when observed is 0/NULL)
    observed = merged["observed_popularity"]
    merged["popularity_retention"] = np.where(
        observed.notna() & (observed != 0),
        merged["pure_popularity"] / observed,
        np.nan,
    )

    # ---- Labels --------------------------------------------------------
    merged["industry_label"] = merged["industry_id"].map(industry_labels).fillna("")
    merged["benchmark_label"] = merged["benchmark_code"].map(bench_labels).fillna("")

    # ---- Select output columns + sanitize ------------------------------
    out_cols = [
        "date", "industry_id", "benchmark_code",
        "industry_label", "benchmark_label",
        "industry_etf_amount", "industry_etf_num", "industry_return",
        "benchmark_etf_amount", "benchmark_etf_num", "benchmark_return",
        "industry_overlap_weight", "benchmark_overlap_weight",
        "industry_overlap_amount", "benchmark_overlap_amount",
        "pure_flow", "pure_growth", "pure_popularity",
        "observed_popularity", "popularity_retention",
    ]
    out = merged[out_cols].copy()

    # Round numeric columns to 6 decimals — aligns with SQL NUMERIC scale
    # and strips float artifacts.
    out = out.round(6)
    # Replace ±inf with NaN — division by zero can produce inf.
    out = out.replace([np.inf, -np.inf], np.nan)
    # Replace NaN with None so asyncpg serializes them as SQL NULL.
    # astype(object) prevents pandas from converting None back to NaN.
    out = out.astype(object).where(pd.notna(out), None)

    rows = out.to_dict(orient="records")
    n = await bulk_upsert_async(
        conn, TABLE, rows,
        key_columns=["date", "industry_id", "benchmark_code"],
        batch_size=2000,
    )
    return n


# ----------------------------------------------------------------------------
#  Main
# ----------------------------------------------------------------------------
async def main():
    t0 = time.time()
    print_build_header(
        "ANALYZE CAPITAL FLOW (INDUSTRY × BROAD-MARKET)",
        table=TABLE,
        model="overlap-weighted proportional attribution",
    )

    conn = await get_db_connection_async()
    try:
        # ---- Step 1: broad-market benchmarks ----------------------------
        print("\n[1/6] Fetching broad-market benchmarks...", flush=True)
        benchmarks = await fetch_broad_market_benchmarks(conn)
        print(f"    → {len(benchmarks)} broad-market benchmark codes",
              flush=True)
        if not benchmarks:
            print("    → no broad-market benchmarks; exiting.", flush=True)
            return

        # ---- Step 2: industries + representative indices ----------------
        print("\n[2/6] Fetching industries + representative indices...",
              flush=True)
        industries = await fetch_industries(conn)
        print(f"    → {len(industries)} industries with ETF flow data",
              flush=True)
        rep_indices = await fetch_representative_indices(conn, industries)
        print(f"    → {len(rep_indices)} industries have a representative index",
              flush=True)

        # ---- Step 3: overlap weights -----------------------------------
        print("\n[3/6] Fetching overlap weights (industry rep × benchmark)...",
              flush=True)
        overlap_weights = await fetch_overlap_weights(conn, rep_indices, benchmarks)
        print(f"    → {len(overlap_weights):,} (industry_index, benchmark) pairs "
              f"with weights", flush=True)

        # ---- Step 4: industry ETF flow + returns ------------------------
        print("\n[4/6] Fetching industry ETF flow + returns...", flush=True)
        industry_flow = await fetch_industry_flow(conn)
        print(f"    → {len(industry_flow):,} industry flow rows", flush=True)
        industry_returns = await fetch_industry_returns(conn)
        print(f"    → {len(industry_returns):,} industry return rows",
              flush=True)

        # ---- Step 5: benchmark flow + returns ---------------------------
        print("\n[5/6] Fetching benchmark flow + returns...", flush=True)
        benchmark_data = await fetch_benchmark_data(conn, benchmarks)
        n_bench = (benchmark_data["benchmark_code"].nunique()
                   if not benchmark_data.empty else 0)
        print(f"    → {len(benchmark_data):,} benchmark rows across "
              f"{n_bench} benchmarks", flush=True)

        # ---- Step 6: truncate + build + insert -------------------------
        print(f"\n[6/6] Truncating {TABLE}...", flush=True)
        await truncate_table_async(conn, TABLE)

        print("\n    Building + inserting...", flush=True)
        n = await build_and_insert(
            conn, industry_flow, industry_returns, benchmark_data,
            rep_indices, overlap_weights, benchmarks,
        )
        print(f"    → grand total: {n:,} rows", flush=True)

        # ---- Upsert analysis_identity ---------------------------------
        print(f"\n[done] Upserting analysis.analysis_identity registry...",
              flush=True)
        await conn.execute(
            """
            INSERT INTO analysis.analysis_identity
                (name, detail_name, summary_name, last_run_datetime, description)
            VALUES ($1, $2, NULL, NOW(), $3)
            ON CONFLICT (name) DO UPDATE SET
                detail_name       = EXCLUDED.detail_name,
                summary_name      = EXCLUDED.summary_name,
                last_run_datetime = NOW(),
                description       = EXCLUDED.description
            """,
            ANALYSIS_NAME,
            "capital_flow",
            DESCRIPTION,
        )
        print(f"    → upserted analysis_identity: name={ANALYSIS_NAME!r}, "
              f"detail_name='capital_flow', summary_name=NULL", flush=True)

        print_wall_time(t0)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
