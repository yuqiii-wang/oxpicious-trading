"""Async DB fetch primitives for analyze.sec_alloc_perf_attribution.

Reads composition shared weights (subject x benchmark pairs), benchmark
index closes, and aggregate ETF turnover per (date, tracking_index).
"""
from __future__ import annotations

import datetime
from typing import Optional

import numpy as np
import pandas as pd

from _common.build_commons import rec_col
from _common.df_utils import epoch_ns_array
from analyze.sec_alloc_perf_attribution.config import TOP_N_NON_BROAD


# ---------------------------------------------------------------------------
#  GPU-safe frame construction from asyncpg rows
#
#  asyncpg returns python objects (datetime.date, Decimal, str).  Building
#  ``pd.DataFrame([dict(r) for r in rows])`` from them creates OBJECT-dtype
#  columns — cudf's constructor rejects object dates (MixedTypeError) and
#  falls back to a CPU pandas frame whose string columns are arrow-backed
#  ExtensionArrays.  From that point EVERY downstream op runs on CPU
#  (unique/isin/==/map/join all print [cudf fallback]).
#
#  Instead, build each column as a TYPED numpy array (or python str list,
#  which the cudf constructor turns into a native cudf string column) so
#  the DataFrame is constructed cudf-native on the GPU:
#    - dates   -> float8 epoch-seconds in SQL (extract(epoch)::float8)
#                 materialized as RAW host datetime64[ns] via
#                 epoch_ns_array (wide-op boundary — ns matches the
#                 pandas-native Timestamps these frames merge against)
#    - numerics-> float64 (Decimal -> float via list comp)
#    - codes   -> python str list (cudf string column)
#  No ``pd.to_datetime``/``pd.to_numeric`` calls needed at all.
# ---------------------------------------------------------------------------
def _floats(rows, key: str) -> np.ndarray:
    return np.asarray(
        [float(r[key]) if r[key] is not None else np.nan for r in rows],
        dtype="float64",
    )


async def fetch_codes_with_composition(conn) -> set:
    """Return the set of codes (with exchange suffix for ETFs, bare for
    indices) that have at least one non-cash holding in the LATEST
    snapshot of stats.sec_composition.

    This is used to filter subjects to only those with real composition
    data, so the resulting perf-attr rows actually have shared_weight
    values populated.

    Background: many ETFs (cross-border ETFs like 159920 恒生ETF) and many
    indices (SSE/SZSE-published 000xxx/399xxx that aren't CSI indices)
    have NO published composition. Their SZSE composition CSVs contain
    only a cash placeholder row (cash_sub_flag='必须') that the build
    script filters out, leaving sec_composition empty for those codes.
    Without this filter, every perf-attr row for such a subject would
    have NULL shared_weight and the "Fluctuation Attribution" chart
    would render zero bars.
    """
    rows = await conn.fetch("""
        SELECT DISTINCT code
        FROM stats.sec_composition
        WHERE stock_code IS NOT NULL
          AND source_type IN ('etf', 'index')
    """)
    return {r["code"] for r in rows}


