"""Broad-market benchmark SQL for the industry-attributions step.

Shared composition CTEs (latest/holdings/industry_stocks/benchmark_shared)
plus the MERGED broad-market INSERT that computes the weights AND the
non_this_industry_* columns in one CTE pass (no separate UPDATE step, no
per-industry loop). Two prebuilt variants:

  MERGED_BROAD_MARKET_INSERT_SQL_FULL        — force mode (after TRUNCATE,
                                               indexes dropped, plain INSERT).
  MERGED_BROAD_MARKET_INSERT_SQL_INCREMENTAL — date-filtered
                                               (``sa.date = ANY($1::date[])``)
                                               + trading-day-precise lookback
                                               cap (``$2`` = history start),
                                               needed-pairs-pruned fan-out,
                                               liquidity split, plain INSERT.
"""
from __future__ import annotations

from analyze.industry_sentiments.attributions.config import (
    ROLLING_WINDOWS,
)


# ---------------------------------------------------------------------------
#  Composition CTEs — shared between the broad-market INSERT and the
#  member-index map populate. Defined once to avoid duplication.
#
#  CTE chain:
#    latest            — latest snapshot_date per index code (source_type
#                        ='index', stock_code NOT NULL).
#    holdings          — latest-snapshot holdings (code, stock_code,
#                        weight_pct) for indices.
#    industry_stocks   — DISTINCT (industry_id, stock_code) across all member
#                        indices (the UNION of stocks held by ANY industry
#                        member). Each stock counted once per industry.
#    benchmark_shared  — per (industry_id, benchmark_code): SUM(benchmark
#                        weight_pct) on the industry's union stocks.
#                        Constant across dates. Pairs with no overlap are
#                        absent (NULL after LEFT JOIN -> coerced to 0).
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
      {industry_filter}
),
benchmark_shared AS (
    SELECT
        ist.industry_id,
        h.code AS benchmark_code,
        SUM(h.weight_pct) AS benchmark_shared_weight
    FROM industry_stocks ist
    JOIN holdings h ON h.stock_code = ist.stock_code
    GROUP BY ist.industry_id, h.code
)"""


def _format_composition_ctes(industry_filter: str = "") -> str:
    """Format _COMPOSITION_CTES with the given industry_filter placeholder."""
    return _COMPOSITION_CTES.format(industry_filter=industry_filter)


# ---------------------------------------------------------------------------
#  Rolling price expression
#
#  Non-this-industry price / rolling_Xdays_price / trading_amt computation.
#  Computed ONLY for broad-market benchmarks (sec_index_tags.is_broad_market);
#  for non-broad benchmarks the columns remain NULL.
#
#  Return-based decomposition:
#    shared_portfolio_return = SUM(weight × stock_return) / SUM(weight)
#      for stocks shared between the benchmark and the industry union.
#    non_industry_return = (bench_return - swf × shared_return) / (1 - swf)
#      where swf = benchmark_shared_weight / 100.
#    price (today)  = bench_prev_close × (1 + non_industry_return)
#    rolling_Xdays_price = 100 × exp(sum(ln(1 + non_industry_return))) over
#                      the trailing X-day window ending on `date`. NULL
#                      returns are treated as 0 so the cumprod carries
#                      forward; returns outside [-0.5, 0.5] also treated as 0
#                      to prevent compounding artifacts.
#    trading_amt    = bench.trading_amount - SUM(shared_stock.trading_amount)
def _rolling_price_expr(window: int) -> str:
    """Generate the SQL expression for one rolling X-day price column.

    Computes ``100 × exp(sum(ln(1+r)))`` over the trailing X-day window
    (ROWS BETWEEN (X-1) PRECEDING AND CURRENT ROW). Returns outside
    [-0.5, 0.5] are treated as 0 to prevent artifacts from compounding.
    NULL returns also treated as 0 so the cumprod carries forward.
    """
    return f"""        100.0 * exp(
            SUM(CASE
                WHEN nir.non_industry_return IS NOT NULL
                     AND nir.non_industry_return > -0.5
                     AND nir.non_industry_return <= 0.5
                THEN ln(1.0 + nir.non_industry_return)
                ELSE 0
            END) OVER (
                PARTITION BY nir.industry_id, nir.benchmark_code
                ORDER BY nir.date
                ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW
            )
        ) AS non_this_industry_rolling_{window}days_price"""


def _rolling_select() -> str:
    return ",\n        ".join(_rolling_price_expr(w) for w in ROLLING_WINDOWS)


def _rolling_cols() -> str:
    return ",\n".join(
        f"     benchmark_non_this_industry_rolling_{w}days_price"
        for w in ROLLING_WINDOWS
    )


def _rolling_select_cols() -> str:
    return ",\n".join(
        f"    c.non_this_industry_rolling_{w}days_price"
        for w in ROLLING_WINDOWS
    )


# ---------------------------------------------------------------------------
#  Merged broad-market INSERT (one template, two presets).
#
#  Combines the weight INSERT (industry_shared_weight +
#  benchmark_shared_weight) with the non_this_industry computation
#  (price, rolling_*_price, trading_amt) into a SINGLE INSERT...SELECT
#  that populates ALL columns in one pass.
#
#  This eliminates:
#    * The per-industry loop (61 separate INSERTs) — runs all-at-once.
#    * The separate 2M-row UPDATE pass — non_this_industry_* are computed
#      in the same CTE chain and written at INSERT time.
#
#  Shared tail (identical between variants — computed/non_industry_returns
#  alias ``shared_trading_amt`` from either sp (full) or sta (incremental),
#  so the INSERT column list and expressions are literal-identical):
#    {trading_amt_source}   non_this_industry_trading_amt source column
#    {trading_amt_join}     extra LEFT JOIN feeding it (incremental only)
#
#  Variant slots:
#    FULL: no params/filters — unbounded history, all (industry,
#          benchmark) pairs, liquidity join inline in stock_daily so
#          shared_trading_amt rides the existing fan-out for free.
#    INCREMENTAL ($1 = target dates, $2 = lookback start):
#      * {needed_pairs_cte} — pairs (industry, benchmark) that actually
#        have source rows at the TARGET dates. The old chain fanned out
#        ALL ~6.4K pairs × history; only ~1.4K pairs per day are ever
#        inserted (final SELECT drives from industry_shared). Pruning
#        shared_stocks + non_industry_returns to needed pairs cuts the
#        shared_portfolio fan-out ~4× (measured 2026-08-30).
#      * {pair_filter} — shared_stocks JOIN needed_pairs.
#      * {pairs_filter} — non_industry_returns JOIN needed_pairs (the
#        rolling-window partitions are per pair, so filtering whole
#        pairs out never affects a needed pair's window).
#      * {stock_amt_col}/{returns_amt_col}/{liquidity_join} — LIQUIDITY
#        SPLIT: trading_amount has NO rolling window, so the incremental
#        path drops the stock_liquidity_margin join (2.7M rows scanned
#        per run) from the heavy warm-up path entirely and computes
#        shared_trading_amt only at target dates
#        (shared_trading_amt_target CTE; the EXISTS close check keeps
#        exact parity with the former stock_returns-based SUM).
#      * {stock_hist_filter}/{bench_hist_filter} — trading-day-precise
#        lookback cap ($2, resolved by fetch_incremental_lookback_date):
#        500 window rows + LAG row + grid margin, plus a 45-calendar-day
#        stock-LAG suspension margin baked into $2. Plain INSERT —
#        run_attributions prunes target dates to genuinely missing ones
#        and wraps the step in a transaction.
_MERGED_BROAD_MARKET_INSERT_SQL_TEMPLATE = """
WITH {composition_ctes},
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
{needed_pairs_cte}industry_shared AS (
    SELECT
        cls.industry_id,
        sa.benchmark_code,
        sa.date,
        SUM(sa.code_sec_shared_weight) AS industry_shared_weight
    FROM analysis.sec_alloc_perf_attribution sa
    JOIN cls_members cls ON cls.code = sa.code
    WHERE sa.sec_type = 'index'
      AND sa.benchmark_code IN (SELECT code FROM broad_codes)
    {date_filter}
    GROUP BY cls.industry_id, sa.benchmark_code, sa.date
    HAVING SUM(sa.code_sec_shared_weight) IS NOT NULL
),
-- Non-this-industry price decomposition (computed at INSERT time).
shared_stocks AS (
    SELECT DISTINCT
        ist.industry_id,
        h.code AS benchmark_code,
        h.stock_code,
        h.weight_pct
    FROM industry_stocks ist
    JOIN holdings h ON h.stock_code = ist.stock_code
    {pair_filter}
    WHERE h.code IN (SELECT code FROM broad_codes)
),
unique_stock_codes AS (
    SELECT DISTINCT stock_code FROM shared_stocks
),
stock_daily AS MATERIALIZED (
    SELECT
        usc.stock_code,
        sbs.date,
        sbs.close,
        {stock_amt_col}
        LAG(sbs.close) OVER w AS prev_close
    FROM unique_stock_codes usc
    JOIN stats.stock_basic_stats sbs ON sbs.code = usc.stock_code
    {liquidity_join}
    WHERE sbs.close IS NOT NULL
      {stock_hist_filter}
    WINDOW w AS (PARTITION BY usc.stock_code ORDER BY sbs.date)
),
stock_returns AS MATERIALIZED (
    SELECT
        stock_code,
        date,
        {returns_amt_col}
        CASE
            WHEN prev_close IS NOT NULL AND prev_close != 0
            THEN (close - prev_close) / prev_close
            ELSE NULL
        END AS stock_return
    FROM stock_daily
),
bench_daily AS (
    SELECT
        code AS benchmark_code,
        date,
        close,
        trading_amount,
        LAG(close) OVER w AS prev_close
    FROM stats.index_basic_stats
    WHERE code IN (SELECT code FROM broad_codes)
      AND close IS NOT NULL
      {bench_hist_filter}
    WINDOW w AS (PARTITION BY code ORDER BY date)
),
bench_returns AS (
    SELECT
        benchmark_code,
        date,
        close,
        prev_close,
        trading_amount,
        CASE
            WHEN prev_close IS NOT NULL AND prev_close != 0
            THEN (close - prev_close) / prev_close
            ELSE NULL
        END AS bench_return
    FROM bench_daily
),
shared_portfolio AS MATERIALIZED (
    SELECT
        ss.industry_id,
        ss.benchmark_code,
        sr.date,
        SUM(ss.weight_pct * sr.stock_return)
            / NULLIF(SUM(ss.weight_pct) FILTER (WHERE sr.stock_return IS NOT NULL), 0)
            AS shared_return
        {shared_amt_expr}
    FROM shared_stocks ss
    JOIN stock_returns sr ON sr.stock_code = ss.stock_code
    GROUP BY ss.industry_id, ss.benchmark_code, sr.date
),
{trading_amt_cte}non_industry_returns AS MATERIALIZED (
    SELECT
        br.benchmark_code,
        bsw.industry_id,
        br.date,
        br.prev_close AS bench_prev_close,
        br.trading_amount AS bench_trading_amt,
        sp.shared_return,
        {trading_amt_source}
        bsw.benchmark_shared_weight,
        CASE
            WHEN br.prev_close IS NULL OR br.prev_close = 0 THEN NULL
            WHEN bsw.benchmark_shared_weight IS NULL THEN NULL
            WHEN bsw.benchmark_shared_weight >= 95 THEN NULL
            WHEN bsw.benchmark_shared_weight = 0 OR sp.shared_return IS NULL THEN
                (br.close - br.prev_close) / br.prev_close
            ELSE
                (
                    (br.close - br.prev_close) / br.prev_close
                    - (bsw.benchmark_shared_weight / 100.0) * sp.shared_return
                ) / (1.0 - bsw.benchmark_shared_weight / 100.0)
        END AS non_industry_return
    FROM bench_returns br
    JOIN benchmark_shared bsw
        ON bsw.benchmark_code = br.benchmark_code
    {pairs_filter}
    LEFT JOIN shared_portfolio sp
        ON sp.benchmark_code = br.benchmark_code
       AND sp.industry_id = bsw.industry_id
       AND sp.date = br.date
    {trading_amt_join}
),
computed AS MATERIALIZED (
    SELECT
        nir.industry_id,
        nir.benchmark_code,
        nir.date,
        CASE
            WHEN nir.bench_prev_close IS NOT NULL
                 AND nir.non_industry_return IS NOT NULL
                 AND abs(nir.non_industry_return) <= 0.5
            THEN nir.bench_prev_close * (1 + nir.non_industry_return)
            ELSE NULL
        END AS non_this_industry_price,
{rolling_select},
        CASE
            WHEN nir.bench_trading_amt IS NOT NULL
                 AND nir.shared_trading_amt IS NOT NULL
            THEN nir.bench_trading_amt - nir.shared_trading_amt
            ELSE NULL
        END AS non_this_industry_trading_amt
    FROM non_industry_returns nir
)
INSERT INTO analysis.industry_attributions
    (industry_id, benchmark_code, date, attribution_type,
     industry_shared_weight, benchmark_shared_weight,
     benchmark_non_this_industry_price,
{rolling_cols},
     benchmark_non_this_industry_trading_amt)
