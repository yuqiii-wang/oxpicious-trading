-- ETF contribution step (analysis.market_beta internal pipeline) guard —
-- bail out early if stats.index_exts (built by builds.index exts phase)
-- has no non-NULL total_etf_trading_amount rows to materialize. (The
-- former relay via stats.cross_stats.code_etf_trading_amount was
-- consolidated away 2026-09-07 as a verbatim duplicate.)
-- args: none
    SELECT COUNT(*) AS n
    FROM stats.index_exts
    WHERE total_etf_trading_amount IS NOT NULL