async def fetch_shared_weights(conn) -> dict:
    """Compute shared weight for every (subject, benchmark) pair.

    Uses the LATEST composition snapshot in stats.sec_composition for each
    code (both ETF and index source_types).  For stocks held by BOTH,
    sums the weights:
      code_sec_shared_weight      = Sum w_subject   on shared stocks
      benchmark_sec_shared_weight = Sum w_benchmark on shared stocks

    Computes ALL pairs (ETFxIndex, IndexxIndex, ETFxETF, IndexxETF) in one
    query.  Callers look up only the pairs they need.

    Pairs where both codes have composition data but ZERO overlapping
    stocks (e.g. CSI 300 vs CSI 500 — disjoint by design) are explicitly
    set to (0, 0) so they appear in the chart with zero-height bars
    instead of being indistinguishable from pairs that lack composition
    data entirely.

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


async def fetch_index_closes(
    conn, start_date: Optional[datetime.date] = None
) -> pd.DataFrame:
    """Fetch daily close prices for indices used as benchmarks (and as
    subject candidates).

    Benchmark pool:
      1. ALL broad-market indices (stats.sec_classification.sector_id='BROAD')
         — kept in full because they are the primary market benchmarks.
      2. Per-industry top-N highest-traded NON-broad indices. For each
         industry_id, the top TOP_N_NON_BROAD indices (sector_id NOT IN
         ('BROAD', 'DEBT')) are kept, ranked by aggregate ETF turnover
         (SUM(stats.index_exts.total_etf_trading_amount) over all dates).
         This ensures every industry is represented by its most liquid
         indices, bounding the subject x benchmark cross product while
         preserving sector diversity.
      3. DEBT-sector indices are always excluded.

    Args:
        conn: asyncpg connection.
        start_date: if provided, only fetch rows with date >= start_date.
            Used to limit data to the lookback window for incremental
            single-date rebuilds.

    Returns DataFrame: [benchmark_code, date, benchmark_close]
      - benchmark_close = the index close (used for downstream rolling-
        correlation computation against subject closes).
      - benchmark_etf_trading_amount is NOT fetched here — it is fetched
        separately by fetch_etf_amount_by_index() and merged in
        build_and_insert() because the new semantics aggregate ETF
        turnover tracking the index (via
        stats.sec_classification.parent_index_code), not the index's own
        turnover.
    """
    # Build query with optional date filter.
    date_where: str = ""
    params: list = [TOP_N_NON_BROAD]
    if start_date is not None:
        date_where = "AND b.date >= $2"
        params.append(start_date)

    sql = f"""
        WITH broad_codes AS (
            -- All broad-market indices (kept in full)
            SELECT b.code
            FROM stats.index_basic_stats b
            JOIN stats.sec_classification sc
                ON sc.code = b.code AND sc.type = 'index'
            WHERE sc.sector_id = 'BROAD'
              AND sc.is_active = TRUE
        ),
        non_broad_ranked AS (
            -- Non-broad, non-debt indices ranked WITHIN each industry
            -- by aggregate ETF turnover.
            SELECT b.code,
                   sc.industry_id,
                   SUM(ie.total_etf_trading_amount) AS total_amt,
                   ROW_NUMBER() OVER (
                       PARTITION BY sc.industry_id
                       ORDER BY SUM(ie.total_etf_trading_amount) DESC NULLS LAST
                   ) AS rn
            FROM stats.index_basic_stats b
            JOIN stats.sec_classification sc
                ON sc.code = b.code AND sc.type = 'index'
            LEFT JOIN stats.index_exts ie
                ON ie.code = b.code
            WHERE sc.sector_id NOT IN ('BROAD', 'DEBT')
              AND sc.is_active = TRUE
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
            extract(epoch from b.date)::float8 AS date,
            b.close::float8 AS close
        FROM stats.index_basic_stats b
        JOIN kept_codes kc ON kc.code = b.code
        WHERE 1=1 {date_where}
        ORDER BY b.code, b.date
    """

    rows = await conn.fetch(sql, *params)

    if not rows:
        return pd.DataFrame(
            columns=["benchmark_code", "date", "benchmark_close"]
        )

    df = pd.DataFrame({
        "benchmark_code": [r["benchmark_code"] for r in rows],
        "date": epoch_ns_array(rec_col(rows, "date")),
        "close": _floats(rows, "close"),
    })
    df = df.sort_values(["benchmark_code", "date"]).reset_index(drop=True)
    # Rename close -> benchmark_close for clarity (used for rolling correlation).
    df = df.rename(columns={"close": "benchmark_close"})
    return df[["benchmark_code", "date", "benchmark_close"]]


async def fetch_index_subject_closes(
    conn, start_date: Optional[datetime.date] = None
) -> pd.DataFrame:
    """Fetch daily close prices for ALL compositioned non-broad non-debt
    indices used as SUBJECTS.

    Subject pool:
      ALL non-broad, non-debt indices that have composition data in
      stats.sec_composition (stock_code IS NOT NULL, source_type='index').
      This is the full universe needed for the Intraday Attribution view
      to show all industries at a selected tick.

    Key difference from fetch_index_closes():
      - fetch_index_closes() returns the BENCHMARK pool (top-N per
        industry by ETF turnover + all broad-market indices) — used by
        the Market Movements top plot shades.
      - fetch_index_subject_closes() returns the FULL SUBJECT pool (all
        compositioned indices) — used by the Intraday Attribution view.
      - Both pools share the same broad-market indices (they appear in
        both benchmark and subject roles).

    Args:
        conn: asyncpg connection.
        start_date: if provided, only fetch rows with date >= start_date.
            Used to limit data to the lookback window for incremental
            single-date rebuilds.

    Returns DataFrame: [code, date, subject_close]
    """
    date_where: str = ""
    params: list = []
    if start_date is not None:
        date_where = "AND b.date >= $1"
        params.append(start_date)

    sql = f"""
        WITH subject_codes AS (
            SELECT DISTINCT b.code
            FROM stats.index_basic_stats b
            JOIN stats.sec_classification sc
                ON sc.code = b.code AND sc.type = 'index'
            WHERE sc.sector_id NOT IN ('BROAD', 'DEBT')
              AND sc.is_active = TRUE
              AND sc.industry_id IS NOT NULL
              AND sc.industry_id <> ''
              AND EXISTS (
                  SELECT 1 FROM stats.sec_composition scm
                  WHERE scm.code = b.code
                    AND scm.stock_code IS NOT NULL
                    AND scm.source_type = 'index'
              )
        )
        SELECT
            b.code,
            extract(epoch from b.date)::float8 AS date,
            b.close::float8 AS subject_close
        FROM stats.index_basic_stats b
        JOIN subject_codes sc ON sc.code = b.code
        WHERE 1=1 {date_where}
        ORDER BY b.code, b.date
    """

    rows = await conn.fetch(sql, *params)

    if not rows:
        return pd.DataFrame(
            columns=["code", "date", "subject_close"]
        )

    df = pd.DataFrame({
        "code": [r["code"] for r in rows],
        "date": epoch_ns_array(rec_col(rows, "date")),
        "subject_close": _floats(rows, "subject_close"),
    })
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    return df[["code", "date", "subject_close"]]


async def fetch_etf_amount_by_index(
    conn, start_date: Optional[datetime.date] = None
) -> pd.DataFrame:
    """Aggregate ETF turnover per (date, tracking_index) — used to populate
    benchmark_etf_trading_amount AND code_etf_trading_amount for index subjects.

    Reads the precomputed total_etf_trading_amount from stats.index_exts
    (built by build_index_exts.py = Sum etf_liquidity_margin.trading_amount
    across ALL ETFs whose stats.sec_classification.parent_index_code =
    index_code on that date). index_exts only stores rows where (date,
    code) exists in stats.index_identity, but every benchmark / index
    subject used by this analysis comes from index_identity, so no values
    are lost.

    This is the "index amount" semantics: instead of using the index's
    own turnover (stats.index_basic_stats.trading_amount, which includes
    ALL market participants — stocks, futures, etc.), we use the ETF-
    market turnover tracking the index — a tighter measure of ETF-market
    liquidity for that benchmark. For index subjects, code_etf_trading_amount
    is this aggregate keyed on the subject code; for benchmark rows,
    benchmark_etf_trading_amount is this aggregate keyed on the benchmark
    code.

    Caveats:
      - Indices with no tracking ETF (e.g. 000001 上证指数, 399001 深证成指)
        have NO rows in this DataFrame -> their benchmark_etf_trading_amount
        will be NULL after the merge, and
        etf_trading_amount_ratio_benchmark_to_code will also be NULL.
      - The ETF universe grows over time as new ETFs are listed, so the
        aggregate for an index mechanically trends upward — this is a known
        bias of the metric (a liquidity view, not a price-attribution view).

    Args:
        conn: asyncpg connection.
        start_date: if provided, only fetch rows with date >= start_date.
            Used to limit data to the lookback window for incremental
            single-date rebuilds.

    Returns DataFrame: [index_code, date, etf_amount]
      - etf_amount is in yuan.
    """
    date_where: str = ""
    params: list = []
    if start_date is not None:
        date_where = "AND date >= $1"
        params.append(start_date)

    sql = f"""
        SELECT code AS index_code,
               extract(epoch from date)::float8 AS date,
               total_etf_trading_amount::float8 AS etf_amount
        FROM stats.index_exts
        WHERE total_etf_trading_amount IS NOT NULL
        {date_where}
    """

    rows = await conn.fetch(sql, *params)

    if not rows:
        return pd.DataFrame(columns=["index_code", "date", "etf_amount"])

    df = pd.DataFrame({
        "index_code": [r["index_code"] for r in rows],
        "date": epoch_ns_array(rec_col(rows, "date")),
        "etf_amount": _floats(rows, "etf_amount"),
    })
    return df[["index_code", "date", "etf_amount"]]
