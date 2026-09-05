"""Industry-grain builder for stats.cross_stats (sec_type='industry').

Migrated from the MERGED broad-market INSERT of
analyze.industry_sentiments.attributions.sql_broad_market (2026-09-04).
Only the CROSS-SECURITY PRIMITIVE migrates here:

  • industry_shared_weight   → code_sec_shared_weight
    (SUM of member indices' pair shared weights vs the benchmark, sourced
    from stats.cross_stats PAIR rows — replaces the former source
    analysis.sec_alloc_perf_attribution; same SUM semantics, same
    NULL/0 handling: HAVING SUM IS NOT NULL, COALESCE(benchmark, 0))
  • benchmark_shared_weight  → benchmark_sec_shared_weight
    (benchmark weight on the UNION of industry member stocks —
    composition CTEs latest/holdings/industry_stocks/benchmark_shared
    carried over verbatim)
  • the trading-amount split: benchmark_trading_amount /
    shared_trading_amount / non_shared_trading_amount
    (shared = Σ stock turnover on industry-union ∩ benchmark stocks,
    close-IS-NOT-NULL parity kept via EXISTS)

The attribution-specific return/rolling-price decomposition
(shared_portfolio returns, non_industry_returns, rolling_*days_price)
STAYS in analysis.industry_attributions — it is not cross-security
sharing logic and keeps its own heavy warm-up chain there.

Both variants share one INSERT template:
  FULL        — force mode (after TRUNCATE; unbounded history, all
                (industry, benchmark) pairs; secondary index dropped).
  INCREMENTAL — $1 = target dates; needed-pairs pruning + target-date
                liquidity split (trading amounts have NO rolling window,
                so no lookback cap is needed at all here).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
#  Composition CTEs — carried over verbatim from the migrated module
#  (single source of truth for the industry-union overlap semantics).
# ---------------------------------------------------------------------------
_COMPOSITION_CTES = """latest AS (
    SELECT code, MAX(snapshot_date) AS max_date
    FROM stats.sec_composition
    WHERE source_type = 'index'
      AND stock_code IS NOT NULL
    GROUP BY code
),
holdings AS (
    SELECT sc.code, sc.stock_code, sc.weight_pct
    FROM stats.sec_composition sc
    JOIN latest ld
        ON sc.code = ld.code AND sc.snapshot_date = ld.max_date
    WHERE sc.source_type = 'index'
      AND sc.stock_code IS NOT NULL
),
industry_stocks AS (
    SELECT DISTINCT cls.industry_id, h.stock_code
    FROM holdings h
    JOIN stats.sec_classification cls
        ON cls.code = h.code AND cls.type = 'index'
    WHERE cls.industry_id IS NOT NULL
      AND cls.industry_id <> ''
      AND cls.is_active = TRUE
      AND cls.is_industry_not_strategy = TRUE
),
benchmark_shared AS (
    -- Per (industry_id, benchmark_code): SUM(benchmark weight_pct) on the
    -- industry's union stocks. Constant across dates. Pairs with no
    -- overlap are absent (NULL after LEFT JOIN -> coerced to 0).
    SELECT
        ist.industry_id,
        h.code AS benchmark_code,
        SUM(h.weight_pct) AS benchmark_shared_weight
    FROM industry_stocks ist
    JOIN holdings h ON h.stock_code = ist.stock_code
    GROUP BY ist.industry_id, h.code
)"""

_SHARED_TAIL = """
INSERT INTO stats.cross_stats
    (code, benchmark_code, date, sec_type,
     code_sec_shared_weight, benchmark_sec_shared_weight,
     benchmark_trading_amount, shared_trading_amount,
     non_shared_trading_amount)
SELECT
    isw.industry_id,
    isw.benchmark_code,
    isw.date,
    'industry' AS sec_type,
    ROUND(isw.industry_shared_weight, 4) AS code_sec_shared_weight,
    COALESCE(ROUND(bsw.benchmark_shared_weight, 4), 0)
        AS benchmark_sec_shared_weight,
    ba.benchmark_trading_amount,
    st.shared_trading_amount,
    CASE
        WHEN ba.benchmark_trading_amount IS NOT NULL
             AND st.shared_trading_amount IS NOT NULL
        THEN ba.benchmark_trading_amount - st.shared_trading_amount
        ELSE NULL
    END AS non_shared_trading_amount
FROM industry_shared isw
LEFT JOIN benchmark_shared bsw
    ON bsw.industry_id = isw.industry_id
   AND bsw.benchmark_code = isw.benchmark_code
LEFT JOIN shared_trading st
    ON st.industry_id = isw.industry_id
   AND st.benchmark_code = isw.benchmark_code
   AND st.date = isw.date
