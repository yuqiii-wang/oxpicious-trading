-- Market crowdedness — per-stock daily return inputs + pe (stats.
-- stock_basic_stats; the v2 co-movement / valuation pillars).
-- pct_change is NULL on the SSE archive, so close rides along and the
-- return is derived in-frame as close/prev_close - 1 where pct_change
-- is missing. pe is NULL for loss-making / no-data names.
-- args:
--   stock_codes: value text[] — the sorted member stock codes
--   lo: value date — the window's lower bound
--   hi: value date — the window's upper bound
    SELECT code AS stock_code,
           extract(epoch from date)::float8 AS date,
           pct_change::float8 AS pct,
           close::float8 AS close,
           pe::float8 AS pe
    FROM stats.stock_basic_stats
    WHERE code = ANY({{stock_codes}}::text[])
      AND (pct_change IS NOT NULL OR close IS NOT NULL OR pe IS NOT NULL)
      AND date >= {{lo}}::date
      AND date <= {{hi}}::date
    ORDER BY code, date ASC
