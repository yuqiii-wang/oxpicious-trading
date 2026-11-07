-- Perf-attr attribution — one subject code's benchmark rows for the latest
-- (or requested) date: shared sec weights, the on-the-fly fractional
-- benchmark/subject returns (LATERAL joins to stats.index_basic_stats —
-- return = (close_t - close_{t-1}) / close_{t-1}) and the ETF-market
-- liquidity triple (benchmark/code total_etf_trading_amount + ratio, NULL
-- when either amount is NULL/0 or |ratio| >= 1e6, from stats.index_exts).
-- Serves GET /api/analysis/perf-attr/attribution.
-- args (listed in the original $n order — the {{...}} refs at the end of
-- these lines pin pg param numbering to the original query's $1/$2/$3
-- because the query's FIRST positional use is $3 and the loader numbers
-- slots by first appearance; removing the refs only renumbers, never
-- rebinds):
--   sec_type: value text — cross_stats sec_type — {{sec_type}}
--   codes: value text[] — the subject's code variants (codeVariants) — {{codes}}
--   target_date: value date — requested date or NULL (= MAX(date) fallback) — {{target_date}}
--   name_table: fragment — PERF_ATTR_NAME_TABLE[sec_type] identity table
    WITH target_date AS (
      SELECT COALESCE(
        {{target_date}}::date,
        (SELECT MAX(date) FROM stats.cross_stats
         WHERE sec_type = {{sec_type}}::text
           AND code = ANY({{codes}}::text[]))
      ) AS max_date
    ),
    subject_name AS (
      SELECT DISTINCT ON (code) code, name
      FROM {{name_table}}
      WHERE code = ANY({{codes}}::text[])
      ORDER BY code, date DESC
    )
    SELECT
      a.benchmark_code,
      bi.name AS benchmark_name,
      a.date,
      a.code_sec_shared_weight,
      a.benchmark_sec_shared_weight,
      er.ratio AS etf_trading_amount_ratio,
      beb.total_etf_trading_amount AS benchmark_etf_trading_amount,
      ceb.total_etf_trading_amount AS code_etf_trading_amount,
      bm.is_broad_market,
      (ib.close - pb.close) / NULLIF(pb.close, 0) AS benchmark_return,
      (sb.close - ps.close) / NULLIF(ps.close, 0) AS subject_return
    FROM stats.cross_stats a
    CROSS JOIN target_date ld
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) code, name
      FROM stats.index_identity
      WHERE code = a.benchmark_code
      ORDER BY code, date DESC
    ) bi ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = a.benchmark_code AND date = a.date
    ) ib ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = a.benchmark_code AND date < a.date
      ORDER BY date DESC LIMIT 1
    ) pb ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = a.code AND date = a.date
    ) sb ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = a.code AND date < a.date
      ORDER BY date DESC LIMIT 1
    ) ps ON true
    LEFT JOIN LATERAL (
      SELECT total_etf_trading_amount FROM stats.index_exts
      WHERE code = a.benchmark_code AND date = a.date
    ) beb ON true
    LEFT JOIN LATERAL (
      SELECT total_etf_trading_amount FROM stats.index_exts
      WHERE code = a.code AND date = a.date
    ) ceb ON true
    -- The ETF liquidity ratio, guarded by a plain WHERE: the ratio row
    -- only exists when 0 < |ratio| < 1e6 (NULL/0 inputs yield a NULL
    -- ratio via NULLIF → no row → the LEFT JOIN surfaces NULL — the
    -- retired CASE's guard, unchanged).
    LEFT JOIN LATERAL (
      SELECT x.ratio
      FROM (SELECT beb.total_etf_trading_amount
                   / NULLIF(ceb.total_etf_trading_amount, 0) AS ratio) x
      WHERE abs(x.ratio) < 1000000
    ) er ON true
    LEFT JOIN LATERAL (
      SELECT BOOL_OR(is_broad_market) AS is_broad_market
      FROM stats.sec_index_tags
      WHERE code = a.benchmark_code
    ) bm ON true
    WHERE a.sec_type = {{sec_type}}::text
      AND a.code = ANY({{codes}}::text[])
      AND a.date = ld.max_date
    ORDER BY a.benchmark_code
