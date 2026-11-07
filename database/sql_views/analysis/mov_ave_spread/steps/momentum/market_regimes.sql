-- mov_ave_spread momentum step — the sec_type's DAILY market-regime
-- registry states (code, date, regime label code, vol evidence) from
-- stats.market_regimes. Feeds
-- analysis.mov_ave_spread.steps.momentum._fetch_market_regimes.
-- args:
--   sec_type: value smallint — the sec_type's static.label_dim code
        SELECT m.code,
               extract(epoch from m.date)::float8 AS date,
               m.regime::int AS regime,
               m.vol::float8 AS vol
        FROM stats.market_regimes m
        WHERE m.sec_type = {{sec_type}}
        ORDER BY m.code, m.date ASC
