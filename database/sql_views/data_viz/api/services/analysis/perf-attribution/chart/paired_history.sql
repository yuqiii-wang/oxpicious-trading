-- Perf-attr paired history — one (subject, benchmark) pair's daily rows:
-- the ETF liquidity ratio derived at READ time from
-- stats.index_exts.total_etf_trading_amount (bench/code, NULL when either
-- amount is NULL/0 or |ratio| >= 1e6 — the former NUMERIC(10,4) build cap),
-- its 5-trading-day trailing mean (NULLs skipped — pandas
-- rolling(5, min_periods=1) parity), etf counts, correlation windows, and
-- the sec_type-appropriate subject close (fragments).
-- Serves GET /api/analysis/perf-attr/chart.
-- args:
--   sec_type: value text — cross_stats sec_type
--   codes: value text[] — the subject's code variants (codeVariants)
--   benchmark_code: value text — the paired benchmark code
--   subject_price_expr: fragment — PERF_ATTR_SUBJECT_SOURCE[sec_type].priceExpr
--   subject_join_clause: fragment — PERF_ATTR_SUBJECT_SOURCE[sec_type].joinClause
SELECT date,
              etf_trading_amount_ratio,
              AVG(etf_trading_amount_ratio) OVER (
                  ORDER BY date ASC
                  ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
              ) AS etf_trading_amount_ratio_ma5,
              benchmark_etf_trading_amount,
              code_etf_trading_amount,
              benchmark_etf_num,
              code_etf_num,
              subject_close,
              benchmark_close,
              corr_20d,
              corr_60d,
              corr_255d
       FROM (
         SELECT a.date,
                er.ratio AS etf_trading_amount_ratio,
                ieb.total_etf_trading_amount AS benchmark_etf_trading_amount,
                iec.total_etf_trading_amount AS code_etf_trading_amount,
                ieb.etf_num AS benchmark_etf_num,
                iec.etf_num AS code_etf_num,
                a.corr_20d,
                a.corr_60d,
                a.corr_255d,
                {{subject_price_expr}} AS subject_close,
                ib.close AS benchmark_close
         FROM stats.cross_stats a
         {{subject_join_clause}}
         LEFT JOIN stats.index_basic_stats ib ON ib.date = a.date AND ib.code = a.benchmark_code
         LEFT JOIN stats.index_exts ieb ON ieb.date = a.date AND ieb.code = a.benchmark_code
         LEFT JOIN stats.index_exts iec ON iec.date = a.date AND iec.code = a.code
         -- The ETF liquidity ratio, guarded by a plain WHERE: the ratio
         -- row only exists when 0 < |ratio| < 1e6 (NULL/0 inputs yield a
         -- NULL ratio via NULLIF → no row → the LEFT JOIN surfaces NULL —
         -- the retired CASE's guard, unchanged).
         LEFT JOIN LATERAL (
           SELECT x.ratio
           FROM (SELECT ieb.total_etf_trading_amount
                      / NULLIF(iec.total_etf_trading_amount, 0) AS ratio) x
           WHERE abs(x.ratio) < 1000000
         ) er ON true
         WHERE a.sec_type = {{sec_type}}::text
           AND a.code = ANY({{codes}}::text[])
           AND a.benchmark_code = {{benchmark_code}}::text
       ) t
       ORDER BY date ASC
