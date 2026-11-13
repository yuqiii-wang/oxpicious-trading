-- Market Participation pipeline (analysis.market_participation) —
-- per-date exchange turnover from stats.exchange_trading_amt, pivoted
-- to one row per date with the SS / SZ proxy columns. Only dates where
-- BOTH proxies reported carry rows (the table's row-existence rule);
-- NULL trading_amount rows are already absent upstream (estimated-close
-- gaps are not inserted there).
-- args: none
    SELECT ss.date AS date,
           extract(epoch from ss.date)::float8 AS date_epoch,
           ss.total_trading_amount::float8  AS ss_trading_amount,
           sz.total_trading_amount::float8  AS sz_trading_amount
    FROM stats.exchange_trading_amt ss
    JOIN stats.exchange_trading_amt sz
      ON sz.date = ss.date AND sz.exchange = 'SZ'
    WHERE ss.exchange = 'SS'
    ORDER BY ss.date
