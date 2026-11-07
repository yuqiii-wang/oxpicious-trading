-- Latest stats.index_exts row for ONE index — the precomputed
-- total_etf_trading_amount (Σ ETF turnover tracking the index, yuan) and
-- its 5-day moving average; both NULL when the index has no tracking ETF.
-- Serves GET /api/sec-composition/linked-etfs.
-- args:
--   index_code: value text — the index code
SELECT total_etf_trading_amount, total_etf_trading_amount_ma5, date::text AS date
         FROM stats.index_exts
        WHERE code = {{index_code}}
        ORDER BY date DESC
        LIMIT 1