SELECT
    isw.industry_id,
    isw.benchmark_code,
    isw.date,
    'trading_amt' AS attribution_type,
    ROUND(isw.industry_shared_weight, 4) AS industry_shared_weight,
    COALESCE(ROUND(bsw.benchmark_shared_weight, 4), 0) AS benchmark_shared_weight,
    c.non_this_industry_price,
{rolling_select_cols},
    c.non_this_industry_trading_amt
FROM industry_shared isw
LEFT JOIN benchmark_shared bsw
    ON bsw.industry_id = isw.industry_id
   AND bsw.benchmark_code = isw.benchmark_code
LEFT JOIN computed c
    ON c.industry_id = isw.industry_id
   AND c.benchmark_code = isw.benchmark_code
   AND c.date = isw.date
{on_conflict}
"""

# Force-mode slot values: unbounded, all pairs, liquidity rides the
# existing fan-out (trading_amount needed for ALL dates there).
_FULL_SLOTS = dict(
    date_filter="",
    needed_pairs_cte="",
    pair_filter="",
    pairs_filter="",
    stock_amt_col="slm.trading_amount,",
    returns_amt_col="trading_amount,",
    liquidity_join=(
        "LEFT JOIN stats.stock_liquidity_margin slm\n"
        "        ON slm.date = sbs.date AND slm.code = sbs.code"
    ),
    stock_hist_filter="",
    bench_hist_filter="",
    shared_amt_expr=",\n        SUM(sr.trading_amount) AS shared_trading_amt",
    trading_amt_cte="",
    trading_amt_source="sp.shared_trading_amt,",
    trading_amt_join="",
)

# Incremental slot values (B-A5 cap, implemented 2026-08-30): needed-pairs
# pruning + liquidity split + trading-day-precise lookback ($1 = target
# dates, $2 = history start from fetch_incremental_lookback_date).
_INCREMENTAL_SLOTS = dict(
    date_filter="AND sa.date = ANY($1::date[])",
    needed_pairs_cte="""needed_pairs AS MATERIALIZED (
    -- Pairs with source rows at the TARGET dates: the only pairs the
    -- final INSERT can emit. Pruning the heavy warm-up chain to these
    -- keeps computed rolling windows bit-identical (windows are
    -- partitioned per pair) while cutting the fan-out ~4x.
    SELECT DISTINCT cls.industry_id, sa.benchmark_code
    FROM analysis.sec_alloc_perf_attribution sa
    JOIN cls_members cls ON cls.code = sa.code
    WHERE sa.sec_type = 'index'
      AND sa.benchmark_code IN (SELECT code FROM broad_codes)
      AND sa.date = ANY($1::date[])
),
""",
    pair_filter=(
        "JOIN needed_pairs np\n"
        "        ON np.industry_id = ist.industry_id\n"
        "       AND np.benchmark_code = h.code"
    ),
    pairs_filter=(
        "JOIN needed_pairs npf\n"
        "        ON npf.industry_id = bsw.industry_id\n"
        "       AND npf.benchmark_code = br.benchmark_code"
    ),
    stock_amt_col="",
    returns_amt_col="",
    liquidity_join="",
    stock_hist_filter="AND sbs.date >= $2::date",
    bench_hist_filter="AND date >= $2::date",
    shared_amt_expr="",
    trading_amt_cte="""shared_trading_amt_target AS MATERIALIZED (
    -- trading_amt has NO rolling window: only target-date values are
    -- inserted, so the liquidity join runs on $1 dates only (the heavy
    -- warm-up path never needs trading_amount). EXISTS keeps exact
    -- parity with the former stock_returns-based SUM (a stock
    -- contributes only if it has a non-NULL close that date).
    SELECT
        ss.industry_id,
        ss.benchmark_code,
        slm.date,
        SUM(slm.trading_amount) AS shared_trading_amt
    FROM shared_stocks ss
    JOIN stats.stock_liquidity_margin slm
        ON slm.code = ss.stock_code
       AND slm.date = ANY($1::date[])
    WHERE EXISTS (
        SELECT 1 FROM stats.stock_basic_stats sbs
        WHERE sbs.code = slm.code AND sbs.date = slm.date
          AND sbs.close IS NOT NULL
    )
    GROUP BY ss.industry_id, ss.benchmark_code, slm.date
),
""",
    trading_amt_source="sta.shared_trading_amt,",
    trading_amt_join=(
        "LEFT JOIN shared_trading_amt_target sta\n"
        "        ON sta.benchmark_code = br.benchmark_code\n"
        "       AND sta.industry_id = bsw.industry_id\n"
        "       AND sta.date = br.date"
    ),
)


def _build_merged_broad_market_insert_sql(slots: dict) -> str:
    """Assemble the merged broad-market INSERT for the given slot preset."""
    return _MERGED_BROAD_MARKET_INSERT_SQL_TEMPLATE.format(
        composition_ctes=_format_composition_ctes(""),
        rolling_select=_rolling_select(),
        rolling_cols=_rolling_cols(),
        rolling_select_cols=_rolling_select_cols(),
        on_conflict="",
        **slots,
    )


# Force mode (all industries at once, plain INSERT after TRUNCATE).
# Secondary indexes are dropped before this runs and recreated after.
MERGED_BROAD_MARKET_INSERT_SQL_FULL = _build_merged_broad_market_insert_sql(
    _FULL_SLOTS)

# Incremental mode (date-filtered, needed-pairs-pruned, liquidity-split,
# trading-day-precise lookback cap, plain INSERT — target dates are pruned
# to genuinely missing ones + transaction-wrapped, so no ON CONFLICT).
MERGED_BROAD_MARKET_INSERT_SQL_INCREMENTAL = _build_merged_broad_market_insert_sql(
    _INCREMENTAL_SLOTS)

# Re-exported for the member-index SQL module (shared composition CTEs).
__all__ = [
    "MERGED_BROAD_MARKET_INSERT_SQL_FULL",
    "MERGED_BROAD_MARKET_INSERT_SQL_INCREMENTAL",
    "LOOKBACK_EXTRA_CALENDAR_DAYS",
    "LOOKBACK_TRADING_DAYS",
    "_format_composition_ctes",
]