LEFT JOIN bench_amt ba
    ON ba.benchmark_code = isw.benchmark_code
   AND ba.date = isw.date
"""

_INDUSTRY_SHARED_CTE = """industry_shared AS (
    SELECT
        cls.industry_id,
        cs.benchmark_code,
        cs.date,
        SUM(cs.code_sec_shared_weight) AS industry_shared_weight
    FROM stats.cross_stats cs
    JOIN cls_members cls ON cls.code = cs.code
    WHERE cs.sec_type = 'index'
      AND cs.benchmark_code IN (SELECT code FROM broad_codes)
    {date_filter}
    GROUP BY cls.industry_id, cs.benchmark_code, cs.date
    HAVING SUM(cs.code_sec_shared_weight) IS NOT NULL
)"""

_SHARED_TRADING_CTE = """shared_trading AS (
    -- Σ stock turnover on industry-union ∩ benchmark stocks. The EXISTS
    -- close check keeps exact parity with the former attributions SUM
    -- (a stock contributes only when it has a non-NULL close that date).
    -- [PERF-BLOCKER — declared] see _perf.py.
    SELECT
        ss.industry_id,
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
    GROUP BY ss.industry_id, ss.benchmark_code, slm.date
)"""

_BENCH_AMT_CTE = """bench_amt AS (
    SELECT code AS benchmark_code, date, trading_amount
           AS benchmark_trading_amount
    FROM stats.index_basic_stats
    WHERE code IN (SELECT code FROM broad_codes)
      {date_filter}
)"""


def _build_insert_sql(*, incremental: bool) -> str:
    """Assemble the industry-grain INSERT for the given mode."""
    if incremental:
        date_filter = "AND cs.date = ANY($1::date[])"
        needed_pairs_cte = """needed_pairs AS MATERIALIZED (
    -- Pairs with cross_stats pair rows at the TARGET dates: the only
    -- pairs the final INSERT can emit. Prunes the liquidity join.
    SELECT DISTINCT cls.industry_id, cs.benchmark_code
    FROM stats.cross_stats cs
    JOIN cls_members cls ON cls.code = cs.code
    WHERE cs.sec_type = 'index'
      AND cs.benchmark_code IN (SELECT code FROM broad_codes)
      AND cs.date = ANY($1::date[])
),
"""
        pair_filter = ("JOIN needed_pairs np\n"
                       "        ON np.industry_id = ist.industry_id\n"
                       "       AND np.benchmark_code = h.code")
        shared_trading = _SHARED_TRADING_CTE.format(
            date_filter="AND slm.date = ANY($1::date[])"
        )
        bench_amt = _BENCH_AMT_CTE.format(date_filter="AND date = ANY($1::date[])")
    else:
        date_filter = ""
        needed_pairs_cte = ""
        pair_filter = ""
        shared_trading = _SHARED_TRADING_CTE.format(date_filter="")
        bench_amt = _BENCH_AMT_CTE.format(date_filter="")

    return f"""
WITH {_COMPOSITION_CTES},
broad_codes AS (
    SELECT DISTINCT code
    FROM stats.sec_index_tags
    WHERE is_broad_market = TRUE
),
cls_members AS (
    SELECT DISTINCT code, industry_id
    FROM stats.sec_classification cls
    WHERE cls.type = 'index'
      AND cls.industry_id IS NOT NULL
      AND cls.industry_id <> ''
      AND cls.is_active = TRUE
      AND cls.is_industry_not_strategy = TRUE
),
{needed_pairs_cte}{_INDUSTRY_SHARED_CTE.format(date_filter=date_filter)},
shared_stocks AS (
    SELECT DISTINCT
        ist.industry_id,
        h.code AS benchmark_code,
        h.stock_code
    FROM industry_stocks ist
    JOIN holdings h ON h.stock_code = ist.stock_code
    {pair_filter}
    WHERE h.code IN (SELECT code FROM broad_codes)
),
{shared_trading},
{bench_amt}
{_SHARED_TAIL}
"""


# Force mode (all industries at once, plain INSERT after TRUNCATE;
# secondary indexes are dropped before this runs and recreated after).
INDUSTRY_INSERT_SQL_FULL = _build_insert_sql(incremental=False)

# Incremental mode (date-filtered, needed-pairs-pruned, target-date
# liquidity split, plain INSERT — target dates are pruned to genuinely
# missing ones and the step runs inside a transaction, so no ON CONFLICT).
INDUSTRY_INSERT_SQL_INCREMENTAL = _build_insert_sql(incremental=True)

__all__ = [
    "INDUSTRY_INSERT_SQL_FULL",
    "INDUSTRY_INSERT_SQL_INCREMENTAL",
]
