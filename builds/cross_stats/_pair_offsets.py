"""Pair-grain weighted-offset SQL pass → code_price_with_benchmark_offset_by_weighted_amt.

THE amount-weighting half of the consolidated benchmark-offset primitive
(the plain offset itself is computed in the pandas pipeline — see
compute/_merge.py). Semantics mirror the attribution decomposition in
analyze.industry_sentiments.attributions.sql_broad_market, where the
benchmark's move is split by the shared fraction
(``non_industry_return = (bench_return − swf × shared_return) / (1 − swf)``)
and the trading-amount split is materialized as
shared vs non_shared turnover. At pair grain:

  shared_amt_weight = shared_trading_amount / benchmark_trading_amount
    shared_trading_amount   = Σ stock_liquidity_margin.trading_amount over
                              the subject ∩ benchmark shared stocks (a
                              stock contributes only with a non-NULL close
                              that date — the industry grain's parity rule)
    benchmark_trading_amount = stats.index_basic_stats.trading_amount
  code_price_with_benchmark_offset_by_weighted_amt
      = code_price_with_benchmark_offset × shared_amt_weight

NULL when the pair has no composition overlap (never in shared_amt), an
input amount is unknown, or the benchmark turnover is 0 — the unweighted
offset stays authoritative.

The shared-stock SET is snapshot-constant (LATEST sec_composition snapshot
per code — same convention as fetch_shared_weights), so only the liquidity
join is date-scoped: INCREMENTAL ($1 = target dates) runs after every
pair-grain build; FULL (unbounded) is the one-off ``--backfill-offsets``
pass, which ALSO backfills the plain offset onto rows written before the
columns existed (LAG over stats.index_basic_stats per code/benchmark).
"""
from __future__ import annotations

# Shared composition + turnover CTEs. {date_filter} slots scope the
# date-varying joins; composition CTEs are date-free by the temporal
# convention (latest snapshot for all dates).
_WEIGHTED_UPDATE_TEMPLATE = """
WITH latest AS (
    SELECT code, MAX(snapshot_date) AS max_date
    FROM stats.sec_composition
    WHERE source_type = 'index'
      AND stock_code IS NOT NULL
    GROUP BY code
),
holdings AS (
    SELECT sc.code,
           sc.stock_code,
           LEFT(sc.stock_code, 6) AS normalized_code
    FROM stats.sec_composition sc
    JOIN latest ld
        ON sc.code = ld.code AND sc.snapshot_date = ld.max_date
    WHERE sc.source_type = 'index'
      AND sc.stock_code IS NOT NULL
),
shared_stocks AS (
    -- (subject, benchmark, shared stock) triples — the same overlap the
    -- shared weights derive from, exploded to stock grain for the
    -- turnover sum. DISTINCT: duplicate snapshot rows collapse.
    SELECT DISTINCT
        h1.code AS subject_code,
        h2.code AS benchmark_code,
        h1.stock_code
    FROM holdings h1
    JOIN holdings h2
        ON h2.normalized_code = h1.normalized_code
    WHERE h1.code <> h2.code
),
shared_amt AS (
    SELECT
        ss.subject_code,
        ss.benchmark_code,
        slm.date,
        SUM(slm.trading_amount) AS shared_trading_amount
    FROM shared_stocks ss
    JOIN stats.stock_liquidity_margin slm
        ON slm.code = ss.stock_code
    {date_filter}
    WHERE EXISTS (
        SELECT 1 FROM stats.stock_basic_stats sbs
        WHERE sbs.code = slm.code AND sbs.date = slm.date
          AND sbs.close IS NOT NULL
    )
    GROUP BY ss.subject_code, ss.benchmark_code, slm.date
),
bench_amt AS (
    SELECT code AS benchmark_code, date,
           trading_amount AS benchmark_trading_amount
    FROM stats.index_basic_stats
    WHERE 1=1
      {bench_date_filter}
)
UPDATE stats.cross_stats cs
SET code_price_with_benchmark_offset_by_weighted_amt =
        cs.code_price_with_benchmark_offset
        * (sa.shared_trading_amount / ba.benchmark_trading_amount)
FROM shared_amt sa
JOIN bench_amt ba
    ON ba.benchmark_code = sa.benchmark_code
   AND ba.date = sa.date
WHERE cs.sec_type = 'index'
  AND cs.code = sa.subject_code
  AND cs.benchmark_code = sa.benchmark_code
  AND cs.date = sa.date
  AND ba.benchmark_trading_amount <> 0
  AND cs.code_price_with_benchmark_offset IS NOT NULL
"""

# Incremental: target dates only ($1). The (sec_type, date) secondary
# index drives the probe; the UPDATE joins on the PK.
PAIR_WEIGHTED_UPDATE_SQL_INCREMENTAL = _WEIGHTED_UPDATE_TEMPLATE.format(
    date_filter="AND slm.date = ANY($1::date[])",
    bench_date_filter="AND date = ANY($1::date[])",
)

# Full: unbounded (the one-off backfill pass; runs once per deployment).
PAIR_WEIGHTED_UPDATE_SQL_FULL = _WEIGHTED_UPDATE_TEMPLATE.format(
    date_filter="",
    bench_date_filter="",
)

# Backfill of the PLAIN offset onto rows written before the column
# existed (per-series LAG over stats.index_basic_stats — each series'
# own trading calendar, matching the pandas diff() convention). Rows
# already carrying an offset are untouched, so the pass is idempotent
# and never clobbers pipeline-written values.
PAIR_OFFSET_BACKFILL_SQL = """
WITH code_diffs AS (
    SELECT code, date,
           close - LAG(close) OVER (PARTITION BY code ORDER BY date)
               AS code_price_change
    FROM stats.index_basic_stats
    WHERE close IS NOT NULL
),
pair_changes AS (
    SELECT cs.code, cs.benchmark_code, cs.date,
           cd.code_price_change,
           bd.benchmark_code_price_change
    FROM stats.cross_stats cs
    JOIN code_diffs cd
        ON cd.code = cs.code AND cd.date = cs.date
    JOIN (
        SELECT code AS benchmark_code, date,
               close - LAG(close) OVER (PARTITION BY code ORDER BY date)
                   AS benchmark_code_price_change
        FROM stats.index_basic_stats
        WHERE close IS NOT NULL
    ) bd
        ON bd.benchmark_code = cs.benchmark_code
       AND bd.date = cs.date
    WHERE cs.sec_type = 'index'
      AND cs.code_price_with_benchmark_offset IS NULL
)
UPDATE stats.cross_stats cs
SET code_price_with_benchmark_offset =
        pc.code_price_change - pc.benchmark_code_price_change
FROM pair_changes pc
WHERE cs.sec_type = 'index'
  AND cs.code = pc.code
  AND cs.benchmark_code = pc.benchmark_code
  AND cs.date = pc.date
"""

__all__ = [
    "PAIR_WEIGHTED_UPDATE_SQL_INCREMENTAL",
    "PAIR_WEIGHTED_UPDATE_SQL_FULL",
    "PAIR_OFFSET_BACKFILL_SQL",
]
