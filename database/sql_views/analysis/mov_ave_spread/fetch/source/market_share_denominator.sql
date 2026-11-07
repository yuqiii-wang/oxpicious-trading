-- Market-share denominator for the mov_ave_spread trading-amount
-- market-share MAs: per-date SUM(total_trading_amount) across
-- stats.exchange_trading_amt rows whose exchange is a primary exchange
-- (stats.sec_classification.is_primary_exchange = TRUE — the set is
-- derived at query time so flag changes need no redeploy).
-- Feeds analysis.mov_ave_spread.fetch._fetch_market_share_denominator.
-- args: none
    SELECT date, SUM(total_trading_amount) AS denominator
    FROM stats.exchange_trading_amt
    WHERE exchange IN (
        SELECT DISTINCT exchange
        FROM stats.sec_classification
        WHERE is_primary_exchange = TRUE
          AND exchange IS NOT NULL
    )
    GROUP BY date
