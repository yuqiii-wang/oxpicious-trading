"""DB fetch primitives for builds.cross_stats (pair grain).

Ported from analyze.sec_alloc_perf_attribution.fetch (2026-09-04) with the
config import re-pointed; logic unchanged for parity. Reads composition
shared weights (subject x benchmark pairs), benchmark index closes, and
aggregate ETF turnover per (date, tracking_index).

GPU-safe frame construction: asyncpg returns python objects (date, Decimal,
str) — building ``pd.DataFrame([dict(r) for r in rows])`` would create
OBJECT-dtype columns that poison cudf (MixedTypeError → CPU fallback
cascade). Instead every column is built as a TYPED numpy array (or python
str list → native cudf string column):
  - dates    -> float8 epoch-seconds in SQL (extract(epoch)::float8)
                materialized as raw host datetime64[ns] via epoch_ns_array
  - numerics -> float64 (Decimal → float via list comp)
  - codes    -> python str list (cudf string column)
"""
from __future__ import annotations

import datetime
from typing import Optional

import numpy as np
import pandas as pd

from _common.build_commons import rec_col
from _common.df_utils import epoch_ns_array
from builds.cross_stats.config import TOP_N_NON_BROAD


def _floats(rows, key: str) -> np.ndarray:
    return np.asarray(
        [float(r[key]) if r[key] is not None else np.nan for r in rows],
        dtype="float64",
    )


async def fetch_codes_with_composition(conn) -> set:
    """Codes with at least one non-cash holding in the LATEST
    stats.sec_composition snapshot (source_type IN ('etf','index')).

    Used to filter subjects to only those with real composition data so
    perf-attr rows actually carry shared weights (cross-border ETFs and
    SSE/SZSE-published indices without published compositions would
    otherwise emit NULL-weight rows and render zero-height chart bars).
    """
    rows = await conn.fetch("""
        SELECT DISTINCT code
        FROM stats.sec_composition
        WHERE stock_code IS NOT NULL
          AND source_type IN ('etf', 'index')
    """)
    return {r["code"] for r in rows}


async def fetch_shared_weights(conn) -> dict:
    """Shared weight for every (subject, benchmark) pair.

    Uses the LATEST composition snapshot per (code, source_type). For
    stocks held by BOTH:
      code_sec_shared_weight      = Σ w_subject   on shared stocks
      benchmark_sec_shared_weight = Σ w_benchmark on shared stocks
    All pair families in one query; callers look up the pairs they need.

    Pairs with composition data but ZERO overlapping stocks are set to
    (0, 0) explicitly — disjoint indices (CSI 300 vs CSI 500) appear in
    charts with explicit zero bars instead of being indistinguishable
    from pairs lacking composition data.

    Returns dict: {(subject_code, benchmark_code): (code_wt, bench_wt)}
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

    result: dict = {}
    for r in rows:
        csw = float(r["code_sec_shared_weight"])
        bsw = float(r["benchmark_sec_shared_weight"])
        # Skip NaN (PostgreSQL NUMERIC supports NaN; SUM propagates it)
        if csw != csw or bsw != bsw:
            continue
        result[(r["subject_code"], r["benchmark_code"])] = (csw, bsw)

    # [PERF-BLOCKER — declared] O(N^2) zero-fill loop: explicit (0,0) for
    # every compositioned pair without overlap (~1M dict writes once per
    # run, ~1s). Vectorizing needs a cross-join frame that costs more.
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
    """Daily closes for the BENCHMARK pool.

      1. ALL broad-market indices (sec_classification.sector_id='BROAD').
      2. Per-industry top-TOP_N_NON_BROAD non-broad indices ranked by
         aggregate ETF turnover (stats.index_exts.total_etf_trading_amount).
      3. DEBT-sector indices always excluded.

    Returns DataFrame [benchmark_code, date, benchmark_close].
    """
    date_where: str = ""
    params: list = [TOP_N_NON_BROAD]
    if start_date is not None:
        date_where = "AND b.date >= $2"
        params.append(start_date)

    sql = f"""
        WITH broad_codes AS (
            SELECT b.code
            FROM stats.index_basic_stats b
            JOIN stats.sec_classification sc
                ON sc.code = b.code AND sc.type = 'index'
            WHERE sc.sector_id = 'BROAD'
              AND sc.is_active = TRUE
        ),
        non_broad_ranked AS (
            SELECT b.code,
                   sc.industry_id,
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
            SELECT code FROM non_broad_ranked WHERE rn <= $1
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
        "benchmark_close": _floats(rows, "close"),
    })
    df = df.sort_values(["benchmark_code", "date"]).reset_index(drop=True)
    return df[["benchmark_code", "date", "benchmark_close"]]


async def fetch_index_subject_closes(
    conn, start_date: Optional[datetime.date] = None
) -> pd.DataFrame:
    """Daily closes for ALL compositioned non-broad non-debt indices
    (the SUBJECT pool — full universe for the Intraday Attribution view;
    broad-market indices appear in both benchmark and subject roles and
    are re-added by the runner).

    Returns DataFrame [code, date, subject_close].
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
        return pd.DataFrame(columns=["code", "date", "subject_close"])

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
    """Aggregate ETF turnover per (date, tracking_index) from the
    precomputed stats.index_exts.total_etf_trading_amount (built by
    builds.index exts phase). Populates benchmark_etf_trading_amount AND
    code_etf_trading_amount for index subjects — the ETF-MARKET turnover
    tracking the index, NOT the index's own turnover (a tighter
    ETF-market liquidity measure; known upward bias as the ETF universe
    grows).

    Indices with no tracking ETF (000001, 399001) have NO rows → NULL
    amounts and NULL ratio downstream.

    Returns DataFrame [index_code, date, etf_amount] (yuan).
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
